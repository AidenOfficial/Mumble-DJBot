import configparser
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402
from media import lyrics, recognize  # noqa: E402


LRC = """[ar:米津玄師]
[ti:Lemon]
[00:00.00] 作词 : 米津玄師
[00:00.00] 作词 : 米津玄師 → Lời : Yonezu Kenshi
[00:00.85]夢ならばどれほどよかったでしょう
[00:00.85] Giá như tất cả chỉ là một giấc mơ
[00:06.65]未だにあなたのことを夢にみる
[00:10.00]
[00:11.00]
[00:12.34][01:40.50]忘れた物を取りに帰るように
"""


def entry(title, artist, duration, synced=LRC, plain='', album='Lemon', source='lrclib', **extra):
    return dict({'title': title, 'artists': [artist], 'album': album, 'duration': duration,
                 'synced': synced, 'plain': plain, 'instrumental': False, 'source': source}, **extra)


class UrlItem:
    type = 'url'

    def __init__(self, _id='vid1', title='米津玄師 MV「Lemon」', duration=256, track='Lemon',
                 artist='米津玄師', artwork_done=True, shazam=''):
        self.id = _id
        self.title = title
        self.duration = duration
        self.track = track
        self.artist = artist
        self.artwork_done = artwork_done
        self.shazam = shazam
        self.url = 'https://www.youtube.com/watch?v=SX_ViT4Ra7k'
        self.path = ''
        self.ready = 'yes'
        self.downloading = False

    def format_title(self):
        return self.title


class FileItem:
    type = 'file'

    def __init__(self, title='Lemon', artist='米津玄師', duration=256):
        self.id = 'file1'
        self.title = title
        self.artist = artist
        self.duration = duration

    def format_title(self):
        return self.title


class ParseTest(unittest.TestCase):
    def test_parse_lrc(self):
        lines = lyrics.parse_lrc(LRC)
        self.assertEqual((0.85, '夢ならばどれほどよかったでしょう'), lines[2])
        # 一行多个时间戳:各放一份,按时间排序
        self.assertEqual((12.34, '忘れた物を取りに帰るように'), lines[-2])
        self.assertEqual((100.5, '忘れた物を取りに帰るように'), lines[-1])
        # [ar:] 这类标签不是歌词
        self.assertNotIn('米津玄師', [s for _, s in lines])

    def test_offset_tag_and_json_lines(self):
        text = '{"t":0,"c":[{"tx":"作曲: "},{"tx":"x"}]}\n[offset:+500]\n[00:02.000]a\n[00:00.20]b'
        self.assertEqual([(0.0, 'b'), (1.5, 'a')], lyrics.parse_lrc(text))
        self.assertEqual([(1.0, 'x')], lyrics.parse_lrc('[00:01:00]x'))  # mm:ss:xx

    def test_clean_lines(self):
        lines = lyrics.clean_lines(lyrics.parse_lrc(LRC))
        texts = [s for _, s, _ in lines]
        # 署名行、同一时间戳的翻译行去掉,连续空行并成一行
        self.assertEqual(['夢ならばどれほどよかったでしょう', '未だにあなたのことを夢にみる', '',
                          '忘れた物を取りに帰るように', '忘れた物を取りに帰るように'], texts)
        self.assertEqual([], lyrics.clean_lines([(0, ''), (1, 'Lyrics by: someone'), (2, '')]))

    def test_clean_plain(self):
        self.assertEqual('line one\n\nline two',
                         lyrics.clean_plain('作曲：someone\r\nline one\n\n\n\nline two\n'))

    def test_merge_translation(self):
        lines = [[1.0, 'a', ''], [2.0, '', ''], [3.0, 'b', ''], [4.0, 'c', '']]
        lyrics.merge_translation(lines, [(0.0, '作词: x'), (1.1, 'A'), (2.0, 'gap'), (3.0, 'B'), (9.0, 'Z')])
        self.assertEqual(['A', '', 'B', ''], [t for _, _, t in lines])

    def test_instrumental_text(self):
        self.assertTrue(lyrics._instrumental_text([[0, '纯音乐，请欣赏', '']]))
        self.assertFalse(lyrics._instrumental_text([[0, '纯音乐', ''], [1, 'a', ''], [2, 'b', ''], [3, 'c', '']]))

    def test_chat_lines(self):
        data = {'lines': [[0, 'a <b>', 'tr'], [1, '', '']]}
        self.assertEqual(['a &lt;b&gt;', '<i>tr</i>', '&nbsp;'], lyrics.chat_lines(data))
        self.assertEqual(['x', '&nbsp;', 'y'], lyrics.chat_lines({'lines': [], 'plain': 'x\n\ny'}))


class ChooseTest(unittest.TestCase):
    def test_title_and_artist_must_match(self):
        results = [entry('Lemon', 'Someone Else', 256), entry('Lemonade', '米津玄師', 256),
                   entry('Lemon', '米津玄師', 256, album='STRAY SHEEP')]
        self.assertEqual('STRAY SHEEP', lyrics.choose(results, 'Lemon', '米津玄師', 256)['album'])
        self.assertIsNone(lyrics.choose(results[:2], 'Lemon', '米津玄師', 256))

    def test_rejects_live_and_far_durations(self):
        results = [entry('晴天', '周杰伦', 299, album='無與倫比演唱會 CD1'),
                   entry('晴天', '周杰伦', 400, album='叶惠美')]
        self.assertIsNone(lyrics.choose(results, '晴天', '周杰伦', 269))
        self.assertIsNotNone(lyrics.choose(results[1:], '晴天', '周杰伦', 0))  # 不知道时长时不看

    def test_prefers_synced_then_closest_duration(self):
        results = [entry('Lemon', '米津玄師', 256, synced='', plain='plain'),
                   entry('Lemon', '米津玄師', 290, album='far'),
                   entry('Lemon', '米津玄師', 258, album='near')]
        self.assertEqual('near', lyrics.choose(results, 'Lemon', '米津玄師', 256)['album'])

    def test_artist_listed_with_others(self):
        r = entry('Shape of You', 'Ed Sheeran & Friends', 233)
        self.assertIsNotNone(lyrics.choose([r], 'Shape of You', 'Ed Sheeran', 233))


class FindTest(unittest.TestCase):
    def test_falls_back_to_second_source(self):
        calls = []

        def empty(track, artist):
            calls.append('lrclib')
            return []

        def netease(track, artist):
            calls.append('netease')
            return [{'title': track, 'artists': [artist], 'album': '', 'duration': 256,
                     'netease_id': 1, 'source': 'netease'}]

        def convert(match):
            return {'status': 'found', 'lines': [[0.5, 'x', '叉']], 'plain': 'x'}
        data = lyrics.find('Lemon', '米津玄師', 256, [(empty, lyrics._from_lrclib), (netease, convert)])
        self.assertEqual(['lrclib', 'netease'], calls)
        self.assertEqual('netease', data['source'])
        self.assertEqual(256, data['duration'])

    def test_instrumental_stops_search(self):
        def inst(track, artist):
            return [entry(track, artist, 200, instrumental=True)]

        def never(track, artist):
            raise AssertionError('should not be asked')
        data = lyrics.find('Song', 'Band', 200, [(inst, lyrics._from_lrclib), (never, lyrics._from_lrclib)])
        self.assertEqual('instrumental', data['status'])

    def test_title_line_is_dropped(self):
        def src(track, artist):
            return [entry(track, artist, 223, synced='[00:00.10]周杰倫 - 稻香\n[00:00.50]\n'
                                                     '[00:30.00]对这个世界如果你有太多的抱怨\n[00:35.00]稻香')]
        data = lyrics.find('稻香', '周杰倫', 223, [(src, lyrics._from_lrclib)])
        self.assertEqual(['对这个世界如果你有太多的抱怨', '稻香'], [s for _, s, _ in data['lines']])
        self.assertEqual('对这个世界如果你有太多的抱怨\n稻香', data['plain'])

    def test_source_error_is_skipped(self):
        def broken(track, artist):
            raise RuntimeError('boom')
        self.assertIsNone(lyrics.find('a', 'b', 0, [(broken, lyrics._from_lrclib)]))

    def test_netease_conversion_merges_translation(self):
        saved = lyrics.netease_lyric
        lyrics.netease_lyric = lambda _id: {
            'synced': '[00:01.00]夢ならば\n[00:03.00]未だに', 'translated': '[00:01.00]如果是梦\n[00:03.00]至今',
            'instrumental': False}
        try:
            data = lyrics._from_netease({'netease_id': 5})
        finally:
            lyrics.netease_lyric = saved
        self.assertEqual([[1.0, '夢ならば', '如果是梦'], [3.0, '未だに', '至今']], data['lines'])


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._saved = (var.config, var.settings_db_path, getattr(var, 'playlist', None))
        self.tmp = tempfile.TemporaryDirectory()
        config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
        config.add_section('bot')
        config.set('bot', 'lyrics_lookup', 'True')
        config.set('bot', 'lyrics_folder', self.tmp.name)
        config.set('bot', 'artwork_shazam', 'True')
        var.config = config
        lyrics._memory.clear()
        self.lrclib_calls = 0

    def tearDown(self):
        var.config, var.settings_db_path, var.playlist = self._saved
        lyrics._memory.clear()
        self.tmp.cleanup()

    def sources(self, duration=256):
        def lrclib(track, artist):
            self.lrclib_calls += 1
            return [entry(track, artist, duration)]
        return [(lrclib, lyrics._from_lrclib)]


class SourcesConfigTest(StoreTestCase):
    def test_default_order(self):
        self.assertEqual([lyrics.SOURCES['lrclib'], lyrics.SOURCES['netease']], lyrics.configured_sources())

    def test_custom_order_ignores_unknown(self):
        var.config.set('bot', 'lyrics_sources', ' NetEase, bogus, lrclib, netease')
        self.assertEqual([lyrics.SOURCES['netease'], lyrics.SOURCES['lrclib']], lyrics.configured_sources())

    def test_netease_search_parses_cloudsearch(self):
        from media import artwork
        saved = artwork._get_json
        artwork._get_json = lambda *a, **k: {'result': {'songs': [{
            'id': 536622304, 'name': 'Lemon', 'dt': 256000, 'ar': [{'name': '米津玄師'}],
            'al': {'name': 'Lemon', 'picUrl': 'http://p2.music.126.net/x.jpg'}}]}}
        try:
            r = artwork.search_netease('Lemon 米津玄師')[0]
        finally:
            artwork._get_json = saved
        self.assertEqual(('Lemon', ['米津玄師'], 256.0, 536622304), (r['title'], r['artists'], r['duration'], r['netease_id']))
        self.assertEqual('https://p2.music.126.net/x.jpg?param=1000y1000', r['image'])


class IdentityTest(StoreTestCase):
    def test_url_item(self):
        self.assertEqual(('Lemon', '米津玄師'), lyrics.identity(UrlItem()))
        self.assertEqual('waiting', lyrics.identity(UrlItem(track=None, artist='', artwork_done=False)))
        self.assertEqual('waiting', lyrics.identity(UrlItem(track='', artist='', shazam='pending')))
        self.assertIsNone(lyrics.identity(UrlItem(track='', artist='')))

    def test_url_item_without_artwork_lookup(self):
        var.config.set('bot', 'artwork_lookup', 'False')
        self.assertIsNone(lyrics.identity(UrlItem(track=None, artwork_done=False)))

    def test_file_item(self):
        self.assertEqual(('Lemon', '米津玄師'), lyrics.identity(FileItem()))
        self.assertIsNone(lyrics.identity(FileItem(artist='??')))

    def test_status(self):
        self.assertEqual('waiting', lyrics.status(UrlItem(track=None, artwork_done=False)))
        self.assertEqual('none', lyrics.status(UrlItem(track='', artist='')))
        self.assertEqual('pending', lyrics.status(UrlItem()))
        var.config.set('bot', 'lyrics_lookup', 'False')
        self.assertEqual('off', lyrics.status(UrlItem()))


class ResolveTest(StoreTestCase):
    def test_found_and_cached(self):
        item = UrlItem()
        data = lyrics.resolve(item, self.sources())
        self.assertEqual('found', data['status'])
        self.assertTrue(data['aligned'])  # 时长一样
        self.assertEqual(0.0, data['offset'])
        self.assertEqual('found', lyrics.status(item))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, 'vid1.json')))
        # 换个进程(内存缓存清空)也能从磁盘读回来,不再联网
        lyrics._memory.clear()
        self.assertEqual(data['lines'], lyrics.get(item)['lines'])
        lyrics.resolve(item, self.sources())
        self.assertEqual(1, self.lrclib_calls)

    def test_identity_change_refetches(self):
        item = UrlItem()
        lyrics.resolve(item, self.sources())
        item.track = 'Flamingo'
        self.assertIsNone(lyrics.get(item))
        self.assertEqual('pending', lyrics.status(item))
        lyrics.resolve(item, self.sources())
        self.assertEqual(2, self.lrclib_calls)

    def test_none_is_retried_after_a_while(self):
        item = UrlItem()
        data = lyrics.resolve(item, [])
        self.assertEqual('none', data['status'])
        self.assertEqual('none', lyrics.status(item))
        lyrics.save(item.id, dict(data, fetched=data['fetched'] - lyrics.RETRY_NONE_DAYS * 86400 - 1))
        self.assertEqual('pending', lyrics.status(item))

    def test_mv_longer_than_song_waits_for_alignment(self):
        item = UrlItem(duration=300)
        item.ready = 'no'  # 音频还没下好:先存下来,等能听歌识曲了再对齐
        data = lyrics.resolve(item, self.sources(256))
        self.assertFalse(data['aligned'])
        self.assertEqual('pending', data['align'])

    def test_bad_file_name_is_ignored(self):
        self.assertIsNone(lyrics.path_for('../evil'))
        self.assertIsNone(lyrics.load('../evil'))

    def test_set_offset(self):
        item = UrlItem(duration=300)
        item.ready = 'no'
        lyrics.resolve(item, self.sources(256))
        data = lyrics.set_offset(item, 12.345)
        self.assertEqual(12.35, data['offset'])
        self.assertTrue(data['aligned'])
        self.assertTrue(data['manual'])
        self.assertEqual(600.0, lyrics.set_offset(item, 9999)['offset'])
        self.assertIsNone(lyrics.set_offset(UrlItem(_id='other'), 1))


class AlignTest(StoreTestCase):
    def setUp(self):
        super().setUp()
        self._saved_rec = (recognize._recognize_clip, recognize.shazam_available, lyrics._audio_ready)
        recognize.shazam_available = lambda: True
        lyrics._audio_ready = lambda item: True
        self.clips = []

    def tearDown(self):
        recognize._recognize_clip, recognize.shazam_available, lyrics._audio_ready = self._saved_rec
        super().tearDown()

    def fake_clip(self, result, intro=20.0, skew=0.05):
        def clip(path, start):
            self.clips.append(start)
            if isinstance(result, Exception):
                raise result
            # 和真的 Shazam 一样:offset ≈ (1 + timeskew) × 原曲位置 - 0.8
            song_pos = start - intro
            return dict(result, offset=(1 + skew) * song_pos - lyrics.SHAZAM_LAG, timeskew=skew) if result else None
        recognize._recognize_clip = clip

    def test_offset_from_shazam(self):
        # MV 有 20 秒片头:截取点在视频 105 秒处,对应原曲 85 秒
        self.fake_clip({'title': 'Lemon', 'artists': ['米津玄師']})
        data = lyrics.resolve(UrlItem(_id='mv', duration=300, track='Lemon'), self.sources(256))
        self.assertEqual(1, len(self.clips))
        self.assertTrue(data['aligned'])
        self.assertEqual(20.0, data['offset'])
        self.assertEqual('done', data['align'])

    def test_same_length_is_still_checked(self):
        # LRCLIB 的时长不可靠:时长一样时先滚动,能认的话照样校准
        self.fake_clip({'title': 'Lemon', 'artists': ['米津玄師']}, intro=0.1)
        data = lyrics.resolve(UrlItem(_id='same', duration=256), self.sources(256))
        self.assertEqual(1, len(self.clips))
        self.assertEqual(0.0, data['offset'])
        self.assertEqual('done', data['align'])

    def test_implausible_offset_is_ignored(self):
        self.fake_clip({'title': 'Lemon', 'artists': ['米津玄師']}, intro=-60)
        data = lyrics.resolve(UrlItem(_id='mv', duration=256), self.sources(256))
        self.assertEqual(0.0, data['offset'])
        self.assertTrue(data['aligned'])  # 时长一样:保留按时长判断的结果
        self.assertEqual('failed', data['align'])

    def test_other_song_is_not_trusted(self):
        self.fake_clip({'title': 'Flamingo', 'artists': ['米津玄師']})
        data = lyrics.resolve(UrlItem(_id='mv', duration=300), self.sources(256))
        self.assertFalse(data['aligned'])
        self.assertEqual('failed', data['align'])
        self.assertEqual(2, len(self.clips))  # 两个截取点都试过

    def test_manual_offset_is_not_overwritten(self):
        item = UrlItem(_id='mv', duration=300)
        saved_ready = lyrics._audio_ready
        lyrics._audio_ready = lambda item: False
        lyrics.resolve(item, self.sources(256))
        lyrics.set_offset(item, 7.5)
        lyrics._audio_ready = saved_ready
        self.fake_clip({'title': 'Lemon', 'artists': ['米津玄師']})
        self.assertEqual(7.5, lyrics.resolve(item, self.sources(256))['offset'])
        self.assertEqual([], self.clips)

    def test_offline_keeps_pending(self):
        self.fake_clip(recognize.ShazamUnavailable())
        item = UrlItem(_id='mv', duration=300)
        data = lyrics.resolve(item, self.sources(256))
        self.assertEqual('pending', data['align'])
        # 下次 ensure 会再排队
        self.assertTrue(lyrics._needs_align(item, data))

    def test_shazam_disabled(self):
        var.config.set('bot', 'artwork_shazam', 'False')
        self.fake_clip({'title': 'Lemon', 'artists': ['米津玄師']})
        data = lyrics.resolve(UrlItem(_id='mv', duration=300), self.sources(256))
        self.assertEqual([], self.clips)
        self.assertEqual('', data['align'])  # 不去试
        self.assertFalse(data['aligned'])


class EnsureTest(StoreTestCase):
    def setUp(self):
        super().setUp()
        while not lyrics._queue.empty():
            lyrics._queue.get_nowait()
        lyrics._queued.clear()
        lyrics._urgent.clear()

    def tearDown(self):
        while not lyrics._queue.empty():
            lyrics._queue.get_nowait()
        lyrics._queued.clear()
        lyrics._urgent.clear()
        lyrics._events.clear()
        super().tearDown()

    def test_queues_once_and_skips_when_cached(self):
        item = UrlItem()
        lyrics.ensure(item)
        lyrics.ensure(item)
        self.assertEqual(1, lyrics._queue.qsize())
        lyrics.ensure(item, urgent=True)  # 插队:再排一次,高优先级
        self.assertEqual(2, lyrics._queue.qsize())
        self.assertEqual(0, lyrics._queue.get_nowait()[0])

        other = UrlItem(_id='cached')
        lyrics.resolve(other, self.sources())
        lyrics.ensure(other)
        self.assertNotIn('cached', lyrics._queued)

    def test_disabled_or_waiting_does_nothing(self):
        lyrics.ensure(UrlItem(track=None, artwork_done=False))
        var.config.set('bot', 'lyrics_lookup', 'False')
        lyrics.ensure(UrlItem())
        self.assertEqual(0, lyrics._queue.qsize())


class CommandTest(StoreTestCase):
    class Bot:
        def __init__(self):
            self.sent = []
            self.mumble = type('M', (), {'max_message_length': 5000})()

        def send_msg(self, msg, text=None):
            self.sent.append(msg)

    class Wrapper:
        def __init__(self, item):
            self._item = item
            self.id = item.id

        def item(self):
            return self._item

    class Playlist(list):
        def current_item(self):
            return self[0] if self else False

    def run_cmd(self, item=None):
        import constants
        from commands import playback
        constants.load_lang('en_US')
        var.playlist = self.Playlist([self.Wrapper(item)] if item else [])
        bot = self.Bot()
        playback.cmd_lyrics(bot, 'user', None, 'lyrics', '')
        return bot.sent

    def test_disabled(self):
        var.config.set('bot', 'lyrics_lookup', 'False')
        self.assertIn('lyrics_lookup', self.run_cmd(UrlItem())[0])

    def test_nothing_playing(self):
        self.assertEqual(['Nothing is playing right now.'], self.run_cmd())

    def test_unknown_song(self):
        self.assertIn("Couldn't tell", self.run_cmd(UrlItem(track='', artist=''))[0])

    def test_sends_cached_lyrics(self):
        item = UrlItem()
        lyrics.resolve(item, self.sources())
        sent = self.run_cmd(item)
        self.assertEqual(1, len(sent))
        self.assertIn('<b>Lyrics</b>: 米津玄師 - Lemon <i>(from LRCLIB)</i>', sent[0])
        self.assertIn('夢ならばどれほどよかったでしょう', sent[0])

    def test_not_found(self):
        item = UrlItem()
        lyrics.resolve(item, [])
        self.assertIn('No lyrics found', self.run_cmd(item)[0])


class WebApiTest(StoreTestCase):
    def setUp(self):
        super().setUp()
        from flask import Flask
        import web_api
        from tests.test_web_api import FakeBot, FakeCache, FakeDB, FakeWrapper
        self._saved_web = {n: getattr(var, n, None) for n in ('bot', 'cache', 'db')}
        var.bot = FakeBot()
        var.db = FakeDB()
        self.item = UrlItem()
        self.item.display_type = lambda: 'url'
        var.playlist = type('P', (list,), {'version': 1, 'mode': 'one-shot', 'current_index': 0,
                                           'current_item': lambda self: self[0]})([FakeWrapper(self.item)])
        var.cache = FakeCache({self.item.id: self.item})
        self._ensure = lyrics.ensure
        lyrics.ensure = lambda item, urgent=False: None  # 不起后台任务
        app = Flask(__name__)
        app.register_blueprint(web_api.create_blueprint(lambda f: f))
        self.client = app.test_client()

    def tearDown(self):
        lyrics.ensure = self._ensure
        for n, v in self._saved_web.items():
            setattr(var, n, v)
        super().tearDown()

    def test_status_and_fetch(self):
        self.assertEqual('pending', self.client.get('/api/status').get_json()['current']['lyrics'])
        self.assertEqual({'status': 'pending'}, self.client.get('/api/lyrics/vid1').get_json())
        lyrics.resolve(self.item, self.sources())
        self.assertEqual('found', self.client.get('/api/status').get_json()['current']['lyrics'])
        data = self.client.get('/api/lyrics/vid1').get_json()
        self.assertEqual('lrclib', data['source'])
        self.assertTrue(data['aligned'])
        self.assertEqual('夢ならばどれほどよかったでしょう', data['lines'][0][1])
        self.assertEqual(404, self.client.get('/api/lyrics/missing').status_code)

    def test_offset(self):
        lyrics.resolve(self.item, self.sources())
        rv = self.client.post('/api/lyrics/vid1/offset', json={'offset': 1.5})
        self.assertEqual(1.5, rv.get_json()['offset'])
        self.assertEqual(400, self.client.post('/api/lyrics/vid1/offset', json={'offset': 'x'}).status_code)
        self.assertEqual(400, self.client.post('/api/lyrics/vid1/offset', json={'offset': 'nan'}).status_code)
        other = UrlItem(_id='other')
        var.cache[other.id] = other
        self.assertEqual(409, self.client.post('/api/lyrics/other/offset', json={'offset': 1}).status_code)


if __name__ == '__main__':
    unittest.main()
