# coding=utf-8
"""Unit tests for the pure helpers in util.py (no network, no Mumble)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import util  # noqa: E402


class TestGetBilibiliUrlFromInput:
    def test_bv_id_is_converted_to_av_url(self):
        # BV pages answer 412 to yt-dlp; the av form must come back.
        assert util.get_bilibili_url_from_input("BV1xx411c7mD") == \
            "https://www.bilibili.com/video/av2"

    def test_full_bv_link(self):
        url = util.get_bilibili_url_from_input(
            "https://www.bilibili.com/video/BV1xx411c7mD")
        assert url == "https://www.bilibili.com/video/av2"

    def test_av_id_passthrough(self):
        assert util.get_bilibili_url_from_input("av2") == \
            "https://www.bilibili.com/video/av2"

    def test_part_suffix_is_kept(self):
        url = util.get_bilibili_url_from_input("BV1xx411c7mD p3")
        assert url.startswith("https://www.bilibili.com/video/av2")
        assert "p=3" in url

    def test_garbage_returns_falsy(self):
        assert not util.get_bilibili_url_from_input("definitely not a video")


class TestGetUrlFromInput:
    def test_plain_url(self):
        assert util.get_url_from_input("https://example.com/song.mp3") == \
            "https://example.com/song.mp3"

    def test_href_html_from_mumble_chat(self):
        # Mumble chat wraps pasted links in an <a href="..."> tag.
        assert util.get_url_from_input(
            '<a href="https://example.com/x">https://example.com/x</a>') == \
            "https://example.com/x"

    def test_not_a_url(self):
        assert not util.get_url_from_input("hello world")


class TestSolveFilepath:
    def test_empty(self):
        assert util.solve_filepath("") == ""

    def test_absolute_posix(self):
        assert util.solve_filepath("/tmp/x.db") == "/tmp/x.db"

    def test_absolute_windows(self):
        # Regression: C:/... used to be treated as relative and prefixed
        # with the repo directory, breaking every Windows absolute path.
        if os.name == "nt":
            assert util.solve_filepath("C:/tmp/x.db") == "C:/tmp/x.db"

    def test_relative_is_anchored_to_repo(self):
        path = util.solve_filepath("configuration.default.ini")
        assert os.path.isfile(path)


class TestVolumeHelper:
    def test_set_volume_roundtrip(self):
        h = util.VolumeHelper()
        h.set_volume(0.5)
        assert h.plain_volume_set == 0.5
        assert h.volume_set > 0
