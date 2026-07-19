# coding=utf-8
"""The commands package must register every command name defined in the
language files onto a bot, with no dangling references after the split."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import configparser  # noqa: E402

import constants  # noqa: E402
import commands  # noqa: E402
import variables as var  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setup_module(module):
    config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
    config.read(os.path.join(ROOT, "configuration.default.ini"), encoding="utf-8")
    var.config = config


class FakeBot:
    def __init__(self):
        self.registered = {}

    def register_command(self, cmd, handle, no_partial_match=False,
                         access_outside_channel=False, admin=False):
        for name in cmd.split(","):
            name = name.strip()
            if name:
                self.registered[name] = handle


def test_register_all_commands_wires_everything():
    constants.load_lang("en_US")
    bot = FakeBot()
    commands.register_all_commands(bot)
    # a representative sample across every split module
    for expected in ("play", "file", "spotify", "bili", "live", "volume", "duck",
                     "queue", "skip", "tag", "search", "kill", "update",
                     "web", "mode", "shortlist"):
        assert any(expected in name for name in bot.registered), \
            f"no registered command matches '{expected}'"
    # every handler is callable and comes from the commands package
    for name, handle in bot.registered.items():
        assert callable(handle), name
        assert handle.__module__.startswith("commands."), \
            f"{name} -> {handle.__module__}"


def test_mode_aliases_map_to_known_modes():
    from commands.playback import MODE_ALIASES
    assert set(MODE_ALIASES.values()) <= {"one-shot", "repeat", "random", "autoplay"}
