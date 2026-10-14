"""WEB-07: legacy /upload filename path traversal + arbitrary file write.
Fully contained: music_folder and all writes stay under THIS dir's sandbox/.
"""
import configparser, io, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(HERE, 'sandbox'), exist_ok=True)
SANDBOX = tempfile.mkdtemp(prefix='up_', dir=os.path.join(HERE, 'sandbox'))
sys.path.insert(0, os.getcwd())
import variables as var
from tests.test_web_api import FakeBot, FakeCache, FakePlaylist

music = os.path.join(SANDBOX, 'music', 'uploads')  # default targetdir is 'uploads/'
os.makedirs(music)
music_root = os.path.join(SANDBOX, 'music') + '/'
cfg = configparser.ConfigParser(interpolation=None)
cfg.read_dict({'webinterface': {'auth_method': 'none', 'upload_enabled': 'True',
    'access_email_header': '', 'access_team_domain': '', 'access_aud': ''}, 'bot': {'username': 'b'}})
var.config = cfg; var.music_folder = music_root; var.tmp_folder = SANDBOX + '/tmp/'
var.bot = FakeBot(); var.playlist = FakePlaylist(); var.cache = FakeCache(); var.user_db = None
import interface
c = interface.web.test_client()

def up(fname, mime='audio/mpeg', targetdir='uploads/'):
    return c.post('/upload', data={'file': (io.BytesIO(b'PAYLOAD'), fname, mime),
                  'targetdir': targetdir}, content_type='multipart/form-data')

# 1) one ../ stays inside music (what the ORIGINAL flawed test checked)
r1 = up('../a.mp3')
inside = os.path.join(music_root, 'a.mp3')
# 2) two ../ escapes music_folder entirely -> lands in SANDBOX root (outside music)
r2 = up('../../escaped.mp3')
escaped = os.path.join(SANDBOX, 'escaped.mp3')
# 3) arbitrary extension with spoofed audio mimetype passes the type gate
r3 = up('../../evil.pth', mime='audio/x-anything')
pth = os.path.join(SANDBOX, 'evil.pth')
# 4) html mimetype is rejected (the only real gate)
r4 = up('../../nope.txt', mime='text/html')

print('1 one-dotdot  ->', r1.status_code, '| landed in music/:', os.path.exists(inside))
print('2 two-dotdot  ->', r2.status_code, '| ESCAPED music_folder:', os.path.exists(escaped),
      '->', os.path.relpath(escaped, music_root))
print('3 .pth w/ audio mime ->', r3.status_code, '| written outside music:', os.path.exists(pth))
print('4 .txt w/ html  mime ->', r4.status_code, '(415 = rejected, type gate is the ONLY check)')
