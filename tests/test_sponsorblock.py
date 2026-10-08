"""SponsorBlock:链接解析、片段合并、查询(含 B 站多 P 保护)、播放器跳过逻辑。"""
import configparser
import hashlib
import logging
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import util  # noqa: E402
import variables as var  # noqa: E402
from bot.player import PlayerMixin  # noqa: E402
from media import sponsorblock as sb  # noqa: E402


class FakeResp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class RefTest(unittest.TestCase):
    def test_youtube_forms(self):
        for url in ('https://www.youtube.com/watch?v=kJQP7kiw5Fk&t=10', 'https://youtu.be/kJQP7kiw5Fk',
                    'https://www.youtube.com/shorts/kJQP7kiw5Fk', 'https://music.youtube.com/watch?v=kJQP7kiw5Fk'):
            self.assertEqual(sb.video_ref(url), ('youtube', 'kJQP7kiw5Fk', None), url)

    def test_bilibili_av_converted_back_to_bv(self):
        for bv in ('BV1GJ411x7h7', 'BV17x411w7KC', 'BV1iCHXzJEdk'):
            self.assertEqual(sb.av_to_bv(util.bv_to_av(bv)), bv)
        url = util.get_bilibili_url_from_input('BV1GJ411x7h7')  # bot 内部存的是 av 形式
        self.assertEqual(sb.video_ref(url), ('bilibili', 'BV1GJ411x7h7', None))
        self.assertEqual(sb.video_ref('https://www.bilibili.com/video/BV1GJ411x7h7?p=3')[2], '3')

    def test_other_sites(self):
        self.assertIsNone(sb.video_ref('https://soundcloud.com/x/y'))
        self.assertIsNone(sb.video_ref(''))

    def test_merge_and_lookup(self):
        merged = sb.merge_segments([(50, 60), (0, 10), (9.8, 20), (100, 100.5)])
        self.assertEqual(merged, [(0, 20), (50, 60)])
        self.assertEqual(sb.segment_at(merged, 5), (0, 20))
        self.assertIsNone(sb.segment_at(merged, 19.8))   # 离结尾不到 0.5 秒,不值得跳
        self.assertIsNone(sb.segment_at(merged, 30))


class FetchTest(unittest.TestCase):
    def setUp(self):
        sb._cache.clear()

    def test_hash_prefix_query_and_filtering(self):
        vid = 'kJQP7kiw5Fk'
        prefix = hashlib.sha256(vid.encode()).hexdigest()[:4]
        payload = [{'videoID': 'other', 'segments': [{'segment': [1, 50], 'actionType': 'skip'}]},
                   {'videoID': vid, 'segments': [
                       {'segment': [0, 21.8], 'actionType': 'skip', 'category': 'music_offtopic'},
                       {'segment': [100, 110], 'actionType': 'mute'},
                       {'segment': [249.4, 281.5], 'actionType': 'skip'}]}]
        with mock.patch('media.sponsorblock.requests.get', return_value=FakeResp(200, payload)) as get:
            segs = sb.fetch_segments(f'https://youtu.be/{vid}', ['music_offtopic'])
            sb.fetch_segments(f'https://youtu.be/{vid}', ['music_offtopic'])  # 命中缓存
        self.assertEqual(segs, [(0, 21.8), (249.4, 281.5)])
        self.assertEqual(get.call_count, 1)
        self.assertTrue(get.call_args[0][0].endswith(f'/api/skipSegments/{prefix}'))
        self.assertNotIn(vid, str(get.call_args))  # 隐私:不发完整 ID

    def test_not_found_and_errors_are_empty(self):
        with mock.patch('media.sponsorblock.requests.get', return_value=FakeResp(404, None)):
            self.assertEqual(sb.fetch_segments('https://youtu.be/aaaaaaaaaaa', ['sponsor']), [])
        with mock.patch('media.sponsorblock.requests.get', side_effect=OSError('down')):
            self.assertEqual(sb.fetch_segments('https://youtu.be/bbbbbbbbbbb', ['sponsor']), [])

    def test_bilibili_multipart_is_not_guessed(self):
        bv = 'BV1GJ411x7h7'
        multi = [{'videoID': bv, 'segments': [{'segment': [0, 30], 'cid': '1'}, {'segment': [5, 40], 'cid': '2'}]}]
        single = [{'videoID': bv, 'segments': [{'segment': [0, 30], 'cid': '1'}]}]
        url = f'https://www.bilibili.com/video/{bv}'
        with mock.patch('media.sponsorblock.requests.get', return_value=FakeResp(200, multi)):
            self.assertEqual(sb.fetch_segments(url, ['sponsor']), [])
        sb._cache.clear()
        with mock.patch('media.sponsorblock.requests.get', return_value=FakeResp(200, single)) as get:
            self.assertEqual(sb.fetch_segments(url, ['sponsor']), [(0, 30)])
            self.assertIn('bsbsb.top', get.call_args[0][0])
            self.assertEqual(sb.fetch_segments(url + '?p=2', ['sponsor']), [])  # 第 2P 对不上 cid


class Player(PlayerMixin):
    def __init__(self):
        self.log = logging.getLogger('test')
        self.thread = mock.Mock()
        self.read_pcm_size = 999
        self.playhead = 0
        self.song_start_at = 123
        self.wait_for_ready = False
        self.on_interrupting = False
        self._playing_id = 'abc'
        self._playing_duration = 280
        self._sb_segments = {'abc': [(0, 21.8), (249.4, 280)]}


class PlayerSkipTest(unittest.TestCase):
    def test_skip_relaunches_from_segment_end(self):
        p = Player()
        p.playhead = 3
        thread = p.thread
        self.assertTrue(p._sponsorblock_skip())
        thread.kill.assert_called_once()
        self.assertIsNone(p.thread)
        self.assertEqual(p.playhead, 21.8)
        self.assertTrue(p.wait_for_ready and p._quiet_relaunch)

    def test_outro_to_end_moves_to_next_song(self):
        p = Player()
        p.playhead = 250
        self.assertTrue(p._sponsorblock_skip())
        self.assertFalse(p.wait_for_ready)        # 主循环会切下一首

    def test_outside_segments_does_nothing(self):
        p = Player()
        p.playhead = 100
        self.assertFalse(p._sponsorblock_skip())
        p.thread.kill.assert_not_called()

    def test_quiet_relaunch_suppresses_announcement(self):
        p = Player()
        p.stereo = True
        p.redirect_ffmpeg_log = False
        p.last_ffmpeg_err = ''
        import collections
        p._ffmpeg_stderr_lines = collections.deque()
        p._quiet_relaunch = True
        p.send_channel_msg = mock.Mock()
        saved = (var.config, var.play_history)
        var.config = configparser.ConfigParser()
        var.config.read_dict({'bot': {'announce_current_music': 'True', 'normalize_volume': 'False',
                                      'sponsorblock': 'False'}, 'debug': {'ffmpeg': 'False'}})
        var.play_history = None
        wrapper = mock.Mock(id='abc')
        wrapper.is_ready.return_value = True
        wrapper.uri.return_value = '/x'
        wrapper.format_debug_string.return_value = 'x'
        wrapper.item.return_value = mock.Mock(type='url', url='', duration=280, id='abc')
        try:
            with mock.patch('bot.player.sp.Popen'), mock.patch('bot.player.threading.Thread'):
                p.launch_music(wrapper, 21.8)
                p.send_channel_msg.assert_not_called()
                p.launch_music(wrapper, 0)       # 普通启动照常播报
                p.send_channel_msg.assert_called_once()
        finally:
            var.config, var.play_history = saved

    def test_prefetch_only_for_url_items_when_enabled(self):
        p = Player()
        p._sb_segments = {}
        saved = var.config
        var.config = configparser.ConfigParser()
        var.config.read_dict({'bot': {'sponsorblock': 'True', 'sponsorblock_categories': 'music_offtopic'}})
        done = threading.Event()
        try:
            with mock.patch('media.sponsorblock.fetch_segments', side_effect=lambda *a: done.set() or [(1, 5)]):
                url_item = mock.Mock(id='u1', type='url', url='https://youtu.be/kJQP7kiw5Fk')
                p._sponsorblock_prefetch(mock.Mock(item=lambda: url_item))
                done.wait(2)
                file_item = mock.Mock(id='f1', type='file', url='')
                p._sponsorblock_prefetch(mock.Mock(item=lambda: file_item))
            for _ in range(50):
                if p._sb_segments.get('u1'):
                    break
                threading.Event().wait(0.02)
            self.assertEqual(p.skip_segments_for('u1'), [(1, 5)])
            self.assertNotIn('f1', p._sb_segments)
        finally:
            var.config = saved


if __name__ == '__main__':
    unittest.main()
