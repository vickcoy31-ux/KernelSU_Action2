#!/usr/bin/env python3
"""Read the __versions table out of a kernel module.

A module built with CONFIG_MODVERSIONS carries, in a section named __versions,
one entry per imported symbol: the CRC of the type definition the module was
compiled against. insmod compares those against the running kernel and refuses
the whole module if any of them differ. That is why a kernel can link clean,
match the device's config in every option, and still leave the device unable to
mount anything.

    include/linux/moduleloader.h:
        struct modversion_info { s64 crc; char name[]; };

A live dump of a stock cqhci.ko shows what is actually on disk:

    2d b3 24 7c 00 00 00 00 | "module_layout\\0" | 00 00 ... 41 bytes
    ad 64 b7 dc 00 00 00 00 | "memset\\0"        | 00 00 ... 48 bytes

So the crc is eight bytes little-endian, the name follows immediately, and the
rest of each 64-byte slot is zero. Advancing by sizeof() + strlen() + 1 -- which
is what modpost does -- lands in that padding, and guessing a fixed stride is
worse, because 64 happens to fit this file and says nothing about the next one.
The scan below keys on the exact shape instead: a non-zero low word, a zero high
word, and an identifier starting one byte later.
"""
import struct

SHT_PROGBITS = 1

IDENT = set(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_')


def sections(path):
    """Return (filedata, [(name, type, offset, size), ...]) for a 64-bit ELF."""
    d = open(path, 'rb').read()
    if d[:4] != b'\x7fELF' or d[4] != 2:
        return None
    e_shoff, = struct.unpack_from('<Q', d, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHH', d, 0x3a)

    raw = []
    for i in range(e_shnum):
        sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size = \
            struct.unpack_from('<IIQQQQ', d, e_shoff + i * e_shentsize)
        raw.append((sh_name, sh_type, sh_offset, sh_size))

    sh = raw[e_shstrndx]
    strs = d[sh[2]:sh[2] + sh[3]]

    def name(n):
        return strs[n:strs.index(b'\x00', n)].decode('ascii', 'replace')

    return d, [(name(r[0]), r[1], r[2], r[3]) for r in raw]


def versions(path):
    """{symbol: crc} for every entry in __versions, or None if not an ELF64."""
    r = sections(path)
    if not r:
        return None
    d, secs = r
    for nm, ty, off, size in secs:
        if nm != '__versions' or ty != SHT_PROGBITS:
            continue
        out = {}
        end = off + size
        for p in range(off, end - 9):
            lo, = struct.unpack_from('<I', d, p)
            hi, = struct.unpack_from('<I', d, p + 4)
            if lo == 0 or hi != 0 or d[p + 8] not in IDENT:
                continue
            q = p + 8
            while q < end and d[q] in IDENT:
                q += 1
            if q < end and d[q] == 0 and (q - p - 8) >= 3:
                out[d[p + 8:q].decode('ascii')] = lo
        return out
    return {}


def modinfo(path):
    """The key=value strings out of .modinfo -- vermagic, name, license."""
    r = sections(path)
    if not r:
        return {}
    d, secs = r
    for nm, ty, off, size in secs:
        if nm == '.modinfo' and ty == SHT_PROGBITS:
            out = {}
            for line in d[off:off + size].split(b'\x00'):
                if b'=' in line:
                    k, _, v = line.partition(b'=')
                    out[k.decode('ascii', 'replace')] = v.decode('ascii', 'replace')
            return out
    return {}