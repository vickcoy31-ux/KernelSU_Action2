It boots.

```
Linux localhost 5.10.205-android12-9-28698995 #1 SMP PREEMPT
Thu Oct 1 02:05:43 UTC 2026 aarch64 Toybox

Linux version 5.10.205-android12-9-28698995 (kernelsu-action@Github-Action)
(ZyC clang version 16.0.6, LLD 16.0.6)
```

Checked on the tablet, after the fact:

```
boot_completed          1
uptime                  70 s
root                    uid=0(root) context=u:r:ksu:s0   built-in, no LKM
/data                   mounted, 48 GB, 45 GB free
modules loaded          340
wifi                    wlan_drv_gen4m_6789 loaded, 2 interfaces
bluetooth / gps / fm    loaded
dmesg module failures   none at all
```

Of the six modules whose CRCs still disagree, four loaded anyway: `blocktag`,
`mtk_pbm`, `musb_boost`, `mtk_sync`. Two did not: `teeeperf` and
`mediatek-cpufreq-hw`, both optional, and the cpufreq driver has a fallback. No
other module anywhere in the log was refused.

## What was actually wrong

Not the patches, not the config, not the ramdisk, not the toolchain, not AVB, not
a missing DTB. One Kconfig option that does not exist in Samsung's tree and does
in ours, taking its default, which compiles out the definition of a symbol 155 of
the tablet's 202 modules import.

```
arch/arm64/kernel/process.c

#if defined(CONFIG_STACKPROTECTOR) && !defined(CONFIG_STACKPROTECTOR_PER_TASK)
unsigned long __stack_chk_guard __ro_after_init;
EXPORT_SYMBOL(__stack_chk_guard);
#endif
```

`CONFIG_MODVERSIONS=y`, so every module records a CRC per imported symbol and
`insmod` refuses a module whose CRCs disagree. `__stack_chk_guard` is missing
from our vmlinux, so `cqhci` for UFS storage is refused along with 154 others,
`init` has nothing to mount, and the kernel dies with no panic, no pstore record
and no `adbd` to catch. Seven adb polls across the failure showed exactly that:
the device never appeared.

```
                      build #116      build #118
__stack_chk_guard     absent          0x8f678b07
CRC demanded          0x8f678b07      0x8f678b07
symbols missing       1               0
```

The CRC came out identical, not merely present. That is what makes the diagnosis
tight rather than merely plausible: the fix did not nudge the symbol back into
range, it reproduced the exact value 155 modules were built against.

## How it was found

Not by reading the tree. Both bootloops had a correct config by every check that
existed, and reading source code had already produced two confident dead ends:
that `lib/stackprotector.c` held the definition (it exists in neither this tree
nor upstream v5.10) and that GitHub code search showed no
`EXPORT_SYMBOL(__stack_chk_guard)` in the fork (that repository is not indexed,
so the empty result was the search failing, not the symbol being absent).

What worked was measuring both sides. The tablet's own firmware carries 202 stock
modules with their `__versions` tables intact, and a build already produces
`Module.symvers` with the other half. 2,305 values on one side, 17,624 on the
other, and one number did not line up.

Then the diff that mattered. An earlier check had reported "0 differences" in the
config, and that was wrong: it walked only keys present in both files and skipped
keys absent from one, which is precisely the case here. `scripts/configdiff.py`
reports absent keys separately and finds 45 options that exist only in our build,
`CONFIG_STACKPROTECTOR_PER_TASK` among them.

## The two mistakes worth keeping

**A script that verified everything and still produced the wrong kernel.**
`build-stage1.sh` had `SRC` hardcoded to the previous run's download directory, so
it faithfully rebuilt and re-checked the kernel that had already bootlooped and
reported 15/15 passed, producing a byte-identical copy of it. Every assertion was
correct and the input was not. Caught by comparing the output hash against the
known-bad one. It now takes the source directory as an argument and asserts
`__stack_chk_guard` is present in the binary about to be flashed, so the failure
this whole commit is about cannot recur silently.

**Reading the bytes instead of the structure.** The first two `__versions` parsers
assumed an 8-byte stride for `struct modversion_info` and produced nothing but
garbage names; the layout has no padding and the slots are 64 bytes. Dumping the
raw bytes of `cqhci.ko` showed the shape directly.

## Left undone

Five CRC mismatches remain, `sync_file_create` and four cpufreq entry points.
Four of their six modules loaded anyway and the two that did not are optional, so
they are not urgent, but they are the same class of problem: prototype drift
between this newer tree and Samsung's older one.

The other untested difference is the compiler. Samsung used
`Android (7284624) clang version 12.0.5`; this builds with 16.0.6. It is the
largest remaining divergence between the two kernels and no measurement has
pointed at it, so it stays a suspicion rather than a lead.

## Files

```
config/gta9_guard.env          the profile that boots
scripts/modversions.py         reads __versions out of a .ko
scripts/crccheck.py            collect + compare, fails when CRCs disagree
scripts/configdiff.py          config diff that reports absent keys
docs/CRC_CHECK.md              the full account
```

The measurement needs three things on hand: the tablet's firmware
(`meta-data/fota/RECOVERY/RAMDISK/lib/modules`, 202 stock modules), a build's
`Module.symvers`, and `scripts/crccheck.py`. The reference table is worth keeping:
2,305 CRCs with zero disagreement between modules, which makes it safe to treat as
ground truth rather than as an interpretation.