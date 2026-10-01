#!/usr/bin/env python3
"""Diff two kernel .config files properly, key by key.

An earlier check in this project reported "0 differences" between the tablet's
config and ours and that was wrong. A naive comparison walks the keys present in
both files and skips the ones present in only one, which silently drops exactly
the case that matters here: CONFIG_STACKPROTECTOR_PER_TASK does not appear in
the tablet's config at all, and appears as =y in ours.

Absent is not the same as "not enabled", so this treats a missing key as unknown
and reports it separately instead of folding it into a match. Both files also
record "# CONFIG_X is not set" lines, which must be read as an explicit n rather
than as an absent key.

    python3 scripts/configdiff.py <live.config> <resolved .config>
"""
import sys


def load(path):
    out = {}
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#') and ' is not set' not in line:
                continue
            if line.startswith('# CONFIG_') and line.endswith(' is not set'):
                out[line[2:-len(' is not set')]] = 'n'
            elif line.startswith('CONFIG_') and '=' in line:
                k, _, v = line.partition('=')
                out[k] = v
    return out


def main():
    live, ours = load(sys.argv[1]), load(sys.argv[2])
    both = sorted(set(live) & set(ours))
    diff = [(k, live[k], ours[k]) for k in both if live[k] != ours[k]]
    only_live = sorted(set(live) - set(ours))
    only_ours = sorted(set(ours) - set(live))

    print(f"  kunci di config tablet : {len(live)}")
    print(f"  kunci di config build  : {len(ours)}")
    print(f"  kunci di kedua sisi    : {len(both)}")
    print()
    print(f"  NILAI BEDA             : {len(diff)}")
    for k, a, b in diff:
        print(f"    {k:<46} tablet={a:<12} build={b}")
    print()
    print(f"  HANYA di tablet ({len(only_live)}) -- entah tidak ada di tree kita,")
    print(f"  atau kconfig menghapusnya. Bukan perbedaan, tapi dicatat:")
    for k in only_live[:60]:
        print(f"    {k:<46} tablet={live[k]}")
    if len(only_live) > 60:
        print(f"    ... dan {len(only_live)-60} lagi")
    print()
    print(f"  HANYA di build ({len(only_ours)}) -- opsi yang TIDAK ADA di config")
    print(f"  tablet sama sekali. Inilah yang bisa diam-diam mengubah perilaku:")
    for k in only_ours:
        print(f"    {k:<46} build={ours[k]}")

    return 1 if diff or only_ours else 0


if __name__ == '__main__':
    sys.exit(main())