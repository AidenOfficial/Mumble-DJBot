import sys, os, re, time, configparser, sqlite3, tempfile, html
sys.path.insert(0, os.getcwd())
import variables as var
cfg = configparser.ConfigParser(interpolation=None, allow_no_value=True)
cfg.read('configuration.default.ini', encoding='utf-8')
var.config = cfg
import constants; constants.load_lang('en_US')
import commands, database, util

# 1. partial-match map
class FB:
    def __init__(self): self.cmd_handle = {}
    def register_command(self, cmd, handle, no_partial_match=False, access_outside_channel=False, admin=False):
        for c in cmd.split(','):
            c=c.strip()
            if c: self.cmd_handle[c] = {'handle':handle,'partial_match':not no_partial_match,'admin':admin}
b = FB(); commands.register_all_commands(b)
print("== unique 1-2 letter prefixes (non-admin unless marked):")
for n in (1,2):
    seen=set()
    for c in sorted(b.cmd_handle):
        p=c[:n]
        if p in seen or p in b.cmd_handle: continue
        seen.add(p)
        m=[x for x in b.cmd_handle if x.startswith(p) and b.cmd_handle[x]['partial_match']]
        if len(m)==1:
            print(f"  !{p:<3} -> {m[0]:<14} {'ADMIN' if b.cmd_handle[m[0]]['admin'] else ''}")
print("rtrms admin flag:", b.cmd_handle['rtrms']['admin'])

# 2. user_ban mismatch
tmp = tempfile.mktemp(suffix='.db')
conn = sqlite3.connect(tmp); conn.execute("CREATE TABLE botamusique (section TEXT, option TEXT, value TEXT, UNIQUE(section, option))"); conn.commit(); conn.close()
db = database.SettingsDatabase(tmp); var.db = db
db.set("user_ban", "Bob", None)   # what cmd_user_ban stores for "!userban Bob"
user = "Bob"
banned = any(user.lower() == i[0] for i in var.db.items("user_ban"))
print("== !userban Bob then Bob sends command -> banned?", banned)

# 3. ReDoS via !fm / !listfile
t=time.time()
try:
    re.search(r'(a|a)+$', 'a'*26+'b')
except Exception as e: print(e)
print("== ReDoS (a|a)+$ on 27-char title: %.1fs" % (time.time()-t))

# 4. html.unescape in get_url_from_input
u = util.get_url_from_input('https://example.com/x?q=&lt;img&#9;src=&quot;http://attacker.tld/p&quot;&gt;')
print("== get_url_from_input ->", repr(u))
print("   rendered in url_item:", constants.tr_cli('url_item', url=u, title='t', user='u'))

# 5. ban exact-match bypass
db.set("url_ban", util.get_url_from_input("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), None)
for v in ["https://youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ", "https://m.youtube.com/watch?v=dQw4w9WgXcQ"]:
    print("   banned?", db.has_option('url_ban', util.get_url_from_input(v)), v)

# 6. save_music_library truthiness
cfg.set('bot','save_music_library','False')
print("== save_music_library=False -> bool(config.get)=", bool(var.config.get('bot','save_music_library')), " getboolean=", var.config.getboolean('bot','save_music_library'))

# 7. int(plain_volume) clamp bug in cmd_max_volume
print("== int(0.8) > 0.5 ?", int(0.8) > 0.5)
os.remove(tmp)
