# coding=utf-8
"""队列指针 / 等待态 / 用户输入的回归测试,对应代码审计的
PC-01(下载中被二次校验删文件)、PC-02(失败后吞掉下一首)、PC-03(重复条目删错)、
PC-13(等待态从不补发下载)、PC-14(single 模式死循环)、PC-15(等待中删当前曲播错歌)、
CCD-01(用户正则冻结进程)、CCD-02(!repeat 无上限)。

直接驱动真实的 _loop_iteration、Playlist、CachedItemWrapper 和 web_api._queue_remove,
只把 ffmpeg 和下载线程换成假的。"""
import collections
import configparser
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import constants  # noqa: E402
import variables as var  # noqa: E402
from bot.player import PlayerMixin  # noqa: E402
from media import playlist as mp  # noqa: E402
from media.cache import CachedItemWrapper  # noqa: E402


def make_config():
    c = configparser.ConfigParser(interpolation=None, allow_no_value=True)
    for s in ('bot', 'debug', 'youtube_dl'):
        c.add_section(s)
    for k, v in {'announce_current_music': 'False', 'normalize_volume': 'False',
                 'stream_while_downloading': 'False', 'prefetch_count': '2',
                 'save_playlist': 'False'}.items():
        c.set('bot', k, v)
    c.set('debug', 'ffmpeg', 'False')
    return c


class FakeItem:
    def __init__(self, id_, ready='yes'):
        self.id = id_
        self.ready = ready
        self.type = 'url'
        self.title = id_
        self.duration = 100
        self.downloading = False
        self.version = 0

    def is_ready(self):
        return self.ready == 'yes' and not self.downloading

    def is_failed(self):
        return self.ready == 'failed'

    def validate(self):
        return True

    def uri(self):
        return '/music/' + self.id

    def format_title(self):
        return self.id

    def format_debug_string(self):
        return self.id

    def format_song_string(self, user=''):
        return self.id


class FakeCache(dict):
    def save(self, id):
        pass

    def free(self, id):
        self.pop(id, None)

    def free_all(self):
        self.clear()

    def free_and_delete(self, id):
        self.pop(id, None)

    def get_item_by_id(self, id):
        return self.get(id)


def W(id_, ready='yes'):
    """真实的 CachedItemWrapper,背后是假缓存。"""
    if id_ not in var.cache:
        var.cache[id_] = FakeItem(id_, ready)
    return CachedItemWrapper(var.cache, id_, 'url', 'u')


def fill(pl, wrappers, index):
    list.extend(pl, wrappers)  # 绕过 append():不起校验线程
    pl.current_index = index
    return pl


def ids(pl):
    return [w.id for w in pl]


class FakeSendAudio:
    def get_buffer_size(self):
        return 0.0

    def add_sound(self, pcm):
        pass


class FakeStdout:
    def read(self, n):
        return b''


class FakeProc:
    """已经读到 EOF、以 rc 退出的 ffmpeg。"""
    def __init__(self, rc):
        self.rc = rc
        self.stdout = FakeStdout()

    def wait(self, timeout=None):
        return self.rc

    def kill(self):
        pass


class Player(PlayerMixin):
    def __init__(self):
        self.log = logging.getLogger('test')
        self.stereo = True
        self.mumble = mock.Mock()
        self.mumble.send_audio = FakeSendAudio()
        self.exit = False
        self.thread = None
        self.read_pcm_size = 0
        self.pcm_buffer_size = 1920
        self.last_ffmpeg_err = ''
        self._ffmpeg_stderr_lines = collections.deque(maxlen=20)
        self._active_downloads = set()
        self._download_lock = threading.Lock()
        self.is_pause = False
        self.pause_at_id = ''
        self.playhead = 0
        self.song_start_at = -1
        self.wait_for_ready = False
        self.on_interrupting = False
        self.messages = []
        self.launched = []
        self.downloads = []

    def send_channel_msg(self, msg):
        self.messages.append(msg)

    # 不起真的 ffmpeg / 下载线程
    def launch_music(self, wrapper, start_from=0):
        self.launched.append(wrapper.id)
        self._kick_id = None
        self.thread = None  # 假装瞬间播完

    def async_download(self, item):
        self.downloads.append(item.id)

    def async_download_next(self):
        pass

    def run(self, n):
        errors = []
        for _ in range(n):
            try:
                self._loop_iteration()
            except Exception as e:  # 同 loop() 的兜底
                errors.append(e)
                self.thread = None
                self.wait_for_ready = False
        return errors


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(var, k, None) for k in ('config', 'playlist', 'cache', 'play_history', 'bot')}
        var.config = make_config()
        var.cache = FakeCache()
        var.play_history = None
        var.bot = None
        constants.load_lang('en_US')
        patcher = mock.patch('bot.player.time.sleep')
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(var, k, v)


class RemoveByIdTest(Base):
    def test_duplicates_are_all_removed_and_nothing_else(self):
        # !repeat 3 之后 [A, A, A, A, B, C],A 失败
        pl = fill(mp.RepeatPlaylist(), [W('A'), W('A'), W('A'), W('A'), W('B'), W('C')], 0)
        var.playlist = pl
        pl.remove_by_id('A')
        self.assertEqual(['B', 'C'], ids(pl))  # 以前:['A', 'A', 'C'],B 被删、A 成了孤儿

    def test_pointer_follows_the_current_item(self):
        pl = fill(mp.RepeatPlaylist(), [W('A'), W('B'), W('A'), W('C')], 2)
        pl.remove_by_id('A')
        self.assertEqual(['B', 'C'], ids(pl))
        self.assertEqual('C', pl.current_item().id)  # 当前那份 A 删掉后落在它的下一首上


class FailureDoesNotSkipNextTest(Base):
    def decode_failure(self, player):
        player.thread = FakeProc(rc=1)  # 解码失败
        player.wait_for_ready = False
        player.run(1)

    def test_oneshot_keeps_the_following_song(self):
        var.playlist = fill(mp.OneshotPlaylist(), [W('A'), W('B'), W('C')], 0)
        p = Player()
        self.decode_failure(p)
        self.assertEqual(['B', 'C'], ids(var.playlist))  # 以前只剩 ['C']
        self.assertEqual('B', var.playlist.current_item().id)

    def test_repeat_moves_to_the_following_song(self):
        var.playlist = fill(mp.RepeatPlaylist(), [W('A'), W('B'), W('C')], 0)
        p = Player()
        self.decode_failure(p)
        self.assertEqual('B', var.playlist.current_item().id)  # 以前是 C

    def test_failed_download_plays_the_following_song(self):
        var.playlist = fill(mp.RepeatPlaylist(), [W('A', ready='failed'), W('B'), W('C')], 0)
        p = Player()
        p.wait_for_ready = True
        p.run(3)  # 移除 A → next() → 播 B
        self.assertEqual(['B'], p.launched)

    def test_oneshot_web_poll_between_removal_and_next(self):
        # one-shot 的 current_item() 会把 -1 改回 0;以前退指针的做法在这里会让
        # next() 把原来的下一首删掉。
        var.playlist = fill(mp.OneshotPlaylist(), [W('A', ready='failed'), W('B'), W('C')], 0)
        p = Player()
        p.wait_for_ready = True
        p.run(1)                          # 移除 A
        var.playlist.current_item()       # Web 轮询 /api/status
        p.run(2)
        self.assertEqual(['B'], p.launched)
        self.assertEqual(['B', 'C'], ids(var.playlist))

    def test_single_mode_failure_of_last_item(self):
        var.playlist = fill(mp.SingleLoopPlaylist(), [W('A'), W('B')], 1)
        p = Player()
        p.thread = FakeProc(rc=1)
        errors = p.run(5)
        self.assertEqual([], errors)       # 以前每 0.1 秒一次 IndexError
        self.assertEqual('A', p.launched[0])


class WaitStateTest(Base):
    def test_skip_while_paused_then_resume_downloads_the_new_current(self):
        items = [W('A')] + [W(x, ready='validated') for x in 'BCDE']
        var.playlist = fill(mp.RepeatPlaylist(), items, 0)
        p = Player()
        p.pause_at_id, p.is_pause = 'A', True
        for _ in range(3):  # 暂停时连按三次 !skip,超出预取范围
            var.playlist.skip_current()
            var.playlist.next()
            p.wait_for_ready = True
        p.resume()
        p.run(1)
        self.assertEqual('D', var.playlist.current_item().id)
        self.assertEqual(['D'], p.downloads)  # 以前没人下载 D,永远等下去

    def test_waits_while_another_thread_is_still_downloading(self):
        # 清空后重加同一链接:旧对象的下载还在跑,新对象要等它结束再补发
        var.playlist = fill(mp.RepeatPlaylist(), [W('X', ready='validated')], 0)
        p = Player()
        p.wait_for_ready = True
        p._active_downloads.add('X')
        p.run(5)
        self.assertEqual([], p.downloads)
        p._active_downloads.discard('X')
        p.run(1)
        self.assertEqual(['X'], p.downloads)

    def test_gives_up_after_repeated_kicks(self):
        var.playlist = fill(mp.RepeatPlaylist(), [W('A'), W('B', ready='validated'), W('C')], 1)
        p = Player()
        p.wait_for_ready = True
        p.run(10)  # 假下载什么都不做,B 永远不就绪
        self.assertEqual(['B'] * PlayerMixin.MAX_DOWNLOAD_KICKS, p.downloads)
        self.assertNotIn('B', ids(var.playlist))
        self.assertEqual('C', p.launched[0])  # 放弃 B 之后接着放它的下一首
        self.assertTrue(p.messages)


class RemoveFromQueueTest(Base):
    def setUp(self):
        super().setUp()
        import web_api
        self.web_api = web_api

    def test_remove_current_while_waiting_plays_the_following_song(self):
        var.playlist = fill(mp.RepeatPlaylist(), [W('A'), W('B', ready='validated'), W('C')], 1)
        p = var.bot = Player()
        p.wait_for_ready = True  # 正在等 B 下载
        self.web_api._queue_remove(1)
        p.run(1)
        self.assertEqual(['C'], p.launched)  # 以前重播 A

    def test_remove_first_item_while_waiting(self):
        var.playlist = fill(mp.RepeatPlaylist(), [W('A', ready='validated'), W('B'), W('C')], 0)
        p = var.bot = Player()
        p.wait_for_ready = True
        self.web_api._queue_remove(0)
        p.run(1)
        self.assertEqual(['B'], p.launched)  # 以前指针变 -1,播的是队尾的 C

    def test_remove_playing_song_oneshot(self):
        var.playlist = fill(mp.OneshotPlaylist(), [W('A'), W('B'), W('C')], 0)
        p = var.bot = Player()
        p.thread = FakeProc(rc=-9)  # A 正在放
        self.web_api._queue_remove(0)
        var.playlist.current_item()  # Web 轮询
        p.run(2)
        self.assertEqual(['B'], p.launched)
        self.assertEqual(['B', 'C'], ids(var.playlist))

    def test_remove_playing_song_repeat(self):
        var.playlist = fill(mp.RepeatPlaylist(), [W('A'), W('B'), W('C')], 1)
        p = var.bot = Player()
        p.thread = FakeProc(rc=-9)  # B 正在放
        self.web_api._queue_remove(1)
        p.run(2)
        self.assertEqual(['C'], p.launched)

    def test_remove_other_item_leaves_playback_alone(self):
        var.playlist = fill(mp.RepeatPlaylist(), [W('A'), W('B'), W('C')], 1)
        p = var.bot = Player()
        self.web_api._queue_remove(2)
        self.assertEqual(['A', 'B'], ids(var.playlist))
        self.assertEqual('B', var.playlist.current_item().id)


class ValidateDuringDownloadTest(Base):
    def test_second_validate_keeps_the_growing_file(self):
        import media.url as mu
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(var, 'tmp_folder', tmp.name + os.sep, create=True), \
                mock.patch.object(var, 'db', mock.Mock(has_option=lambda *a: False), create=True):
            item = mu.URLItem('https://example.com/v')
            # 边下边播进行中的状态
            item.ready, item.downloading, item.duration = 'preparing', True, 3600
            with open(item.path, 'wb') as f:
                f.write(b'x' * 1000)
            open(item.path + '.incomplete', 'w').close()
            item._get_info_from_url = mock.Mock(side_effect=AssertionError('不该再读信息'))
            # !repeat / 重复点同一链接 / 切换模式 都会让校验线程再调一次
            self.assertTrue(item.validate())
            self.assertTrue(os.path.exists(item.path))  # 以前文件被删
            self.assertEqual('preparing', item.ready)

    def test_leftover_from_a_crash_is_still_discarded(self):
        import media.url as mu
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(var, 'tmp_folder', tmp.name + os.sep, create=True), \
                mock.patch.object(var, 'db', mock.Mock(has_option=lambda *a: False), create=True):
            item = mu.URLItem('https://example.com/v')
            item.ready, item.downloading = 'preparing', False  # 库里残留的状态,没有线程在下载
            with open(item.path, 'wb') as f:
                f.write(b'x' * 1000)
            open(item.path + '.incomplete', 'w').close()
            item._get_info_from_url = mock.Mock(return_value=False)
            item.validate()
            self.assertFalse(os.path.exists(item.path))  # 截断的残留文件照常清理


class UserPatternTest(Base):
    EVIL = r'(\w+\s?)*$'  # 当正则执行时是灾难性回溯
    TITLE = 'Bohemian_Rhapsody_Queen_Remaster_2011 (Official)'

    def setUp(self):
        super().setUp()
        var.music_db = mock.Mock()
        self.addCleanup(setattr, var, 'music_db', None)
        self.bot = mock.Mock()

    def test_filematch_treats_input_literally(self):
        from commands import sources
        var.music_db.query_music.return_value = [{'title': self.TITLE, 'path': 'a.mp3'}]
        var.cache = mock.Mock()
        start = time.monotonic()
        sources.cmd_play_file_match(self.bot, 'u', 'txt', 'filematch', self.EVIL)
        self.assertLess(time.monotonic() - start, 1)
        self.bot.send_msg.assert_called_once_with(constants.tr_cli('no_file'), 'txt')

    def test_listfile_matches_substring_case_insensitively(self):
        from commands import sources
        files = [{'title': 'Bohemian', 'path': 'Queen/Bohemian.mp3'},
                 {'title': 'Other', 'path': 'misc/other.mp3'},
                 {'title': 'Evil', 'path': self.TITLE}]
        var.music_db.query_music.return_value = files
        with mock.patch.object(sources, 'send_multi_lines') as send:
            sources.cmd_list_file(self.bot, 'u', 'txt', 'listfile', 'queen/')
            lines = send.call_args[0][1]
            self.assertTrue(any('Queen/Bohemian.mp3' in m for m in lines))
            self.assertFalse(any('misc/other.mp3' in m for m in lines))

            start = time.monotonic()
            sources.cmd_list_file(self.bot, 'u', 'txt', 'listfile', self.EVIL)
            self.assertLess(time.monotonic() - start, 1)


class RepeatLimitTest(Base):
    def test_repeat_is_capped(self):
        from commands import playback
        var.playlist = fill(mp.RepeatPlaylist(), [W('A')], 0)
        bot = mock.Mock()
        with mock.patch.object(mp.BasePlaylist, 'async_validate'):
            playback.cmd_repeat(bot, 'u', 'txt', 'repeat', '999999999')
            self.assertEqual(1, len(var.playlist))
            bot.send_msg.assert_called_once()
            playback.cmd_repeat(bot, 'u', 'txt', 'repeat', str(playback.MAX_REPEAT))
            self.assertEqual(1 + playback.MAX_REPEAT, len(var.playlist))


if __name__ == '__main__':
    unittest.main()
