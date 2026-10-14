import configparser, os, sys, tempfile
sys.path.insert(0,os.getcwd())
import variables as var
from tests.test_web_api import FakeBot, FakeCache, FakePlaylist
base=tempfile.mkdtemp()
cfg=configparser.ConfigParser(interpolation=None)
cfg.read_dict({'webinterface':{'auth_method':'none','access_email_header':'','access_team_domain':'','access_aud':''},'bot':{'username':'b'}})
var.config=cfg; var.music_folder=base+'/'; var.tmp_folder=base+'/tmp/'; os.makedirs(var.tmp_folder,exist_ok=True)
var.bot=FakeBot(); var.playlist=FakePlaylist(); var.cache=FakeCache(); var.user_db=None
# intercept the outbound HTTP that RadioItem makes so we SEE the SSRF target
import requests
hits=[]
requests.head=lambda url,*a,**k: hits.append(('HEAD',url)) or (_ for _ in ()).throw(requests.exceptions.ConnectionError())
requests.get =lambda url,*a,**k: hits.append(('GET',url))  or (_ for _ in ()).throw(requests.exceptions.ConnectionError())
import media.radio  # uses module-level requests
import interface
c=interface.web.test_client()
r=c.post('/post', json={'add_radio':'http://169.254.169.254/latest/meta-data/'})
print('add_radio status', r.status_code, 'SSRF outbound targets:', hits[:3])
