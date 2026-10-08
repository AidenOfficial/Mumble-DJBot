"""长视频开播优化:复用视频信息、断线续传不删文件、文件名纠正、准备进度/预计时间、只在有人等时播报。"""
import configparser
import logging
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import constants  # noqa: E402
import variables as var  # noqa: E402
from bot.player import PlayerMixin  # noqa: E402


def make_config():
    config = configparser.ConfigParser(interpolation=None)
    config.read(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'configuration.default.ini'))
    config.set('bot', 'tmp_folder_max_size', '-1')
    return config


class Base(unittest.TestCase):
    def setUp(self):
        import media.url  # noqa: F401  注册 url 类型
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = {n: getattr(var, n, None) for n in ('config', 'tmp_folder', 'db', 'music_db', 'playlist', 'cache')}
        var.config = make_config()
        var.tmp_folder = self.tmp.name + os.sep
        var.playlist, var.cache = [], {}
        constants.load_lang('en_US')

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(var, n, v)
        self.tmp.cleanup()

    def item(self):
        import media.url
        item = media.url.URLItem('https://www.bilibili.com/video/av170001')
        item.title, item.duration, item.ready = 'Long', 3000, 'validated'
        return item


class FakeYDL:
    """可编排的 YoutubeDL:记录调用,按剧本写文件或抛错。"""
    script = []
    calls = []
    cookies_seen = []

    def __init__(self, opts):
        self.opts = opts
        self.cookiejar = mock.Mock()
        self.cookiejar.set_cookie.side_effect = lambda c: FakeYDL.cookies_seen.append(c)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _run(self, kind, arg):
        FakeYDL.calls.append(kind)
        action = FakeYDL.script.pop(0)
        path = self.opts['outtmpl']
        if action == 'partial':
            with open(path, 'ab') as f:
                f.write(b'x' * 10)
            raise RuntimeError('22089194 bytes read, 51358771 more expected')
        if action == 'ok':
            with open(path, 'ab') as f:
                f.write(b'y' * 10)
            return {'requested_downloads': [{'filepath': path}]}
        if action == 'ok_with_ext':
            with open(path + '.m4a', 'wb') as f:
                f.write(b'z')
            return {'requested_downloads': [{'filepath': path + '.m4a'}]}
        raise RuntimeError(action)

    def process_ie_result(self, info, download=True):
        return self._run('reuse', info)

    def extract_info(self, url, download=True):
        return self._run('extract', url)


class DownloadTest(Base):
    def run_download(self, item, script):
        FakeYDL.script, FakeYDL.calls, FakeYDL.cookies_seen = list(script), [], []
        with mock.patch('media.url.youtube_dl.YoutubeDL', FakeYDL), mock.patch('media.url.time.sleep'):
            return item._download()

    def test_reuses_validation_info_with_its_cookies(self):
        item = self.item()
        item._info, item._info_at, item._info_cookies = {'id': 'x', 'thumbnail': None}, time.time(), ['buvid']
        self.assertTrue(self.run_download(item, ['ok']))
        self.assertEqual(FakeYDL.calls, ['reuse'])        # 没有第二次读取信息
        self.assertEqual(FakeYDL.cookies_seen, ['buvid'])
        self.assertIsNone(item._info)                       # 只用一次

    def test_stale_info_is_not_reused(self):
        item = self.item()
        item._info, item._info_at = {'id': 'x'}, time.time() - item.INFO_REUSE_SECONDS - 1
        self.run_download(item, ['ok'])
        self.assertEqual(FakeYDL.calls, ['extract'])

    def test_dropped_connection_resumes_instead_of_deleting(self):
        item = self.item()
        item._info, item._info_at = {'id': 'x'}, time.time()
        self.assertTrue(self.run_download(item, ['partial', 'partial', 'ok']))
        # 重试时重新读取信息(拿新直链),已下载的部分保留续传
        self.assertEqual(FakeYDL.calls, ['reuse', 'extract', 'extract'])
        with open(item.path, 'rb') as f:
            self.assertEqual(f.read(), b'x' * 20 + b'y' * 10)
        self.assertEqual(item.stage, 'ready')

    def test_gives_up_after_all_attempts(self):
        from media.item import PreparationFailedError
        item = self.item()
        var.config.set('bot', 'download_attempts', '3')
        with self.assertRaises(PreparationFailedError):
            self.run_download(item, ['partial'] * 3)
        self.assertFalse(os.path.exists(item.path))
        self.assertEqual(item.stage, 'failed')

    def test_file_written_with_extension_is_renamed(self):
        item = self.item()
        self.assertTrue(self.run_download(item, ['ok_with_ext']))
        self.assertTrue(os.path.exists(item.path))
        self.assertTrue(item.is_ready())   # 以前这里会被判成"文件丢失"


class EtaTest(Base):
    def test_eta_from_speed_for_streaming_and_full_download(self):
        item = self.item()
        item.stage, item.speed, item.total_bytes, item.downloaded_bytes = 'downloading', 1_000_000, 30_000_000, 0
        # 边下边播:只要 30 秒 / 3000 秒 = 1% = 300KB
        self.assertAlmostEqual(item.eta_to_playable(0, 30, True), 0.3)
        self.assertAlmostEqual(item.eta_to_playable(0, 30, False), 30.0)
        item.no_stream = True
        self.assertAlmostEqual(item.eta_to_playable(0, 30, True), 30.0)

    def test_estimate_before_download_learns_typical_durations(self):
        import media.url
        saved = dict(media.url.URLItem.typical_secs)
        try:
            item = self.item()
            item._set_stage('fetching_info')
            item.stage_since -= 8  # 这次读取信息花了 8 秒
            item._set_stage('pending')
            self.assertGreater(media.url.URLItem.typical_secs['fetching_info'], saved['fetching_info'])
            item._set_stage('starting')
            self.assertIsNotNone(item.eta_estimate())
            item._set_stage('downloading')
            self.assertIsNone(item.eta_estimate())  # 有真实速度了就不用估
        finally:
            media.url.URLItem.typical_secs = saved


class FakeWrapper:
    def __init__(self, item):
        self._item, self.id = item, item.id

    def item(self):
        return self._item

    def is_ready(self):
        return self._item.is_ready()


class FakePlaylist(list):
    current_index = 0

    def current_item(self):
        return self[0] if self else False


class Player(PlayerMixin):
    def __init__(self):
        self.log = logging.getLogger('test')
        self.is_pause, self.wait_for_ready, self.playhead, self.exit = False, True, 0, False
        self._wait_started = time.time() - 3
        self.sent = []

    def send_channel_msg(self, msg):
        self.sent.append(msg)


class PrepTest(Base):
    def setUp(self):
        super().setUp()
        self.it = self.item()
        self.w = FakeWrapper(self.it)
        var.playlist = FakePlaylist([self.w])
        self.p = Player()

    def test_waiting_detection(self):
        self.assertTrue(self.p.is_waiting_for(self.it.id))
        self.p.wait_for_ready = False
        self.assertFalse(self.p.is_waiting_for(self.it.id))   # 已经在放(边下边播)
        self.p.wait_for_ready, self.p.is_pause = True, True
        self.assertFalse(self.p.is_waiting_for(self.it.id))

    def test_prep_status_while_buffering(self):
        self.it.stage, self.it.speed, self.it.total_bytes = 'downloading', 2_000_000, 70_000_000
        self.it.downloaded_bytes, self.it.progress = 350_000, 0.005
        prep = self.p.prep_status(self.w)
        self.assertEqual(prep['stage'], 'downloading')
        self.assertTrue(prep['streaming'])
        self.assertEqual(prep['target_secs'], 30)
        self.assertEqual(prep['buffered_secs'], 15.0)
        self.assertAlmostEqual(prep['eta'], (700_000 - 350_000) / 2_000_000, places=1)
        self.assertFalse(prep['eta_estimated'])
        self.assertGreaterEqual(prep['elapsed'], 3)

    def test_chat_notice_only_while_someone_waits(self):
        self.it.stage = 'fetching_info'
        self.it.downloading = True
        clock = [1000.0]

        def fake_time():
            clock[0] += 2
            return clock[0]
        done = {'n': 0}

        def is_ready():
            done['n'] += 1
            return done['n'] > 6
        self.it.is_ready = is_ready
        with mock.patch('bot.player.time.time', fake_time), mock.patch('bot.player.time.sleep'):
            self.p._report_download_progress(self.w)
        self.assertEqual(len(self.p.sent), 1)
        self.assertIn('fetching video info', self.p.sent[0])

        # 已经开播(边下边播)或后台预下载:不播报
        self.p.sent.clear()
        self.p.wait_for_ready = False
        done['n'] = 0
        with mock.patch('bot.player.time.time', fake_time), mock.patch('bot.player.time.sleep'):
            self.p._report_download_progress(self.w)
        self.assertEqual(self.p.sent, [])


class StatusApiTest(unittest.TestCase):
    def test_prep_and_download_fields(self):
        from tests.test_web_api import WebApiTestCase
        case = WebApiTestCase('test_status_with_current_item')
        case.setUp()
        try:
            var.bot.is_waiting_for = lambda item_id: True
            var.bot.prep_status = lambda w: {'stage': 'fetching_info', 'eta': 4.0}
            var.playlist[0].item().downloading = True
            var.playlist[0].item().progress = 0.5
            data = case.client.get('/api/status').get_json()
            self.assertEqual(data['prep']['stage'], 'fetching_info')
            self.assertEqual(data['current']['download']['progress'], 0.5)
        finally:
            case.tearDown()


if __name__ == '__main__':
    unittest.main()
