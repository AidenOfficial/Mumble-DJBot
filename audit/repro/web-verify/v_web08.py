from v_common import *
# none mode: cross-site "simple" form post (no cookie involved at all)
i = setup('none')
c = i.web.test_client()
r = c.post('/post', data={'action': 'clear'}, headers={'Origin': 'http://evil.example', 'Referer': 'http://evil.example/x'},
           content_type='application/x-www-form-urlencoded')
print('none-mode form POST /post action=clear from evil Origin ->', r.status_code)
r = c.post('/api/queue', data={'action': 'clear'}, headers={'Origin': 'http://evil.example'},
           content_type='application/x-www-form-urlencoded')
print('none-mode form POST /api/queue (form) from evil Origin ->', r.status_code)
# token mode: what does the session cookie look like?
i = setup('token')
var.db.set('web_token', 'tok1234', 'alice'); var.db.set('user', 'alice', '{}')
c = i.web.test_client()
r = c.get('/api/status?token=tok1234')
print('token login ->', r.status_code, 'Set-Cookie:', r.headers.get('Set-Cookie'))
print('config SESSION_COOKIE_SAMESITE =', i.web.config.get('SESSION_COOKIE_SAMESITE'), '| SECURE =', i.web.config.get('SESSION_COOKIE_SECURE'), '| HTTPONLY =', i.web.config.get('SESSION_COOKIE_HTTPONLY'))
# state change via GET anywhere?
import re
src = open('interface.py').read()
print('GET routes:', re.findall(r'@web.route\("([^"]+)", methods=\[\'GET\'\]\)', src))
