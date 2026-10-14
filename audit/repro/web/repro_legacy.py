"""Contained checks against interface.py with a fake bot and a temp music folder."""
import configparser, io, os, sys, tempfile
sys.path.insert(0, os.getcwd())
import variables as var
from tests.test_web_api import FakeBot, FakeCache, FakePlaylist

base = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(__file__)))
music = os.path.join(base, 'music') + '/'
os.makedirs(music)
cfg = configparser.ConfigParser(interpolation=None)
cfg.read_dict({'webinterface': {'auth_method': 'none', 'upload_enabled': 'True', 'max_attempts': '2',
                                'access_email_header': '', 'access_team_domain': '', 'access_aud': ''},
               'bot': {'delete_allowed': 'False', 'username': 'b'}, 'commands': {'command_symbol': '!', 'requests_webinterface_access': 'web'}})
var.config = cfg; var.music_folder = music; var.tmp_folder = base + '/tmp/'
var.bot = FakeBot(); var.playlist = FakePlaylist(); var.cache = FakeCache(); var.user_db = None
import interface
c = interface.web.test_client()

# 1) legacy /upload: filename taken verbatim, mimetype taken from the client
r = c.post('/upload', data={'file': (io.BytesIO(b'not audio'), '../outside.mp3', 'audio/mpeg'),
                            'targetdir': ''}, content_type='multipart/form-data')
print('upload status', r.status_code, 'outside file written:', os.path.exists(os.path.join(base, 'outside.mp3')))

# 2) legacy /post delete_item_from_library with delete_allowed=False
victim = os.path.join(music, 'song.mp3'); open(victim, 'w').close()
class It:
    id = 'x'
    def uri(self): return victim
var.cache['x'] = It()
var.cache.get_item_by_id = lambda i: var.cache.get(i)
var.cache.free_and_delete = lambda i: None
var.playlist.remove_by_id = lambda i: None
r = c.post('/post', json={'delete_item_from_library': 'x'})
print('delete status', r.status_code, 'file still exists:', os.path.exists(victim))

# 3) ban logic keyed on remote_addr, which ReverseProxied takes from X-Real-IP
cfg.set('webinterface', 'auth_method', 'token'); var.is_proxified = True; interface.init_proxy(); var.language = 'en'
var.db = type('D', (), {'get': lambda self, *a, **k: None})()
for i in range(5):
    c.get('/api/status?token=bad', headers={'X-Real-IP': f'10.0.0.{i}'})
print('banned after 5 bad tokens w/ rotating X-Real-IP:', interface.banned_ip)
print('plain request from 10.0.0.1 ->', c.get('/api/status', headers={'X-Real-IP': '10.0.0.1'}).status_code)
