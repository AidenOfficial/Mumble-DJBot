# coding=utf-8
"""Unit tests for live-stream items (media/livestream.py) and the player's
live reconnect logic. yt-dlp is mocked throughout - no network."""
import configparser
import logging
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import constants  # noqa: E402
import variables as var  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setUpModule():
    config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
    config.read(os.path.join(ROOT, "configuration.default.ini"), encoding="utf-8")
    var.config = config
    constants.load_lang("en_US")


import media.livestream as live  # noqa: E402
from media.item import ValidationFailedError, item_loaders, item_id_generators  # noqa: E402
from bot.player import PlayerMixin  # noqa: E402


def fake_ydl(info):
    """A YoutubeDL stand-in whose extract_info always returns `info`."""
    ydl = mock.MagicMock()
    ydl.__enter__ = mock.Mock(return_value=ydl)
    ydl.__exit__ = mock.Mock(return_value=False)
    ydl.extract_info = mock.Mock(return_value=info)
    return ydl


class PickLiveEntriesTest(unittest.TestCase):
    def test_filters_to_live_entries_only(self):
        info = {'entries': [
            {'title': 'vod', 'url': 'https://y/1', 'live_status': 'was_live'},
            {'title': 'live now', 'url': 'https://y/2', 'live_status': 'is_live'},
            None,
            {'title': 'flagged', 'webpage_url': 'https://y/3', 'is_live': True},
            {'title': 'plain', 'url': 'https://y/4'},
        ]}
        lives = live.pick_live_entries(info)
        self.assertEqual([e['url'] for e in lives], ['https://y/2', 'https://y/3'])

    def test_empty_and_none_inputs(self):
        self.assertEqual(live.pick_live_entries(None), [])
        self.assertEqual(live.pick_live_entries({'entries': None}), [])


class LiveStreamItemTest(unittest.TestCase):
    URL = "https://www.youtube.com/watch?v=jfKfPfyJRdk"

    def test_id_differs_from_plain_url_item(self):
        live_id = item_id_generators['livestream'](url=self.URL)
        url_id = item_id_generators['url'](url=self.URL)
        self.assertNotEqual(live_id, url_id)

    def test_validate_accepts_running_live(self):
        item = live.LiveStreamItem(self.URL)
        with mock.patch.object(live.youtube_dl, 'YoutubeDL',
                               return_value=fake_ydl({'is_live': True, 'title': ' lofi radio '})):
            self.assertTrue(item.validate())
        self.assertEqual(item.ready, 'yes')
        self.assertEqual(item.title, 'lofi radio')

    def test_validate_rejects_non_live(self):
        item = live.LiveStreamItem(self.URL)
        with mock.patch.object(live.youtube_dl, 'YoutubeDL',
                               return_value=fake_ydl({'is_live': False, 'title': 'a vod'})):
            with self.assertRaises(ValidationFailedError):
                item.validate()
        self.assertEqual(item.ready, 'failed')

    def test_uri_resolves_fresh_stream_url(self):
        item = live.LiveStreamItem(self.URL)
        with mock.patch.object(live.youtube_dl, 'YoutubeDL',
                               return_value=fake_ydl({'url': 'https://cdn/x.m3u8'})):
            self.assertEqual(item.uri(), 'https://cdn/x.m3u8')

    def test_uri_falls_back_to_formats(self):
        item = live.LiveStreamItem(self.URL)
        info = {'formats': [{'url': 'https://cdn/a'}, {'url': 'https://cdn/b'}]}
        with mock.patch.object(live.youtube_dl, 'YoutubeDL',
                               return_value=fake_ydl(info)):
            self.assertEqual(item.uri(), 'https://cdn/b')

    def test_dict_roundtrip_forces_revalidation(self):
        item = live.LiveStreamItem(self.URL, title="t")
        item.ready = 'yes'
        item.version = 1
        loaded = item_loaders['livestream'](item.to_dict())
        self.assertEqual(loaded.url, self.URL)
        self.assertEqual(loaded.type, 'livestream')
        # a saved stream may have ended - must not be considered ready
        self.assertEqual(loaded.ready, 'pending')


class FakeItem:
    def __init__(self, type_='livestream', id_='abc'):
        self.type = type_
        self.id = id_


class FakeWrapper:
    def __init__(self, item):
        self._item = item

    def item(self):
        return self._item


class FakePlaylist:
    def __init__(self, item):
        self.current_index = 0
        self._wrapper = FakeWrapper(item)

    def current_item(self):
        return self._wrapper


class LivestreamRetryTest(unittest.TestCase):
    def make_player(self, item, played_secs=0.0):
        p = PlayerMixin()
        p.log = logging.getLogger("test")
        p.stereo = False
        p.read_pcm_size = int(played_secs * 48000 * 2)
        p.playhead = played_secs
        p.song_start_at = -1
        p.wait_for_ready = False
        self._old_playlist = getattr(var, 'playlist', None)
        var.playlist = FakePlaylist(item)
        self.addCleanup(self._restore_playlist)
        return p

    def _restore_playlist(self):
        var.playlist = self._old_playlist

    def test_non_livestream_is_not_retried(self):
        p = self.make_player(FakeItem(type_='url'))
        self.assertFalse(p._livestream_retry(1))

    def test_clean_end_after_long_play_moves_on(self):
        p = self.make_player(FakeItem(), played_secs=120)
        self.assertFalse(p._livestream_retry(0))

    def test_mid_play_drop_reconnects_and_skips_history(self):
        p = self.make_player(FakeItem(), played_secs=120)
        self.assertTrue(p._livestream_retry(1))
        self.assertTrue(p.wait_for_ready)
        self.assertEqual(p.playhead, 0)
        self.assertTrue(p._skip_history_once)

    def test_dead_starts_give_up_after_max_retries(self):
        item = FakeItem()
        p = self.make_player(item, played_secs=0)
        for _ in range(PlayerMixin.LIVESTREAM_MAX_RETRIES):
            self.assertTrue(p._livestream_retry(1))
        self.assertFalse(p._livestream_retry(1))

    def test_long_play_resets_the_dead_start_counter(self):
        item = FakeItem()
        p = self.make_player(item, played_secs=0)
        for _ in range(PlayerMixin.LIVESTREAM_MAX_RETRIES):
            self.assertTrue(p._livestream_retry(1))
        # a later drop after 2 minutes of audio is a fresh incident
        p.read_pcm_size = int(120 * 48000 * 2)
        self.assertTrue(p._livestream_retry(1))


if __name__ == '__main__':
    unittest.main()
