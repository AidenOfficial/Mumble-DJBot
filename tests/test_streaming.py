import configparser
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402
from bot.player import PlayerMixin  # noqa: E402


class FakeURLItem:
    """Mimics the URLItem attributes/methods the streaming logic relies on
    (media.url imports yt-dlp etc., overkill for these unit tests)."""

    def __init__(self, duration=600, progress=0.0, downloading=True,
                 ready='preparing', path=None):
        self.duration = duration
        self.progress = progress
        self.downloading = downloading
        self.ready = ready
        self.path = path
        self.no_stream = False

    # Copy of URLItem.playable_from so the decision logic under test matches
    # production; the real implementation is exercised via test_urlitem_*.
    def playable_from(self, playhead, buffer_secs):
        if self.ready == 'yes':
            return True
        if self.no_stream or not self.downloading or not self.duration:
            return False
        if self.path is not None and not os.path.exists(self.path):
            return False
        downloaded_secs = (self.progress or 0.0) * self.duration
        return downloaded_secs >= min(self.duration - 1, playhead + buffer_secs)


class FakeWrapper:
    def __init__(self, item):
        self._item = item
        self.id = 'fake'

    def item(self):
        return self._item

    def is_ready(self):
        return self._item.ready == 'yes'

    def playable_from(self, playhead, buffer_secs):
        return self._item.playable_from(playhead, buffer_secs)


class FakePlaylist:
    def __init__(self, wrapper):
        self.current_index = 0 if wrapper else -1
        self._wrapper = wrapper

    def current_item(self):
        return self._wrapper


class Player(PlayerMixin):
    """Bare PlayerMixin with just the state the streaming methods touch."""

    def __init__(self):
        self.playhead = 0
        self.read_pcm_size = 0
        self.wait_for_ready = False
        self.song_start_at = 123.0
        self.log = logging.getLogger("test")


class StreamingTestCase(unittest.TestCase):
    def setUp(self):
        config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
        config.add_section('bot')
        config.set('bot', 'stream_while_downloading', 'True')
        config.set('bot', 'stream_buffer_seconds', '30')
        config.set('bot', 'stream_min_duration', '300')
        self._saved_config = getattr(var, 'config', None)
        self._saved_playlist = getattr(var, 'playlist', None)
        var.config = config
        self.player = Player()

    def tearDown(self):
        var.config = self._saved_config
        var.playlist = self._saved_playlist

    def set_item(self, **kwargs):
        self.item = FakeURLItem(**kwargs)
        self.wrapper = FakeWrapper(self.item)
        var.playlist = FakePlaylist(self.wrapper)
        return self.item

    # ---- _stream_playable -------------------------------------------------

    def test_playable_when_buffered_past_watermark(self):
        self.set_item(duration=600, progress=0.1)  # 60s downloaded
        self.assertTrue(self.player._stream_playable(self.wrapper))

    def test_not_playable_below_watermark(self):
        self.set_item(duration=600, progress=0.04)  # 24s < 30s buffer
        self.assertFalse(self.player._stream_playable(self.wrapper))

    def test_watermark_follows_playhead(self):
        self.set_item(duration=600, progress=0.1)  # 60s downloaded
        self.player.playhead = 50                  # needs 80s
        self.assertFalse(self.player._stream_playable(self.wrapper))
        self.item.progress = 0.15                  # 90s downloaded
        self.assertTrue(self.player._stream_playable(self.wrapper))

    def test_end_of_file_relaxes_buffer(self):
        # 595s of 600s downloaded, playhead at 580: full 30s buffer can
        # never be satisfied, min(duration-1, ...) must kick in
        self.set_item(duration=600, progress=0.999)
        self.player.playhead = 580
        self.assertTrue(self.player._stream_playable(self.wrapper))

    def test_short_items_never_stream(self):
        self.set_item(duration=200, progress=0.9)  # < stream_min_duration
        self.assertFalse(self.player._stream_playable(self.wrapper))

    def test_disabled_by_config(self):
        var.config.set('bot', 'stream_while_downloading', 'False')
        self.set_item(duration=600, progress=0.5)
        self.assertFalse(self.player._stream_playable(self.wrapper))

    def test_no_stream_flag_blocks(self):
        item = self.set_item(duration=600, progress=0.5)
        item.no_stream = True
        self.assertFalse(self.player._stream_playable(self.wrapper))

    def test_unknown_duration_never_streams(self):
        self.set_item(duration=0, progress=0.5)
        self.assertFalse(self.player._stream_playable(self.wrapper))

    # ---- _stream_rewait ---------------------------------------------------

    def test_clean_eof_mid_download_rewaits(self):
        self.set_item(duration=600, progress=0.2)
        self.player.read_pcm_size = 10000
        self.player.playhead = 100
        self.assertTrue(self.player._stream_rewait(0))
        self.assertTrue(self.player.wait_for_ready)
        self.assertEqual(-1, self.player.song_start_at)
        self.assertFalse(self.item.no_stream)

    def test_failed_launch_marks_no_stream(self):
        # ffmpeg could not open the growing file (e.g. moov atom at the end)
        self.set_item(duration=600, progress=0.2)
        self.player.read_pcm_size = 0
        self.assertTrue(self.player._stream_rewait(183))
        self.assertTrue(self.item.no_stream)
        self.assertTrue(self.player.wait_for_ready)

    def test_clean_eof_without_audio_marks_no_stream(self):
        self.set_item(duration=600, progress=0.2)
        self.player.read_pcm_size = 0
        self.assertTrue(self.player._stream_rewait(0))
        self.assertTrue(self.item.no_stream)

    def test_finished_download_advances_normally(self):
        self.set_item(duration=600, progress=1.0, downloading=False, ready='yes')
        self.player.read_pcm_size = 10000
        self.player.playhead = 600
        self.assertFalse(self.player._stream_rewait(0))

    def test_kill_signals_advance_normally(self):
        # a user skip kills ffmpeg (-9); never re-wait on that
        self.set_item(duration=600, progress=0.2)
        self.player.read_pcm_size = 10000
        self.assertFalse(self.player._stream_rewait(-9))
        self.assertFalse(self.player._stream_rewait(-15))

    def test_played_to_known_end_advances(self):
        self.set_item(duration=600, progress=0.999)
        self.player.read_pcm_size = 10000
        self.player.playhead = 599
        self.assertFalse(self.player._stream_rewait(0))

    def test_disabled_config_never_rewaits(self):
        var.config.set('bot', 'stream_while_downloading', 'False')
        self.set_item(duration=600, progress=0.2)
        self.player.read_pcm_size = 10000
        self.assertFalse(self.player._stream_rewait(0))

    def test_empty_playlist_never_rewaits(self):
        var.playlist = FakePlaylist(None)
        var.playlist.current_index = -1
        self.assertFalse(self.player._stream_rewait(0))


class URLItemPlayableFromTestCase(unittest.TestCase):
    """Exercise the real URLItem.playable_from against a temp file, keeping
    it honest with the FakeURLItem copy above."""

    @classmethod
    def setUpClass(cls):
        import importlib
        try:
            importlib.import_module('media.url')
            cls.has_media_url = True
        except Exception:
            cls.has_media_url = False

    def make_item(self, tmp, duration=600, progress=0.5, with_file=True):
        import media.url
        item = media.url.URLItem.__new__(media.url.URLItem)
        item.ready = 'preparing'
        item.downloading = True
        item.no_stream = False
        item.duration = duration
        item.progress = progress
        item.path = os.path.join(tmp, 'cachefile')
        if with_file:
            open(item.path, 'wb').close()
        return item

    def test_urlitem_playable_from(self):
        if not self.has_media_url:
            self.skipTest("media.url dependencies unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            item = self.make_item(tmp, duration=600, progress=0.1)
            self.assertTrue(item.playable_from(0, 30))
            self.assertFalse(item.playable_from(50, 30))
            item.no_stream = True
            self.assertFalse(item.playable_from(0, 30))

    def test_urlitem_requires_file_on_disk(self):
        if not self.has_media_url:
            self.skipTest("media.url dependencies unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            item = self.make_item(tmp, duration=600, progress=0.5, with_file=False)
            self.assertFalse(item.playable_from(0, 30))

    def test_urlitem_ready_is_always_playable(self):
        if not self.has_media_url:
            self.skipTest("media.url dependencies unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            item = self.make_item(tmp, duration=600, progress=1.0)
            item.ready = 'yes'
            item.downloading = False
            self.assertTrue(item.playable_from(0, 30))


if __name__ == '__main__':
    unittest.main()
