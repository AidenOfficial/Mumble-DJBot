"""最小复现:播放核心与并发组候选发现。只读仓库,不改任何文件。"""
import configparser
import logging
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = os.getcwd()
sys.path.insert(0, ROOT)

import constants  # noqa: E402
import variables as var  # noqa: E402


def make_config(**bot):
    config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
    config.read(os.path.join(ROOT, 'configuration.default.ini'), encoding='utf-8')
    config.set('bot', 'tmp_folder_max_size', '-1')
    for k, v in bot.items():
        config.set('bot', k, v)
    return config


class ValidateDiscardsInProgressDownload(unittest.TestCase):
    """F-A: stream_while_downloading=True(默认)时,正在下载(ready='preparing',
    nopart 文件在最终路径上增长,.incomplete 标记存在)的条目被再次 validate()
    (同 URL 二次入队 / !repeat / !mode 切换都会触发)-> 文件被删,下载完成后
    is_ready() 永远为 False。"""

    def setUp(self):
        import media.url  # noqa: F401
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = {n: getattr(var, n, None) for n in ('config', 'tmp_folder', 'db')}
        var.config = make_config()
        var.tmp_folder = self.tmp.name + os.sep
        var.db = mock.Mock()
        var.db.has_option.return_value = False
        constants.load_lang('en_US')

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(var, n, v)
        self.tmp.cleanup()

    def test_second_validate_deletes_growing_file(self):
        import media.url
        item = media.url.URLItem('https://www.youtube.com/watch?v=abcdefghijk')
        item.title, item.duration, item.ready = 'Long', 3000, 'validated'
        # --- 模拟 URLItem._download 已经开始:状态 + 标记 + nopart 文件 ---
        item.downloading = True
        item.ready = 'preparing'
        open(item.path + '.incomplete', 'w').close()
        ytdlp_fd = open(item.path, 'wb')          # yt-dlp 持有的写句柄
        ytdlp_fd.write(b'x' * 1000); ytdlp_fd.flush()
        self.assertTrue(os.path.exists(item.path))

        # --- 另一线程(Validating)对同一 item 调 validate() ---
        with mock.patch.object(item, '_get_info_from_url', return_value=True):
            item.validate()

        # 文件被 _discard_incomplete_download 删掉了
        self.assertFalse(os.path.exists(item.path), "in-progress download file was unlinked")
        self.assertEqual(item.ready, 'validated')   # 'preparing' 被覆盖

        # yt-dlp 继续往已 unlink 的 inode 写,最后 _download 成功路径置 ready='yes'
        ytdlp_fd.write(b'y' * 1000); ytdlp_fd.close()
        item.ready = 'yes'
        item.downloading = False
        # 主循环随后调用 is_ready():文件不在 -> 回落 'validated' -> 永远 False
        self.assertFalse(item.is_ready())
        self.assertEqual(item.ready, 'validated')
        self.assertFalse(item.playable_from(0, 30))   # 也无法边下边播
        self.assertFalse(item.is_failed())            # 也不会被当成失败移除 -> 主循环卡在等待

    def test_mode_switch_requeues_every_item_for_validation(self):
        """from_list() 走的是被覆盖的 extend() -> 所有条目进入 pending_items 并起校验线程。"""
        import media.playlist as pl
        w = [mock.Mock(id='a'), mock.Mock(id='b')]
        old = pl.RepeatPlaylist()
        list.extend(old, w)
        with mock.patch.object(pl.BasePlaylist, 'async_validate') as av:
            new = pl.get_playlist('random', old)
        self.assertEqual(set(x.id for x in new.pending_items), {'a', 'b'})
        av.assert_called()


class RemoveByIdWithDuplicates(unittest.TestCase):
    """F-B: remove_by_id 收集索引后按升序逐个 remove(),后面的索引已经漂移。"""

    def setUp(self):
        self._cache = getattr(var, 'cache', None)
        var.cache = mock.Mock()

    def tearDown(self):
        var.cache = self._cache

    def test_removes_wrong_item(self):
        import media.playlist as pl
        A1, B, A2, C = (mock.Mock(id='A'), mock.Mock(id='B'), mock.Mock(id='A'), mock.Mock(id='C'))
        p = pl.RepeatPlaylist()
        list.extend(p, [A1, B, A2, C])
        p.current_index = 0
        p.remove_by_id('A')
        ids = [w.id for w in p]
        # 期望 ['B', 'C'];实际 C 被删,第二个 A 留下
        self.assertEqual(ids, ['B', 'C'], f"got {ids}")


class FfmpegFailureSkipsFollowingItem(unittest.TestCase):
    """F-C: 主循环 unable_play 路径 remove_by_id(current) 后 current_index 不变
    (指向了下一首),紧接着 playlist.next() 再前进一次 -> 跳过/删除一首好歌。"""

    def setUp(self):
        self._cache = getattr(var, 'cache', None)
        var.cache = mock.Mock()

    def tearDown(self):
        var.cache = self._cache

    def test_repeat_mode_skips_next(self):
        import media.playlist as pl
        bad, good1, good2 = mock.Mock(id='bad'), mock.Mock(id='g1'), mock.Mock(id='g2')
        p = pl.RepeatPlaylist()
        list.extend(p, [bad, good1, good2])
        p.current_index = 0
        # == bot/player.py:749 + 755 的顺序 ==
        p.remove_by_id('bad')
        nxt = p.next()
        self.assertIs(nxt, good1, f"expected g1 to play next, got {nxt.id}")

    def test_oneshot_mode_deletes_next(self):
        import media.playlist as pl
        bad, good1, good2 = mock.Mock(id='bad'), mock.Mock(id='g1'), mock.Mock(id='g2')
        p = pl.OneshotPlaylist()
        list.extend(p, [bad, good1, good2])
        p.current_index = 0
        p.remove_by_id('bad')
        nxt = p.next()
        self.assertIs(nxt, good1, f"expected g1, got {nxt.id}; queue now {[w.id for w in p]}")


class ActiveDownloadsLeakOnThreadStartFailure(unittest.TestCase):
    """F-D: async_download 先登记 _active_downloads 再 start();start 抛异常则 id 永不回收。"""

    def test_id_stuck_after_start_failure(self):
        from bot.player import PlayerMixin

        class P(PlayerMixin):
            def __init__(self):
                self.log = logging.getLogger('t')
                self._download_lock = threading.Lock()
                self._active_downloads = set()
        p = P()
        item = mock.Mock(id='abcdef0123456789')
        item.format_debug_string.return_value = 'x'
        with mock.patch('bot.player.threading.Thread') as T:
            T.return_value.start.side_effect = RuntimeError("can't start new thread")
            with self.assertRaises(RuntimeError):
                p.async_download(item)
        self.assertIn(item.id, p._active_downloads)
        # 之后对同一条目的任何 async_download 都静默返回 None
        self.assertIsNone(p.async_download(item))


if __name__ == '__main__':
    unittest.main()
