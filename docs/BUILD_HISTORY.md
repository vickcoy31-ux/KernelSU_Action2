# GTA9 kernel build — failure ledger

Working notes for `vickcoy31-ux/KernelSU_Action2` building
`vickcoy31-ux/kernelvalidevo` @ `14.0` (MediaTek MT6789, kernel 5.10).

**Purpose:** keep a durable record of what has already been tried, so a new
attempt does not repeat an approach that was already shown to fail. Update this
file in the same commit that changes `config.env`, `scripts/build.sh`, or the
workflows.

---

> ## ⚠️ READ THIS BEFORE §1
>
> **Everything below the line was written up to run #89. §6 covers runs #89 to
> #111 and reverses two of its conclusions. Do not act on §1–§5 without reading
> §6 first.**
>
> **1. "Do NOT fix this with `CONFIG_MODULES=y`" (§1) is wrong, and following it
> is what cost runs #90–#111.** The objection recorded there was that a modular
> build's `.ko` files "were not built against this config and would not load".
> That is true, and it is *also true of the monolithic build* — the objection
> was never a reason to prefer monolithic. A modular build reached a clean link
> in one attempt (`fd83e99` onward); the monolithic build never did, because
> `init/Kconfig` here has no `default y` for `CONFIG_MODULES`, so the transpose
> in `scripts/kconfig/symbol.c` turns every `=m` into `=y` and 191 per-SoC
> objects enter `vmlinux` at once. Which of them collide is the linker's
> decision, so the list was unbounded: #107 needed nine pins, #108 then produced
> nineteen duplicates from a completely different set.
>
> **2. "STATUS: the build works" was true of the build and false of the kernel.**
> Run #88 emitted a valid 8,308,668-byte `Image.gz` and reported
> `5.10.205-ga5002f750537-dirty` as its release. That string is already wrong.
> The device's modules are built against `5.10.205-android12-9-28698995`, and
> `insmod` compares that string. Run #111 finally got a boot image that was
> correct in every other respect and it still would not have worked, for exactly
> this reason. **A clean build has never been the thing that was missing.**
>
> **3. `gta9_00_defconfig` is the wrong base, and §1's diagnosis of why was also
> the wrong reason.** It is a truncated copy, yes — but the fix is not to fill
> its gaps. It is the device's own config, read out of the stock `boot.img`
> (`IKCFG_ST` at offset `0x1834220`, 217,350 bytes). §6 has the details.

---

## STATUS: superseded — see §6

## STATUS: the build works

**Run #88 (commit `3cab9e6`) and run #89 (commit `2148478`) both succeeded.**

```
compile error        0
duplicate symbol     0
undefined symbol     0
implicit declaration 0
##[error]            0

SYSMAP   System.map
OBJCOPY  arch/arm64/boot/Image
GZIP     arch/arm64/boot/Image.gz
[+] kernel image: Image.gz (8.0M)
[+] kernel release: 5.10.205-ga5002f750537-dirty
```

Artifacts produced: a flashable `AnyKernel3-*.zip` (11.4 MB), the bare
`Image.gz` (8,308,668 bytes, gzip magic `1F 8B 08 00` confirmed) and the
resolved `.config`.

It took 88 runs. The 87 before this one never produced a kernel image. **This
file is kept as-is on purpose:** every entry below is the record of an approach
that was tried and shown to fail, and that record is what made the 88th run
possible. Read §5 onwards for the shape of the failures before changing anything.

Nothing here is untested any more. The one part that has **not** been exercised
is the device itself — nobody has flashed this zip to an SM-X110 and booted it.
If the tablet comes up to a black screen, the first things to look at are
`NEED_DTBO` (set to `false` in run #88; see §4d for why that tree cannot build
a `dtbo.img`) and `is_slot_device` in `anykernel.sh`, which is `auto` and may
need to be `1`.

As of 2026-09-28 the build has **never succeeded**: 61 `Build Kernel` runs,
57 failures, 0 successes.

For a narrative companion — the cast, the physics, and the four symbol shapes
that classify every failure in this file — see
[`KERNEL_CAST.md`](KERNEL_CAST.md). This file remains the source of truth;
the cast is only a map for reading it faster.

---

## 1. Confirmed root cause (established from build run 36435843022)

Four facts, all verified against the kernel tree and the build's own resolved
`.config`:

1. **`gta9_00_defconfig` is a truncated copy.** It is 532 lines. The vendor's
   real `arch/arm64/configs/gta9_defconfig` in the same tree is 7938 lines and
   is self-consistent. The trimmed copy is missing whole subsystems — the entire
   USB block, for example.

2. **`init/Kconfig` in this tree has no `default y` for `CONFIG_MODULES`:**
   ```
   menuconfig MODULES
   	bool "Enable loadable module support"
   	option modules
   	help
   ```
   Upstream Linux 5.10 has `default y` here; this tree dropped it. So
   `CONFIG_MODULES` resolves to `n`, and `scripts/kconfig/symbol.c` then runs
   `/* transpose mod to yes if modules are not enabled */` — every tristate `m`
   in the defconfig becomes `y`. Verified: `CONFIG_SEC_EXT`,
   `CONFIG_MEDIATEK_MT6577_AUXADC`, `CONFIG_MTK_CCU`, `CONFIG_MTK_VMM_DBG`,
   `CONFIG_MTK_CAMERA_MEM_SUPPORT`, `CONFIG_MTK_WIDEVINE_DRM` are all `=m` in
   the defconfig and all resolved `y`.

3. **The build is therefore monolithic.** "Build this as a module" is not
   available. A built-in consumer can never resolve a reference to a provider
   that is `=n` or absent.

4. **Consequence.** Every `=n` in `EXTRA_DEFCONFIG` creates undefined symbols at
   the `vmlinux` link rather than fixing anything at the link stage. All 19
   symbols pinned to `=n` are `=m` (or `=y`) in the vendor's `gta9_defconfig`.
   **Not one of them is `n` upstream.** They were switched off to work around
   *compile* errors (missing headers on 5.10), and that workaround is what now
   breaks the *link*.

### Do NOT "fix" this with `CONFIG_MODULES=y`

It would resolve the link, but it converts hundreds of currently built-in
drivers into `.ko` files. This kernel only replaces `boot.img`; the device loads
modules from the stock `/vendor/lib/modules`, which were not built against this
config and would not load. Features would disappear on the device silently. A
monolithic build is the correct shape here — fill the gaps instead.

### The real gap: three swallowed directory gates

These are `bool` with no `default` and absent from the defconfig, so they
resolve to `n` and their whole subdirectory is never entered by the Makefile,
even though Kconfig happily sets the child tristates to `y`:

| Gate | Swallows | Consequence |
| --- | --- | --- |
| `CONFIG_DMABUF_HEAPS` | all of `drivers/dma-buf/heaps/` | `CONFIG_MTK_TRUSTED_MEMORY_SUBSYSTEM=m` in the defconfig is silently a no-op → `dmabuf_to_secure_handle` undefined |
| `CONFIG_REMOTEPROC` | all of `drivers/remoteproc/` | `CONFIG_MTK_CCU_RPROC=m` in the defconfig is silently a no-op → `mtk_ccu_rproc_get_inforeg` undefined |
| `CONFIG_NVMEM` | all of `drivers/nvmem/` | not yet reached; preemptively enabled |

`CONFIG_BATTERY_ID_ADC` is a `tristate` with no `default` and is absent from the
defconfig, so it likewise resolves to `n` on its own.

**Rule:** to clear a link error, enable the provider to match the vendor
defconfig. Do **not** disable another consumer to hide it.

---

## 2. Symptom → attempted → verdict

`n` = do not retry.


| Run | Symptom | What was tried | Verdict |
| --- | --- | --- | --- |
| #41 | compile fail, MTK_MKP | `CONFIG_MTK_MKP=n` | kept, but cuts providers |
| #41 | compile fail, MTK_PERF_* | `CONFIG_MTK_PERF_COMMON=n`, `CONFIG_MTK_PERF_TRACKER=n` | kept, cuts providers |
| — | MKP incompatible with 5.10 `struct cred` | `mkp_demo.c` cannot build on 5.10 — needs a source patch, not a config disable | still open |
| #42 | googlesource 503 for GCC | `ENABLE_GCC_ARM64=false` | good, keep |
| #43–#46 | vendor `-Werror` | `KCFLAGS=-Wno-error...` blanket | good, keep |
| #47 | `mt-plat/aee.h` missing | `CONFIG_MTK_VOW_SUPPORT=n` | kept, cuts providers |
| #48 | broken slog/mrdump on 5.10 | `CONFIG_MTK_AEE_FEATURE=n` | **now causes `mrdump_regist_hang_bt`** |
| #49 | missing `scp_ipi.h` | `CONFIG_MTK_ECCCI_DRIVER=n` | kept, cuts providers |
| #49 | dup/undefined from MTK_COMBO | `CONFIG_MTK_COMBO=n` | kept, cuts providers |
| #49 | ReSukiSU static-export check | unstatic `sel_handle_status_ops` in `selinuxfs.c` | good, keep |
| #50 | `flask.h` not generated | `CONFIG_NET/INET/SECURITY/AUDIT/SECURITY_NETWORK/SELINUX=y` | good, keep |
| #51 | `write_op` static | unstatic `write_op` in `selinuxfs.c` | good, keep |
| #51 | `sys_enter` tracepoint API on 5.10 | hook mode → `tracepoint` | good, keep |
| #52–#56 | duplicate symbols (camera PDA, CMDQ, MDP, DCS) | drop non-MT6789 objects / rename globals (`g_core_mask_table_dcs`, `larb2_mdp`) in `build.sh` | good, keep |
| #55 | frm/fs dup symbols | `CONFIG_MTK_CAMERA_ISP_RAW_SUPPORT=n` | kept, cuts providers |
| #57 | `read_data` dup, VCP/USB_BOOST | `CONFIG_MTK_TINYSYS_VCP_SUPPORT=n`, `CONFIG_MTK_USB_BOOST=n`, rename `DW9763AF read_data` | kept, cuts providers |
| #58 | dup `tracepoint_cleanup` | make it `static` in `mtk_pbm.c` only | good, keep |
| #58 | EXTRA_DEFCONFIG never applied | restore `EXTRA_DEFCONFIG=` prefix in `config.env` | good, keep |
| #59 | battery-gauge dup | `CONFIG_GAUGE_MT6375=n`, `CONFIG_BATTERY_MT6359P=n` | **now causes `battery_get_profile_id`** |
| #60 | `R_AARCH64_MOVW_UABS_*` relocation errors vs `scpreg`/`core_ids`/`gic_nonsecure_priorities` in `scp/rv/` | `CONFIG_MTK_TINYSYS_SCP_SUPPORT=n` | **did not work** — see §3.1; traded the relocation error for 7 undefined `scp_*` symbols |
| #61 | vendor-hook tracepoints absent on 5.10 | bypass `android_vh_iommu_iovad_*` in `iommu_debug.c`, keep `CONFIG_MTK_IOMMU_MISC_DBG=m` | good, keep |
| #62 | dup `dev`/`reg` globals | make `dev`/`reg` `static` in `ccu_drv.c` + `mtk-mmdvfs-debug.c` | good, keep |
| #63 | 14 × USB undefined symbols | `CONFIG_USB_SUPPORT/USB/USB_OTG/USB_GADGET=y` from the vendor defconfig | **FIXED** — all 14 gone in run #64 |
| #64 | diagnostic artifact missing | `include-hidden-files: true` on upload-artifact | good, keep |
| #65 | 3 swallowed directory gates + 2 no-default tristates | `CONFIG_DMABUF_HEAPS`(+deferred-free,page-pool), `CONFIG_MTK_TRUSTED_MEMORY_SUBSYSTEM`, `CONFIG_REMOTEPROC`, `CONFIG_NVMEM`, `CONFIG_BATTERY_ID_ADC`, `CONFIG_MTK_DEVINFO`, `CONFIG_TRACEPOINTS` all `=y`; `CONFIG_SEC_DEBUG` `n`→`y` | **FIXED** — all 8 undefined symbols gone; run failed on a single new `duplicate symbol` instead |
| #66 | `duplicate symbol: dmabuf_release_check` (3 sites) | build.sh patch 12 makes the header-defined helper `static inline`; plus patch 11 + SCP re-enable, and `RTC_CLASS`/`RTC_LIB`/`ODM_BOARD_ID_STATUS_SUPPORT`/`ANDROID_VENDOR_HOOKS` `=y` | **partly fixed** — `dmabuf_release_check` gone, SCP and the 4 config gates accepted; new 18-way `duplicate symbol: monitor_hang_regist_ldt` |
| #67 | `duplicate symbol: monitor_hang_regist_ldt` (18 sites) | `CONFIG_MTK_HANG_DETECT` back to `=y`; build.sh patch 13 guards the 2 unguarded `mrdump_regist_hang_bt()` calls in `hang_detect.c` | **FIXED** — duplicate gone; exposed `TRACEPOINTS` being promptless (see §3.5) |
| #68 | compile failure in `sec_hard_reset_hook.c` — 5 errors, all from one unterminated block comment | fixed in #69; the 12 undefined symbols from #67 were **never retested** because the build died before reaching the link |
| #69 | patch 14 self-inflicted `-Werror=comment` + SUSFS enabled | comment-closed; self-verifying check added; `ENABLE_SUSFS=true` + `ENABLE_PATH_UMOUNT=true` | **in flight** |
| #72–#74 | 3 × `duplicate symbol: mtk_pm_qos_update_request` | build.sh patch 16 | duplicate cleared in #76 |
| #75 | build died before compiling — bare `grep -c` returns 1 under `set -e` | all counts made non-fatal | good, keep |
| #76 | duplicate **fixed**; 20 undefined reported by a probe whose `-imacros` path was wrong | probe hardened, then switched off | the 20 were **real** — see §4c |
| #77 | probe off, still 20 undefined, 0 duplicate, 0 compile | commit `c89d25c`: media core + `CONFIG_ANDROID` restored, patch 5 revised, patch 17 added, 2 consumers dropped | **run #78** |

### 3.6 A generated block comment can void the whole file (run #68)

**Correction, recorded because the wrong version is easy to re-learn:** run #68
did **not** fail with undefined symbols. It produced **zero** `undefined symbol`
errors. It died in compilation, on 5 errors in one file, all caused by my own
patch 14. I initially reported it as "48 remaining undefined symbols" by
filtering the log loosely and reading only the first 20 matching lines.

Two things made that mistake easy:

- the linker's `--error-limit 20` does **not** apply to compile errors, so
  "20 errors shown" is a number that means different things at different stages;
- the run log is ~4.5 MB, and a loose pattern plus a head-limited read silently
  truncates it.

Consequence worth remembering: the 12 symbols fixed for run #68
(`MAILBOX`, `RPMSG_MTK_CCD`, `FTRACE`, `ENABLE_DEFAULT_TRACERS`, and the
`register_mrdump_reset_delay` guard) were **never actually verified**. Run #70 is
the first build that reaches the link with those changes in place. Do not treat
them as solved until a run gets past `LD vmlinux`.

My own patch broke the build it was meant to fix. The guard comment ended with
a bare `/*` line:

```
	/* sec-mrdump-guard: provider is CONFIG_MTK_AEE_IPANIC, unreachable
	/* while CONFIG_MTK_AEE_FEATURE is pinned n in EXTRA_DEFCONFIG.
	/* This only widened the hard-reset window; the panic path is
	/* untouched. */
	/*                          <-- opens a comment, never closed
#if IS_ENABLED(CONFIG_MTK_AEE_IPANIC)
	register_mrdump_reset_delay(hard_reset_delay);
#endif
```

That trailing `/*` swallowed the four lines after it, and clang rejected the
file:

```
sec_hard_reset_hook.c:232:2: error: '/*' within block comment [-Werror,-Wcomment]
sec_hard_reset_hook.c:235:4: error: unterminated /* comment
sec_hard_reset_hook.c:246:1: error: expected '}'
```

**Two lessons, both about verifying the right thing:**

1. Counting the guard marker proved the sed/awk *ran*. It said nothing about
   whether the file still *parsed*. Assert the post-condition, not the action.
2. `grep -c '/\*'` vs `grep -c '\*/'` is **not** a balance check. It counts
   lines, not occurrences; line 199 of this file has `/* 6 seconds */` on one
   line and counts twice. And **C comments do not nest** — a `/*` inside an
   open comment is plain text, so any checker that counts those as new openers
   reports phantoms. Patch 14 now walks the file with an `incomment` flag and
   fails the build if the file ends inside a comment.

`DISABLE_CC_WERROR=true` does not help here: this tree sets `KCFLAGS` with
`-Wno-error` for a specific list, and `-Wcomment` is not on it.


---

## 3. Outstanding after run #64, and what each was traced to

`ld.lld: error: undefined symbol:` at `Makefile:1335: vmlinux`. The linker stops
at 20 errors (`--error-limit`), so more are hidden behind the first twenty.

| Symbol | Referenced by | Real provider (traced) | Fix |
| --- | --- | --- | --- |
| `secdbg_pdev` | `samsung/sec_chipid.o` | `CONFIG_SEC_DEBUG` (`drivers/samsung/debug/sec_debug_base.c`) — forced `n` | `CONFIG_SEC_DEBUG=y` |
| `register_hook_bootstat` | `samsung/sec_bootstat.o` | `CONFIG_SEC_DEBUG` **and** `CONFIG_SEC_BOOTSTAT` (wrapped in `#if IS_ENABLED`) | both `y` |
| `get_devinfo_with_index` | `iio/adc/mt6577_auxadc.o` | **no provider exists in the tree.** Call site is inside the `#else` of `#if IS_ENABLED(CONFIG_MTK_DEVINFO)` | `CONFIG_MTK_DEVINFO=y` compiles the call out |
| `battery_get_profile_id` | `power/supply/mtk_battery.o` | `CONFIG_BATTERY_ID_ADC` (`drivers/power/supply/battery_id_adc.c`). **Not** `CONFIG_GAUGE_MT6375` — that guess was wrong | `CONFIG_BATTERY_ID_ADC=y` |
| `dmabuf_to_secure_handle` | `camera_mem.o`, `widevine_driver.o` | `CONFIG_MTK_TRUSTED_MEMORY_SUBSYSTEM`, swallowed by `CONFIG_DMABUF_HEAPS` | `CONFIG_DMABUF_HEAPS=y` (+ deferred-free, page-pool) |
| `mtk_ccu_rproc_get_inforeg` | `misc/mediatek/vmm_dbg/mtk-vmm-dbg.o` | `CONFIG_MTK_CCU_RPROC`, swallowed by `CONFIG_REMOTEPROC` | `CONFIG_REMOTEPROC=y` |
| `for_each_kernel_tracepoint`, `tracepoint_probe_register` | ReSukiSU tracepoint hook | `CONFIG_TRACEPOINTS` | `CONFIG_TRACEPOINTS=y` |
| `scp_get_reserve_mem_{virt,size,phys}`, `scp_register_sensor`, `scp_deregister_sensor`, `scp_ipidev`, `scp_A_{un,}register_notify` | `sensorhub/ipi_comm.o` (19+ refs), `sensorhub/ready.o`, `conn_scp/conap_scp_ipi.o` | see §3.1 — neither config value works | **needs a source patch** |
| `mrdump_regist_hang_bt` | `misc/mediatek/monitor_hang/hang_detect.o` | `CONFIG_MTK_AEE_FEATURE`, pinned `n` at run #47 | **unresolved** |
| `for_each_kernel_tracepoint`, `tracepoint_probe_register` | `pbm/mtk_pbm.o`, `mtprof/bootprof.o`, `pidmap/pidmap.o` | `CONFIG_TRACEPOINTS` | fixed in run #65 |
| `register_trace_android_vh_logbuf` | logbuf consumer | vendor-hook infrastructure | **unresolved** |
| `rtc_time64_to_tm` | rtc consumer | `CONFIG_RTC_CLASS` | **unresolved** |
| `g_board_id_status` | `misc/mediatek/usb20/musb_dr.o` | unknown | **unresolved** |

### 3.1 The SCP trap — why `MTK_TINYSYS_SCP_SUPPORT` cannot be config'd either way

`drivers/misc/mediatek/scp/Makefile` is, in full:

```make
obj-y += rv/
```

No config gate at all — `rv/` is *always* built in. Meanwhile
`drivers/misc/mediatek/scp/rv/Makefile`:

```make
obj-$(CONFIG_MTK_TINYSYS_SCP_SUPPORT) += scp.o
...
ccflags-y += -D DEBUG_DO -fno-pic -mcmodel=large
```

So only two outcomes exist, and both fail:

| Value | Outcome | Observed in |
| --- | --- | --- |
| `y` or `m` | the `-fno-pic -mcmodel=large` objects land in `drivers/built-in.a`; lld rejects their `R_AARCH64_MOVW_UABS_*` absolute relocations against the PIE vmlinux | run #60 |
| `n` | no objects are built at all, so `scp_ipidev`, `scp_A_register_notify`, `scp_get_reserve_mem_*` etc. become undefined | run #64 |

Disabling it did not fix run #60, it only traded the relocation error for seven
undefined symbols, because the consumers are **not** gated on the same symbol:

| Symbol | Consumers (all built-in) |
| --- | --- |
| `scp_ipidev` | `misc/mediatek/sensor/2.0/sensorhub/ipi_comm.o` |
| `scp_A_register_notify` | `sensorhub/ready.o`, `conn_scp/conap_scp/conap_scp_ipi.o` |
| `scp_get_reserve_mem_{virt,size,phys}` | sensorhub |

**Fix:** source-patch `rv/Makefile` to drop `-fno-pic -mcmodel=large` so the
objects are linkable into vmlinux, and set
`CONFIG_MTK_TINYSYS_SCP_SUPPORT=y`. Follow the existing numbered-patch pattern
in `scripts/build.sh` rather than trying to solve it with config.


### 3.2 Header-defined helper causing a 3-way duplicate (run #65)

`drivers/dma-buf/heaps/mtk_heap_priv.h:63` **defines a function in the header
body** with external linkage:

```c
/* common function */
void dmabuf_release_check(const struct dma_buf *dmabuf)
{
	...
	WARN(!list_empty(&dmabuf->attachments), ...);
```

Every `.c` that includes it — `mtk_sec_heap.c`, `mtk_heap_debug.c` and
`system_heap.c` — therefore emits its own global copy, and lld reports:

```
ld.lld: error: duplicate symbol: dmabuf_release_check
>>> defined at system_heap.c   dma-buf/heaps/system_heap.o
>>> defined at mtk_sec_heap.c   dma-buf/heaps/mtk_sec_heap.o
>>> defined at mtk_heap_debug.c dma-buf/heaps/mtk_heap_debug.o
```

Note the grep trap: none of the three `.c` files *mentions* the name except as a
**call site** — the definition only exists in the header, so searching the
sources for a definition finds nothing.

`include/linux/dma-buf.h` has **no** declaration for that name, confirming it is
purely an MTK debug helper (WARNs and dumps leftover dma-buf attachments), never
a cross-module API. Fix: `static inline` in the header, so each translation
unit gets a private copy. `inline` additionally avoids `-Wunused-function` in
translation units that include the header but never call it.

### 3.5 `TRACEPOINTS` is a PROMPTLESS bool — config lines for it are silently discarded

**This is the single most important finding, and it invalidates part of my own
run #65/#66 reasoning.**

`init/Kconfig:2157`:

```
config TRACEPOINTS
	bool
```

No prompt, no `default`. So `CONFIG_TRACEPOINTS=y` written into the defconfig is
**accepted by the file but dropped by Kconfig**. Confirmed in the resolved
`.config` from run #67: `CONFIG_TRACEPOINTS` is **absent entirely** — not `=n`,
not `=y`. The only way to set it is a `select` from a real symbol, e.g.
`FTRACE`, `KPROBE_EVENTS`, `PREEMPTIRQ_TRACEPOINTS`, `HAVE_SYSCALL_TRACEPOINTS`.

`kernel/Makefile` gates the provider with `obj-$(CONFIG_TRACEPOINTS) +=
tracepoint.o`, so with the symbol unset, `for_each_kernel_tracepoint` and
`tracepoint_probe_register` (both in `kernel/tracepoint.c`, lines 773 and 554)
are simply never compiled — which is exactly what the linker reports.

This also explains the `android_vh_logbuf` failure, which my run #66 commit
described as only ~70% certain:

```
drivers/android/Kconfig:9      if ANDROID
drivers/android/Kconfig:68     config ANDROID_VENDOR_HOOKS
drivers/android/Kconfig:70     	depends on TRACEPOINTS
drivers/android/Kconfig:123    endif # if ANDROID
```

`ANDROID_VENDOR_HOOKS` is gated behind **two** prerequisites, both currently
false:

1. `CONFIG_ANDROID` — `# CONFIG_ANDROID is not set` (absent from the truncated
   defconfig, `bool` with no default), so the whole `drivers/android/`
   directory is never entered.
2. `CONFIG_TRACEPOINTS` — promptless, so it cannot be set at all from a
   defconfig line.

So `ANDROID_VENDOR_HOOKS=y` in `EXTRA_DEFCONFIG` was a **no-op**. Confirmed
absent in the run #67 `.config` while `CONFIG_REMOTEPROC=y`,
`CONFIG_SEC_DEBUG=y` and `CONFIG_DMABUF_HEAPS=y` from the same batch all landed
correctly.

**Rule: a `=y` line that does not appear in the resolved `.config` means the
symbol is promptless or dependency-blocked — adding it again will never work.**
Always check the resolved `.config` (artifact `kernel-config-<DEVICE>-<time>`),
never infer success from the absence of an error.

### 3.5a A fifth class: mutually exclusive selectors (run #68)

The fix for the tracepoint symbols is `ENABLE_DEFAULT_TRACERS`, which `select
TRACING`, and `TRACING` is what `select TRACEPOINTS`. But:

```kconfig
kernel/trace/Kconfig:378
config ENABLE_DEFAULT_TRACERS
	bool "Trace process context switches and events"
	depends on !GENERIC_TRACER
	select TRACING
```

`GENERIC_TRACER` is raised by `FUNCTION_TRACER`, `FTRACE_SYSCALLS` and
`BLK_DEV_IO_TRACE`. Set any of those in the same run and
`ENABLE_DEFAULT_TRACERS` goes invisible — **with no warning, while `FTRACE=y`
still appears to have worked.** Never set these together with it.

`ENABLE_DEFAULT_TRACERS` is the cheapest route in: no `-mfentry`
instrumentation (that is `FUNCTION_TRACER`, which the vendor ships `n`), no
`RELAY` or `DEBUG_FS` (that is `BLK_DEV_IO_TRACE`). It also drags in
`STACKTRACE`, which is what supplies `stack_trace_save_tsk`.

**Do not use `ADD_KPROBES_CONFIG`.** It writes `CONFIG_MODULES=y`, and since
`KPROBES` `depends on MODULES` it is otherwise unreachable. Enabling modules
inverts the `m`→`y` transpose, so 193 defconfig symbols become real modules and
the monolithic build loses the providers its built-in consumers depend on. It
does not degrade gracefully.

### Four distinct symbol classes seen so far

| Class | Symptom | Example |
| --- | --- | --- |
| Present in vendor defconfig, missing from truncated | resolves to `n` | `CONFIG_USB` → 14 undefined symbols |
| `bool` directory gate with no default | child tristates are `y` but the subdir is never entered | `CONFIG_DMABUF_HEAPS`, `CONFIG_REMOTEPROC` |
| `EXTRA_DEFCONFIG` pins a shared parent to `n` | consumer elsewhere references the removed provider | `MTK_AEE_FEATURE=n` → `mrdump_regist_hang_bt` |
| **Promptless `bool`, no `default`** | **defconfig line is silently discarded; the symbol is absent from `.config`** | **`TRACEPOINTS` → cannot be set from config at all** |

### 3.4 `MTK_HANG_DETECT` is a two-sided trap (run #66)
`drivers/misc/mediatek/include/mt-plat/aee.h` is included by ~18 objects and
switches on one symbol:

```c
#if IS_ENABLED(CONFIG_MTK_HANG_DETECT)
void monitor_hang_regist_ldt(void (*fn)(void));      /* declaration only */
#else
void monitor_hang_regist_ldt(void (*fn)(void)) { }   /* DEFINITION, in a header */
#endif
```

| Value | Result |
| --- | --- |
| `=y` | `hang_detect.c` is built and calls `mrdump_regist_hang_bt()` **unguarded**; its provider is `mrdump_panic.c` under `CONFIG_MTK_AEE_IPANIC` ← `CONFIG_MTK_AEE_FEATURE`, which our `EXTRA_DEFCONFIG` pins to `n` because AEE does not compile on 5.10 → undefined symbol (run #65) |
| `=n` | the `#else` branch turns the header into a definition site, and every includer emits a global copy → **18-way duplicate symbol** (run #66) |

The fix is the driver side, not the config: `hang_detect.c` already guards its
other mrdump calls correctly —

```c
#if IS_ENABLED(CONFIG_MTK_AEE_IPANIC)
		mrdump_regist_hang_bt(NULL);
		mrdump_common_die(AEE_REBOOT_MODE_HANG_DETECT, "	Hang Detect", NULL);
#else
		panic("hang_detect: system blocked");
#endif
```

— but the vendor missed the two calls in `monitor_hang_init` (line 1537) and
`monitor_hang_exit` (line 1550). build.sh patch 13 applies the same guard, so
the build matches the vendor's own intent: hang detection stays on, and the
mrdump hook is simply skipped when AEE ipanic is unavailable.

**Two sed traps hit while writing that patch, both caught by dry-running it on a
real copy of the file first:**

1. `s|...|...|` — using `|` as the `s` delimiter breaks as soon as the pattern
   contains alternation `(show_task_info|NULL)`. Use `%`.
2. `grep -q '^\t...'` — GNU `grep` BRE does **not** interpret `\t` as a tab. The
   one-tab anchor silently matched nothing. Use `$(printf '\t')`.

The anchor on a single leading tab is what protects the already-guarded call at
line 562, which is indented with **two** tabs. Verified with `cat -A`:
line 562 = `^I^I`, lines 1537/1550 = `^I`. Patch 13 is idempotent via its
`mrdump-regist-guard` marker and asserts exactly 2 guards were added.

### 3.3 Remaining symbols, traced

| Symbol | Provider | Gating | Why it was `n` |
| --- | --- | --- | --- |
| `mrdump_regist_hang_bt` | `aee/mrdump/mrdump_panic.c` | `CONFIG_MTK_AEE_IPANIC` ← `CONFIG_MTK_AEE_FEATURE` | our own `EXTRA_DEFCONFIG` forces `MTK_AEE_FEATURE=n`. The consumer `monitor_hang/hang_detect.c` is gated by the **independent** `CONFIG_MTK_HANG_DETECT`, and its calls at lines 1537/1550 are **unguarded** in the vendor source, so no macro can hide them. Since `MTK_AEE_FEATURE` is pinned `n` because it does not compile on 5.10, the consumer is the side that has to go: `CONFIG_MTK_HANG_DETECT=n` loses hang detection only |
| `rtc_time64_to_tm` | `drivers/rtc/lib.c` | `CONFIG_RTC_LIB` — a **promptless `bool`** reachable only via `select` from `CONFIG_RTC_CLASS`, which is `default n` and absent from the truncated defconfig | same hidden-bool class as `DMABUF_HEAPS`/`REMOTEPROC`. Fix `CONFIG_RTC_CLASS=y` **and** `CONFIG_RTC_LIB=y` explicitly, since a later explicit `n` would silently override the `select`. The device does have an MT6397 PMIC RTC, so this is correct for the hardware |
| `g_board_id_status` | `mediatek/board_id/board_id_status.c` | `CONFIG_ODM_BOARD_ID_STATUS_SUPPORT` — `tristate` **with a prompt but no `default`** | absent from the truncated defconfig. It is an ODM board-id var, not Samsung; the only DT node is for project `ot11`, so on gta9 the driver never probes and the variable simply stays `0` |
| `register_trace_android_vh_logbuf` | `drivers/android/vendor_hooks.c` (`EXPORT_TRACEPOINT_SYMBOL_GPL`) | `CONFIG_ANDROID_VENDOR_HOOKS` **and** `CONFIG_TRACEPOINTS` — `DECLARE_HOOK` degrades to `DECLARE_EVENT_NOP` unless both are set | both absent from the truncated defconfig, `=y` in the vendor one. Not fully confirmed which object referenced it in run #64, but setting both makes consumer and provider self-consistent |


### Dead symbols — do not bother setting these

`CONFIG_SEC_CHIPID` and `CONFIG_SEC_BOOTSTAT` appear in **no** `obj-` line
anywhere. `sec_chipid.o` and `sec_bootstat.o` are members of the composite
object `sec_ext.o`, gated solely by `CONFIG_SEC_EXT`, which is forced `y` by
`CONFIG_SEC_MISC=y` (`SEC_MISC` is a `bool` that `depends on SEC_EXT`). Setting
them changes nothing about the build. `CONFIG_SEC_BOOTSTAT` matters for exactly
one thing: the `#if IS_ENABLED(CONFIG_SEC_BOOTSTAT)` around
`register_hook_bootstat` in `sec_debug_pmsg.c`.


---

## 4. How to diagnose the next run

```bash
gh run view <id> -R vickcoy31-ux/KernelSU_Action2 --log-failed \
  | grep -E "undefined symbol|duplicate symbol|fatal error"

# the resolved config is archived per run as an artifact named
#   kernel-config-<DEVICE>-<BUILD_TIME>
gh run download <id> -R vickcoy31-ux/KernelSU_Action2 -n kernel-config-... -D ./cfg
```

Diff the downloaded `.config` against the previous run's to see exactly which
option changed. Prefer reading the resolved `.config` over the defconfig — the
defconfig is the truncated one and is not what the build used.

---

## 4a. ReSukiSU + SUSFS: what is actually possible (run #69)

Run #69 failed in `Apply kernel patches`, not at link:

```
50_add_susfs_in_gki-android12-5.10.patch applied with fuzz; verify the result
failed to apply 10_enable_susfs_for_ksu.patch
susfs4ksu's ... still target the old flat KernelSU layout; modern forks have
since moved to a modular kernel/ tree
```

### The kernel-side half works, with a caveat

The kernel patch applies — but **with fuzz**, meaning some context lines did not
match and `patch` force-fitted it. The build would continue, so this would not
surface as a failure. `SUSFS_BRANCH` is correctly auto-resolved: kernel 5.10 →
`gki-android12-5.10`.

### ReSukiSU ships SUSFS Kconfig natively

`kernel/Kconfig` in ReSukiSU @ `main` already declares:

```
config KSU_SUSFS
	bool "SUSFS Inline Hook"
	  SuSFS Officially support kernel 5.10+
	  But the kernel side susfs compatibility MUST completed by yourself.
```

plus `KSU_SUSFS_SUS_PATH`, `_SUS_MOUNT`, `_SUS_KSTAT`, `_SPOOF_UNAME`,
`_ENABLE_LOG`, `_SPOOF_CMDLINE_OR_BOOTCONFIG`, `_OPEN_REDIRECT`, `_SUS_MAP` —
**more** than the susfs4ksu patch offers.

So the failure is **not** that ReSukiSU cannot do SUSFS.

### What is missing is the implementation, not the config

`ReSukiSU/kernel/selinux/` contains only `rules.c`, `selinux.c`, `selinux.h`,
`sepolicy.c`, `selinux_defs.h`. There is no `susfs_zygote_sid`, no
`susfs_set_batch_sid()`, no `ksu_handle_sys_reboot()`. All of that lives in
the patch that failed to apply.

### Why the patch does not apply

The patch edits identifiers ReSukiSU has since removed or replaced:

| Patch touches | Current ReSukiSU |
| --- | --- |
| `hook/tp_marker.h` | **removed** → replaced by `static_key_true` |
| `ksu_stop_input_hook_runtime()` | **renamed** → `ksu_is_input_hook_enabled` |
| `ksu_late_loaded` | **removed** |
| `ksu_bundled` | **removed** |
| `input_event_kp` kprobe | **gone** → syscall-table hook instead |

`patches.sh` predicted this in its own warning text.

**Worse, the patch rewrites `ksu_handle_sys_reboot`, which collides with the
tracepoint hook mode that run #61 established.** Applying it would risk
regressing that fix.

### Worth knowing regardless

```
config KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS
	default y
```

ReSukiSU hides `ksu`/`susfs` symbols from `/proc/kallsyms` **by default**,
independent of the `ENABLE_SUSFS` switch. That is a stock feature, not
something we have to add.

### Plan

1. Get the image booting first — the 48 remaining undefined symbols. Several of
   them (`tracepoint_probe_register`, `register_trace_sys_enter`) are needed by
   ReSukiSU itself, so this is not a detour.
2. Then integrate SUSFS on top of a working tree, using ReSukiSU's own
   `CONFIG_KSU_SUSFS=y` rather than re-introducing the susfs4ksu KSU-side
   patch. Leave `KSU_HOOK_MODE=tracepoint` alone.

## 4b. Duplicate tracepoints: one fixed, one deliberately deferred (run #72)

Run #72 was the first build to reach the `vmlinux` link with a working
compiler, and it produced **zero undefined symbols**. Everything chased since
#67 is resolved. What was left were six duplicate symbols forming two pairs:

```
__tracepoint_tracing_mark_write        kernel/trace/trace_tracing_mark_write.c  vs  ged_log.c
__tracepoint_mtk_pm_qos_update_request regulator/mtk-dvfsrc-regulator.c         vs  mtk-vmm-regulator.c
```
(plus `__traceiter_` and `__SCK__tp_func_` variants of each)

### Why these appeared only now

`CONFIG_TRACEPOINTS` finally resolved to `y` in run #68, via
`ENABLE_DEFAULT_TRACERS`. Until then `kernel/trace/` was never compiled at all,
so the upstream `tracing_mark_write` event did not exist to collide with. This
is a consequence of a genuine fix, not a regression — but it is the second time
turning a ghost on has exposed more work underneath.

### Key fact about how TRACE_EVENT names things

`TRACE_SYSTEM` does **not** appear in the symbol a `TRACE_EVENT` generates —
that symbol is always `__tracepoint_<event>`. `TRACE_INCLUDE_FILE` names the
generated *file*, not the symbol. So two different `TRACE_SYSTEM`s may still
collide if they declare the same event name.

### Pair 1 — fixed in #73

`drivers/gpu/mediatek/ged/include/ged_tracepoint.h` declares
`TRACE_EVENT(tracing_mark_write, ...)`; upstream declares the same event name.
`ged_log.c` includes the header and **never calls the tracepoint**, so the fix
is a rename with no call-site change and no behaviour change. build.sh patch 15.

### Pair 2 — traced, NOT fixed (deliberate)

`drivers/regulator/mtk-vmm-trace.h` lines 15-44 are a **byte-identical
copy-paste** of `drivers/regulator/mtk-dvfsrc-regulator-trace.h` lines 15-40:
the same `DECLARE_EVENT_CLASS(mtk_pm_qos_request, ...)` plus the same
`DEFINE_EVENT(mtk_pm_qos_request, mtk_pm_qos_update_request, ...)`. Its include
guard is `_TRACE_ISPDVFS_EVENTS_H`, which is not even this file's name — more
evidence it was pasted.

Both files have a correct, distinct `TRACE_INCLUDE_FILE`, which is why the
duplication was not caught earlier: each file legitimately generates its own
tracepoint code, and both use the same event name.

**Why it was deferred rather than fixed.** Deleting lines 15-44 from
`mtk-vmm-trace.h` does remove the duplicate, and leaves `TRACE_INCLUDE_FILE`,
`<trace/define_trace.h>` and `vmm__update_voltage` intact. But
`mtk-vmm-regulator.c:248` still calls `trace_mtk_pm_qos_update_request`, and
after the removal that file has no prototype for it — dvfsrc supplies the
*symbol* at link time, not a *declaration* at compile time. The result would
compile with an implicit-declaration warning which **would not fail the build**,
because #72 turned on `-Wno-error=implicit-function-declaration`. That risks the
VMM tracepoint silently doing nothing, which is harder to notice later than a
build failure.

The correct fix is to delete the duplicate block **and** add an explicit
`DECLARE_TRACE`/`TRACE_EVENT` declaration for the borrowed event in
`mtk-vmm-regulator.c`. That needs a real compile to confirm, so it is a
deliberate next step, not a drive-by patch.

**Do not treat `trace_mtk_pm_qos_update_request` as working until that is done.**

### Pair 2, attempt 1 — step 1 worked, step 2 did not (run #74)

Run #74 kept all three duplicates even though both halves of build.sh patch 16
reported success:

```
[*] REGULATOR_TRACE: removed the copied DECLARE_EVENT_CLASS/DEFINE_EVENT
[*] REGULATOR_TRACE: mtk-vmm-regulator.c now takes the declaration from the dvfsrc header
```

Step 1 is real — the pasted block is gone. Step 2 does nothing, and the reason
looks correct but is not:

```
include/trace/define_trace.h:93   #define TRACE_HEADER_MULTI_READ
include/trace/define_trace.h:95   #include TRACE_INCLUDE(TRACE_INCLUDE_FILE)
include/trace/define_trace.h:99   #define DECLARE_TRACE(name, proto, args)   <- no-op
include/trace/define_trace.h:118  #undef TRACE_HEADER_MULTI_READ
```

`define_trace.h` defines `TRACE_HEADER_MULTI_READ` **itself**, re-reads the
header to collect `DECLARE_TRACE` prototypes, then undefines it. The dvfsrc
header guard is `#if !defined(_TRACE_MTK_QOS_REGULATOR_H) || defined(TRACE_HEADER_MULTI_READ)`.

So the multi-read path is driven **by define_trace.h re-including the header**,
not by the `.c` including it a second time. `TRACE_HEADER_MULTI_READ` is
defined nowhere in the sources — it is a transient define internal to
`define_trace.h`.

A plain extra `#include` from `mtk-vmm-regulator.c` therefore does **not**
produce a declaration: the guard sees `_TRACE_MTK_QOS_REGULATOR_H` already set
and `TRACE_HEADER_MULTI_READ` unset, skips the body, and the trailing
`define_trace.h` re-emits from the still-defined `CREATE_TRACE_POINTS`. The
linker confirms it — the symbol still comes from `mtk-vmm-regulator.o` at
`__tracepoints+0x40`.

### Why step 2 was reverted rather than rewritten

The natural replacement is `DECLARE_TRACE(mtk_pm_qos_update_request, ...)`,
available from `include/linux/tracepoint.h:419`. Two things are unverified and
either could waste a run:

1. `TP_PROTO` appears in **no `.c` file anywhere** in the tree. It is meant for
   use inside `TRACE_EVENT`; using it at file scope may not compile.
2. `mtk-vmm-regulator.c` includes `mtk-vmm-trace.h`, which pulls in
   `define_trace.h`, which **redefines `DECLARE_TRACE` to a no-op** at line 99.
   If that happens first, a `DECLARE_TRACE` placed after the include is
   silently a no-op — and we are back to an implicit declaration that will not
   fail the build, because `KCFLAGS` now carries
   `-Wno-error=implicit-function-declaration`.

Both need a real preprocessor to settle. There is no compiler on the workstation,
and guessing is what produced three wrong conclusions in a row earlier. So
patch 16 keeps only the verified step 1, and step 2 is **not committed**.

**Next step:** either a run that dumps the resolved preprocessor input around
that include, or install a compiler on the runner, before any further edit.

### Pair 2, result of run #76 — the duplicate is FIXED

Run #76 reported:

```
duplicate symbol : 0
compile error    : 0
```

Patch 16 step 1 — deleting the pasted `DECLARE_EVENT_CLASS`/`DEFINE_EVENT`
from `mtk-vmm-trace.h` — was sufficient on its own. The three
`mtk_pm_qos_update_request` duplicates that survived every run since #72 are
gone, and the build again reaches the `vmlinux` link with nothing standing in
the way. Step 2 is therefore unnecessary and stays reverted.

### The 20 undefined symbols in #76 were an artefact of the probe

The same run also reported 20 undefined symbols, which looked alarming. They
were not real:

```
<built-in>:1:19: fatal error: 'out/.config' file not found
```

The probe passed `-imacros out/.config`, and `-imacros` resolves against the
include path rather than the working directory, so every `CONFIG_*` define was
silently dropped. A failed `-E` still writes a partial `.i`, and the probe went
on to read that partial file. Core kernel API guarded by `#ifdef` then looked
missing — which is why the list was full of things that cannot plausibly be
absent: `video_device_alloc`, `v4l2_ctrl_handler_init_class`,
`media_entity_pads_init`, `exec_ccci_kern_func_by_md_id`.

The probe now uses an absolute `-imacros` path, skips outright when `.config`
is missing, and deletes a partial `.i` instead of reading it. It also no longer
uses a bare `grep -c` anywhere — under `set -Eeuo pipefail` with an ERR trap a
zero-match `grep -c` returns 1 and kills the build, which is how #75 died.

**Lesson:** a diagnostic that cannot fail cleanly will invent problems. Three
separate false conclusions today — #68 read as 48 undefined symbols when there
were none, the `grep -c` comment-balance check, and this — all came from
trusting output without verifying the instrument that produced it. Check that
the instrument worked before acting on its numbers.

### Correction: the instrument was broken, but the symbols were real too

The section above is right that the probe in #76 failed and that the 20 symbols
it reported were fabricated. It is **wrong** in its reasoning for dismissing
them — the claim that they "cannot plausibly be absent" was simply false. See
§4c: with the probe switched off, run #77 reported the same 20.

## 4c. Run #77 — lld stops at the first duplicate, so errors hide in layers

Run #77, probe off, `REGULATOR_TRACE_PROBE=false`:

```
compile error (.c:N:N: error:) : 0
duplicate symbol                : 0
undefined symbol                : 20
```

Same 20 as #76, and the probe was not running. So they are real. The count
also tells the story of the last three runs:

| run | duplicate | undefined | reached |
|-----|-----------|-----------|---------|
| #72 | 6 | 0 | `LD vmlinux` |
| #73 | 3 | 0 | `LD vmlinux` |
| #74 | 3 | 0 | `LD vmlinux` |
| #76 | 0 | 20 | `LD vmlinux` |
| #77 | 0 | 20 | `LD vmlinux` |

`undefined = 0` in #72–#74 did **not** mean a clean link. A `duplicate symbol`
is fatal to lld: it stops there, so it never gets as far as reporting the
undefined references sitting underneath. Clearing the duplicates in #76 did not
create these 20 — it uncovered them.

The proof that nothing regressed in between: the resolved `.config` artifacts
from #74 and #77 are **identical** for every symbol involved. Both have

```
# CONFIG_MEDIA_SUPPORT is not set
CONFIG_MEDIA_CONTROLLER=y
```

`CONFIG_MEDIA_CONTROLLER=y` with `MEDIA_SUPPORT` off is itself a symptom of the
truncated defconfig — a value that survived its own dependency.

**General rule, and the reason to read the stage before the count:** lld's
`--error-limit 20` only applies to the link, and the link reports at most one
class of fatal problem. `duplicate = 0` is not a clean bill of health, it is
just proof the layer above cleared. Always ask which stage produced a number.

### The 20, grouped by what was actually wrong

| # | Symbols | Cause | Fix |
|---|---------|-------|-----|
| 11 | `video_device_alloc`, `v4l2_ctrl_*`, `v4l2_async_*`, `media_entity_pads_init`, `v4l2_fwnode_endpoint_parse` | media core absent from the truncated defconfig | `CONFIG_MEDIA_SUPPORT=y`, `CONFIG_VIDEO_DEV=y`, +8 more, all `=y` in the vendor defconfig |
| 3 | `register_trace_android_vh_{show_mem,logbuf,meminfo_proc_show}` | `CONFIG_ANDROID` off, so `ANDROID_VENDOR_HOOKS` could never be satisfied | `CONFIG_ANDROID=y` |
| 3 | `n3d_init`, `n3d_exit`, `set_sensor_streaming_state` | **my own regression** — patch 5, below | patch 5 revised |
| 2 | `register_/unregister_3way_semaphore_notifier` | `obj-m` under `CONFIG_MODULES=n` | new patch 17 |
| 1 | `exec_ccci_kern_func_by_md_id` | provider gated on `CONFIG_MTK_ECCCI_DRIVER`, our run-#48 pin | `CONFIG_MTK_PMIC_PROTECT=n` |
| 1 | `connectivity_register_state_notifier` | provider gated on `CONFIG_MTK_COMBO`, our pin | `CONFIG_MTK_CONN_SCP=n` |

### Patch 5 was a regression I introduced, and I had marked it solved

Patch 5 (run #55/#56) deleted the whole line

```make
include $(IMGSENSOR_DRIVER_PATH)/common/$(COMMON_VERSION)/n3d_fsync/Makefile
```

from `src/isp6s/Makefile`. That did clear the duplicate symbols. It also removed
`n3d.o`, `n3d_clk.o`, `n3d_hw.o` and `vsync_recorder.o` from
`imgsensor_isp6s-objs`, and `src/common/v1_1/imgsensor.c` calls into them from
`imgsensor_init()` and `imgsensor_ioctl()` with no `#ifdef`. Six duplicate
symbols traded for three undefined ones, and the duplicate fix was recorded as
done and stayed done for twenty runs.

The correct cut is narrower. `n3d_fsync/Makefile` is only a wrapper; the
duplicated objects come one level down, in
`n3d_fsync/frame-sync/frame_sync_drv.mk`, which adds `frame_sync.o`,
`frame_sync_algo.o` and `frame_monitor.o` — the same three `src-v4l2/frame-sync`
builds. The include stays; those three lines go. `frame_sync_drv.mk` is a
different file from the `src-v4l2` copy, so the edit cannot reach it, and its
`subdir-ccflags-y` block is kept because it is unrelated to the objects and does
set the include path `n3d.c` is compiled with.

Checked before editing: `vsync_recorder` exists only under
`common/v1_1/n3d_fsync/`, and no `n3d*` file exists anywhere under
`src-v4l2/`, so restoring the include cannot reintroduce a duplicate.

### `obj-m` is a silent dead end under `CONFIG_MODULES=n`

`register_3way_semaphore_notifier()` is defined in exactly one place,
`sound/soc/mediatek/common/mtk-afe-external.c`, and that Makefile hangs it off

```make
obj-m += mtk-afe-external.o
```

With `CONFIG_MODULES=n` that builds nothing at all. There is no second route to
the file either: `CONFIG_SOUND` is off, so `sound/Makefile` never descends into
`soc/`; `sound/soc/Makefile` enters `mediatek/` only under `CONFIG_SND_SOC`; and
`sound/soc/mediatek/Makefile` enters `common/` only under
`CONFIG_SND_SOC_MEDIATEK`. Three gates deep, all closed, none of them obvious
from the error message.

Turning on the ALSA/SOC stack to reach one 58-line file that includes nothing
but its own header and `<linux/module.h>` is far larger a change than the
problem, so patch 17 instead makes the three directories reachable
(`obj-y += soc/` → `obj-y += mediatek/` → `obj-y += common/`) and flips that one
line to `obj-y`. Every other entry in those three Makefiles stays behind its own
`CONFIG_*`, so nothing else is pulled in. Each edit is guarded by a `grep -q`,
so a re-run is a no-op.

**Class:** any `obj-m` line in a tree without modules is a rule that has never
been exercised. It fails as an *undefined symbol in a completely different
driver*, with nothing in the message pointing at the Makefile at fault. Worth
grepping the tree for when an undefined symbol has no obvious provider.

### Two pins where the consumer is the right thing to drop

For the last two symbols the provider is gated behind a `=n` that **we** set
ourselves, and the consumer calls it unguarded:

- `exec_ccci_kern_func_by_md_id` — declared in `include/mt-plat/mtk_ccci_common.h`,
  defined in the `eccci` tree, gated on `CONFIG_MTK_ECCCI_DRIVER`, pinned `=n`
  in run #48 because that tree could not find `scp_ipi.h`. Consumer is
  `pmic_protect/mt63xx-oc-debug.c`, gated on `CONFIG_MTK_PMIC_PROTECT`, which
  has no `#ifdef` around the call.
- `connectivity_register_state_notifier` — defined in
  `connectivity/common/connectivity_build_in_adapter.o`, built by
  `obj-$(CONFIG_MTK_COMBO)`, another of our pins. Consumer is
  `conn_scp/conap_scp/conap_scp_core.c`, gated on `CONFIG_MTK_CONN_SCP`, calling
  it unguarded at `conap_scp_init()`.

The standing rule in this repo is *fix the provider, never disable the consumer*,
because disabling a consumer orphans whatever was consuming **it**. It does not
apply here, for two reasons. First, the pins were not arbitrary — reopening
`CONFIG_MTK_ECCCI_DRIVER` would undo the run-#48 work outright. Second, with
the provider gone the consumer cannot function anyway: there is no notifier
list for it to register on, and no modem IPC transport to call. Dropping the
consumer loses a driver that was already non-functional, and it is small and
self-contained, so the blast radius is one Makefile's worth of objects rather
than an entire subsystem.

It is now `false`. Run #87 built the kernel for the first time and then failed
in `check_output()` rather than in the build:

```
[+] kernel image: Image.gz (8.0M)
[x] NEED_DTBO=true but .../out/arch/arm64/boot/dtbo.img was not produced
```

No `dtbo.img` can be produced by this source, and it is not a misconfiguration.
`arch/arm64/boot/dts/mediatek/Makefile` builds overlays from a config-supplied
list, and in the run-#87 `.config` both halves are inert:

```
CONFIG_BUILD_ARM64_DTB_OVERLAY_IMAGE=y
CONFIG_BUILD_ARM64_DTB_OVERLAY_IMAGE_NAMES=""
```

An empty names list makes `dtbo-y` expand to nothing. The two `dtb-y` lines
above it are commented out in the source as well:

```make
#dtb-y += mt6789.dtb
#dtb-y += mt8781_gta9_eur_open_00.dtb
```

The vendor defconfig carries no `BUILD_ARM64_DTB_*` line at all — those are set
by `build.py` in the vendor's platform build, per project, and are not in this
repository. There is no overlay source in the tree either, and no binary
`.dtbo` is committed anywhere in the repo.

**Class, and it has now happened twice:** a flag inherited from an xiaoleGun
config that assumes something the vendor's build system supplies from outside
the repository. `USE_CUSTOM_ANYKERNEL3=false` behaves the same way in spirit —
it selects the upstream template rather than this device's. The difference is
that this one hard-fails, so it was caught.

**The cost, stated plainly:** if the device needs a DTBO flashed alongside the
kernel, the tablet boots without it. For SM-X110 the kernel is installed with
the stock DTB, so `false` is the expected value. `CONFIG_MTK_DTBO_FEATURE=y` is
*set* in the config and has not been traced to any overlay source — that is the
next thing to check if the display does not come up.

## 4e. Run #87–#88: the build finishes, and the zip has a placeholder

Run #88 is the first green run. Two small things were found in the output rather
than in a failure.

`NEED_DTBO` was set `false` — §4d.

`anykernel.sh` shipped with `kernel.string=ExampleKernel by osm0sis`, the
AnyKernel3 template placeholder, which is the one line of the zip a user reads
on the recovery screen before flashing. `package.sh` now rewrites it, overridable
with `AK3_KERNEL_STRING`.

The other template leftovers were checked before being left alone.
`device.name1` through `device.name4` are `maguro`, `toro`, `toroplus`, `tuna` —
the Galaxy Nexus codenames from upstream's example. With `do.devicecheck=0`,
which `package.sh` already set, those values are read only for banner text and
are never compared against `ro.product.device`. They are inert. Writing a
guessed SM-X110 codename there would be decoration that says nothing true, so
they stay. The two settings in that file that do matter were already handled and
are present in the shipped zip: `BLOCK=auto` and `is_slot_device=auto`.

# HANDOVER — picking this up cold

Written so this can be resumed without re-deriving anything.

## Current state

As of run #77, the last completed run before the fix campaign, the result was:

```
compile error (.c:N:N: error:) : 0
duplicate symbol                : 0
undefined symbol                : 20
```

The build reached `LD vmlinux` with the link free of duplicates and 20 undefined
symbols outstanding. Everything from there to run #88 is in §4c onward. Run #88
succeeded and run #89 confirmed it.

## If a build fails, the order that worked

1. **Read the stage before reading the error.** Compile errors and link errors
   are different problems, and lld's `--error-limit 20` applies only to the
   link. Reading a number without knowing its stage was wrong three times.
2. **Know that `duplicate symbol: 0` does not mean the link is clean.** lld
   stops dead on a duplicate, so an earlier run reporting `undefined = 0` may
   simply never have got as far as checking. Every fix uncovers a layer.
3. **Check the resolved `.config` artifact, not the defconfig.** The defconfig
   is truncated; the artifact is what the build used. Every real discovery here
   came from the artifact. Comparing two runs' artifacts side by side is what
   proved the 20 symbols in #77 were not a regression.
4. **Verify the instrument before trusting it.** Three false conclusions came
   from output being wrong, not code being wrong.
5. One idea per run.

## Traps that will bite again

- **`EXTRA_DEFCONFIG` `=n` entries cut providers.** A built-in consumer cannot
  resolve a `=n` provider in this monolithic build. All 19 `=n` entries are
  `=m`/`=y` in the vendor defconfig; none is `n` upstream. Do not add more.
- **Never set `CONFIG_MODULES=y`.** It inverts the `m`→`y` transpose.
- **`ADD_KPROBES_CONFIG` must stay `false`** — it sets `CONFIG_MODULES=y`.
- **`ENABLE_DEFAULT_TRACERS` is mutually exclusive** with `FUNCTION_TRACER`,
  `FTRACE_SYSCALLS`, `BLK_DEV_IO_TRACE`.
- **A `=y` absent from the resolved `.config` means promptless or
  dependency-blocked** — adding it again cannot work (§1, §3.5).
- **17 of the original 19 `=n` entries are untouched.** Leave them unless a log
  says otherwise.
- **Any `obj-m` line is dead** in this tree. It builds nothing under
  `CONFIG_MODULES=n` and surfaces as an undefined symbol in an unrelated
  driver, with no hint that a Makefile is at fault.
- **A fix that trades one error class for another is not a fix.** Patch 5
  removed six duplicate symbols and introduced three undefined ones; it was
  filed under "fixed" and stayed there for twenty runs. When a fix removes a
  build unit, check what else that unit provided before marking it done.

## Fixed, do not redo

| Area | Fix |
| --- | --- |
| 14 USB undefined symbols | `CONFIG_USB_SUPPORT/USB/USB_OTG/USB_GADGET=y` |
| 3 swallowed directory gates | `CONFIG_DMABUF_HEAPS`, `CONFIG_REMOTEPROC`, `CONFIG_NVMEM` |
| `-Werror` suppression dead since #43 | `KCFLAGS` is now its own config value |
| SCP non-PIC relocation errors | build.sh patch 11 |
| `dmabuf_release_check` 3-way duplicate | build.sh patch 12 |
| `monitor_hang_regist_ldt` 18-way duplicate | build.sh patch 13 |
| `register_mrdump_reset_delay` | build.sh patch 14 |
| ged `tracing_mark_write` | build.sh patch 15 |
| `mtk_pm_qos_update_request` | build.sh patch 16, step 1 only |
| 11 video/v4l2 undefined | `CONFIG_MEDIA_SUPPORT`, `CONFIG_VIDEO_DEV`, +8 (commit `c89d25c`) |
| 3 `android_vh` undefined | `CONFIG_ANDROID=y` (commit `c89d25c`) |
| 3 `n3d_*` undefined | patch 5 revised — keeps the include, drops the 3 duplicate objects |
| 2 `*_3way_semaphore_notifier` | build.sh patch 17, new |
| `exec_ccci_kern_func_by_md_id` | `CONFIG_MTK_PMIC_PROTECT=n` |
| `connectivity_register_state_notifier` | `CONFIG_MTK_CONN_SCP=n` |
| 3 `android_vh` (2nd pass) | `CONFIG_ANDROID_VENDOR_HOOKS=y` (run #79) |
| 2 `sync_file_*` undefined | `CONFIG_SYNC_FILE=y` (run #79) |
| `wakeup_source_init` | `CONFIG_PM_WAKELOCKS=y` (run #80) |
| `scmi_protocol_register` | `CONFIG_ARM_SCMI_PROTOCOL=y` (run #80) |
| 8 `gpueb_*` undefined | build.sh patch 19 — `obj-m` → `obj-y` |
| 7 `cmdq_sec_*` undefined | build.sh patch 20 — `ifeq (...,m)` → `ifneq (...)` |
| `trace_tracing_mark_write` | patch 15 step 2 — the 13 ged call sites |
| `trace_mtk_pm_qos_update_request` | patch 16 revised — renamed, not deleted |
| `helper_fp` duplicate | build.sh patch 21 |
| 4 display undefined | `CONFIG_DRM=y`, `CONFIG_DRM_MEDIATEK=y` (run #82) |
| `xbc_node_find_value` | `CONFIG_BOOT_CONFIG=y` (run #82) |
| `mtk_drm_fbdev.c` 4 compile errors | build.sh patch 23 — stop building that object |
| `mmqos.h` C2x extension | build.sh patch 24 — local `-Werror` neutralised |
| `mtk_dramc.h` not found | build.sh patch 25 — dead include removed |
| 6 `mml_*` duplicates | build.sh patch 26 — kept only the mt6879 tp variant |
| `task_is_booster` ×2 | build.sh patches 22 and 27 |
| 5 implicit declarations | `CONFIG_CPU_FREQ=y`, `CONFIG_CPU_IDLE=y`, `CONFIG_DEBUG_FS=y` (run #86) |
| lld's 20-error cap | build.sh patch 18 — `--error-limit=0` |
| no `dtbo.img` | `NEED_DTBO=false` (run #88) — see §4d |
| `ExampleKernel` in the zip | `package.sh` sets `kernel.string` (run #89) |

## Not done, on purpose

- **SUSFS.** `ENABLE_SUSFS` and `ENABLE_PATH_UMOUNT` are `false`. The
  kernel-side patch applies but **with fuzz**; the susfs4ksu KSU-side patch does
  not apply to ReSukiSU's current layout. ReSukiSU ships its own
  `CONFIG_KSU_SUSFS` and `KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS` (default `y`), so
  hiding from kallsyms already works. See §4a.
- **`trace_mtk_pm_qos_update_request` may not actually fire.** Its prototype is
  gone from `mtk-vmm-regulator.c` and the call compiles only because `KCFLAGS`
  carries `-Wno-error=implicit-function-declaration`. The link is clean so the
  duplicate is gone, but the tracepoint is probably a no-op. Verify before
  relying on it. See §4b.

## If starting completely fresh

`gta9_defconfig` (7938 lines, the vendor one in the kernel repo) is
self-consistent and is what the device ships. This whole exercise exists
because `gta9_00_defconfig` (532 lines) is a truncated copy of it. Switching
wholesale would very likely build in one shot, at the cost of reopening the six
runs of multi-platform duplicate symbols (#52–#57) that `CONFIG_USB_DWC3`,
`CONFIG_USB_MTU3_DUAL_ROLE` and `CONFIG_USB_XHCI_PLATFORM` pull in. That trade
was considered and declined; if the incremental path is abandoned, this is the
thing to try.

## 5. Dead / unwired files

- `defconfig_fragments/gta9-disable-mtk-modules.config` — **not referenced by
  anything.** `config.env`, `scripts/*.sh` and both workflows never mention
  `defconfig_fragments`. Its `CONFIG_MTK_MKP=n` / `CONFIG_MTK_PERF_*=n` entries
  are duplicated in `EXTRA_DEFCONFIG` instead. Either wire it in or delete it.

---

# 6. Runs #89–#111: the build was never the problem

Twenty-three runs, five commits, and the conclusion is uncomfortable: **every
approach in §1–§5 was aimed at making a kernel that would not link, while the
thing that actually determines whether the tablet works is a string that no
amount of linking fixes.**

## 6.1 The base config came out of the device, not out of the repo

`gta9_00_defconfig` is 478 compiled options, and the kernel it produced reached
userspace and restarted: `CONFIG_ARM_GIC`, `CONFIG_ARM_GIC_V3`, ten `COMMON_CLK`
drivers, `CONFIG_OF_RESERVED_MEM`, `CONFIG_INTERCONNECT` and every
`CONFIG_DRM_LCD_*` panel are simply absent. There is no configuration in this
tree that both has the drivers and belongs to branch `14.0`, so the base became
the device's own, extracted from the stock `boot.img`:

```
IKCFG_ST  at offset 0x1834220
217,350 bytes
2,721 CONFIG_ options   (6,431 lines including comments and menu entries)
```

That file is the only authoritative config for this device, and it is modular:
`CONFIG_MODULES=y`. It is stored in this repo as `config/gta9_stock_defconfig`.

The build's resolved `.config` comes out at 2,714 options — seven fewer, which
are the `MT6983` pins from `EXTRA_DEFCONFIG`, and nothing else. Five values
differ from the tablet's. That is worth stating precisely, because it is what
bounds the remaining risk in §6.5: the built-in symbol set is very nearly
identical to the one the tablet's modules were compiled against.

## 6.2 Going modular is what fixed it, and §1 argued against it

The transposition in `scripts/kconfig/symbol.c` is real and §1 documents it
correctly:

```c
if (val == mod)
        if (!sym_is_choice_value(sym) && modules_sym->curr.tri == no)
                val = yes;
```

`init/Kconfig` here has no `default y` for `CONFIG_MODULES`, so it resolves to
`n` unless something sets it, and then **every** `=m` in the defconfig becomes
`=y`. The device's own config sets it to `y` explicitly, which is why using that
config is what makes the transpose stop firing.

With modules on, 191 per-SoC options stay as 191 separate `.ko` files instead of
colliding inside `vmlinux`, and the whole class of "duplicate symbol" failures
disappears. Commit `532284f` removed `CONFIG_MODULES=n` from `config.env` for
this reason.

**It did not work immediately, and the reason is the most useful thing in this
section.** `532284f` left four patches in `scripts/build.sh` that had been
written for the monolithic world, and all four are actively harmful under
modules:

| patch | what it does | why it only ever made sense with `MODULES=n` |
| --- | --- | --- |
| 5 | deletes the frame-sync objects from `imgsensor_isp6s-objs` | the duplicate was across two *modules* |
| 17 | `obj-m` → `obj-y` for `mtk-afe-external.o` | `obj-m` builds nothing when modules are off |
| 19 | `obj-m` → `obj-y` for `gpueb.o` | same |
| 20 | `ifeq ($(CONFIG_MTK_GZ_TZ_SYSTEM),m)` → `ifneq (...,)` | the literal `m` only misbehaves once transposed |

Patch 19 alone caused all nine undefined symbols of run `36704893015`. It pulls
nine objects into `vmlinux`, and `vmlinux` then needs every symbol they call —
`mtk_ipi_register`, `mtk_ipi_send_compl`, `mtk_ipi_device_register`,
`ipi_monitor_dump` (`CONFIG_MTK_IPI=m`), `mtk_mbox_probe`, `mtk_mbox_write`
(`CONFIG_MTK_MBOX=m`), `mtk_smem_init` (smem is a module) and
`ssc_vlogic_bound_{register,unregister}_notifier` (`CONFIG_MTK_SSC_MODULE=m`).
Every one of them was reported against `vmlinux.o`, and every `referenced by`
line named a file under `drivers/gpu/mediatek/gpueb/`.

They are all behind `modules_on()` in `scripts/build.sh` now, which asks the
defconfig and lets `EXTRA_DEFCONFIG` override it — the loop that applies
`EXTRA_DEFCONFIG` runs *later* in `prepare_defconfig`, so reading the file alone
would report the wrong answer for the profile that pins `CONFIG_MODULES=n`. A
defconfig that does not mention `CONFIG_MODULES` is modular, because that is
kconfig's default.

**Patch 5 is the clearest illustration of the mistake.** There are two
`frame_sync_drv.mk` files and they append to *different* object lists:

```
src/common/v1_1/n3d_fsync/frame-sync/frame_sync_drv.mk -> imgsensor_isp6s-objs
    -> imgsensor_isp6s.ko    CONFIG_MTK_IMGSENSOR=m
src-v4l2/frame-sync/frame_sync_drv.mk                  -> imgsensor-objs
    -> imgsensor.ko          CONFIG_MTK_V4L2_IMGSENSOR=m
```

Two modules each carrying their own `frame_sync.o` is ordinary, and modpost does
not compare globals between modules. Only when `CONFIG_MODULES=n` folded both
into `vmlinux` did they collide. Patch 5 removed the objects from the isp6s copy
anyway, which also removed the definition of `FrameSyncInit`, and `n3d.c:691`
still calls it. The duplicate it was written to kill did not exist; the symbol
it broke was real.

## 6.3 Two upstream defects, not ours

Neither is a config problem and neither is fixable by configuration.

**`show_stack` arity.** `connectivity_build_in_adapter.c` calls it with three
arguments in a kernel whose `show_stack` takes two. It is present in three of
the four public MT6789 trees, so it is not this fork. Patched in
`patches/gta9-connectivity-show-stack-signature.patch`.

**`gpudfd_mt6895`.** `drivers/gpu/mediatek/gpufreq/v2/Makefile` gives every
per-SoC object set a gpudfd file except MT6895's:

```make
mtk_gpufreq_mt6789-y := ... gpufreq_mt6789.o gpudfd_mt6789.o
mtk_gpufreq_mt6833-y := ... gpufreq_mt6833.o gpudfd_mt6833.o
mtk_gpufreq_mt6855-y := ... gpufreq_mt6855.o gpudfd_mt6855.o
mtk_gpufreq_mt6879-y := ... gpufreq_mt6879.o gpudfd_mt6879.o
mtk_gpufreq_mt6893-y := ... gpufreq_mt6893.o gpudfd_mt6893.o
mtk_gpufreq_mt6895-y := ... gpufreq_mt6895.o            <- no gpudfd
mtk_gpufreq_mt6983-y := ... gpufreq_mt6983.o gpudfd_mt6983.o
```

`gpufreq_mt6895.c` calls `gpudfd_init`, and the `obj-` line is still gated on
`gpudfd_mt6895.c` existing, so the wildcard test passes and the module is built
without the object it needs. GTA9 is MT6789, so patches 1/3/4's "keep only
mt6789" treatment is applied to it as well (patch 29).

## 6.4 Run #111: a clean build at last

```
compile errors    0
undefined symbols 0
duplicate symbols 0
modpost errors    0

GZIP    arch/arm64/boot/Image.gz
kernel release  5.10.205-gta9-ga5002f750537
```

Artifacts: `Image.gz` 18,903,927 B (→ `Image` 39,823,872 B), `AnyKernel3`
21,984,555 B, `.config` 48,946 B. 54m44s.

Repacked locally against the tablet's own `boot.img.lz4` (sha256
`B361173E…F3744`, pinned):

```
new-boot.img       67,108,864 B   ANDROID! magic
arm64 Image magic  ARM\x64 at offset 56, reserved fields identical to stock
ramdisk.cpio       byte-identical to stock
only the kernel differs; every other component byte-identical
ReSukiSU present, 397 KernelSU strings   (stock: 0 of each)
```

**Three counts went to zero, and the kernel still could not be used.** That is
the whole lesson of runs #89–#111.

## 6.5 The thing that actually matters: vermagic

```
stock   5.10.205-android12-9-28698995
built   5.10.205-gta9-ga5002f750537
```

The device is modular. It loads drivers from `/vendor_dlkm`, and those are the
`.ko` files Samsung built, each carrying the vermagic that build produced.
`insmod` compares it:

```
insmod: version magic '5.10.205-android12-9-28698995' should be
        '5.10.205-gta9-ga5002f750537'
```

Every module fails. A tablet with no modules does not reach userspace. **Odin
reports success throughout**, because nothing about flashing a boot image is
wrong — the failure only appears as a device that restarts.

Note that run #88's kernel was already wrong in this way: it reported
`5.10.205-ga5002f750537-dirty`. Every kernel this project has ever produced
would have been refused by the device's module loader.

Two facts make this fixable rather than a dead end:

```
# CONFIG_MODULE_SIG is not set     (stock and ours)
CONFIG_MODVERSIONS=y               (stock and ours)
```

No module is signed, so there is no key to obtain and no signature to forge.
The check is the version string, plus symbol CRCs that the same source tree
produces. The string is the only thing in the way, and it is a string we set.

```ini
KERNEL_LOCALVERSION=-android12-9-28698995   # config/gta9_vermagic.env
```

`build.sh` also turns `CONFIG_LOCALVERSION_AUTO` off with it, because otherwise
`scripts/setlocalversion` appends this tree's git describe (`-ga5002f750537`, or
`-dirty` once a patch touches a tracked file) and the string stops matching
again. Pinning a localversion and asking for a moving one is contradictory, so
the pin wins.

`.github/workflows/gta9-odin.yml` fails the run if that exact string is not
found in the built `Image`, because a mismatch is invisible in every other part
of a successful build.

**Residual risk, stated plainly:** `CONFIG_MODVERSIONS=y` means symbol CRCs are
compared. The resolved `.config` differs from the tablet's in five values and
omits seven options (the `MT6983` pins), so a module touching one of those could
still fail with *"disagrees about version of symbol"*. That cannot be ruled out
without the device.

## 6.6 Two config mistakes that cost a full build each

**`KCFLAGS` was retyped instead of copied.** The first modular run
(`36700720837`) compiled for 33 minutes and then stopped on

```
drivers/samsung/sec_hard_reset_hook.c:236:46: error: '/*' within block
comment [-Werror,-Wcomment]
```

which is a warning that became an error only because the profile did not carry
the `KCFLAGS` line the working profile had. It was fixed by copying the 323
characters across, not by writing them out again. **When a profile is derived
from a working one, copy the values; do not retype them.** The same mistake was
caught a second time while creating `config/gta9_vermagic.env` and was fixed
before the run.

**`KERNEL_IMAGE_NAME` was absent.** It defaults to `Image.gz-dtb`, the GKI name.
This tree is not GKI — it has a device tree, `gta9_00`, for MT8781 — and the
build writes `Image.gz`. The upload step looked for a file that was never
produced and reported a failure that had nothing to do with the build.

## 6.7 Where things stand

| | |
| --- | --- |
| build | clean, reproducible, ~55 min, ccache-warm afterwards |
| boot image | repacks the tablet's own stock `boot.img`, ramdisk byte-identical |
| base config | the device's own, read out of the stock `boot.img` |
| modules | modular, as the device expects |
| release string | pinned to the tablet's; checked by the workflow |
| ReSukiSU | integrated, tracepoint hook, verified present in the image |
| WiFi/BT | `CONFIG_MTK_COMBO` never pinned |
| unverified | everything that needs the device: boot, module load, symbol CRCs |

Device-side state: stock, unmodified. `system` and `vendor` are untouched, and
`vendor_dlkm` still holds Samsung's own modules, which is what makes the
vermagic approach work at all.

Recovery, if a flash goes wrong: the pristine
`AP_XID-X110XXS4BYBF-20250304193711.tar` (20,862,770 B, verified) still holds
the original `boot.img.lz4`.

## 6.8 If picking this up cold

1. Read §6 before §1. §1 recommends a monolithic build and it is wrong.
2. Do not touch `config/gta9_stock_defconfig`. It is the device's config.
3. Never pin `CONFIG_MODULES=n`. It is what produced the unbounded duplicate
   pile in §1–§5 and it is what `modules_on()` now exists to prevent.
4. Never pin `CONFIG_MTK_COMBO`. A kernel without the WiFi/Bluetooth combo chip
   is a tablet that cannot connect to anything.
5. Do not add a patch without a comment saying which `CONFIG_MODULES` state it
   assumes. Four of them were wrong for twenty runs for exactly that reason.
6. Before a run, check what actually changed. Two of the last three runs failed
   on a profile value that a diff would have shown in one second.
