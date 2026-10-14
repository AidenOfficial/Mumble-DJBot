"""Compare config keys referenced in code vs configuration.default.ini / example.ini."""
import ast, configparser, os, re, sys
ROOT = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
SKIP_DIRS = {'tests', 'venv', 'webui', 'node_modules', '.git', 'web', 'static', 'scripts', '__pycache__'}
CONFIG_NAMES = {'config', 'default_config'}

refs = {}  # (section,key) -> [(file,line,method,has_fallback)]
dynamic = []
for dp, dns, fns in os.walk(ROOT):
    dns[:] = [d for d in dns if d not in SKIP_DIRS]
    for fn in fns:
        if not fn.endswith('.py'):
            continue
        p = os.path.join(dp, fn)
        rel = os.path.relpath(p, ROOT)
        tree = ast.parse(open(p, encoding='utf-8').read(), p)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            meth = node.func.attr
            if meth not in ('get', 'getint', 'getboolean', 'getfloat', 'has_option', 'items'):
                continue
            obj = node.func.value
            # var.config.X or config.X
            name = obj.attr if isinstance(obj, ast.Attribute) else (obj.id if isinstance(obj, ast.Name) else None)
            if name not in CONFIG_NAMES:
                continue
            args = node.args
            fb = any(k.arg == 'fallback' for k in node.keywords)
            if meth == 'items':
                continue
            if len(args) >= 2 and all(isinstance(a, ast.Constant) for a in args[:2]):
                refs.setdefault((args[0].value, args[1].value), []).append((rel, node.lineno, meth, fb))
            else:
                dynamic.append((rel, node.lineno, meth, ast.unparse(node)))

def load(path, commented=False):
    keys = set()
    sec = None
    for line in open(path, encoding='utf-8'):
        s = line.strip()
        m = re.match(r'^\[(\w+)\]', s)
        if m:
            sec = m.group(1); continue
        if commented:
            m = re.match(r'^#\s?([A-Za-z_][A-Za-z0-9_]*)\s*=', s)
            if m and sec:
                keys.add((sec, m.group(1).lower()))
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=', s)
        if m and sec:
            keys.add((sec, m.group(1).lower()))
    return keys

default = load(os.path.join(ROOT, 'configuration.default.ini'))
example = load(os.path.join(ROOT, 'configuration.example.ini'), commented=True)

print("== referenced in code but MISSING from default.ini ==")
for (s, k), locs in sorted(refs.items()):
    if (s, k.lower()) not in default:
        nofb = [l for l in locs if not l[3] and l[2] != 'has_option']
        tag = "CRASH-RISK(no fallback)" if nofb else "has fallback/has_option"
        print(f"  [{s}] {k}: {tag}")
        for l in locs:
            print(f"      {l[0]}:{l[1]} {l[2]} fallback={l[3]}")

print("\n== in default.ini but never referenced as a literal in code ==")
used = {(s, k.lower()) for (s, k) in refs}
for s, k in sorted(default - used):
    if s in ('commands', 'radio'):
        continue
    print(f"  [{s}] {k}")

print("\n== documented in example.ini but NOT in default.ini (doc drift; user enabling it triggers 'Unexpected config items') ==")
for s, k in sorted(example - default):
    if s == 'radio':
        continue
    print(f"  [{s}] {k}")

print("\n== in default.ini but not documented in example.ini ==")
for s, k in sorted(default - example):
    if s in ('radio',):
        continue
    print(f"  [{s}] {k}")

print("\n== dynamic (non-literal) config lookups ==")
for d in dynamic:
    print("  ", d)
