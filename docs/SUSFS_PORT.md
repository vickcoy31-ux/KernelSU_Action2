# SUSFS on this device: what was measured

The short version: the kernel side of the port fits this tree well, and the
KernelSU driver side does not, because the patch was written for SukiSU-Ultra
and ReSukiSU has diverged from it. Nothing here has been attempted on the device.

## What the device says

```
ksud susfs show version           -> Error: Unsupported SuSFS command: 0x555e1
ksud susfs show enabled_features  -> Error: Unsupported SuSFS command: 0x555e2
ksud susfs show variant           -> Error: Unsupported SuSFS command: 0x555e3
ksud susfs config list_all        -> "enabled": true
```

The kernel rejects the commands; the persisted config claims SUSFS is on. The
manager shows the same thing on the module card: `CMD: '0x555e1', SuSFS
operation not supported, please enable it in kernel`.

`0x555e1` and friends are the SUSFS command codes, so the kernel answering
"unsupported" is the kernel saying the handler was never compiled in.

## Why: the build was configured without it

```
config/gta9_guard.env   ENABLE_SUSFS=false
resolved .config        # CONFIG_KSU_SUSFS is not set
```

`scripts/build.sh:122` only calls `susfs_defconfig` when `ENABLE_SUSFS` is true,
so nothing in the kernel carries the hooks. The `susfs4ksu` module installs its
userspace binary, and that binary talks to a kernel that has no side to answer.

## What is in place already

```
config KSU_SUSFS in ReSukiSU/ReSukiSU main       present
  depends on THREAD_INFO_IN_TASK && 64BIT
THREAD_INFO_IN_TASK in kernelvalidevo 14.0      present (init/Kconfig)
64BIT                                          n/a, arm64
```

So the Kconfig condition is satisfied. What is missing is the kernel side.

## The port, measured rather than assumed

`gitlab.com/simonpunk/susfs4ksu`, branch `gki-android12-5.10`, which is the
branch ReSukiSU's own documentation names for this case.

```
kernel_patches/fs/susfs.c                 59,700 B   new file, copied
kernel_patches/include/linux/susfs.h       7,693 B   new file, copied
kernel_patches/include/linux/susfs_def.h   5,709 B   new file, copied
kernel_patches/50_add_susfs_*.patch       88,687 B   hooks 24 kernel files
kernel_patches/KernelSU/10_enable_*.patch 110,857 B   hooks the KernelSU driver
```

Each file was fetched from the tree and tested on its own with `git apply
--check`, because `git apply` is atomic: one file that will not match makes the
whole patch fail and the error output then names every file in it.

```
kernel side, against vickcoy31-ux/kernelvalidevo 14.0
  22 of 24 files clean
  106 of 124 hunks, 85.5%
  manual: fs/namespace.c (10 hunks), fs/proc/task_mmu.c (8 hunks)
    namespace.c wants #include <linux/fs_context.h> and a pnode.h include in a
      particular order; this tree is older and has neither in that order
    task_mmu.c wants a show_map_vma signature this tree spells differently

KernelSU side, against the driver each fork ships
  against ReSukiSU/ReSukiSU@main        4 of 95 hunks,  4.2%   4 of 28 files
  against SukiSU-Ultra/SukiSU-Ultra@main 59 of 95 hunks, 62.1%  21 of 28 files
```

That second table is the finding. The KernelSU-side patch is written against
SukiSU-Ultra's driver. It fits that driver for 62% of its hunks and ReSukiSU's
for 4%. ReSukiSU is a re-fork that has moved on, so this patch was never going
to land on it.

## What that costs

```
stay on ReSukiSU
  kernel side   2 files by hand, roughly 2 to 4 hours
  driver side   91 of 95 hunks by hand: a rewrite of the SUSFS integration
                into ReSukiSU's driver. Developer work, not configuration.

switch to SukiSU-Ultra
  kernel side   2 files by hand
  driver side   36 of 95 hunks by hand
  loses ReSukiSU as a variant and its manager
```

Neither is a one-line change to a defconfig.

## Corrections made while working this out

Three conclusions in this document were wrong on the way here, all from the
measuring rather than from the kernel.

**"THREAD_INFO_IN_TASK is absent."** It was grepped in `arch/arm64/Kconfig`, which
is where an arch-specific option would be. It is defined in `init/Kconfig` and
has been present all along, with 20 references in `include/linux/thread_info.h`.

**"All 24 kernel files fail to apply."** `git apply` is atomic. One file that
will not match fails the entire patch and the error output names every file in
it, so 22 files that apply cleanly were reported as failures.

**"This port is several days."** That followed from the line above. On the kernel
side the work is two files.

A fourth measurement bug was in this project's own per-file splitter, which then
reported 22 clean files as failing. The per-file numbers above come from a
rewrite using an extraction method that had been verified against a single file
first.

The pattern is the same one that has run through this project repeatedly: an
empty or negative result from a tool gets read as a fact about the device. The
measurements that decided the bootloop all had the same risk, and the ones that
mattered were checked in both directions.

## Not attempted

Nothing here was tried on the tablet. The device runs the kernel from
`gta9_guard.env` with four of six modules working, ReZygisk alive, and built-in
root. SUSFS would add root hiding, which is a separate question from anything
currently broken.