import configparser
import logging
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402
from bot.player import PlayerMixin  # noqa: E402
from media.playlist import OneshotPlaylist, RepeatPlaylist, RandomPlaylist  # noqa: E402


class FakeWrapper:
    _n = 0

    def __init__(self, ready=False):
        FakeWrapper._n += 1
        self.id = "item%03d" % FakeWrapper._n
        self._ready = ready

    def is_ready(self):
        return self._ready


class Prefetcher(PlayerMixin):
    """PlayerMixin cut down to the prefetch machinery: record which items
    async_download would have started instead of spawning threads."""

    def __init__(self, active=None):
        self.log = logging.getLogger("test")
        self._download_lock = threading.Lock()
        self._active_downloads = set(active or [])
        self.started = []

    def async_download(self, item):
        self.started.append(item.id)
        self._active_downloads.add(item.id)

    def send_channel_msg(self, msg):
        pass


def make_playlist(cls, wrappers, current_index=0):
    playlist = cls()
    list.extend(playlist, wrappers)
    playlist.current_index = current_index
    return playlist


class UpcomingItemsTestCase(unittest.TestCase):
    def test_base_window_after_current(self):
        w = [FakeWrapper() for _ in range(5)]
        playlist = make_playlist(RandomPlaylist, w, current_index=1)
        self.assertEqual([w[2], w[3], w[4]],
                         playlist.upcoming_items(3))

    def test_base_window_clamps_at_end(self):
        w = [FakeWrapper() for _ in range(3)]
        playlist = make_playlist(RandomPlaylist, w, current_index=1)
        self.assertEqual([w[2]], playlist.upcoming_items(5))

    def test_oneshot_skips_head(self):
        # in one-shot mode index 0 is the currently playing item
        w = [FakeWrapper() for _ in range(4)]
        playlist = make_playlist(OneshotPlaylist, w, current_index=0)
        self.assertEqual([w[1], w[2]], playlist.upcoming_items(2))

    def test_repeat_wraps_without_duplicates(self):
        w = [FakeWrapper() for _ in range(3)]
        playlist = make_playlist(RepeatPlaylist, w, current_index=2)
        self.assertEqual([w[0], w[1]], playlist.upcoming_items(5))

    def test_zero_count_empty(self):
        w = [FakeWrapper() for _ in range(3)]
        playlist = make_playlist(RandomPlaylist, w, current_index=0)
        self.assertEqual([], playlist.upcoming_items(0))


class PrefetchTestCase(unittest.TestCase):
    def setUp(self):
        config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
        config.add_section('bot')
        config.set('bot', 'prefetch_count', '3')
        self._saved_config = getattr(var, 'config', None)
        self._saved_playlist = getattr(var, 'playlist', None)
        var.config = config

    def tearDown(self):
        var.config = self._saved_config
        var.playlist = self._saved_playlist

    def test_prefetches_window_beyond_next(self):
        w = [FakeWrapper() for _ in range(5)]
        var.playlist = make_playlist(RandomPlaylist, w, current_index=0)
        player = Prefetcher()
        player._prefetch_upcoming()
        # window is upcoming_items(3) == w1..w3; w1 is left to the loop's
        # own next-item download, prefetch covers w2 and w3
        self.assertEqual([w[2].id, w[3].id], player.started)

    def test_concurrency_cap_respected(self):
        w = [FakeWrapper() for _ in range(6)]
        var.playlist = make_playlist(RandomPlaylist, w, current_index=0)
        player = Prefetcher(active={'already-running-1', 'already-running-2'})
        player._prefetch_upcoming()
        self.assertEqual([], player.started)

    def test_ready_items_are_skipped(self):
        w = [FakeWrapper(ready=True) for _ in range(4)]
        var.playlist = make_playlist(RandomPlaylist, w, current_index=0)
        player = Prefetcher()
        player._prefetch_upcoming()
        self.assertEqual([], player.started)

    def test_window_slides_with_playback(self):
        w = [FakeWrapper(ready=True) for _ in range(3)] + \
            [FakeWrapper() for _ in range(2)]
        var.playlist = make_playlist(RandomPlaylist, w, current_index=1)
        player = Prefetcher()
        player._prefetch_upcoming()
        # upcoming = w2 (ready, immediate next, skipped by [1:]), w3, w4
        self.assertEqual([w[3].id, w[4].id], player.started)

    def test_count_one_disables_extra_prefetch(self):
        var.config.set('bot', 'prefetch_count', '1')
        w = [FakeWrapper() for _ in range(5)]
        var.playlist = make_playlist(RandomPlaylist, w, current_index=0)
        player = Prefetcher()
        player._prefetch_upcoming()
        self.assertEqual([], player.started)


if __name__ == '__main__':
    unittest.main()
