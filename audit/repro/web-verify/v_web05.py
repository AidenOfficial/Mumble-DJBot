from v_common import *
import secrets, logging, io, threading, urllib.request
t = secrets.token_urlsafe(5); print('token sample:', t, 'len', len(t), 'bits', 5*8)
i = setup('token')
# same logger wiring as bot/startup.py: werkzeug logger -> StreamHandler (web_logfile empty)
buf = io.StringIO()
wl = logging.getLogger('werkzeug'); wl.setLevel(logging.INFO); wl.addHandler(logging.StreamHandler(buf)); wl.propagate = False
from werkzeug.serving import make_server
srv = make_server('127.0.0.1', 0, i.web, threaded=True)
th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
try: urllib.request.urlopen('http://127.0.0.1:%d/?token=%s' % (srv.server_port, t), timeout=5)
except Exception as e: pass
srv.shutdown()
print('werkzeug access log line:', buf.getvalue().strip())
