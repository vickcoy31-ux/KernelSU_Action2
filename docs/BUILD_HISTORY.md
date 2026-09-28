# GTA9 kernel build — failure ledger

Working notes for `vickcoy31-ux/KernelSU_Action2` building
`vickcoy31-ux/kernelvalidevo` @ `14.0` (MediaTek MT6789, kernel 5.10).

**Purpose:** keep a durable record of what has already been tried, so a new
attempt does not repeat an approach that was already shown to fail. Update this
file in the same commit that changes `config.env`, `scripts/build.sh`, or the
workflows.

As of 2026-09-28 the build has **never succeeded**: 61 `Build Kernel` runs,
57 failures, 0 successes.

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
| #60 | relocation errors | `CONFIG_MTK_TINYSYS_SCP_SUPPORT=n` | **now causes 7 `scp_*` undefined symbols** |
| #61 | vendor-hook tracepoints absent on 5.10 | bypass `android_vh_iommu_iovad_*` in `iommu_debug.c`, keep `CONFIG_MTK_IOMMU_MISC_DBG=m` | good, keep |
| #62 | dup `dev`/`reg` globals | make `dev`/`reg` `static` in `ccu_drv.c` + `mtk-mmdvfs-debug.c` | good, keep |
| #63 | 14 × USB undefined symbols | `CONFIG_USB_SUPPORT/USB/USB_OTG/USB_GADGET=y` from the vendor defconfig | **FIXED** — all 14 gone in run #64 |
| #64 | diagnostic artifact missing | `include-hidden-files: true` on upload-artifact | good, keep |
| #65 | 3 swallowed directory gates + 2 no-default tristates | `CONFIG_DMABUF_HEAPS`(+deferred-free,page-pool), `CONFIG_MTK_TRUSTED_MEMORY_SUBSYSTEM`, `CONFIG_REMOTEPROC`, `CONFIG_NVMEM`, `CONFIG_BATTERY_ID_ADC`, `CONFIG_MTK_DEVINFO`, `CONFIG_TRACEPOINTS` all `=y`; `CONFIG_SEC_DEBUG` `n`→`y` | **in flight** |

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
| `scp_get_reserve_mem_{virt,size,phys}`, `scp_register_sensor`, `scp_deregister_sensor`, `scp_ipidev`, `scp_A_{un,}register_notify` | SCP consumers | `CONFIG_MTK_TINYSYS_SCP_SUPPORT`, pinned `n` at run #60 for "relocation errors" | **unresolved** — the original relocation error is not yet understood |
| `mrdump_regist_hang_bt` | mrdump consumer | `CONFIG_MTK_AEE_FEATURE`, pinned `n` at run #47 | **unresolved** |
| `register_trace_android_vh_logbuf` | logbuf consumer | vendor-hook infrastructure | **unresolved** |
| `rtc_time64_to_tm` | rtc consumer | `CONFIG_RTC_CLASS` | **unresolved** |
| `g_board_id_status` | `misc/mediatek/usb20/musb_dr.o` | unknown | **unresolved** |

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

## 5. Dead / unwired files

- `defconfig_fragments/gta9-disable-mtk-modules.config` — **not referenced by
  anything.** `config.env`, `scripts/*.sh` and both workflows never mention
  `defconfig_fragments`. Its `CONFIG_MTK_MKP=n` / `CONFIG_MTK_PERF_*=n` entries
  are duplicated in `EXTRA_DEFCONFIG` instead. Either wire it in or delete it.
