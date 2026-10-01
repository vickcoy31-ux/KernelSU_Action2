#!/usr/bin/env python3
"""Tests for the write-side checks in ksu_ops.

Every check exists to stop a specific bad write, so each one is exercised with an
input that should be rejected. A validation function that never rejects anything
is worse than none, because it looks like a safety net.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ksu_ops import (check_allowlist_restore, check_feature, check_template,
                     check_umount, OK, WARN, ERR)

fails = []


def check(name, cond):
    print(f"  [{'ok' if cond else 'GULAT'}] {name}")
    if not cond:
        fails.append(name)


def levels(fl):
    return {f.level for f in fl}


print("=== check_feature ===")
sup = ['su_compat', 'kernel_umount', 'sulog', 'adb_root', 'selinux_hide']

f = check_feature('sulog', 1, sup, 1)
check('nilai sama -> peringatan, bukan error', WARN in levels(f) and ERR not in levels(f))
f = check_feature('sulog', 0, sup, 1)
check('perubahan sah -> tidak ada error', ERR not in levels(f))
f = check_feature('tidak_ada', 1, sup, 0)
check('fitur tak dikenal -> ERROR', ERR in levels(f))
f = check_feature('sulog', 7, sup, 1)
check('nilai bukan 0/1 -> ERROR', ERR in levels(f))
f = check_feature('adb_root', 1, sup, 0)
check('nyalakan adb_root -> peringatan', WARN in levels(f))
f = check_feature('su_compat', 0, sup, 1)
check('matikan su_compat -> peringatan', WARN in levels(f))
f = check_feature('sulog', 2, [], 1)
check('daftar fitur kosong + nilai buruk -> ERROR', ERR in levels(f))

print("\n=== check_umount ===")
mods = ['/data/adb/modules/adaway', '/data/adb/modules/adb']
f = check_umount('/data/adb/modules/adaway/system', ['/x'], mods)
check('path modul sah -> tanpa error', ERR not in levels(f))
f = check_umount('data/adb/modules/x', [], mods)
check('path relatif -> ERROR', ERR in levels(f))
check('path relatif langsung return', len(f) == 1)
f = check_umount('', [], mods)
check('path kosong -> ERROR', ERR in levels(f))
f = check_umount('/data/adb/modules/adaway', ['/data/adb/modules/adaway'], mods)
check('aturan sudah ada -> peringatan', WARN in levels(f))
f = check_umount('/data/adb/modules/hilang/system', [], mods)
check('modul tidak ada -> peringatan, bukan error', WARN in levels(f) and ERR not in levels(f))
f = check_umount('/data/adb/mod ules/x', [], mods)
check('path berispasi -> ERROR', ERR in levels(f))

print("\n=== check_template ===")
f = check_template('nethunter.root', 'content { a b }', ['shizuku.root'])
check('template sah -> tanpa error', ERR not in levels(f))
f = check_template('sudah.ada', 'x', ['sudah.ada'])
check('id bentrok -> peringatan', WARN in levels(f))
f = check_template('../jahat', 'x', [])
check('id dengan path traversal -> ERROR', ERR in levels(f))
f = check_template('ok', '   ', [])
check('isi kosong -> ERROR', ERR in levels(f))
f = check_template('ok', 'x' * 20000, [])
check('terlalu panjang -> ERROR', ERR in levels(f))

print("\n=== check_allowlist_restore ===")
INSTALLED = {'com.android.shell': 2000, 'com.example.app': 10123}

good = (b'USK\x7f' + (4).to_bytes(4, 'little') + (1).to_bytes(4, 'little')
        + (b'com.android.shell' + b'\x00' * 200).ljust(252, b'\x00')
        + b'\x00' * (784 - 4 - 252))
check('berkas sah -> tanpa error', ERR not in levels(check_allowlist_restore(good, INSTALLED)))

bad_magic = b'PK\x03\x04' + good[4:]
f = check_allowlist_restore(bad_magic, INSTALLED)
check('magic salah -> ERROR', ERR in levels(f))
f = check_allowlist_restore(b'', INSTALLED)
check('berkas kosong -> ERROR', ERR in levels(f))

bad_ver = (b'USK\x7f' + (9).to_bytes(4, 'little') + (1).to_bytes(4, 'little')
           + (b'com.android.shell' + b'\x00' * 200).ljust(252, b'\x00')
           + b'\x00' * (784 - 4 - 252))
check('versi bukan 4 -> ERROR', ERR in levels(check_allowlist_restore(bad_ver, INSTALLED)))

def entry(pkg):
    return (pkg.encode() + b'\x00' * 252)[:252].ljust(252, b'\x00') + b'\x00' * (784 - 4 - 252)

missing = (b'USK\x7f' + (4).to_bytes(4, 'little') + (1).to_bytes(4, 'little')
           + entry('com.tidak.terpasang'))
f = check_allowlist_restore(missing, INSTALLED)
check('paket tidak terpasang -> peringatan', WARN in levels(f))

dup = (b'USK\x7f' + (4).to_bytes(4, 'little') + (2).to_bytes(4, 'little')
       + entry('com.android.shell') + entry('com.android.shell'))
f = check_allowlist_restore(dup, INSTALLED)
check('paket dobel -> peringatan', WARN in levels(f))

trunc = good + b'\x01\x02\x03'
f = check_allowlist_restore(trunc, INSTALLED)
check('ekor tidak utuh -> peringatan', WARN in levels(f))

noname = (b'USK\x7f' + (4).to_bytes(4, 'little') + (1).to_bytes(4, 'little')
          + b'\x00' * 784)
check('entri kosong -> ERROR', ERR in levels(check_allowlist_restore(noname, INSTALLED)))

short = b'USK\x7f\x04\x00'
check('berkas kepotong -> ERROR', ERR in levels(check_allowlist_restore(short, INSTALLED)))

print()
if fails:
    print(f"GAGAL {len(fails)}: " + ", ".join(fails))
    sys.exit(1)
print("SEMUA LULUS")