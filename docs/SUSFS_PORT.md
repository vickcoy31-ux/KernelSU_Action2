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

### The kernel side applies completely

```
cd tree && patch -p1 --force --fuzz=3 < 50_add_susfs_in_gki-android12-5.10.patch
  hunks succeeded   : 70
  hunks with fuzz 3 :  1
  hunks failed      :  0
  .rej files        :  0
  files changed     : 24 of 24
  "susfs" in tree   : 384 occurrences
```

All 24 files. No manual work on the kernel side.

The one hunk that attached with fuzz 3 is `fs/namespace.c`'s include block, and
it landed correctly:

```c
#include <linux/fslog.h>
#ifdef CONFIG_KSU_SUSFS
#include <linux/susfs_def.h>
#endif
#include "pnode.h"
#include "internal.h"
```

`scripts/lib.sh:apply_patch` tries no-fuzz first, then a reverse check for
already-applied, then `--fuzz=3`, and only fails if all three miss. So this is
the path CI actually takes, and it succeeds.

### The two sides share exactly one contract

Every `extern` the kernel patch introduces was checked against where it is
defined. Ten of eleven resolve inside the kernel side itself -- five are
`DEFINE_STATIC_KEY_FALSE/TRUE` symbols, `susfs_srcu_open_redirect` is a
`DEFINE_SRCU(...)` in `fs/susfs.c:873`, `susfs_fake_qstr_name` is a
`QSTR_INIT` in `fs/susfs.c:48`, and the rest are static functions the kernel
patch defines in the same file it calls them from.

Two do not:

```
security/selinux/avc.c:2512  +extern u32 susfs_ksu_sid;
security/selinux/avc.c:2513  +extern u32 susfs_priv_app_sid;

10_enable_susfs_for_ksu.patch:2454  +u32 susfs_ksu_sid      __read_mostly = 0;
10_enable_susfs_for_ksu.patch:2458  +u32 susfs_priv_app_sid __read_mostly = 0;
```

So two globals are what the kernel side needs from the driver side in order to
link. That is a much smaller thing than the patch's 95 hunks, though it is not
the whole of what the feature needs: the `0x555e1`/`0x555e2`/`0x555e3` command
handlers that `ksud susfs show` sends also live in the driver-side patch, and
without them the kernel builds but still answers "unsupported" to the manager.

### The driver side is the real work

```
against ReSukiSU/ReSukiSU@main        4 of 95 hunks,  4.2%
against SukiSU-Ultra/SukiSU-Ultra@main 59 of 95 hunks, 62.1%
```

That is the finding. The KernelSU-side patch is written against SukiSU-Ultra's
driver. It fits that driver for 62% of its hunks and ReSukiSU's for 4%.

Its changes are not additive. They are a coordinated change to the execve hook
path, and the signatures have to agree across ten files:

```c
// adb_root.h: two pt_regs handlers collapse into one that takes a path
-long ksu_adb_root_handle_execve(struct pt_regs *regs);
-long ksu_adb_root_handle_execveat(struct pt_regs *regs);
+long ksu_adb_root_handle_execveat(const char *filename, void __user ***envp_user_ptr);

// sulog/event.h: one capture function is removed, the other changes shape
-struct ksu_sulog_pending_event *ksu_sulog_capture_root_execve(const char __user *filename_user, ...);
-struct ksu_sulog_pending_event *ksu_sulog_capture_sucompat(const char __user *filename_user, ...);
+struct ksu_sulog_pending_event *ksu_sulog_capture_sucompat(const char *filename,
+                                                           struct user_arg_ptr *argv_user, gfp_t gfp);
```

Because of that, the driver-side port cannot be done hunk by hunk. It is one
change to the hook path, and the signatures must line up across every file that
touches it.

## What that costs

```
stay on ReSukiSU
  kernel side   done. patch(1) applies all 24 files as-is.
  driver side   91 of 95 hunks, as a coordinated rewrite of the execve hook
                path rather than 91 independent edits.

switch to SukiSU-Ultra
  kernel side   done, identical
  driver side   36 of 95 hunks
  loses ReSukiSU as a variant and its manager
```

## Corrections made while working this out

Several conclusions here were wrong on the way, all from the measuring rather
than from the kernel. They are listed because each one changed a decision.

**"THREAD_INFO_IN_TASK is absent."** Grepped in `arch/arm64/Kconfig`, which is
where an arch-specific option would be. It is in `init/Kconfig` and has 20
references in `include/linux/thread_info.h`.

**"All 24 kernel files fail to apply."** `git apply` is atomic: one file that
will not match fails the whole patch and the error output then names every file
in it. 22 files applied cleanly and were reported as failures.

**"This port is several days."** Followed from the line above.

**"Two kernel files need hand-writing."** Still wrong, and wrong because of the
tool. All of this section measured with `git apply`, but CI applies patches with
`patch(1)` via `lib.sh:apply_patch`, which retries at `--fuzz=3`. Under the tool
the build uses, all 24 files apply and neither needs hand-writing.

**"16 symbols have no definition, so the kernel cannot link."** A regex guessed
that function definitions always put the return type and name on one line. Most
of the 16 are defined -- as `DEFINE_STATIC_KEY_FALSE`, `DEFINE_SRCU`,
`QSTR_INIT`, or static functions. Two genuinely come from the driver patch, and
the number is now known rather than guessed.

Three of those five were found by a script whose own output was the giveaway: a
"no definition" verdict that contradicted a plain reading of the file. The
pattern is the same one that has run through this project repeatedly -- an empty
or negative result from a tool gets read as a fact about the device. The
measurements that decided the bootloop all had the same risk, and the ones that
mattered were checked in both directions.

## Not attempted

Nothing here was tried on the tablet. The device runs the kernel from
`gta9_guard.env` with four of six modules working, ReZygisk alive, and built-in
root. SUSFS would add root hiding, which is a separate question from anything
currently broken.