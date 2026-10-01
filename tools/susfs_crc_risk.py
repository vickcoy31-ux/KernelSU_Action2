#!/usr/bin/env python3
"""Which exported symbols does the SUSFS patch change the prototype of?

The previous attempt counted 100 "prototypes" that were mostly statements
inside function bodies -- dput(dentry), seq_putc(m,'\n') -- because a regex
cannot tell a prototype from a call. It also assumed anything non-static could
be imported by a module, which is wrong: only EXPORT_SYMBOL_* marks a symbol
as such.

Two conditions have to hold together for a CRC break to be possible:

  1. the patch changes the prototype of a function that already exists, since
     genksyms hashes the prototype and not the body, so a body-only edit is
     harmless and a changed parameter list is not, and
  2. that function is exported, since an unexported symbol cannot be imported
     by a vendor module and its CRC is irrelevant.

So this gets the exported set out of the unpatched tree, gets the set of
prototype changes out of the patch, and intersects them.
"""
import os
import re
import urllib.request

TREE = r'C:\Users\User\bootwork\susfs-port'
SUSFS = r'C:\Users\User\AppData\Local\Temp\susfs\kernel_patches'
KP = os.path.join(SUSFS, '50_add_susfs_in_gki-android12-5.10.patch')

# ---------------------------------------------------------------- patch side
lines = open(KP, encoding='utf-8', errors='replace').read().split('\n')

added, removed = [], []
cur = None
for l in lines:
    m = re.match(r'^\+\+\+ b/(.*)$', l) or re.match(r'^\+\+\+ a/(.*)$', l)
    if m:
        cur = m.group(1)
        continue
    if l[:1] == '+':
        added.append((cur, l[1:]))
    elif l[:1] == '-':
        removed.append((cur, l[1:]))

# A prototype sits at file scope: in a patch it appears as a whole added or
# removed line that ends in ';' and has no leading whitespace-free statement
# keywords. Requiring the line to start in column 0 or with a known storage
# class, and to contain no 'return'/'=' before the name, filters out calls.
KEYWORDS = ('return', 'if', 'for', 'while', 'switch', 'goto', 'else',
            'case', 'do', 'break', 'continue')
STORAGE = ('static', 'extern', 'inline', '__init', 'EXPORT_SYMBOL', '#define')


def prototypes(rows):
    got = {}
    for f, b in rows:
        s = b.rstrip('\n')
        t = s.strip()
        if not t or t.startswith(('#', '//', '*', '/*')):
            continue
        if any(t.startswith(k + ' ') or t.startswith(k + '(') for k in KEYWORDS):
            continue
        if not t.endswith(';') and '{' in t:
            continue
        # argument list must not contain an '=' default or a '->' or a '"'
        m = re.match(r'^(?P<pre>.*?)\b(?P<name>[A-Za-z_]\w*)\s*'
                     r'\((?P<args>[^;{]*)\)\s*;$', t)
        if not m:
            continue
        args = m.group('args')
        if '=' in args or '->' in args or '"' in args:
            continue
        pre = m.group('pre').strip()
        if not pre:
            continue                      # a bare name with no return type
        if not re.fullmatch(r'[\s\w*]+', pre):
            continue
        name = m.group('name')
        # a call has an argument that looks like an expression; a prototype's
        # does not. Cheap discriminator: no ',' separated term containing a dot
        if re.search(r'[a-z]\.[a-z]', args):
            continue
        got.setdefault(f, {})[name] = (pre, ' '.join(args.split()))
    return got


pa, pr = prototypes(added), prototypes(removed)
print(f'  file dengan prototype ditambah : {len(pa)}')
print(f'  file dengan prototype dihapus : {len(pr)}')
print()

proto_changes = {}
for f in set(pa) | set(pr):
    a, r = pa.get(f, {}), pr.get(f, {})
    for n in set(a) & set(r):
        if a[n][1] != r[n][1] or a[n][0] != r[n][0]:
            proto_changes.setdefault(f, []).append((n, r[n], a[n]))
    for n in set(a) - set(r):
        proto_changes.setdefault(f, []).append((n, None, a[n]))
    for n in set(r) - set(a):
        proto_changes.setdefault(f, []).append((n, r[n], None))

print(f'=== prototype yang berubah: {sum(len(v) for v in proto_changes.values())} ===')
for f in sorted(proto_changes):
    for n, before, after in proto_changes[f]:
        b = f'{before[0]} {n}({before[1]})' if before else '(tidak ada)'
        af = f'{after[0]} {n}({after[1]})' if after else '(dihapus)'
        print(f'  {f}')
        print(f'      - {b[:90]}')
        print(f'      + {af[:90]}')

# -------------------------------------------------------------- exported set
print()
print('=== exported di tree asli? ===')
EXPORT = re.compile(
    r'EXPORT_SYMBOL(?:_GPL|_NS|_STRIP|_MAX|_FS|_POLL|_OK|_ATOMIC|_PERCPU|_RESTART)?'
    r'\s*\(\s*([A-Za-z_]\w*)\s*\)')

risky = []
for f in sorted(set(list(pa) + list(pr))):
    p = os.path.join(TREE, f.replace('/', os.sep))
    if not os.path.exists(p):
        continue
    txt = open(p, encoding='utf-8', errors='replace').read()
    exported = set(EXPORT.findall(txt))
    changed = {n for n in set(pa.get(f, {})) | set(pr.get(f, {}))}
    hit = sorted(changed & exported)
    print(f'  {f:<34} diekspor {len(exported):>3}, prototype berubah {len(changed)}'
          + (f'  IRISAN: {", ".join(hit)}' if hit else ''))
    for n in hit:
        risky.append((f, n, exported))

print()
if risky:
    print(f'  === {len(risky)} prototype BERUBAH pada simbol yang diekspor ===')
    for f, n, _ in risky:
        p = os.path.join(TREE, f.replace('/', os.sep))
        old = re.search(r'\b' + re.escape(n) + r'\s*\([^;{]*\)\s*\{',
                        open(p, encoding='utf-8', errors='replace').read())
        print(f'    {f}  {n}')
        if old:
            print(f'        di tree sekarang: {" ".join(old.group(0).split())[:88]}')
    print()
    print('  Ini kandidat CRC yang berubah. Modul vendor yang mengimpornya akan')
    print('  ditolak insmod -- gejala yang sama seperti bootloop kedua.')
else:
    print('  TIDAK ADA prototype diekspor yang berubah.')
    print()
    print('  Semua perubahan signature ada pada simbol non-exported atau pada')
    print('  fungsi baru, sehingga tidak ada modul vendor yang mengimpornya.')
    print('  Patch SUSFS tidak menambah risiko MODVERSIONS.')