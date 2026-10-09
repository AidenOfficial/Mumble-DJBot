import os, sys, time, types, configparser, tempfile, logging
ROOT = sys.argv[1] if len(sys.argv) > 1 else os.getcwd(); sys.path.insert(0, ROOT); os.chdir(ROOT)
import variables as var, constants
cfg = configparser.ConfigParser(interpolation=None, allow_no_value=True); cfg.read('configuration.default.ini'); var.config = cfg
constants.load_lang('en_US')
logging.getLogger('bot').setLevel(logging.CRITICAL)
import media.playlist as mp
from commands.playback import cmd_repeat
class W:
    id = 'x'; user = 'u'
    def format_debug_string(self): return 'w'
    def format_song_string(self): return 'w'
    def is_ready(self): return True
    def validate(self): return True
    def item(self): return self
pl = mp.get_playlist('one-shot')
pl._check_valid = lambda: None          # neutralise validation thread
var.playlist = pl
pl.append(W()); pl.current_index = 0
bot = types.SimpleNamespace(send_channel_msg=lambda m: None, send_msg=lambda m, t: None)
for n in (50, 100):
    pl = mp.get_playlist('one-shot'); pl._check_valid = lambda: None; var.playlist = pl; pl.append(W()); pl.current_index = 0
    t = time.time(); cmd_repeat(bot, 'u', None, 'repeat', str(n)); dt = time.time() - t
    print(f"!repeat {n}: {dt:.2f}s, playlist len {len(pl)}")
