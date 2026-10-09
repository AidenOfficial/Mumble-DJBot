from v_common import *
import io, os, werkzeug
import importlib.metadata as m; print('werkzeug', m.version('werkzeug'))
i = setup('none')
base = var.base_dir
os.makedirs(var.music_folder, exist_ok=True)
c = i.web.test_client()
def up(fn, targetdir='', mt='audio/mpeg'):
    return c.post('/upload', data={'file': (io.BytesIO(b'PWNED'), fn, mt), 'targetdir': targetdir}, content_type='multipart/form-data')
for fn in ['../one.mp3', '../../two.mp3']:
    r = up(fn)
    print(repr(fn), '->', r.status_code)
for root, d, f in os.walk(base):
    for x in f:
        print('  on disk:', os.path.relpath(os.path.join(root, x), base))
print('music folder =', os.path.relpath(var.music_folder, base))
# also: spoofed mimetype with non-audio content
r = up('evil.php', 'sub', 'audio/x-fake'); print('evil.php w/ audio mimetype ->', r.status_code)
r = up('evil.php', 'sub', 'text/html'); print('evil.php w/ html mimetype ->', r.status_code)
# raw wire check: what filename does werkzeug hand us for a raw multipart body?
from werkzeug.test import EnvironBuilder
body = (b'--B\r\nContent-Disposition: form-data; name="file"; filename="../../raw.mp3"\r\nContent-Type: audio/mpeg\r\n\r\nX\r\n'
        b'--B\r\nContent-Disposition: form-data; name="targetdir"\r\n\r\n\r\n--B--\r\n')
r = c.post('/upload', data=body, headers={'Content-Type': 'multipart/form-data; boundary=B'})
print('raw multipart ../../raw.mp3 ->', r.status_code, os.path.exists(os.path.join(base, 'raw.mp3')))
