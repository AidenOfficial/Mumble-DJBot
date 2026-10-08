"""崩溃加固相关的回归测试:ffmpeg 命令构造、下载线程兜底、URL 元数据容错、
校验线程锁释放。"""
import configparser
import logging
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import constants  # noqa: E402
import variables as var  # noqa: E402
from bot.player import PlayerMixin  # noqa: E402


def make_config(**bot):
    config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
    for section in ('bot', 'debug', 'youtube_dl'):
        config.add_section(section)
    defaults = {'announce_current_music': 'False', 'normalize_volume': 'True',
                'normalize_volume_target': '-16', 'max_track_duration': '0',
                'download_attempts': '1', 'stream_while_downloading': 'False'}
    defaults.update(bot)
    for k, v in defaults.items():
        config.set('bot', k, v)
    config.set('debug', 'ffmpeg', 'False')
    config.set('debug', 'youtube_dl', 'False')
    config.set('youtube_dl', 'cookie_file', '')
    config.set('youtube_dl', 'user_agent', '')
    return config


class FakeWrapper:
    def __init__(self, uri='/music/a.opus', ready=True, _id='abc'):
        self._uri = uri
        self._ready = ready
        self.id = _id
        self.user = 'u'
        self.version = 0

    def is_ready(self):
        return self._ready

    def playable_from(self, *_):
        return False

    def uri(self):
        return self._uri

    def format_debug_string(self):
        return self._uri

    def format_title(self):
        return 'title'


class Player(PlayerMixin):
    def __init__(self):
        self.log = logging.getLogger('test')
        self.stereo = True
        self.redirect_ffmpeg_log = False
        self.last_ffmpeg_err = ''
        import collections
        self._ffmpeg_stderr_lines = collections.deque(maxlen=20)
        self._active_downloads = set()
        self._download_lock = threading.Lock()
        self.wait_for_ready = True  # 下载线程跑的是主循环正在等的那首
        self.messages = []

    def send_channel_msg(self, msg):
        self.messages.append(msg)


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(var, k, None) for k in ('config', 'play_history', 'playlist', 'cache')}
        var.config = make_config()
        var.play_history = None
        constants.load_lang('en_US')

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(var, k, v)


class FfmpegCommandTest(Base):
    def launch(self, uri, start_from):
        player = Player()
        with mock.patch('bot.player.sp.Popen') as popen, \
                mock.patch('bot.player.threading.Thread'):
            player.launch_music(FakeWrapper(uri), start_from)
        return popen.call_args[0][0]

    def test_seek_is_an_input_option(self):
        cmd = self.launch('/music/long.webm', 3000)
        # -ss 必须出现在 -i 之前,否则长视频恢复播放要从头解码
        self.assertLess(cmd.index('-ss'), cmd.index('-i'))
        self.assertEqual(cmd[cmd.index('-ss') + 1], '3000.000000')

    def test_no_seek_from_start(self):
        cmd = self.launch('/music/long.webm', 0)
        self.assertNotIn('-ss', cmd)

    def test_network_input_gets_timeouts(self):
        cmd = self.launch('https://radio.example/stream', 0)
        self.assertIn('-rw_timeout', cmd)
        self.assertLess(cmd.index('-reconnect'), cmd.index('-i'))

    def test_live_sources_never_seek(self):
        player = Player()
        wrapper = FakeWrapper('https://radio.example/stream')
        wrapper.item = lambda: type('Radio', (), {'type': 'radio'})()
        with mock.patch('bot.player.sp.Popen') as popen, \
                mock.patch('bot.player.threading.Thread'):
            player.launch_music(wrapper, 900)
        self.assertNotIn('-ss', popen.call_args[0][0])

    def test_local_file_has_no_network_options(self):
        cmd = self.launch('/music/a.opus', 0)
        self.assertNotIn('-reconnect', cmd)


class DownloadThreadTest(Base):
    def test_unexpected_validation_error_does_not_escape(self):
        player = Player()
        wrapper = FakeWrapper()
        wrapper.validate = mock.Mock(side_effect=TypeError("'>' not supported"))
        var.playlist = mock.Mock()
        player._active_downloads.add(wrapper.id)
        self.assertFalse(player._download(wrapper))
        var.playlist.remove_by_id.assert_called_once_with(wrapper.id, rewind=False)
        self.assertNotIn(wrapper.id, player._active_downloads)
        self.assertEqual(len(player.messages), 1)

    def test_unexpected_prepare_error_marks_failed(self):
        player = Player()
        item = mock.Mock(ready='validated')
        wrapper = FakeWrapper(ready=False)
        wrapper.validate = mock.Mock(return_value=True)
        wrapper.prepare = mock.Mock(side_effect=OSError('disk full'))
        wrapper.item = mock.Mock(return_value=item)
        var.playlist = mock.Mock()
        self.assertFalse(player._download(wrapper))
        self.assertEqual(item.ready, 'failed')


class URLMetadataTest(Base):
    @classmethod
    def setUpClass(cls):
        try:
            import media.url  # noqa: F401
            cls.ok = True
        except Exception:
            cls.ok = False

    def make_item(self, tmp):
        import media.url
        var.tmp_folder = tmp + os.sep
        return media.url.URLItem('https://example.com/watch?v=x')

    def fake_ydl(self, info):
        ydl = mock.MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.extract_info.return_value = info
        return mock.patch('media.url.youtube_dl.YoutubeDL', return_value=ydl)

    def test_none_duration_is_tolerated(self):
        if not self.ok:
            self.skipTest('media.url deps missing')
        with tempfile.TemporaryDirectory() as tmp:
            item = self.make_item(tmp)
            var.config = make_config(max_track_duration='60')
            var.db = mock.Mock()
            var.db.has_option.return_value = False
            with self.fake_ydl({'duration': None, 'title': None}):
                self.assertTrue(item.validate())
            self.assertEqual(item.duration, 0)
            self.assertEqual(item.ready, 'validated')

    def test_prepare_on_ready_item_does_not_assert(self):
        if not self.ok:
            self.skipTest('media.url deps missing')
        with tempfile.TemporaryDirectory() as tmp:
            item = self.make_item(tmp)
            open(item.path, 'wb').close()
            item.ready = 'yes'
            self.assertTrue(item.prepare())


class ValidationLockTest(Base):
    def test_lock_released_after_unexpected_error(self):
        import media.playlist
        pl = media.playlist.OneshotPlaylist()
        bad = mock.Mock(id='bad', version=0)
        bad.validate.side_effect = RuntimeError('boom')
        pl.pending_items.append(bad)
        var.cache = mock.Mock()
        pl._check_valid()
        self.assertFalse(pl.validating_thread_lock.locked())


if __name__ == '__main__':
    unittest.main()
