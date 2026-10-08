import configparser, os, sys, tempfile, types
sys.path.insert(0, os.getcwd())
import variables as var
from tests.test_web_api import FakeBot, FakeCache, FakePlaylist

base = tempfile.mkdtemp()
cfg = configparser.ConfigParser(interpolation=None)
cfg.read_dict({'webinterface': {'auth_method': 'none', 'upload_enabled': 'True',
    'max_upload_file_size': '4G', 'upload_extract_audio': 'False',
    'access_email_header': '', 'access_team_domain': '', 'access_aud': '',
    'is_web_proxified': 'False', 'flask_secret': 'x'},
    'bot': {'username': 'b'}, 'commands': {'command_symbol': '!'}})
var.config = cfg
var.music_folder = base + '/'
var.tmp_folder = base + '/tmp/'
os.makedirs(var.tmp_folder, exist_ok=True)
var.bot = FakeBot(); var.playlist = FakePlaylist(); var.cache = FakeCache(); var.user_db = None
var.is_proxified = False

import web_upload
# Pretend the staging disk has only 100 MB free.
FAKE_FREE = 100 * 1024 * 1024
import shutil
_real = shutil.disk_usage
shutil.disk_usage = lambda p: types.SimpleNamespace(total=FAKE_FREE, used=0, free=FAKE_FREE)

import interface
c = interface.web.test_client()

# Each init declares a 95 MB file -- individually < 100 MB free, so each passes
# the `size*1.05 > free` guard. But there is NO reservation and NO cap on the
# number of concurrent pending uploads, so N of them all pass against the SAME
# 100 MB of free space. Their .part files together can far exceed the disk.
sizes = 95 * 1024 * 1024
ids = []
for i in range(8):
    r = c.post('/api/upload/init', json={'filename': f'song{i}.mp3', 'size': sizes})
    print('init %d -> %s %s' % (i, r.status_code, r.get_json()))
    if r.status_code == 200:
        ids.append(r.get_json()['upload_id'])

print('accepted uploads:', len(ids),
      '| declared total bytes =', len(ids) * sizes,
      '| real free claimed    =', FAKE_FREE)
print('=> server promised space it does not have; combined declared size is %.1fx the free disk'
      % (len(ids) * sizes / FAKE_FREE))

# There is also no cap on how many pending uploads one identity may hold, and
# stale .part files are only pruned after 24h (STALE_SECONDS).
print('STALE_SECONDS =', web_upload.STALE_SECONDS, '(=%d h)' % (web_upload.STALE_SECONDS // 3600))
print('per-user pending-upload cap in code:',
      'NONE' if 'MAX' not in open('web_upload.py').read().upper().replace('MAX_UPLOAD_BYTES','') else 'present')
shutil.disk_usage = _real
