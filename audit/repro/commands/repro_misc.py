import os, sys, re, time, html, types, configparser, tempfile
ROOT = sys.argv[1] if len(sys.argv) > 1 else os.getcwd(); sys.path.insert(0, ROOT); os.chdir(ROOT)
import variables as var, constants
cfg = configparser.ConfigParser(interpolation=None, allow_no_value=True); cfg.read('configuration.default.ini'); var.config = cfg
constants.load_lang('en_US')
import util

print("=== G. ReDoS in !filematch / !listfile (re.search(parameter, title)) ===")
pat = r'(.*.*)*!'           # 8 chars, typed in chat as: !fm (.*.*)*!
for n in (10, 11, 12, 13):
    t = time.time(); re.search(pat, 'a' * n); dt = time.time() - t
    print(f"  title len {n}: {dt:.3f}s")

print("=== H. URL ban exact-match bypass ===")
banned = util.get_url_from_input('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
print("  stored ban key:", banned)
for v in ['https://youtu.be/dQw4w9WgXcQ', 'https://m.youtube.com/watch?v=dQw4w9WgXcQ',
          'http://www.youtube.com/watch?v=dQw4w9WgXcQ', 'https://youtube.com/watch?v=dQw4w9WgXcQ',
          'https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1', 'https://WWW.YOUTUBE.COM/watch?v=dQw4w9WgXcQ',
          'https://www.youtube.com/watch?feature=x&v=dQw4w9WgXcQ', 'https://www.youtube.com:443/watch?v=dQw4w9WgXcQ']:
    print(f"  {util.get_url_from_input(v) == banned!s:5}  {v}")

print("=== I. href attribute break-out via html.unescape in get_url_from_input ===")
# What a stock Mumble client sends when the user types:  !radio http://x.test/a"><b>BIG</b>
typed = 'http://x.test/a"><b>BIG</b>'
wire = html.escape(typed, quote=True)
msg = re.sub(r'<.*?>', '', '!radio ' + wire)          # bot/core.py:322
arg = msg.split(' ', 1)[1]
url = util.get_url_from_input(arg)
print("  wire:", wire); print("  url :", url)
print("  rendered:", constants.tr_cli('radio_item', url=url, title='t', name='n', user='u'))

print("=== J. util.update() with documented target_version=stable and bot.version='git' ===")
import requests
class R: text = '8.0.0\n'
requests.get = lambda *a, **k: R()
cfg.set('bot', 'target_version', 'stable')
try:
    util.update('git')
except Exception as e:
    print("  raised:", type(e).__name__, e)
