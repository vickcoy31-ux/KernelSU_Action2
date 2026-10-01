#!/usr/bin/env python3
"""Refuse to flash a boot image that is not going to work.

This exists because a build script once reported 15 out of 15 checks passed while
producing a byte-for-byte copy of the image that had already bootlooped. Every
assertion in it was correct and the input was not. This puts the assertions in
one place, makes them block, and records the hashes of the images known to have
failed so the same one cannot be flashed twice.

Run it before every dd. It changes nothing on the device; with --device it only
reads the tablet, to compare against what the tablet actually says.

    python tools/verify_boot.py <boot.img> [--device R9RX1015Y2K]
    python tools/verify_boot.py <boot.img> --quick

Exit code is 0 only when nothing failed. A warning does not fail the run; an
error does.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# Measured off this machine's tools rather than assumed.
BOOTWORK = os.environ.get('BOOTWORK', r'C:\Users\User\bootwork')
LZ4 = os.path.join(BOOTWORK, 'lz4.exe')
MAGISKBOOT = os.path.join(BOOTWORK, 'magiskboot.exe')
STOCK_LZ4 = os.path.join(BOOTWORK, 'boot.img.lz4')
STOCK_SHA256 = 'b361173e96ee8e85d647fdc152da1dca82fccffc3acdf614d319eaaf458f3744'

# The tablet's own uname -a, read off the device.
PINNED_RELEASE = '5.10.205-android12-9-28698995'

# Images that have already been written to /dev/block/sdc41 and came back to
# recovery. Hash of the file, not of the kernel inside it.
KNOWN_BAD = {
    '6406aad8a5150acb126d6037a9f5290c8eb01960f29ce198c49d92cd9a2409b8':
        '7-patch build. Bootlooped: __stack_chk_guard was absent, 155 modules refused.',
    'bfd26e5509885496bdbf5f14ede280fbd04efce2e4c8b85127b07e174bb5dc13':
        '25-patch build. Bootlooped: same missing canary.',
}

RED, AMBER, GREEN, DIM, OFF = '\033[31m', '\033[33m', '\033[32m', '\033[90m', '\033[0m'
if os.name == 'nt' and not sys.stdout.isatty():
    RED = AMBER = GREEN = DIM = OFF = ''


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def ok(self, msg: str, detail: str = '') -> None:
        print(f'  {GREEN}ok  {OFF}{msg}' + (f'{DIM}  {detail}{OFF}' if detail else ''))

    def warn(self, msg: str, detail: str = '') -> None:
        self.warnings.append(msg)
        print(f'  {AMBER}AWAS{OFF} {msg}' + (f'{DIM}  {detail}{OFF}' if detail else ''))

    def fail(self, msg: str, detail: str = '') -> None:
        self.errors.append(msg)
        print(f'  {RED}GAGAL{OFF} {msg}' + (f'{DIM}  {detail}{OFF}' if detail else ''))

    def note(self, msg: str) -> None:
        print(f'  {DIM}   {msg}{OFF}')


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def run(argv: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=180,
                           errors='replace')
        return p.returncode, (p.stdout or '') + (p.stderr or '')
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


# --------------------------------------------------------------- unpacking ---

def unpack(img: str, outdir: str) -> dict[str, str]:
    """Unpack a boot image with magiskboot. Returns {name: path}.

    magiskboot writes into the current directory, not next to its argument, so
    the working directory has to be set. Without that it drops the parts wherever
    the process happened to start and this reports the image as having no
    kernel in it.
    """
    os.makedirs(outdir, exist_ok=True)
    before = set(os.listdir(outdir))
    try:
        p = subprocess.run([MAGISKBOOT, 'unpack', os.path.abspath(img)], cwd=outdir,
                           capture_output=True, text=True, timeout=180,
                           errors='replace')
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError('magiskboot gagal dijalankan: ' + str(e))
    if p.returncode != 0:
        raise RuntimeError('magiskboot unpack gagal: '
                           + ((p.stdout or '') + (p.stderr or '')).strip()[:200])
    got = {}
    for f in set(os.listdir(outdir)) - before:
        q = os.path.join(outdir, f)
        if os.path.isfile(q):
            got[f] = q
    if not got:
        raise RuntimeError('magiskboot tidak menghasilkan berkas apa pun')
    return got


def stock_unpack(outdir: str) -> dict[str, str]:
    """Unpack the pinned stock image, so 'only the kernel differs' means something."""
    os.makedirs(outdir, exist_ok=True)
    plain = os.path.join(outdir, 'boot.img')
    if not os.path.exists(plain):
        rc, out = run([LZ4, '-d', '-f', STOCK_LZ4, plain])
        if rc != 0 or not os.path.exists(plain):
            raise RuntimeError('gagal membuka stock boot.img.lz4: ' + out.strip()[:160])
    return unpack(plain, os.path.join(outdir, 'parts'))


# -------------------------------------------------------------- the checks ---

def check_magic(rep: Report, path: str) -> None:
    with open(path, 'rb') as fh:
        head = fh.read(8)
    if head == b'ANDROID!':
        rep.ok('magic ANDROID!', f'{os.path.basename(path)}')
    else:
        rep.fail('bukan gambar boot Android', f'header {head!r}, seharusnya ANDROID!')


def check_kernel_shape(rep: Report, kernel: str) -> None:
    with open(kernel, 'rb') as fh:
        head = fh.read(64)
    magic = head[56:60]
    if magic == b'ARM\x64':
        rep.ok('kernel arm64 yang sah', f'ARM\\x64 di offset 56')
    else:
        rep.fail('kernel bukan arm64 Image yang sah', f'magic {magic!r}')


def check_release(rep: Report, kernel: str) -> None:
    with open(kernel, 'rb') as fh:
        blob = fh.read()
    want = PINNED_RELEASE.encode()
    if want in blob:
        rep.ok('release string cocok dengan tablet', PINNED_RELEASE)
    else:
        rep.fail('release string TIDAK cocok dengan tablet',
                 f'harus ada "{PINNED_RELEASE}". Tanpa itu insmod menolak semua modul '
                 f'/vendor_dlkm karena vermagic beda.')


def check_canary(rep: Report, kernel: str) -> None:
    """The symbol whose absence caused both bootloops."""
    with open(kernel, 'rb') as fh:
        blob = fh.read()
    n = blob.count(b'__stack_chk_guard')
    if n:
        rep.ok('__stack_chk_guard ada di kernel',
               f'{n} kemunculan; 155 modul tablet memakainya')
    else:
        rep.fail('__stack_chk_guard TIDAK ADA',
                 'ini penyebab kedua bootloop: cqhci dan 154 modul lain ditolak '
                 'insmod, init tidak punya storage, kernel mati tanpa log')


def check_known_bad(rep: Report, digest: str) -> None:
    why = KNOWN_BAD.get(digest.lower())
    if why:
        rep.fail('gambar ini PERNAH bootloop dan hash-nya sama persis', why)
    else:
        rep.ok('hash tidak ada di daftar gambar yang gagal')


def check_only_kernel_differs(rep: Report, new: dict, stock: dict) -> None:
    unexpected, missing, extra = [], [], []
    for name, p in stock.items():
        q = new.get(name)
        if q is None:
            missing.append(name)
        elif name == 'kernel':
            rep.ok('kernel berbeda dari stock', '(memang harusnya)')
        elif not files_equal(p, q):
            unexpected.append(name)
    for name in new:
        if name not in stock:
            extra.append(name)
    if not unexpected and not missing and not extra:
        rep.ok('hanya kernel yang berubah', f'{len(stock)} bagian lain identik')
    else:
        for n in unexpected:
            rep.fail('berubah padahal tidak seharusnya', n)
        for n in missing:
            rep.fail('bagian hilang dari gambar', n)
        for n in extra:
            rep.fail('bagian tambahan yang tidak ada di stock', n)


def check_ramdisk_identical(rep: Report, new: dict, stock: dict) -> None:
    a, b = new.get('ramdisk.cpio'), stock.get('ramdisk.cpio')
    if not a or not b:
        rep.warn('ramdisk tidak ada di salah satu gambar, tidak bisa dibandingkan')
        return
    if files_equal(a, b):
        rep.ok('ramdisk byte-identical dengan stock')
    else:
        rep.fail('ramdisk BERUBAH dari stock',
                 'ganti ramdisk hampir selalu berarti bootloop')


def check_markers(rep: Report, kernel: str) -> None:
    with open(kernel, 'rb') as fh:
        blob = fh.read()
    for s in (b'ReSukiSU', b'KernelSU', b'SukiSU'):
        if blob.count(s):
            rep.ok(f'{s.decode()} ada di kernel', f'{blob.count(s)} kemunculan')
        else:
            rep.warn(f'{s.decode()} tidak ada', 'kernel mungkin bukan hasil build ReSukiSU')


def check_size(rep: Report, path: str) -> None:
    n = os.path.getsize(path)
    if n == 67108864:
        rep.ok('ukuran 64 MiB', f'{n:,} B')
    else:
        rep.warn('ukuran bukan 64 MiB', f'{n:,} B')


def read_crc_table(path: str) -> dict[str, int]:
    """crc<TAB>symbol[<TAB>count] -> {symbol: crc}."""
    out = {}
    with open(path, encoding='ascii', errors='replace') as fh:
        for line in fh:
            f = line.rstrip('\n').split('\t')
            if len(f) >= 2:
                try:
                    out[f[1]] = int(f[0], 16)
                except ValueError:
                    continue
    return out


def read_symvers(path: str) -> dict[str, int]:
    out = {}
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            f = line.rstrip('\n').split('\t')
            if len(f) >= 2:
                try:
                    out[f[1]] = int(f[0], 16) & 0xffffffff
                except ValueError:
                    continue
    return out


def read_sibling_exports() -> set[str]:
    """Symbols the device's modules import from each other rather than vmlinux.

    Measured, not guessed: /vendor_dlkm modules name 157 symbols in their
    __versions that a kernel which boots and runs every one of them does not
    export. They are MediaTek vendor APIs (fpsgo_, connsys_, KREE_, CFh4, ISP_*)
    living in sibling modules. Subtracting them without a list would mean
    reporting them as missing, which is what the first run of this check did.
    """
    path = os.path.join(HERE, 'reference', 'vendor-module-exports.txt')
    out = set()
    if not os.path.exists(path):
        return out
    with open(path, encoding='ascii', errors='replace') as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith('#'):
                out.add(line)
    return out


def check_crc(rep: Report, symvers: str, quick: bool) -> None:
    """Compare the build's CRCs against what the tablet's own modules demand.

    Without --symvers this is skipped rather than passed. A skipped check that
    reports as a pass is the exact failure this project has already made twice,
    once in a build script and once in a test that reported green while a
    TypeError inside it was swallowed.
    """
    refs = [('vendor-crcs.tsv', 'modul yang Android benar-benar pakai'),
            ('recovery-crcs.tsv', 'modul RECOVERY')]
    if not symvers:
        for name, why in refs:
            rep.warn(f'CRC vs {name} tidak diperiksa',
                     f'{why}; butuh --symvers Module.symvers dari build yang sama')
        return
    if not os.path.exists(symvers):
        rep.fail('Module.symvers tidak ditemukan', symvers)
        return

    have = read_symvers(symvers)
    rep.note(f'Module.symvers: {len(have)} simbol')
    sibling = read_sibling_exports()
    if sibling:
        rep.note(f'{len(sibling)} simbol dipercayai diekspor modul lain, bukan vmlinux')
    for name, why in refs:
        path = os.path.join(HERE, 'reference', name)
        if not os.path.exists(path):
            rep.warn(f'tabel referensi {name} tidak ada', path)
            continue
        if quick:
            rep.note(f'lewati CRC vs {name} (--quick)')
            continue
        want = read_crc_table(path)
        missing = sorted(set(want) - set(have) - sibling)
        diff = [s for s in (set(want) & set(have)) if want[s] != have[s]]
        if missing:
            rep.fail(f'{len(missing)} simbol hilang vs {name}',
                     ', '.join(missing[:6]) + (' ...' if len(missing) > 6 else ''))
        else:
            rep.ok(f'0 simbol hilang vs {name}', f'{len(want)} simbol, {why}')
        if diff:
            rep.warn(f'{len(diff)} CRC beda vs {name}', ', '.join(sorted(diff)[:4]))


def files_equal(a: str, b: str) -> bool:
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    return sha256(a) == sha256(b)


# -------------------------------------------------------------------- main ---

def main() -> int:
    ap = argparse.ArgumentParser(description='Periksa gambar boot sebelum ditulis ke tablet.')
    ap.add_argument('image')
    ap.add_argument('--device', help='serial adb, untuk membandingkan dengan tablet')
    ap.add_argument('--symvers', help='Module.symvers dari build yang sama')
    ap.add_argument('--quick', action='store_true', help='lewati yang mahal')
    ap.add_argument('--adb', default=os.path.join(BOOTWORK, 'adb.exe'))
    args = ap.parse_args()

    rep = Report()
    img = args.image
    print(f'\nMemeriksa {img}\n')

    if not os.path.exists(img):
        rep.fail('berkas tidak ada', img)
        return 1

    digest = sha256(img)
    size = os.path.getsize(img)
    print(f'  sha256 {digest}')
    print(f'  ukuran {size:,} B\n')

    check_magic(rep, img)
    check_size(rep, img)
    check_known_bad(rep, digest)

    release = PINNED_RELEASE
    if args.device and os.path.exists(args.adb):
        rc, out = run([args.adb, '-s', args.device, 'shell', 'uname -r'])
        if rc == 0 and out.strip():
            release = out.strip().splitlines()[0].strip()
            print(f'  {DIM}uname -r di tablet: {release}{OFF}')
            if release != PINNED_RELEASE:
                rep.fail('release string tablet tidak sama dengan yang dipatok',
                         f'tabkitanya {PINNED_RELEASE}, sekarang {release}')
    print()

    for tool in (MAGISKBOOT, LZ4):
        if not os.path.exists(tool):
            rep.fail('tool tidak ada', tool)

    tmp = tempfile.mkdtemp(prefix='verifyboot-')
    try:
        new = unpack(img, os.path.join(tmp, 'new'))
        stock = stock_unpack(os.path.join(tmp, 'stock'))

        kernel = new.get('kernel')
        if not kernel:
            rep.fail('tidak ada kernel di dalam gambar')
            return 1
        rep.note(f'kernel {os.path.getsize(kernel):,} B, '
                 f'{len(new)} bagian, {len(stock)} bagian di stock')
        print()

        check_only_kernel_differs(rep, new, stock)
        check_ramdisk_identical(rep, new, stock)
        print()
        check_kernel_shape(rep, kernel)
        check_release(rep, kernel)
        check_canary(rep, kernel)
        check_markers(rep, kernel)
        print()
        check_crc(rep, args.symvers, args.quick)
    except RuntimeError as e:
        rep.fail('gagal membongkar gambar', str(e))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if rep.errors:
        print(f'{RED}{len(rep.errors)} masalah yang MENOLAK penulisan:{OFF}')
        for e in rep.errors:
            print(f'  - {e}')
        if rep.warnings:
            print(f'\n{AMBER}{len(rep.warnings)} peringatan:{OFF}')
            for w in rep.warnings:
                print(f'  - {w}')
        return 1
    print(f'{GREEN}Tidak ada yang menghalangi. Aman ditulis ke /dev/block/sdc41.{OFF}')
    if rep.warnings:
        print(f'{AMBER}{len(rep.warnings)} peringatan, tapi tidak memblokir:{OFF}')
        for w in rep.warnings:
            print(f'  - {w}')
    return 0


if __name__ == '__main__':
    sys.exit(main())