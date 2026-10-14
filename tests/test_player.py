# coding=utf-8
"""Unit tests for pure playback helpers (fade curves, PCM alignment)."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.player import PlayerMixin  # noqa: E402


def make_pcm(n_samples, value=1000):
    return struct.pack("<" + "h" * n_samples, *([value] * n_samples))


class TestFadeout:
    def test_mono_fadeout_attenuates(self):
        p = PlayerMixin()
        pcm = make_pcm(480)
        out = p._fadeout(pcm, stereo=False, fadein=False)
        assert len(out) == len(pcm)
        first = struct.unpack("<h", out[0:2])[0]
        last = struct.unpack("<h", out[-2:])[0]
        assert first == 1000          # fade starts at full volume
        assert abs(last) < abs(first)  # and decays

    def test_mono_fadein_starts_quiet(self):
        p = PlayerMixin()
        pcm = make_pcm(480)
        out = p._fadeout(pcm, stereo=False, fadein=True)
        first = struct.unpack("<h", out[0:2])[0]
        last = struct.unpack("<h", out[-2:])[0]
        assert abs(first) < abs(last)
        assert last == 1000

    def test_partial_frame_is_dropped_not_crashing(self):
        # A truncated final buffer must not raise struct.error.
        p = PlayerMixin()
        pcm = make_pcm(480) + b"\x01"          # odd trailing byte
        out = p._fadeout(pcm, stereo=False)
        assert len(out) == 480 * 2

    def test_stereo_alignment(self):
        p = PlayerMixin()
        pcm = make_pcm(480) + b"\x01\x02"       # half a stereo frame extra
        out = p._fadeout(pcm, stereo=True)
        assert len(out) % 4 == 0

    def test_no_silence_padding_appended(self):
        # Regression: upstream appended an equal-length run of zeros,
        # doubling every faded chunk with a silent gap.
        p = PlayerMixin()
        pcm = make_pcm(480)
        out = p._fadeout(pcm, stereo=False)
        assert len(out) == len(pcm)


class _SeekItem:
    def __init__(self, type_='url', duration=200):
        self.type = type_
        self.duration = duration


class _SeekWrapper:
    def __init__(self, item):
        self.id = 'w1'
        self._item = item

    def item(self):
        return self._item


class _SeekPlaylist(list):
    def current_item(self):
        return self[0] if self else None


class TestSeek:
    def make(self, item, paused=False):
        import logging
        import variables as var
        p = PlayerMixin()
        p.log = logging.getLogger('test')
        p.is_pause = paused
        p.playhead = 10.0
        p.pause_at_id = ''
        p.played = []
        p.play = lambda index=-1, start_at=0: p.played.append((index, start_at))
        self._saved = var.playlist
        var.playlist = _SeekPlaylist([_SeekWrapper(item)] if item else [])
        return p

    def teardown_method(self):
        import variables as var
        var.playlist = getattr(self, '_saved', None)

    def test_seek_while_playing_restarts_at_position(self):
        p = self.make(_SeekItem())
        assert p.seek(42.5)
        assert p.played == [(-1, 42.5)]

    def test_seek_is_clamped(self):
        p = self.make(_SeekItem(duration=100))
        assert p.seek(500)
        assert p.played == [(-1, 99)]
        assert p.seek(-3)
        assert p.played[-1] == (-1, 0.0)

    def test_seek_while_paused_only_moves_playhead(self):
        p = self.make(_SeekItem(), paused=True)
        assert p.seek(60)
        assert p.played == []
        assert p.playhead == 60
        assert p.pause_at_id == 'w1'  # resume() 会从 60 秒续播

    def test_cannot_seek_radio_or_unknown_duration(self):
        assert not self.make(_SeekItem('radio', 0)).seek(5)
        assert not self.make(_SeekItem('livestream', 3600)).seek(5)
        assert not self.make(_SeekItem('url', 0)).seek(5)
        assert not self.make(None).seek(5)
