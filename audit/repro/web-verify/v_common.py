import configparser, os, sys, tempfile, base64
sys.path.insert(0, os.getcwd())
import variables as var
from tests.test_web_api import FakeBot, FakeCache, FakePlaylist
import jinja2

def setup(auth_method='token', proxified=True, secret='ChangeThisPassword', extra=None):
    base = tempfile.mkdtemp(prefix='websec_')
    os.makedirs(base + '/music')
    var.base_dir = base
    cfg = configparser.ConfigParser(interpolation=None)
    wi = {'auth_method': auth_method, 'upload_enabled': 'True', 'max_attempts': '3',
          'access_email_header': '', 'access_team_domain': '', 'access_aud': '',
          'flask_secret': secret, 'is_web_proxified': str(proxified), 'user': 'u', 'password': 'p'}
    wi.update(extra or {})
    cfg.read_dict({'webinterface': wi, 'bot': {'username': 'b', 'delete_allowed': 'False'},
                   'commands': {'command_symbol': '!', 'requests_webinterface_access': 'web'}})
    var.config = cfg; var.music_folder = base + '/music/'; var.tmp_folder = base + '/tmp/'
    os.makedirs(var.tmp_folder, exist_ok=True)
    var.language = 'en'
    var.bot = FakeBot(); var.playlist = FakePlaylist(); var.cache = FakeCache(); var.user_db = None
    class DB:
        store = {}
        def get(self, s, k, fallback=None): return self.store.get((s, k), fallback)
        def set(self, s, k, v): self.store[(s, k)] = v
        def remove_option(self, s, k): self.store.pop((s, k), None)
    var.db = DB()
    import interface
    if not hasattr(interface.web, '_orig_wsgi'): interface.web._orig_wsgi = interface.web.wsgi_app
    interface.web.wsgi_app = interface.web._orig_wsgi
    interface.web.secret_key = secret
    interface.web.jinja_loader = jinja2.DictLoader({'need_token.en.html': 'NEED_TOKEN_PAGE'})
    var.is_proxified = proxified
    if proxified:
        interface.init_proxy()
    interface.banned_ip.clear(); interface.bad_access_count.clear()
    return interface
