from v_common import *
import requests
hits = []
class R:
    headers = {'content-type': 'application/json'}
    def json(self): return {'servertitle': 'INTERNAL-SERVICE-TITLE-LEAKED'}
def head(url, *a, **k): hits.append(('HEAD', url)); return R()
def get(url, *a, **k): hits.append(('GET', url)); return R()
requests.head = head; requests.get = get
i = setup('none')
import media.radio, media.item
from media.item import item_builders, item_id_generators
class Cache(FakeCache):
    def get_item(self, **kw):
        _id = item_id_generators[kw['type']](**kw)
        if _id not in self: self[_id] = item_builders[kw['type']](**kw)
        return self[_id]
    def __init__(self): super().__init__()
var.cache = Cache()
c = i.web.test_client()
r = c.post('/post', json={'add_radio': 'http://169.254.169.254/latest/meta-data/'})
print('POST /post add_radio ->', r.status_code, 'outbound:', hits)
print('queue titles:', [w.item().title for w in var.playlist])
# api/search/add -> url type: show it reaches the URL builder with no host filter
seen = []
item_builders['url'] = lambda **kw: (seen.append(kw['url']) or (_ for _ in ()).throw(RuntimeError('stop')))
try:
    r = c.post('/api/search/add', json={'source': 'youtube', 'url': 'http://127.0.0.1:8181/internal'})
    print('search/add status', r.status_code)
except Exception as e:
    print('search/add raised', e)
print('url builder received:', seen)
# control: what chat guard would say
import util
for u in ['http://169.254.169.254/latest/meta-data/', 'http://127.0.0.1:8181/', 'http://192.168.1.1/']:
    print('is_public_url', u, util.is_public_url(u))
