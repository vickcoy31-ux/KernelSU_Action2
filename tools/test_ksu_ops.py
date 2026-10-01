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
                     check_umount, parse_sulog, sulog_describe, sulog_elapsed,
                     sulog_summary, sulog_time, OK, WARN, ERR)

fails = []


def check(name, cond, *why):
    extra = ''
    if why:
        extra = f'   (dapat: {why[0]!r}'
        if len(why) > 1:
            extra += f', harap: {why[1]!r}'
        extra += ')'
    print(f"  [{'ok' if cond else 'GULAT'}] {name}{extra}")
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
print("=== parse_sulog ===")
# Three real lines, verbatim from the tablet's own sulog-2026-10-01.log.
L1 = 'type=daemon_start boot_id="9a0bb860-d1ef-45a4-8808-8b437287204c"'
L2 = ('ts_ns=8181804999 seq=1 type=root_execve version=1 retval=0 pid=794 tgid=794 '
      'ppid=793 uid=0 euid=0 comm="ksud" file="/proc/self/exe" argv="/proc/self/exe sulogd"')
L3 = ('ts_ns=78121086157 seq=45 type=sucompat version=1 retval=0 pid=10535 tgid=10535 '
      'ppid=10533 uid=0 euid=0 comm="sh" file="/system/bin/su" argv="su -c ls -la '
      '/proc/last_kmsg /proc/kmsg 2>/dev/null"')

r = parse_sulog('\n'.join([L1, L2, L3]))
check('tiga record', len(r), 3)
check('tipe pertama', r[0]['type'], 'daemon_start')
check('boot_id', r[0]['boot_id'], '9a0bb860-d1ef-45a4-8808-8b437287204c')
check('comm', r[1]['comm'], 'ksud')
check('argv dengan spasi utuh', r[2]['argv'],
      'su -c ls -la /proc/last_kmsg /proc/kmsg 2>/dev/null')
check('retval di-cast string', r[1]['retval'], '0')

# The bug a whitespace split would cause: "if=" and "w" inside a quoted argv.
L4 = ('ts_ns=1 seq=2 type=sucompat comm="sh" argv="sh -w if=up bs=x of=y"')
r4 = parse_sulog(L4)
check('kunci palsu di dalam argv tidak jadi kunci', 'if' not in r4[0], True)
check('kunci asli masih ada', r4[0]['comm'], 'sh')
check('argv dengan "=" di dalamnya', r4[0]['argv'], 'sh -w if=up bs=x of=y')

# Escapes the writer actually produces.
L5 = 'ts_ns=1 seq=3 type=sucompat comm="sh" argv="su -c echo \\" halo \\""'
r5 = parse_sulog(L5)
check('kutip ter-escape dilepas', r5[0]['argv'], 'su -c echo " halo "')

check('baris kosong diabaikan', parse_sulog('\n\n\n') == [], parse_sulog('\n\n\n'))
check('baris tanpa "=" diabaikan', parse_sulog('halo dunia\ntype=x\n')[0]['type'], 'x')
check('file kosong', parse_sulog('') == [], 'harus daftar kosong')

s = sulog_summary(r)
check('total', s['total'], 3)
check('hitung per tipe', s['counts'], {'daemon_start': 1, 'root_execve': 1, 'sucompat': 1})
check('kumpulan uid', s['uids'], ['0'])

check('waktu = durasi sejak boot', sulog_time(r[1]['ts_ns']) == '0:08',
      sulog_time(r[1]['ts_ns']))
check('waktu > 1 menit jadi m:ss', sulog_elapsed('78121086157') == '1:18',
      sulog_elapsed('78121086157'))
check('waktu > 1 jam jadi h:mm:ss', sulog_elapsed('6922485061951') == '1:55:22',
      sulog_elapsed('6922485061951'))
check('waktu > 1 hari, dan hari tidak disamar jadi jam',
      sulog_elapsed('90061000000000') == '1d 1h 01m 01s',
      sulog_elapsed('90061000000000'))
check('waktu tak ada -> kosong', sulog_time({}) == '', sulog_time({}))
check('wadah bukan angka -> kosong', sulog_time({'ts_ns': 'abc'}) == '', sulog_time({'ts_ns': 'abc'}))
check('waktu negatif ditolak', sulog_elapsed('-5') == '', sulog_elapsed('-5'))

d = sulog_describe(r[1])
check('deskripsi memuat comm dan uid', 'ksud' in d and 'uid 0' in d, True)
check('deskripsi daemon bawa boot_id', 'boot_id' in sulog_describe(r[0]), True)

print()
if fails:
    print(f"GAGAL {len(fails)}: " + ", ".join(fails))
    sys.exit(1)
print("SEMUA LULUS")