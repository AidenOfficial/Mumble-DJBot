import configparser
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402
from media import artwork  # noqa: E402
from media.artwork import Candidate  # noqa: E402


def song(title, artists, duration, album='Album', image='http://img/x.jpg'):
    return {'title': title, 'artists': list(artists), 'album': album,
            'duration': duration, 'image': image, 'source': 'fake'}


class ParseTitleTest(unittest.TestCase):
    def tracks(self, title):
        return [c.track for c in artwork.parse_title(title)]

    def test_book_title_marks(self):
        title = '【QQ飞车｜回忆杀】百万级录音棚听《Loves Me Not》QQ飞车登陆BGM【Hi-Res】'
        self.assertEqual(['Loves Me Not'], self.tracks(title))

    def test_japanese_brackets_with_artist_before(self):
        cands = artwork.parse_title('米津玄師 MV「Lemon」')
        self.assertEqual(Candidate('Lemon'), cands[0])
        self.assertIn(Candidate('Lemon', '米津玄師'), cands)

    def test_curly_quotes(self):
        self.assertEqual('夜に駆ける', self.tracks('【中日字幕】“夜に駆ける”YOASOBI')[0])

    def test_artist_dash_title(self):
        cands = artwork.parse_title('Rick Astley - Never Gonna Give You Up (Official Music Video)')
        self.assertEqual(Candidate('Never Gonna Give You Up', 'Rick Astley'), cands[0])

    def test_title_slash_artist(self):
        cands = artwork.parse_title('ヴァンパイア / DECO*27 feat. 初音ミク')
        self.assertEqual('ヴァンパイア', cands[0].track)

    def test_space_separated_cjk(self):
        cands = artwork.parse_title('周杰伦 晴天 高音质')
        self.assertIn(Candidate('晴天', '周杰伦'), cands)

    def test_plain_title(self):
        self.assertEqual(['Bohemian Rhapsody'], self.tracks('Bohemian Rhapsody [HD]'))

    def test_skip_covers_and_compilations(self):
        self.assertTrue(artwork.should_skip('【翻唱】Lemon 米津玄师'))
        self.assertTrue(artwork.should_skip('Lemon (piano cover)'))
        self.assertTrue(artwork.should_skip('YOASOBI 武道館 LIVE'))
        self.assertTrue(artwork.should_skip('Some song', duration=3600))
        self.assertFalse(artwork.should_skip('百万级录音棚听《Loves Me Not》', duration=180))


class ChooseMatchTest(unittest.TestCase):
    def choose(self, results, cand, title, duration=0, extra=''):
        hay = artwork.words(title + ' ' + extra)
        return artwork.choose_match(results, cand, hay, duration, title)

    def test_title_must_match_exactly(self):
        # "Loves Me Not" 不能配成 "Love Me Not"
        results = [song('Love Me Not', ['Ravyn Lenae'], 213)]
        self.assertIsNone(self.choose(results, Candidate('Loves Me Not'), 'x《Loves Me Not》', 213))

    def test_same_title_different_artists_without_evidence_is_rejected(self):
        results = [song('Loves Me Not', ['t.A.T.u.'], 175), song('Loves Me Not', ['Lexi Faye'], 174.5)]
        # 两位歌手时长都在误差内:分不清,宁可用视频封面
        self.assertIsNone(self.choose(results, Candidate('Loves Me Not'), '《Loves Me Not》', 175.5))
        # 视频比两首都长得多:也不能确定
        self.assertIsNone(self.choose(results, Candidate('Loves Me Not'), '《Loves Me Not》', 230))

    def test_unique_duration_match_is_accepted(self):
        results = [song('Loves Me Not', ['t.A.T.u.'], 174.7), song('Loves Me Not', ['Kate Grahn'], 203)]
        m = self.choose(results, Candidate('Loves Me Not'), '《Loves Me Not》', 176)
        self.assertEqual(['t.A.T.u.'], m['artists'])

    def test_artist_in_tags_is_evidence(self):
        results = [song('Loves Me Not', ['Kate Grahn'], 203), song('Loves Me Not', ['t.A.T.u.'], 175)]
        m = self.choose(results, Candidate('Loves Me Not'), '《Loves Me Not》', 240, extra='QQ飞车 t.A.T.u.')
        self.assertEqual(['t.A.T.u.'], m['artists'])

    def test_traditional_simplified_artist(self):
        results = [song('江南', ['林俊傑'], 268)]
        m = self.choose(results, Candidate('江南', '林俊杰'), '林俊杰 - 江南 (官方MV)', 300)
        self.assertIsNotNone(m)

    def test_song_title_is_not_artist_evidence(self):
        # 乐队叫 Lemon 唱的歌也叫 Lemon:标题里的 "Lemon" 不能算歌手出现过
        results = [song('Lemon', ['Lemon'], 150)]
        self.assertIsNone(self.choose(results, Candidate('Lemon'), '「Lemon」', 256))

    def test_cover_versions_in_catalog_are_rejected(self):
        results = [song('晴天 (原唱 周杰伦)', ['RyaVocal'], 270.7),
                   song('晴天 (钢琴版) [原唱: 周杰伦]', ['纪钧瀚'], 269.5)]
        self.assertIsNone(self.choose(results, Candidate('晴天', '周杰伦'), '周杰伦 晴天', 270))

    def test_variant_allowed_when_video_is_that_variant(self):
        results = [song('Song (Remix)', ['DJ'], 200)]
        m = self.choose(results, Candidate('Song'), 'DJ - Song (Remix)', 200)
        self.assertIsNotNone(m)

    def test_prefers_plain_title_with_album(self):
        results = [song('Never Gonna Give You Up (Cake Mix)', ['Rick Astley'], 347),
                   song('Never Gonna Give You Up', ['Rick Astley'], 213, album=''),
                   song('Never Gonna Give You Up', ['Rick Astley'], 214, album='Whenever You Need Somebody')]
        m = self.choose(results, Candidate('Never Gonna Give You Up', 'Rick Astley'),
                        'Rick Astley - Never Gonna Give You Up (Official Music Video)', 213)
        self.assertEqual('Whenever You Need Somebody', m['album'])


class RegressionTest(unittest.TestCase):
    """实机日志里出现过的误匹配 / 漏匹配。"""

    def choose(self, results, title, duration):
        cand = artwork.parse_title(title)[0]
        return artwork.choose_match(results, cand, artwork.words(title), duration, title)

    def test_dash_without_space_on_one_side(self):
        self.assertEqual(Candidate('Loves me not', 't.A.T.u.'), artwork.parse_title('t.A.T.u.- Loves me not lyrics')[0])
        self.assertEqual('A-ha', artwork.parse_title('A-ha')[0].track)

    def test_parentheses_that_are_part_of_the_title(self):
        m = self.choose([song('Right Now (Na Na Na)', ['Akon'], 241)], 'Akon - Right Now Na Na Na (Lyrics)', 242)
        self.assertIsNotNone(m)

    def test_taylors_version_preferred_over_original(self):
        results = [song('I Knew You Were Trouble.', ['Taylor Swift'], 219, album='Red'),
                   song("I Knew You Were Trouble (Taylor's Version)", ['Taylor Swift'], 219, album="Red (Taylor's Version)")]
        m = self.choose(results, "Taylor Swift - I Knew You Were Trouble (Taylor's Version) (Lyric Video)", 221)
        self.assertEqual("Red (Taylor's Version)", m['album'])

    def test_karaoke_label_rejected(self):
        results = [song('Baby (Feat. Ludacris) (In The Style Of Justin Bieber)', ['Karaoke - Ameritz'], 225)]
        self.assertIsNone(self.choose(results, 'Justin Bieber - Baby ft. Ludacris', 225))

    def test_named_artist_conflict_rejected_even_with_same_duration(self):
        for title, result in [
            ("Charlie Puth - We Don't Talk Anymore (feat. Selena Gomez) [Official Video]",
             song("We Don't Talk Anymore", ['Pablo Cepeda'], 218)),
            ('One Direction - Best Song Ever (Audio)', song('Best Song Ever', ['Party all night'], 200)),
            ('Shingeki no Kyojin S1 OP1 | Linked Horizon - Guren no Yumiya',
             song('Guren No Yumiya (From "Attack on Titan")', ['PelleK'], 300)),
        ]:
            self.assertIsNone(self.choose([result], title, result['duration']), title)

    def test_localized_artist_still_matches_by_duration(self):
        m = self.choose([song('晴天', ['Jay Chou'], 269.7)], '周杰伦 晴天 高音质', 270)
        self.assertIsNotNone(m)

    def test_rerecorded_versions_rejected(self):
        for t in ('I Want It That Way (Reimagined)', 'Let Her Go (Recorded at Deezer, Sao Paulo)'):
            self.assertTrue(artwork.is_variant({'title': t}, 'Some Video'), t)
        self.assertFalse(artwork.is_variant({'title': 'Lean On', 'artists': ['Major Lazer', 'DJ Snake']},
                                            'Major Lazer - Lean On'))

    def test_party_compilation_detected(self):
        self.assertTrue(artwork.is_compilation({'album': 'High School Party 2020'}))
        self.assertFalse(artwork.is_compilation({'album': 'My World 2.0'}))

    def test_query_language(self):
        self.assertEqual('en', artwork._language("Bon Jovi It's My Life"))
        self.assertEqual('ja', artwork._language('アイドル'))
        self.assertEqual('zh', artwork._language('晴天'))
        self.assertEqual('ko', artwork._language('불면증'))


class FindAlbumTest(unittest.TestCase):
    def setUp(self):
        self._config = var.config
        var.config = None
        self.queries = []

    def tearDown(self):
        var.config = self._config

    def provider(self, results):
        def search(q):
            self.queries.append(q)
            return results
        return search

    def test_skipped_video_does_not_query(self):
        m = artwork.find_album({'title': '【翻唱】Lemon', 'duration': 250}, [self.provider([])])
        self.assertIsNone(m)
        self.assertEqual([], self.queries)

    def test_platform_metadata_wins(self):
        results = [song('Lemon', ['米津玄師'], 256)]
        m = artwork.find_album({'title': 'whatever video title', 'duration': 300,
                                'track': 'Lemon', 'artist': '米津玄師'}, [self.provider(results)])
        self.assertEqual(['米津玄師'], m['artists'])

    def test_compilation_only_used_as_fallback(self):
        compilation = song('Song', ['Artist'], 200, album='Greatest Hits Vol. 3', image='http://img/comp.jpg')
        original = song('Song', ['Artist'], 200, album='Original', image='http://img/orig.jpg')
        m = artwork.find_album({'title': 'Artist - Song', 'duration': 200},
                               [self.provider([compilation]), self.provider([original])])
        self.assertEqual('http://img/orig.jpg', m['image'])
        m = artwork.find_album({'title': 'Artist - Song', 'duration': 200}, [self.provider([compilation])])
        self.assertEqual('http://img/comp.jpg', m['image'])

    def test_call_budget(self):
        artwork.find_album({'title': 'A - B / C 「D」', 'duration': 200}, [self.provider([])] * 5)
        self.assertLessEqual(len(self.queries), artwork.MAX_CALLS_PER_ITEM)


class FakeURLItem:
    type = 'url'

    def __init__(self):
        self.id = 'a' * 32
        self.title = '米津玄師 MV「Lemon」'
        self.duration = 260
        self.url = 'https://www.youtube.com/watch?v=SX_ViT4Ra7k'
        self.thumbnail = ''
        self.artwork = ''
        self.artist = ''
        self.album = ''
        self.version = 0

    def format_title(self):
        return self.title


class ResolveTest(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = (var.config, var.tmp_folder)
        config = configparser.ConfigParser()
        config.add_section('bot')
        config.set('bot', 'artwork_folder', self.tmp.name)
        var.config = config
        var.tmp_folder = self.tmp.name + os.sep
        self.fetched = []
        self._fetch = artwork._fetch_image

        def fake_fetch(url):
            self.fetched.append(url)
            if 'broken' in url:
                raise IOError('404')
            return Image.new('RGB', (1280, 720) if 'ytimg' in url or 'video' in url else (1000, 1000), 'red')
        artwork._fetch_image = fake_fetch
        self.extracted = []
        self._extract = artwork._extract_info

        def fake_extract(url):
            self.extracted.append(url)
            return {}
        artwork._extract_info = fake_extract

    def tearDown(self):
        artwork._fetch_image = self._fetch
        artwork._extract_info = self._extract
        var.config, var.tmp_folder = self._saved
        self.tmp.cleanup()

    def test_album_art_found(self):
        item = FakeURLItem()
        provider = lambda q: [song('Lemon', ['米津玄師'], 256, album='Lemon', image='http://img/album.jpg')]  # noqa
        artwork.resolve(item, {'title': item.title, 'duration': 260, 'thumbnail': 'http://video.jpg'},
                        providers=[provider])
        self.assertEqual('album', item.artwork)
        self.assertEqual('米津玄師', item.artist)
        self.assertEqual('Lemon', item.album)
        self.assertEqual('Lemon', item.track)
        self.assertEqual(['http://img/album.jpg'], self.fetched)
        self.assertTrue(artwork.has_hd(item.id))
        self.assertTrue(item.thumbnail)
        self.assertGreater(item.version, 0)
        from PIL import Image
        with Image.open(artwork.path_for(item.id)) as im:
            self.assertEqual((1000, 1000), im.size)

    def test_falls_back_to_best_video_thumbnail(self):
        item = FakeURLItem()
        info = {'title': item.title, 'duration': 260, 'thumbnail': 'http://broken.jpg',
                'thumbnails': [{'url': 'http://video-small.jpg', 'width': 120, 'height': 90},
                               {'url': 'http://video-big.jpg', 'width': 1280, 'height': 720}]}
        artwork.resolve(item, info, providers=[lambda q: []])
        self.assertEqual('video', item.artwork)
        self.assertEqual(['http://broken.jpg', 'http://video-big.jpg'], self.fetched)
        self.assertEqual('', item.artist)

    def test_lookup_disabled_uses_video_cover(self):
        var.config.set('bot', 'artwork_lookup', 'False')
        item = FakeURLItem()
        called = []
        artwork.resolve(item, {'title': item.title, 'thumbnail': 'http://video.jpg'},
                        providers=[lambda q: called.append(q) or []])
        self.assertEqual([], called)
        self.assertEqual('video', item.artwork)

    def test_nothing_available_marks_none(self):
        item = FakeURLItem()
        item.url = 'https://example.com/a'
        artwork.resolve(item, None, providers=[lambda q: []])
        self.assertEqual('none', item.artwork)
        self.assertTrue(item.artwork_done)
        # 不再重复排队
        artwork.ensure(item)
        self.assertNotIn(item.id, artwork._queued)

    def test_restored_item_reads_info_for_evidence(self):
        # 从上次队列恢复的条目没有 yt-dlp 信息:读一次拿 UP 主 / 标签
        item = FakeURLItem()
        artwork.resolve(item, None, providers=[lambda q: []])
        self.assertEqual([item.url], self.extracted)

    def test_video_cover_never_overwrites_album_cover(self):
        item = FakeURLItem()
        provider = lambda q: [song('Lemon', ['米津玄師'], 256, album='Lemon', image='http://img/album.jpg')]  # noqa
        artwork.resolve(item, {'title': item.title, 'uploader': 'x', 'duration': 260}, providers=[provider])
        self.assertEqual('album', item.artwork)
        # 抢先下载视频封面的线程晚到了
        self.assertFalse(artwork._video_step(item, {'thumbnail': 'http://video.jpg'}))
        self.assertEqual('album', item.artwork)

    def test_video_first_then_album_upgrade(self):
        item = FakeURLItem()
        self.assertTrue(artwork._video_step(item, {'thumbnail': 'http://video.jpg'}))
        self.assertEqual('video', item.artwork)
        self.assertFalse(getattr(item, 'artwork_done', False))
        provider = lambda q: [song('Lemon', ['米津玄師'], 256, album='Lemon', image='http://img/album.jpg')]  # noqa
        artwork.resolve(item, {'title': item.title, 'uploader': 'x', 'duration': 260}, providers=[provider])
        self.assertEqual('album', item.artwork)
        self.assertTrue(item.artwork_done)

    def test_ensure_skips_finished_items(self):
        item = FakeURLItem()
        item.artwork, item.artwork_done = 'none', True
        artwork.ensure(item, urgent=True)
        self.assertNotIn(item.id, artwork._queued)
        # 上一版存的 'video'(没有 artwork_done)要按新规则再查一次
        item.artwork, item.artwork_done = 'video', False
        with open(artwork.path_for(item.id), 'wb') as f:
            f.write(b'x')
        artwork.ensure(item)
        self.assertIn(item.id, artwork._queued)
        with artwork._lock:
            artwork._queued.discard(item.id)

    def test_old_album_item_without_track_is_looked_up_again(self):
        # 上一版的专辑封面条目没存正式歌名:再查一次补上
        item = FakeURLItem()
        item.artwork, item.artwork_done, item.track = 'album', True, None
        with open(artwork.path_for(item.id), 'wb') as f:
            f.write(b'x')
        artwork.ensure(item)
        self.assertIn(item.id, artwork._queued)
        with artwork._lock:
            artwork._queued.discard(item.id)
        # 这次没查到也记下来,不会每次显示都重查
        artwork.resolve(item, {'title': item.title, 'uploader': 'x'}, providers=[lambda q: []])
        self.assertEqual('', item.track)
        artwork.ensure(item)
        self.assertNotIn(item.id, artwork._queued)

    def test_prune_removes_oldest(self):
        var.config.set('bot', 'artwork_folder_max_size', '1')
        paths = []
        for i in range(3):
            p = os.path.join(self.tmp.name, f'{i:032x}.jpg')
            with open(p, 'wb') as f:
                f.write(b'0' * 600 * 1024)
            os.utime(p, (1000 + i, 1000 + i))
            paths.append(p)
        artwork.prune()
        self.assertFalse(os.path.exists(paths[0]))
        self.assertFalse(os.path.exists(paths[1]))
        self.assertTrue(os.path.exists(paths[2]))


if __name__ == '__main__':
    unittest.main()
