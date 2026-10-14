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


class TestCheckExtraConfig:
    @staticmethod
    def _configs(user_ini):
        import configparser
        default = configparser.ConfigParser(interpolation=None, allow_no_value=True)
        default.read(util.solve_filepath('configuration.default.ini'), encoding='utf-8')
        config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
        config.read(util.solve_filepath('configuration.default.ini'), encoding='utf-8')
        config.read_string(user_ini)
        return config, default

    def test_default_config_is_clean(self):
        assert util.check_extra_config(*self._configs('')) == ([], [])

    def test_unknown_option_is_reported(self):
        extra, obsolete = util.check_extra_config(*self._configs('[bot]\nno_such_option = 1\n'))
        assert extra == [('bot', 'no_such_option')] and obsolete == []

    def test_removed_update_options_are_tolerated(self):
        # 旧配置文件里还留着上游的更新配置,不能因此拒绝启动
        extra, obsolete = util.check_extra_config(*self._configs(
            '[bot]\nauto_check_update = True\ntarget_version = stable\npip3_path = venv/bin/pip\n'))
        assert extra == []
        assert sorted(obsolete) == [('bot', 'auto_check_update'), ('bot', 'pip3_path'),
                                    ('bot', 'target_version')]


class TestUpgradeExtractors:
    @staticmethod
    def _fake_run(returncode=0, stdout='', stderr=''):
        calls = []

        def run(cmd, **kwargs):
            import subprocess
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
        return run, calls

    def test_uses_own_interpreter_pip(self, monkeypatch):
        run, calls = self._fake_run(stdout='Successfully installed yt-dlp-2026.10.1\n')
        monkeypatch.setattr(util.sp, 'run', run)
        msg = util.upgrade_extractors()
        assert calls == [[sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp', 'spotdl']]
        assert 'yt-dlp-2026.10.1' in msg and 'Restart' in msg

    def test_already_up_to_date(self, monkeypatch):
        run, _ = self._fake_run(stdout='Requirement already satisfied: yt-dlp\n')
        monkeypatch.setattr(util.sp, 'run', run)
        assert 'up to date' in util.upgrade_extractors()

    def test_pip_failure_is_reported_not_raised(self, monkeypatch):
        run, _ = self._fake_run(returncode=1, stderr='boom')
        monkeypatch.setattr(util.sp, 'run', run)
        assert 'failed' in util.upgrade_extractors().lower()
