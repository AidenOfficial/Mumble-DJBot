"""歌单导入:来源识别、网易云读取、YouTube 匹配打分、后台任务、API。"""
import configparser
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import playlist_import as pi  # noqa: E402
import variables as var  # noqa: E402
import web_users  # noqa: E402


class DetectTest(unittest.TestCase):
    def test_sources(self):
        self.assertEqual(pi.detect_source('https://www.youtube.com/playlist?list=PL123'), 'youtube')
        self.assertEqual(pi.detect_source('https://music.youtube.com/playlist?list=OLAK5'), 'youtube')
        self.assertEqual(pi.detect_source('https://music.163.com/#/playlist?id=79177352'), 'netease')
        self.assertEqual(pi.detect_source('https://music.163.com/playlist?id=79177352&userid=9'), 'netease')
        self.assertEqual(pi.detect_source('https://open.spotify.com/playlist/37i9dQZF1DX'), 'spotify')
        self.assertEqual(pi.detect_source('https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy'), 'spotify')
        for bad in ('https://www.youtube.com/watch?v=abc', 'https://space.bilibili.com/1/favlist?fid=2',
                    'https://music.163.com/#/song?id=1', 'https://open.spotify.com/track/1', 'https://evil.com/?list=1'):
            self.assertIsNone(pi.detect_source(bad), bad)


class NeteaseTest(unittest.TestCase):
    def test_lists_all_tracks_not_just_first_ten(self):
        detail = {'code': 200, 'playlist': {'name': '深夜', 'trackIds': [{'id': i} for i in range(1, 13)],
                                            'tracks': []}}
        songs = {'songs': [{'id': i, 'name': f'Song {i}', 'ar': [{'name': 'A'}, {'name': 'B'}], 'dt': 200000}
                           for i in range(1, 13)]}
        get = mock.Mock(return_value=mock.Mock(json=lambda: detail, raise_for_status=lambda: None))
        post = mock.Mock(return_value=mock.Mock(json=lambda: songs, raise_for_status=lambda: None))
        with mock.patch('playlist_import.requests.get', get), mock.patch('playlist_import.requests.post', post):
            title, tracks = pi.list_netease('https://music.163.com/#/playlist?id=42')
        self.assertEqual(title, '深夜')
        self.assertEqual(len(tracks), 12)
        self.assertEqual(tracks[0], {'title': 'Song 1', 'artist': 'A/B', 'duration': 200.0})
        self.assertEqual(get.call_args.kwargs['params'], {'id': '42'})


class MatchTest(unittest.TestCase):
    TRACK = {'title': 'アイドル', 'artist': 'YOASOBI', 'duration': 213.0}

    def cand(self, title, channel, duration, vid='x'):
        return {'id': vid, 'title': title, 'channel': channel, 'duration': duration,
                'url': f'https://www.youtube.com/watch?v={vid}'}

    def test_prefers_official_over_fan_upload_and_covers(self):
        cands = [self.cand('[AMV/FMV] YOASOBI - IDOL (アイドル)', 'fan', 214, 'amv'),
                 self.cand('アイドル / YOASOBI cover', 'singer', 213, 'cover'),
                 self.cand('YOASOBI「アイドル」 Official Music Video', 'YOASOBI and Echoes', 226, 'mv')]
        self.assertTrue(pi.match_on_youtube(self.TRACK, search=lambda q: cands).endswith('v=mv'))

    def test_topic_channel_wins(self):
        cands = [self.cand('YOASOBI - アイドル (Lyrics)', 'lyric chan', 213, 'lyr'),
                 self.cand('アイドル', 'YOASOBI - Topic', 214, 'topic')]
        self.assertTrue(pi.match_on_youtube(self.TRACK, search=lambda q: cands).endswith('v=topic'))

    def test_wrong_length_rejected(self):
        cands = [self.cand('YOASOBI アイドル 1 hour', 'x', 3600, 'loop')]
        self.assertIsNone(pi.match_on_youtube(self.TRACK, search=lambda q: cands))

    def test_slashes_removed_from_query_and_first_artist_retry(self):
        # 实测:yt-dlp 的 ytsearch 遇到 "A/B/C" 直接返回空
        queries = []

        def search(q):
            queries.append(q)
            if len(queries) == 1:
                return []
            return [self.cand('Prospa - Free Your Mind', 'Prospa', 201, 'ok')]
        track = {'title': 'Free Your Mind [Extended]', 'artist': 'Prospa/Cloonee/Sybil', 'duration': 201}
        self.assertTrue(pi.match_on_youtube(track, search=search).endswith('v=ok'))
        self.assertEqual(queries, ['Prospa Cloonee Sybil Free Your Mind', 'prospa Free Your Mind'])

    def test_vetoes_seen_in_real_imports(self):
        # 同名的别人的歌,时长差很多
        self.assertIsNone(pi.score_candidate(
            {'title': 'Away We Go', 'artist': 'Ted Nights/Magilo', 'duration': 176},
            self.cand('1WayTKT - Away We Go (feat. Matt Beilis)', 'Armair Beats', 257)))
        # 同一歌手的另一首(只共用 "Acoustic" 这种版本词)
        self.assertIsNone(pi.score_candidate(
            {'title': 'Lion - Acoustic', 'artist': 'Hearts & Colors', 'duration': 167},
            self.cand('Hearts & Colors - Waterbed (Acoustic)', 'Hearts & Colors', 186)))
        # 两个词的歌名只对上一个
        self.assertIsNone(pi.score_candidate(
            {'title': 'SMOKE ALT [SKRLX TOOLS]', 'artist': 'Skrillex/ISOxo', 'duration': 185},
            self.cand('Skrillex & ISOxo - Bring Smoke (Knock2 Edit)', 'S6789', 170)))
        self.assertIsNotNone(pi.score_candidate(
            {'title': 'SMOKE ALT [SKRLX TOOLS]', 'artist': 'Skrillex/ISOxo', 'duration': 185},
            self.cand('Skrillex & ISOxo - Smoke (Alt Version)', 'Skrillex', 184)))

    def test_cjk_without_spaces_and_traditional_simplified(self):
        # 中文标题不分词:"周杰倫晴天" 也要能命中
        self.assertIsNotNone(pi.score_candidate({'title': '晴天', 'artist': '周杰伦', 'duration': 269},
                                                self.cand('周杰伦晴天MV', 'x', 270)))
        # 简繁写法不同:时长几乎一致 + 歌手对得上时放行
        self.assertIsNotNone(pi.score_candidate({'title': '灰色头像', 'artist': '許嵩', 'duration': 270},
                                                self.cand('許嵩《灰色頭像》', 'x', 271)))

    def test_search_failure_is_unmatched(self):
        def boom(q):
            raise OSError('no network')
        self.assertIsNone(pi.match_on_youtube(self.TRACK, search=boom))


class SpotifyListTest(unittest.TestCase):
    EMBED = ('<html><script id="__NEXT_DATA__" type="application/json">' + __import__('json').dumps(
        {'props': {'pageProps': {'state': {'data': {'entity': {'name': "That's my 00s", 'trackList': [
            {'title': 'Poker Face', 'subtitle': 'Lady Gaga', 'duration': 237200},
            {'title': 'Love The Way You Lie', 'subtitle': 'Eminem, Rihanna', 'duration': 263373}]}}}}}})
        + '</script></html>')

    def setUp(self):
        self._saved = var.config
        var.config = configparser.ConfigParser()
        var.config.read_dict({'spotify': {'client_id': '', 'client_secret': ''}})

    def tearDown(self):
        var.config = self._saved

    def fake_get(self, n_tracks=None):
        html = self.EMBED
        if n_tracks:
            import json as _j
            tracks = [{'title': f'T{i}', 'subtitle': 'A', 'duration': 1000} for i in range(n_tracks)]
            data = {'props': {'pageProps': {'state': {'data': {'entity': {'name': 'Big', 'trackList': tracks}}}}}}
            html = f'<script id="__NEXT_DATA__" type="application/json">{_j.dumps(data)}</script>'
        return mock.Mock(text=html, raise_for_status=lambda: None)

    def test_embed_page_needs_no_credentials(self):
        with mock.patch('playlist_import.requests.get', return_value=self.fake_get()) as get:
            title, tracks, truncated = pi.list_spotify('https://open.spotify.com/playlist/1RO0QF4WVKhEbho9x4SjB3?si=x')
        self.assertIn('/embed/playlist/1RO0QF4WVKhEbho9x4SjB3', get.call_args[0][0])
        self.assertEqual(title, "That's my 00s")
        self.assertEqual(tracks[1], {'title': 'Love The Way You Lie', 'artist': 'Eminem, Rihanna', 'duration': 263.373})
        self.assertFalse(truncated)

    def test_hundred_tracks_flagged_without_creds_and_spotdl_used_with_creds(self):
        with mock.patch('playlist_import.requests.get', return_value=self.fake_get(100)):
            _, tracks, truncated = pi.list_spotify('https://open.spotify.com/playlist/x')
        self.assertEqual((len(tracks), truncated), (100, True))
        var.config.read_dict({'spotify': {'client_id': 'id', 'client_secret': 'secret'}})
        full = [{'name': f'T{i}', 'artist': 'A', 'duration': 1} for i in range(150)]
        with mock.patch('playlist_import.requests.get', return_value=self.fake_get(100)), \
                mock.patch('media.spotify.list_spotify_tracks', return_value=full):
            _, tracks, truncated = pi.list_spotify('https://open.spotify.com/playlist/x')
        self.assertEqual((len(tracks), truncated), (150, False))


class JobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = (var.user_db, var.config)
        var.user_db = web_users.UserDatabase(os.path.join(self.tmp.name, 's.db'))
        var.config = configparser.ConfigParser()

    def tearDown(self):
        var.user_db, var.config = self._saved
        self.tmp.cleanup()

    def run_job(self, url, tracks, search, playlist_id=None, name=None):
        job_id = 'j1'
        pi._jobs[job_id] = {'id': job_id, 'owner': 'a@x.com', 'url': url, 'source': pi.detect_source(url),
                            'status': 'listing', 'total': 0, 'processed': 0, 'matched': 0, 'started': time.time()}
        source = pi.detect_source(url)
        lister = {'netease': 'list_netease', 'spotify': 'list_spotify', 'youtube': 'list_youtube'}[source]
        result = ('Src', tracks, False) if source == 'spotify' else ('Src', tracks)
        with mock.patch(f'playlist_import.{lister}', return_value=result):
            pi.run_import(job_id, 'a@x.com', url, playlist_id, name, search=search)
        return pi._jobs[job_id]

    def test_netease_import_matches_in_order_and_reports_misses(self):
        tracks = [{'title': f'Song {i}', 'artist': 'Art', 'duration': 200.0} for i in range(6)]

        def search(q):
            n = q.split()[-1]
            if n == '3':
                return []
            return [{'id': f'v{n}', 'title': f'Art - Song {n} (Official Audio)', 'channel': 'Art', 'duration': 201}]
        job = self.run_job('https://music.163.com/#/playlist?id=1', tracks, search)
        self.assertEqual(job['status'], 'done')
        self.assertEqual((job['total'], job['processed'], job['matched'], job['added']), (6, 6, 5, 5))
        self.assertEqual(job['unmatched'], ['Art - Song 3'])
        pl = var.user_db.get_playlist('a@x.com', job['playlist_id'])
        self.assertEqual(pl['name'], 'Src')
        self.assertEqual([i['title'] for i in pl['items']], [f'Art - Song {i}' for i in (0, 1, 2, 4, 5)])
        self.assertTrue(pl['items'][0]['ref'].endswith('v=v0'))
        self.assertEqual(pl['items'][0]['duration'], 200.0)  # 用原歌单的时长

    def test_youtube_import_keeps_direct_links_without_searching(self):
        tracks = [{'title': 'A', 'artist': '', 'duration': 60, 'url': 'https://www.youtube.com/watch?v=aaaaaaaaaaa'}]
        search = mock.Mock()
        pid = var.user_db.create_playlist('a@x.com', 'Mine')
        job = self.run_job('https://www.youtube.com/playlist?list=PL1', tracks, search, playlist_id=pid)
        search.assert_not_called()
        self.assertEqual(job['playlist_id'], pid)

    def test_spotify_not_configured(self):
        job_id = 'j2'
        pi._jobs[job_id] = {'id': job_id, 'owner': 'a@x.com', 'source': 'spotify', 'status': 'listing',
                            'total': 0, 'processed': 0, 'matched': 0, 'started': time.time()}
        with mock.patch('playlist_import.list_spotify_embed', side_effect=RuntimeError('embed down')):
            pi.run_import(job_id, 'a@x.com', 'https://open.spotify.com/playlist/x', None, None)
        self.assertEqual(pi._jobs[job_id]['error'], 'list_failed')


class ApiTest(unittest.TestCase):
    def setUp(self):
        from tests.test_web_users import Base
        self.base = Base('run')
        self.base.setUp()

    def tearDown(self):
        self.base.tearDown()

    def test_import_endpoint(self):
        c, h = self.base.client, self.base.as_user()
        rv = c.post('/api/playlists/import', json={'url': 'https://space.bilibili.com/1/favlist?fid=2'}, headers=h)
        self.assertEqual(rv.get_json()['error'], 'unsupported_source')
        self.assertEqual(c.post('/api/playlists/import', json={'url': 'ftp://x'}, headers=h).status_code, 400)
        self.assertEqual(c.post('/api/playlists/import', json={'url': 'https://open.spotify.com/playlist/x'}).status_code, 403)
        with mock.patch('playlist_import.threading.Thread'):
            rv = c.post('/api/playlists/import', json={'url': 'https://music.163.com/#/playlist?id=1'}, headers=h)
        job_id = rv.get_json()['job_id']
        self.assertEqual(c.get(f'/api/playlists/import/{job_id}', headers=h).get_json()['status'], 'listing')
        # 别人看不到这个任务
        self.assertEqual(c.get(f'/api/playlists/import/{job_id}', headers=self.base.as_user('m@x.com')).status_code, 404)


if __name__ == '__main__':
    unittest.main()
