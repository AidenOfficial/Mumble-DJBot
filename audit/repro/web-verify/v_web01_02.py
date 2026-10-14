from v_common import *
i = setup('token')
c = i.web.test_client()
r = c.get('/api/status'); print('A no-cred:', r.status_code, r.data[:40])
from flask.sessions import SecureCookieSessionInterface
forged = SecureCookieSessionInterface().get_signing_serializer(i.web).dumps({'user': 'attacker'})
c2 = i.web.test_client(); c2.set_cookie('session', forged, domain='localhost')
r = c2.get('/api/status'); print('B forged cookie (default secret):', r.status_code, r.data[:60])
# control: same forged cookie but server uses a non-default secret
i.web.secret_key = 'a-real-random-secret-xyz'
c3 = i.web.test_client(); c3.set_cookie('session', forged, domain='localhost')
r = c3.get('/api/status'); print('B2 forged cookie vs non-default secret:', r.status_code, r.data[:40])
# destructive endpoint with forged cookie
i.web.secret_key = 'ChangeThisPassword'
r = c2.post('/api/queue', json={'action':'clear'}); print('B3 forged cookie POST /api/queue clear:', r.status_code, r.data[:60])

# WEB-02
print('--- WEB-02')
i = setup('token')
c = i.web.test_client()
for n in range(100):
    c.get('/api/status?token=g%d' % n, headers={'X-Real-IP': '9.9.%d.%d' % (n // 250, n % 250)})
print('C attacker rotating IP, 100 bad tokens -> banned:', i.banned_ip, 'tracked ips:', len(i.bad_access_count))
# victim ban (token mode)
i = setup('token')
c = i.web.test_client()
for n in range(5):
    c.get('/api/status?token=bad', headers={'X-Real-IP': '203.0.113.7'})
print('D victim 203.0.113.7 banned after spoofed bad tokens:', i.banned_ip)
r = c.get('/api/status', headers={'X-Real-IP': '203.0.113.7'}); print('  victim next request:', r.status_code)
r = c.get('/api/status', headers={'X-Real-IP': '198.51.100.1'}); print('  other ip request:', r.status_code)
# victim ban (password mode)
i = setup('password')
c = i.web.test_client()
import base64 as b
h = {'Authorization': 'Basic ' + b.b64encode(b'u:wrong').decode(), 'X-Real-IP': '203.0.113.9'}
for n in range(5): c.get('/api/status', headers=h)
print('E password mode victim banned:', i.banned_ip)
# control: no proxify
i = setup('token', proxified=False)
c = i.web.test_client()
for n in range(5):
    c.get('/api/status?token=g%d' % n, headers={'X-Real-IP': '9.9.9.%d' % n})
print('F is_web_proxified=False: banned (real peer 127.0.0.1):', i.banned_ip)
# none mode: no counting at all
i = setup('none')
c = i.web.test_client()
for n in range(5): c.get('/api/status', headers={'X-Real-IP': '7.7.7.7'})
print('G none mode banned/counts:', i.banned_ip, i.bad_access_count)
