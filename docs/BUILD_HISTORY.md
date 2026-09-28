# GTA9 kernel build — failure ledger

Working notes for `vickcoy31-ux/KernelSU_Action2` building
`vickcoy31-ux/kernelvalidevo` @ `14.0` (MediaTek MT6789, kernel 5.10).

**Purpose:** keep a durable record of what has already been tried, so a new
attempt does not repeat an approach that was already shown to fail. Update this
file in the same commit that changes `config.env`, `scripts/build.sh`, or the
workflows.

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

## 5. Dead / unwired files

- `defconfig_fragments/gta9-disable-mtk-modules.config` — **not referenced by
  anything.** `config.env`, `scripts/*.sh` and both workflows never mention
  `defconfig_fragments`. Its `CONFIG_MTK_MKP=n` / `CONFIG_MTK_PERF_*=n` entries
  are duplicated in `EXTRA_DEFCONFIG` instead. Either wire it in or delete it.
