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
