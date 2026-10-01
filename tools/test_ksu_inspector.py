#!/usr/bin/env python3
"""Exercise the parsing in ksu_inspector against real captured data.

Run with no tablet connected it still tests the parsers against files that were
pulled off the device earlier, so a regression shows up without needing hardware.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from ksu_inspector import (parse_allowlist, parse_features, parse_props,
                          parse_uids, _json_list)

W = r'C:\Users\User\bootwork\gta9\ksudata'
fails = []


def check(name, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'GULAT'}] {name}")
    if not ok:
        print(f"         didapat: {got!r}")
        print(f"         Styles  : {want!r}")
        fails.append(name)


print("=== parse_uids dari 'pm list packages -U' ===")
PM = """package:com.android.shell uid:2000
package:bin.mt.plus uid:10236
package:com.joeykrim.rootcheck uid:10237
"""
u = parse_uids(PM)
check('uid shell', u['com.android.shell'], 2000)
check('uid rootcheck', u['com.joeykrim.rootcheck'], 10237)
check('uid bin.mt.plus', u['bin.mt.plus'], 10236)
check('baris tanpa uid diabaikan', parse_uids('package:com.foo\n'), {})

print("\n=== parse_allowlist terhadap file asli dari tablet ===")
p = os.path.join(W, '.allowlist')
if os.path.exists(p):
    blob = open(p, 'rb').read()
    e, note = parse_allowlist(blob, u)
    print(f"  {len(e)} entri, catatan: {note}")
    for x in e:
        print(f"      {x['package']:<28} uid={x['uid']:<7} ({x['uid_src']}) {x['selinux']}")
    check('jumlah entri', len(e), 3)
    check('paket pertama', e[0]['package'], 'com.joeykrim.rootcheck')
    check('uid dari pm, bukan tebakan', (e[0]['uid'], e[0]['uid_src']), (10237, 'pm'))
    check('uid shell benar', e[2]['uid'], 2000)
    check('selinux pertama', e[0]['selinux'], 'u:r:ksu:s0')
    check('paket ketiga', e[2]['package'], 'com.android.shell')

    # Without pm's table the parser must still find something plausible rather
    # than a wrong number, which is what the hardcoded offset used to do.
    e_no, _ = parse_allowlist(blob)
    check('fallback tetap hidup', all(x['uid'] > 0 for x in e_no), True)
    check('fallback menandai sumber', {x['uid_src'] for x in e_no}, {'file'})
else:
    print("  (file tidak ada, lewati)")
    fails.append('berkas allowlist tidak ada')

print("\n=== parse_allowlist harus menolak file salah ===")
e2, n2 = parse_allowlist(b'PK\x03\x04bukan miliknya')
check('file bukan allowlist ditolak', (len(e2), n2.startswith('bukan')), (0, True))
e3, n3 = parse_allowlist(b'')
check('file kosong ditolak', len(e3), 0)
e4, n4 = parse_allowlist(b'USK\x7f\x04\x00\x00\x00\x09\x00\x00\x00')
check('count lebih besar dari isi-file dilaporkan', 'terpotong' in n4, True)

print("\n=== parse_features terhadap output ksud asli ===")
FEATURES = """Available Features:
================================================================================
[ENABLED (1)] su_compat (ID=0)
    SU Compatibility Mode - allows authorized apps to gain root

[ENABLED (0)] sulog (ID=2)
    SU Log - streams kernel sulog events to userspace
"""
f = parse_features(FEATURES)
check('jumlah fitur', len(f), 2)
check('nama fitur pertama', f[0]['name'], 'su_compat')
check('id fitur pertama', f[0]['id'], 0)
check('nilai fitur kedua', f[1]['value'], 0)
check('deskripsi terambil', f[0]['description'].startswith('SU Compatibility'), True)
check('baris ACS tidak jadi fitur', any(x['name'] == '=====' for x in f), False)

print("\n=== parse_features harus aman pada sampah ===")
check('teks kosong', parse_features(''), [])
check('acs saja', parse_features('====\n----\n'), [])

print("\n=== parse_props & _json_list ===")
# dumpsys mixes "key=value" and "key: value", and the first version of this only
# handled the colon, which is how the manager's version turned into "?".
d = parse_props('    versionCode=35153 minSdk=26\n    versionName=v4.2.0-rc2\n'
                '    installerPackageName=com.android.vending\n')
check('versionCode', d['versionCode'], '35153')
check('versionName', d['versionName'], 'v4.2.0-rc2')
check('installer', d['installerPackageName'], 'com.android.vending')
check('tanpa pemisah diabaikan', parse_props('   \nsdk=1\n'), {'sdk': '1'})
check('json list kosong', _json_list('[]'), [])
check('json list isi', _json_list('["a","b"]'), ['a', 'b'])
check('json rusak jadi baris', _json_list('[oops\nx'), ['[oops', 'x'])

print()
if fails:
    print(f"GAGAL {len(fails)}: " + ", ".join(fails))
    sys.exit(1)
print("SEMUA LULUS")