#!/usr/bin/env python3
"""Tests for the pre-flash verifier.

The verifier exists because a build script once reported 15 of 15 checks passed
while producing a byte-identical copy of an image that had already bootlooped.
So the checks themselves are tested against two real images with known outcomes:
one that bootlooped and one that booted.

Run with no arguments it uses those images if they are still present, and skips
the image-level cases if not. The unit cases always run.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import verify_boot as V          # noqa: E402

fails = []


def check(name, cond, detail=''):
    print(f"  [{'ok' if cond else 'GULAT'}] {name}" + (f'   {detail}' if detail else ''))
    if not cond:
        fails.append(name)


BAD = r'C:\Users\User\bootwork\new-boot-7patch-7patch.img'      # bootlooped
GOOD = r'C:\Users\User\bootwork\new-boot-guard.img'             # booted

print('=== Report: error memblokir, peringatan tidak ===')
r = V.Report()
check('baru tidak punya error', r.errors == [])
r.ok('x')
check('ok() tidak menambah error', r.errors == [])
r.warn('peringatan')
check('warn() menambah peringatan bukan error',
      (r.warnings, r.errors) == (['peringatan'], []))
r.fail('masalah')
check('fail() menambah error', len(r.errors) == 1)

print('\n=== tabel referensi terbaca ===')
for ref, expect in (('vendor-crcs.tsv', 2426), ('recovery-crcs.tsv', 2305)):
    p = os.path.join(HERE, 'reference', ref)
    if os.path.exists(p):
        t = V.read_crc_table(p)
        check(f'{ref} terbaca', len(t) == expect, f'{len(t)} simbol, harap {expect}')
        check(f'{ref} nilainya valid', all(0 <= v <= 0xffffffff for v in t.values()))
    else:
        print(f'  (lewati, {ref} tidak ada)')

print('\n=== daftar ekspor modul lain ===')
sib = V.read_sibling_exports()
check('157 simbol tercatat', len(sib) == 157, f'{len(sib)}')
check('tidak ada komentar ikut terbaca', not any(x.startswith('#') for x in sib))
check('nama MEDIA SDK khas', 'CFh4' in sib and 'connsys_log_init' in sib)

print('\n=== symvers ===')
sv = r'C:\Users\User\bootwork\gta9\symvers2\Module.symvers'
if os.path.exists(sv):
    m = V.read_symvers(sv)
    check('Module.symvers terbaca', len(m) > 17000, f'{len(m)} simbol')
    check('__stack_chk_guard ada di dalamnya',
          m.get('__stack_chk_guard') == 0x8f678b07,
          hex(m.get('__stack_chk_guard', 0)))
else:
    print('  (lewati, Module.symvers tidak ada)')

print('\n=== daftar gambar yang sudah gagal ===')
check('ada 2 hash tercatat', len(V.KNOWN_BAD) == 2)
check('hash lowercase, 64 hex',
      all(len(h) == 64 and all(c in '0123456789abcdef' for c in h) for h in V.KNOWN_BAD))
check('hash bootloop 7-patch ada',
      '6406aad8a5150acb126d6037a9f5290c8eb01960f29ce198c49d92cd9a2409b8' in V.KNOWN_BAD)

print('\n=== konstanta yang harus benar ===')
check('release string dipatok', V.PINNED_RELEASE == '5.10.205-android12-9-28698995',
      V.PINNED_RELEASE)
if os.path.exists(V.STOCK_LZ4):
    check('stock image hash cocok', V.sha256(V.STOCK_LZ4) == V.STOCK_SHA256)
else:
    print('  (lewati, stock boot.img.lz4 tidak ada)')
for t in (V.MAGISKBOOT, V.LZ4):
    check(f'{os.path.basename(t)} ada', os.path.exists(t))


def run_verifier(img, *extra):
    return subprocess.run([sys.executable, os.path.join(HERE, 'verify_boot.py'), img, *extra],
                          capture_output=True, text=True, timeout=900)


print('\n=== gambar yang bootloop harus DITOLAK ===')
if os.path.exists(BAD):
    p = run_verifier(BAD)
    check('keluar bukan 0', p.returncode != 0, f'exit {p.returncode}')
    check('menyebut hash yang gagal', 'PERNAH bootloop' in p.stdout)
    check('menyebut canary hilang', '__stack_chk_guard TIDAK ADA' in p.stdout)
else:
    print('  (lewati, gambar tidak ada)')

print('\n=== gambar yang boot harus LOLOS ===')
if os.path.exists(GOOD) and os.path.exists(sv):
    p = run_verifier(GOOD, '--symvers', sv)
    check('keluar 0', p.returncode == 0, f'exit {p.returncode}')
    check('tidak ada yang menghalangi', 'Tidak ada yang menghalangi' in p.stdout)
    check('canary ditemukan', '__stack_chk_guard ada di kernel' in p.stdout)
    check('0 simbol hilang', '0 simbol hilang' in p.stdout)
elif os.path.exists(GOOD):
    p = run_verifier(GOOD)
    check('tanpa symvers tetap keluar 0', p.returncode == 0, f'exit {p.returncode}')
    check('dan memperingatkan CRC tidak diperiksa', 'tidak diperiksa' in p.stdout)
else:
    print('  (lewati, gambar tidak ada)')

print('\n=== berkas yang tidak ada harus GAGAL, bukan error runtime ===')
p = run_verifier(r'C:\Users\User\bootwork\img-hantu-tidak-ada.img')
check('keluar bukan 0', p.returncode != 0, f'exit {p.returncode}')
check('pesan ramah', 'tidak ada' in p.stdout)

print()
if fails:
    print(f"GAGAL {len(fails)}: " + ', '.join(fails))
    sys.exit(1)
print("SEMUA LULUS")