# CRC check: the last unexplained thing

## The situation

Two kernels built from this tree have booted the tablet into recovery instead of
into Android.

| run | patches | build | result |
|-----|---------|-------|--------|
| #113 | 25 | clean | bootloop |
| #116 | 7  | clean | bootloop |

Both builds finished with zero undefined symbols, zero duplicate symbols, zero
modpost errors, and a valid 67,108,864 byte boot image whose ramdisk is
byte-identical to stock.

## What has been ruled out, and how

Each of these was measured on the device or by reading its firmware, not inferred.

**The config.** 2,721 options extracted from `/proc/config.gz` read off the
tablet, compared against the resolved `.config` of our build: zero value
differences among common keys, zero keys only in one side.

**The patches.** 29 versus 7 makes no difference to the outcome, so the
twenty-one `.c` edits in between are not the cause.

**The ramdisk.** Byte-identical to stock.

**The release string.** `5.10.205-android12-9-28698995`, read from `uname -a` on
the working tablet and pinned into the build so module vermagic matches.

**AVB.** `ro.boot.vbmeta.device_state=unlocked`,
`ro.boot.verifiedbootstate=orange`. The signature was never involved. `unsignfile`
is Odin's host-side check, not a boot-time one.

**A missing DTB.** Scanned both kernel images for the FDT magic `d00dfeed`: zero
occurrences in the stock image and zero in ours. Neither carries a device tree, so
both depend on the bootloader in exactly the same way. This one killed a plausible
theory that had not been checked.

**Vermagic.** All 202 stock modules in the tablet's own firmware carry
`5.10.205-android12-9-28698995 SMP preempt mod_unload modversions aarch64`.

## What has not been checked

`CONFIG_MODVERSIONS=y` is on in the config the tablet itself reports.

Every module built with it records, in a `__versions` section, one CRC per
imported symbol taken from the type definitions in the tree it was compiled
against. `insmod` compares those against the running kernel and refuses the whole
module if any single one disagrees.

If our CRCs differ at all, then every driver the tablet loads is refused, `init`
cannot mount anything, and the kernel dies where there is no panic to log, no
pstore record, and no `adbd` to catch. That is precisely what was observed: seven
adb polls across three seconds each, across the whole failure, never showed the
device.

This is the only remaining explanation that can be settled by measurement rather
than argument, which is why it is being measured.

## The measurement

Both sides are already available, and neither has to be taken on trust.

**The reference side** is the tablet's own firmware:

    meta-data/fota/RECOVERY/RAMDISK/lib/modules/

202 stock Samsung modules, built against this kernel, with their `__versions`
tables intact. Read with `scripts/crccheck.py collect`:

```
modul dipindai          : 202
kemunculan simbol       : 8821
simbol unik             : 2305
vermagic unik           : 1
tidak ada simbol yang beda antar modul -> tabel layak jadi acuan
```

Zero conflict across all 202 modules means these 2,305 values are internally
consistent and safe to treat as ground truth. They are not an interpretation;
they are what the tablet actually demands.

**Our side** is `Module.symvers` from a build. No previous run uploaded it, so
`gta9-bisect.yml` now uploads it as `gta9-symvers-<timestamp>`.

Then:

    python3 scripts/crccheck.py collect <dir-of-ko> samsung-crcs.tsv
    python3 scripts/crccheck.py compare samsung-crcs.tsv Module.symvers

The check has been exercised in both directions against synthetic inputs, so a
pass means something: with every CRC forced wrong it exits non-zero and prints
the offending symbols; with every CRC correct it exits zero.

## Reading the result

**CRC BEDA > 0** — this is the cause, and it is fixable. Each differing symbol
points at a type definition that differs between Samsung's tree and this fork.
The list groups by symbol prefix, which will name the subsystem.

**CRC BEDA == 0** — the theory is dead, by measurement. What remains is that the
fork builds but does not boot, and the next thing to vary is not patches again.
`CONFIG_LTO_CLANG=y` is worth an isolation run with LTO disabled, since a tree
can be LTO-clean at link time and still be wrong at run time.

## A note on method

An earlier check for KASAN looked for a `ksan` field, found none, and nearly
concluded KASAN was inactive despite `CONFIG_KASAN=y`. That was the wrong field.
Re-reading `/proc/kallsyms` properly shows 72 `asan` symbols and 1,964 `__cfi`
symbols: KASAN, CFI and LTO are all live in the kernel that runs today. Reading
the raw bytes of `cqhci.ko` also showed the `__versions` layout directly, instead
of the guessed 8-byte alignment that the first two parsers used and that produced
nothing but garbage names.

Three of the mistakes in this project were the same mistake: change something,
then reason about the consequence without checking what else moved with it.