import configparser, os, sys, tempfile
sys.path.insert(0,os.getcwd())
import variables as var
from tests.test_web_api import FakeBot, FakeCache, FakePlaylist
base=tempfile.mkdtemp()
cfg=configparser.ConfigParser(interpolation=None)
cfg.read_dict({'webinterface':{'auth_method':'none','access_email_header':'','access_team_domain':'','access_aud':'','is_web_proxified':'False','flask_secret':'x'},'bot':{'username':'b'},'commands':{'command_symbol':'!'}})
var.config=cfg; var.music_folder=base+'/'; var.tmp_folder=base+'/tmp/'; os.makedirs(var.tmp_folder,exist_ok=True)
var.bot=FakeBot(); var.playlist=FakePlaylist(); var.cache=FakeCache(); var.user_db=None; var.is_proxified=False
import interface
c=interface.web.test_client()
# type confusion: JSON list/str where an int is expected -> unhandled 500
print('A /post play_music=[1] (list) ->', c.post('/post', json={'play_music':[1]}).status_code)
print('B /post delete_music="x"    ->', c.post('/post', json={'delete_music':'x'}).status_code)
print('C /post move_playhead="x"   ->', c.post('/post', json={'move_playhead':'x'}).status_code)
# /playlist range params: int() of arbitrary string, no try/except
var.playlist.append('dummy') if hasattr(var.playlist,'append') else None
print('len(playlist)=', len(var.playlist))
print('D /playlist?range_from=abc  ->', c.get('/playlist?range_from=abc&range_to=5').status_code)
