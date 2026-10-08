import re, glob, configparser, os, sys
root = os.getcwd()
files = [f for f in glob.glob(root+'/**/*.py', recursive=True) if '/tests/' not in f and '/webui/' not in f and 'venv' not in f and '/scripts/' not in f]
pat = re.compile(r"""config\.get(?:boolean|int|float)?\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]([^)]*)\)""")
used = {}
for f in files:
    src = open(f, encoding='utf-8').read()
    for i, line in enumerate(src.splitlines(), 1):
        for m in pat.finditer(line):
            sec, key, rest = m.group(1), m.group(2), m.group(3)
            used.setdefault((sec, key), []).append((os.path.relpath(f, root), i, 'fallback' in rest))
d = configparser.ConfigParser(interpolation=None, allow_no_value=True); d.read(root+'/configuration.default.ini', encoding='utf-8')
dkeys = {(s, o) for s in d.sections() for o in d.options(s)}
# example: parse uncommented and commented keys
ex_unc = set(); ex_com = set(); sec=None
for line in open(root+'/configuration.example.ini', encoding='utf-8'):
    s = line.strip()
    m = re.match(r'^\[(\w+)\]$', s)
    if m: sec = m.group(1); continue
    m = re.match(r'^#\s?([a-z_0-9]+)\s*=', s)
    if m and sec: ex_com.add((sec, m.group(1))); continue
    m = re.match(r'^([a-z_0-9]+)\s*=', s)
    if m and sec: ex_unc.add((sec, m.group(1)))
print("== code keys missing from default.ini (and whether fallback given):")
for k, v in sorted(used.items()):
    if k not in dkeys and k[0] != 'radio':
        print(k, v)
print("== default.ini keys never read by code (excluding commands section):")
cmdkeys = set()
for k in sorted(dkeys):
    if k[0] == 'commands': continue
    if k not in used: print(k)
print("== example.ini keys (commented or not) absent from default.ini:")
for k in sorted(ex_com | ex_unc):
    if k not in dkeys: print(k)
print("== example uncommented values vs default values:")
for k in sorted(ex_unc):
    if k in dkeys:
        e = None
        # crude read of example uncommented
print("== commands keys in default.ini not registered:", end=' ')
src = open(root+'/commands/__init__.py').read()
reg = set(re.findall(r"commands\('(\w+)'\)", src))
print(sorted(set(o for s,o in dkeys if s=='commands') - reg - {'command_symbol','split_username_at_space'}))
print("== registered but missing from default.ini:", sorted(reg - set(o for s,o in dkeys if s=='commands')))
# PROGRESS.md mentions
prog = open(root+'/PROGRESS.md', encoding='utf-8').read()
cands = set(re.findall(r'`?\b([a-z][a-z0-9_]{4,})\s*=', prog))
allopts = {o for s,o in dkeys}
print("== PROGRESS.md 'key = ' mentions not in default.ini:", sorted(c for c in cands if c not in allopts and '_' in c)[:60])
