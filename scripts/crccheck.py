#!/usr/bin/env python3
"""Compare the symbol CRCs a stock module demands against the ones our build produced.

This is the test that settles a question two bootloops have left open. Two
kernels have been built from this tree, both clean, one with all 29 in-tree
patches and one with 7, and neither reached init. The config matches the device
in all 2,721 options, the ramdisk is byte-identical, the release string is the
device's own, no DTB is present in either image so both rely on the bootloader
the same way, and AVB is orange.

What is left that nobody has checked is CONFIG_MODVERSIONS=y. Every .ko records
a CRC for each symbol it imports, taken from the type definitions in the tree it
was built from. insmod refuses the module if any CRC disagrees with the running
kernel. If ours disagree, the tablet's drivers are all refused and init cannot
mount anything, which is exactly the symptom: no panic, no log, no adbd.

So:

    collect   read the 202 stock modules in the tablet's own firmware and record
              every CRC they demand, into a reference table
    compare   read Module.symvers from our build and report every disagreement

Both sides are measured, neither is assumed. Run collect on the firmware, run
compare once a build has uploaded Module.symvers.

    python3 scripts/crccheck.py collect <dir-of-ko> <out.tsv>
    python3 scripts/crccheck.py compare <out.tsv> <Module.symvers> [<out-report>]
"""
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modversions import versions, modinfo


def collect(folder, out):
    files = sorted(f for f in os.listdir(folder) if f.endswith('.ko'))
    if not files:
        print(f"  tidak ada .ko di {folder}")
        return 1
    per, counts, conflicts = {}, {}, {}
    vermagics, seen_syms = {}, 0

    for f in files:
        p = os.path.join(folder, f)
        v = versions(p)
        if v is None:
            print(f"  dilewati (bukan ELF64): {f}")
            continue
        vm = modinfo(p).get('vermagic', '(tidak ada)')
        vermagics.setdefault(vm, []).append(f)
        for s, c in v.items():
            seen_syms += 1
            counts[s] = counts.get(s, 0) + 1
            if s in per and per[s] != c:
                conflicts.setdefault(s, set()).update((per[s], c))
            per[s] = c

    with open(out, 'w', encoding='ascii') as fh:
        for s in sorted(per):
            fh.write("0x%08x\t%s\t%d\n" % (per[s], s, counts[s]))

    print(f"  modul dipindai          : {len(files)}")
    print(f"  kemunculan simbol       : {seen_syms}")
    print(f"  simbol unik             : {len(per)}")
    print(f"  ditulis ke              : {out}")
    print(f"  vermagic unik           : {len(vermagics)}")
    for vm, fs in vermagics.items():
        print(f"    [{len(fs):4} modul] {vm.encode('ascii', 'replace').decode()}")
    if conflicts:
        print(f"  simbol yang beda antar modul: {len(conflicts)}")
        for s in sorted(conflicts)[:10]:
            vals = sorted(x & 0xffffffff for x in conflicts[s])
            print(f"    {s:<40} {['0x%08x' % x for x in vals]}")
        print("  CATATAN: tabel ini tidak konsisten, tidak bisa jadi acuan")
        return 1
    print("  tidak ada simbol yang beda antar modul -> tabel layak jadi acuan")
    return 0


def _read_symvers(path):
    """Module.symvers: crc<TAB>name<TAB>exported<TAB>namespace."""
    out = {}
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            f = line.rstrip('\n').split('\t')
            if len(f) < 2:
                continue
            try:
                out[f[1]] = int(f[0], 16) & 0xffffffff
            except ValueError:
                continue
    return out


def compare(tsv, symvers, out=None):
    want = {}
    with open(tsv, encoding='ascii') as fh:
        for line in fh:
            f = line.rstrip('\n').split('\t')
            if len(f) >= 2:
                want[f[1]] = int(f[0], 16)
    have = _read_symvers(symvers)

    both = sorted(set(want) & set(have))
    same = [s for s in both if want[s] == have[s]]
    diff = [s for s in both if want[s] != have[s]]
    missing = sorted(set(want) - set(have))

    lines = []
    def say(s=''):
        print(s)
        lines.append(s)

    say("=== HASIL PERBANDINGAN CRC ===")
    say(f"  simbol yang diminta tablet   : {len(want)}")
    say(f"  simbol di Module.symvers kita: {len(have)}")
    say(f"  ada di kedua sisi             : {len(both)}")
    say(f"  CRC SAMA                      : {len(same)}")
    say(f"  CRC BEDA                      : {len(diff)}")
    say(f"  tidak ada di build kita      : {len(missing)}")
    say()

    if missing:
        say(f"  {len(missing)} simbol tidak ada di Module.symvers kita.")
        say("  Contoh:")
        for s in missing[:20]:
            say(f"    {s}")
        say("  Simbol yang hilang berarti modul yang memakainya akan ditolak,")
        say("  apa pun nilai CRC-nya.")
        say()

    if diff:
        say(f"  {len(diff)} simbol CRC-nya berbeda. Ini penyebabnya:")
        say("  insmod menolak modul tersebut, init tidak bisa mount, kernel mati.")
        say()
        say(f"  {'simbol':<52}{'tablet':<12}{'kita':<12}diminta")
        say(f"  {'-'*52}{'-'*12}{'-'*12}{'-'*8}")
        cnt = {}
        with open(tsv, encoding='ascii') as fh:
            for line in fh:
                f = line.rstrip('\n').split('\t')
                if len(f) >= 3:
                    cnt[f[1]] = int(f[2])
        for s in diff[:60]:
            say(f"  {s:<52}0x{want[s]:08x}  0x{have[s]:08x}  {cnt.get(s,0)}")
        if len(diff) > 60:
            say(f"  ... dan {len(diff)-60} lagi")
        say()
        by_ns = Counter(re.split(r'[_.]', s)[0] for s in diff)
        say("  kelompok simbol yang beda, 15 teratas:")
        for tok, n in by_ns.most_common(15):
            say(f"    {tok:<28}{n}")
    else:
        say("  TIDAK ADA SATU PUN CRC YANG BEDA.")
        say("  Teori CRC tersingkir oleh pengukuran, bukan oleh penalaran.")
        say("  insmod akan menerima modul tablet apa adanya.")

    if out:
        with open(out, 'w', encoding='ascii') as fh:
            fh.write('\n'.join(lines) + '\n')
        print(f"\n  laporan ditulis ke: {out}")
    return 0 if not diff and not missing else 1


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    mode = sys.argv[1]
    if mode == 'collect':
        return collect(sys.argv[2], sys.argv[3])
    if mode == 'compare':
        return compare(sys.argv[2], sys.argv[3],
                       sys.argv[4] if len(sys.argv) > 4 else None)
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())