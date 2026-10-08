"""Drive MumbleBot._handle_message with a stubbed `mumble` module.
Usage: python audit/repro/commands/repro_dispatch.py <repo-root>"""
import configparser, os, sys, tempfile, types, time, re
ROOT = sys.argv[1]
sys.path.insert(0, ROOT)
os.chdir(ROOT)
# --- stub pymumble 2.x ---
m = types.ModuleType('mumble'); m.Mumble = object
mc = types.ModuleType('mumble.constants'); mc.CONN_STATE = object
sys.modules['mumble'] = m; sys.modules['mumble.constants'] = mc

import variables as var
import constants
from database import SettingsDatabase, MusicDatabase, DatabaseMigration

cfg = configparser.ConfigParser(interpolation=None, allow_no_value=True)
cfg.read('configuration.default.ini')
var.config = cfg
TMP = tempfile.mkdtemp()
var.db = SettingsDatabase(os.path.join(TMP, 's.db'))
var.settings_db_path = os.path.join(TMP, 's.db')
var.music_db = MusicDatabase(os.path.join(TMP, 'm.db'))
DatabaseMigration(var.db, var.music_db).migrate()
constants.load_lang('en_US')

from bot.core import MumbleBot
import commands

class U:
    def __init__(s, name, ch=1, user_id=None):
        s.name, s.channel_id = name, ch
        if user_id is not None:
            s.user_id = user_id
        s.inbox = []
    def send_text_message(s, msg): s.inbox.append(msg)

class Users(dict):
    pass

class Text:
    def __init__(s, actor, message, session=()):
        s.actor, s.message, s.session, s.channel_id = actor, message, list(session), [1]

def make_bot():
    b = MumbleBot.__new__(MumbleBot)
    b.cmd_handle = {}
    b.log = __import__('logging').getLogger('x')
    b.exit = False
    b._display_rms = False
    b.paused = []
    b.pause = lambda: b.paused.append(1)
    mum = types.SimpleNamespace()
    mum.users = Users()
    mum.users.myself = U('bot', 1)
    b.mumble = mum
    commands.register_all_commands(b)
    return b

def run(b, actor, msg, session=()):
    b._handle_message(Text(actor, msg, session))
    return b.mumble.users[actor].inbox

print("=== A. admin = 'Alice'; unregistered guest literally named 'Alice' (no user_id) ===")
cfg.set('bot', 'admin', 'Alice')
b = make_bot(); b.mumble.users[5] = U('Alice')            # no user_id attribute at all
run(b, 5, '!kill'); print("  bot.exit after !kill:", b.exit)

print("=== B. split_username_at_space=True, guest named 'Alice (guest)' ===")
cfg.set('commands', 'split_username_at_space', 'True')
b = make_bot(); b.mumble.users[6] = U('Alice (guest)')
run(b, 6, '!kill'); print("  bot.exit after !kill:", b.exit)
cfg.set('commands', 'split_username_at_space', 'False')

print("=== C. !userban Bob, then Bob runs !kill-free command (!rtrms) ===")
b = make_bot(); b.mumble.users[1] = U('Alice'); b.mumble.users[7] = U('Bob')
run(b, 1, '!userban Bob'); print("  stored:", var.db.items('user_ban'))
inbox = run(b, 7, '!rtrms'); print("  Bob inbox:", inbox, " _display_rms toggled:", b._display_rms)

print("=== D. partial-match prefixes resolving uniquely to ADMIN commands ===")
b = make_bot()
partial = [c for c, h in b.cmd_handle.items() if h['partial_match']]
admin_partial = sorted(c for c in partial if b.cmd_handle[c]['admin'])
for cmd in admin_partial:
    for n in range(1, len(cmd)):
        p = cmd[:n]
        if p in b.cmd_handle: continue
        ms = [c for c in partial if c.startswith(p)]
        if ms == [cmd]:
            print(f"  '!{p}' -> {cmd}"); break
print("  rtrms registration:", b.cmd_handle['rtrms'])

print("=== E. non-admin bypasses: PM from other channel; !joinme from other channel ===")
b = make_bot(); b.mumble.users[8] = U('Mallory', ch=99)
b.mumble.users.myself.move_in = lambda ch, token=None: print("  bot moved to channel", ch, "token", token)
run(b, 8, '!joinme secrettoken', session=[0])

print("=== F. admin !dropdatabase, then any later command ===")
import logging; logging.basicConfig(level=logging.ERROR, format="  LOG %(message)s")
b = make_bot(); b.mumble.users[1] = U('Alice')
b.send_msg = lambda msg, text: b.mumble.users[text.actor].send_text_message(msg)
run(b, 1, '!dropdatabase'); print("  inbox:", b.mumble.users[1].inbox)
b.mumble.users[1].inbox.clear()
try:
    b.message_received(Text(1, '!version'))
except Exception as e:
    print("  raised", e)
print("  inbox after !version:", b.mumble.users[1].inbox, "| var.music_db.db_path == settings path:", var.music_db.db_path == var.settings_db_path)
