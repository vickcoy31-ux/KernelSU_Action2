#!/usr/bin/env bash
# Prepare the defconfig and compile the kernel.

set -Eeuo pipefail
trap 'echo "::error::build.sh failed at line ${LINENO}: ${BASH_COMMAND}" >&2; exit 1' ERR
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# shellcheck source=scripts/kernelsu.sh
. "$(dirname "${BASH_SOURCE[0]}")/kernelsu.sh"
# shellcheck source=scripts/patches.sh
. "$(dirname "${BASH_SOURCE[0]}")/patches.sh"

KERNEL_DIR=${KERNEL_DIR:?KERNEL_DIR must be set}
WORKSPACE=${WORKSPACE:-$(cd "${KERNEL_DIR}/.." && pwd)}
ARCH=${ARCH:-arm64}
OUT="${KERNEL_DIR}/out"

DEFCONFIG_PATH="${KERNEL_DIR}/arch/${ARCH}/configs/${KERNEL_CONFIG}"

# ------------------------------------------------------------- defconfig ---

prepare_defconfig() {
	group "Preparing defconfig"
	set -x
	info "kernel dir : ${KERNEL_DIR}"
	info "defconfig  : ${DEFCONFIG_PATH}"
	if [ ! -d "${KERNEL_DIR}/arch/${ARCH}/configs" ]; then
		warn "configs directory is missing: ${KERNEL_DIR}/arch/${ARCH}/configs"
		warn "contents of ${KERNEL_DIR}/arch/${ARCH}:"
		ls -la "${KERNEL_DIR}/arch/${ARCH}" 2>&1 | head -30 || true
		die "defconfig directory not found; is the kernel source cloned?"
	fi
	if [ ! -f "$DEFCONFIG_PATH" ]; then
		warn "defconfig file is missing: ${DEFCONFIG_PATH}"
		ls -la "${KERNEL_DIR}/arch/${ARCH}/configs" 2>&1 | head -40 || true
		die "defconfig not found: arch/${ARCH}/configs/${KERNEL_CONFIG}"
	fi
	cp "$DEFCONFIG_PATH" "${WORKSPACE}/defconfig.orig"
	set +x

	local kver
	kver=$(kernel_version "$KERNEL_DIR" || echo "0.0")

	if [ "${KSU_VARIANT:-none}" != "none" ]; then
		kconf_enable "$DEFCONFIG_PATH" CONFIG_KSU
		ksu_hook_configs "${KSU_VARIANT}" "${KSU_HOOK_MODE:-auto}" "$DEFCONFIG_PATH" "$kver"

		# ReSukiSU (and its SukiSU family) ships kernel/tools/static_export_check.mk
		# which FAILS the build while the selinux_hide symbols stay 'static' in
		# this tree. The check treats a present "static ..." literal as "not
		# exported" and errors with "You should integrate ReSukiSU in your
		# kernel". GTA9's selinuxfs.c keeps sel_handle_status_ops static, so drop
		# the 'static' qualifier so the symbol becomes globally visible and the
		# check passes. Matches the weishu upstream integration, idempotent.
		local selinuxfs="${KERNEL_DIR}/security/selinux/selinuxfs.c"
		if [ -f "$selinuxfs" ] && grep -q "static const struct file_operations sel_handle_status_ops" "$selinuxfs"; then
			sed -i 's/static const struct file_operations sel_handle_status_ops/const struct file_operations sel_handle_status_ops/' "$selinuxfs"
			info "unstatic'd sel_handle_status_ops for ReSukiSU static-export check"
		fi
		# selinux_hide.c (ReSukiSU) also references selinuxfs's write_op
		# symbol directly. Same static-export problem -> drop 'static'.
		if [ -f "$selinuxfs" ] && grep -q "static ssize_t (\*const write_op\[\])" "$selinuxfs"; then
			sed -i 's/static ssize_t (\*const write_op\[\])/ssize_t (*const write_op[])/' "$selinuxfs"
			info "unstatic'd write_op for ReSukiSU"
		fi

		if is_true "${ENABLE_SUSFS:-false}"; then
			susfs_defconfig "$DEFCONFIG_PATH"
		fi

		if is_true "${ENABLE_KPM:-false}"; then
			# patch_linux resolves symbols at runtime, so kallsyms must be complete.
			kconf_set_many "$DEFCONFIG_PATH" \
				CONFIG_KPM=y CONFIG_KALLSYMS=y CONFIG_KALLSYMS_ALL=y
		fi
	fi

	# Vendor trees routinely ship multi-platform .c files behind 'obj-y' even
	# when the driver is '=m' (see drivers/gpu/mediatek/Makefile) or gate every
	# SoC variant on wildcard presence instead of the platform's config. When
	# LLVM's lld links all built-in objects together, global symbols that are
	# only meant to exist in ONE variant collide as "duplicate symbol". GTA9 is
	# MT6789, so drop non-MT6789 objects and make the collisions go away.
	#
	# 1. Camera PDA (isp_71): the pda/Makefile wildcard-gates every SoC —
	#    isp_71/camera_pda.o (generic, MT6789) plus pda_drv_mt6879/mt6895
	#    (isp_71 other SoCs) and pda_drv_mt6855 (which pulls in
	#    isp_6s/camera_pda.o — a DIFFERENT .c with the same name and same
	#    globals). All collide under lld. Keep only the MT6789 generic object.
	local pda_mk="${KERNEL_DIR}/drivers/misc/mediatek/cameraisp/pda/Makefile"
	if [ -f "$pda_mk" ]; then
		sed -i -E '/obj-\$\(CONFIG_MTK_CAMERA_ISP_PDA_SUPPORT\) \+= pda_drv_mt(6879|6895|6855)\.o/d' "$pda_mk"
		sed -i -E '/pda_drv_mt(6879|6895|6855)-objs/d' "$pda_mk"
		info "PDA: dropped non-MT6789 platform objects (GTA9=MT6789)"
	fi

	# 2. GPU DCS: g_core_mask_table is a file-global in ged_dcs.c (also used by
	#    the gpufreq getter in gpufreq_mt6789.c). Keep ged's copy but rename it
	#    so the two built-in objects no longer collide.
	local ged_dcs="${KERNEL_DIR}/drivers/gpu/mediatek/ged/src/ged_dcs.c"
	if [ -f "$ged_dcs" ] && grep -q "struct gpufreq_core_mask_info \*g_core_mask_table;" "$ged_dcs"; then
		sed -i 's/struct gpufreq_core_mask_info \*g_core_mask_table;/struct gpufreq_core_mask_info *g_core_mask_table_dcs;/' "$ged_dcs"
		sed -i 's/\bg_core_mask_table\b/g_core_mask_table_dcs/g' "$ged_dcs"
		info "GPU DCS: renamed g_core_mask_table -> g_core_mask_table_dcs"
	fi

	# 3. CMDQ mailbox: wildcard-gates every cmdq-platform-mtNNNN.o; two of
	#    them (e.g. mt6833 vs mt6893) collide on exported symbols. Keep only
	#    the MT6789 one for GTA9.
	local cmdq_mk="${KERNEL_DIR}/drivers/misc/mediatek/cmdq/mailbox/Makefile"
	if [ -f "$cmdq_mk" ]; then
		sed -i -E '/obj-\$\(CONFIG_MTK_CMDQ_MBOX_EXT\) \+= cmdq-platform-mt[0-9]+\.o$/ {/cmdq-platform-mt6789\.o$/!d}' "$cmdq_mk"
		info "CMDQ: kept only cmdq-platform-mt6789.o (GTA9=MT6789)"
	fi

	# 4. MDP (MediaTek DataPath): same wildcard pattern — mdp_drv_mt6893.o
	#    plus mdp_drv_mt6879.o collide on cmdq_mdp_* symbols (run #54). Keep
	#    only the MT6789 one for GTA9.
	local mdp_mk="${KERNEL_DIR}/drivers/misc/mediatek/mdp/Makefile"
	if [ -f "$mdp_mk" ]; then
		sed -i -E '/obj-\$\(CONFIG_MTK_MDP\) \+= mdp_drv_mt[0-9]+\.o$/ {/mdp_drv_mt6789\.o$/!d}' "$mdp_mk"
		sed -i -E '/mdp_drv_mt[0-9]+-objs := mdp_drv\.o mdp_mt[0-9]+\.o$/ {/mdp_drv_mt6789-objs/!d}' "$mdp_mk"
		info "MDP: kept only mdp_drv_mt6789.o (GTA9=MT6789)"
	fi

	# 5. IMGSENSOR frame-sync (run #55/#56; REVISED in #77). The legacy src/ tree
	#    builds its own frame_monitor.o + frame_sync_algo.o via
	#    src/common/v1_1/n3d_fsync/frame-sync/frame_sync_drv.mk, and
	#    src-v4l2/frame-sync/frame_sync_drv.mk builds the same-named objects
	#    again -> every frm_*/fs_*/FrameSync* symbol collides under lld.
	#
	#    The first attempt deleted the whole `include .../n3d_fsync/Makefile`
	#    line from src/isp6s/Makefile. That cleared the duplicate but it was too
	#    blunt: n3d_fsync/Makefile is what puts n3d.o, n3d_clk.o, n3d_hw.o and
	#    vsync_recorder.o into imgsensor_isp6s-objs, and
	#    src/common/v1_1/imgsensor.c calls into them from imgsensor_init() and
	#    imgsensor_ioctl(). Dropping the include therefore traded six duplicate
	#    symbols for three undefined ones, which is what run #77 reported:
	#        n3d_init, n3d_exit, set_sensor_streaming_state
	#
	#    The include stays; only the duplicated *objects* go. frame_sync_drv.mk
	#    is a separate file from the one src-v4l2 uses, so editing it here cannot
	#    affect the v4l2 copy. Its subdir-ccflags-y block is deliberately kept --
	#    it is unrelated to the objects and dropping it would change the include
	#    path n3d.c is compiled with.
	local n3d_fsync_mk="${KERNEL_DIR}/drivers/misc/mediatek/imgsensor/src/common/v1_1/n3d_fsync/frame-sync/frame_sync_drv.mk"
	if [ -f "$n3d_fsync_mk" ] && grep -q 'LOCAL_FSYNC_PATH)/frame_' "$n3d_fsync_mk"; then
		sed -i -E '/^imgsensor_isp6s-objs \+=/d; /\$\(LOCAL_FSYNC_PATH\)\/frame_/d' "$n3d_fsync_mk"
		info "IMGSENSOR: dropped the duplicated frame-sync objects, kept n3d.o"
	fi

	# 6. MDP MT6789: its file-global 'struct device *larb2' collides with the
	#    same symbol in camera_pda.o (run #55/#56). It is only used inside
	#    mdp_mt6789.c, so rename it (like g_core_mask_table_dcs).
	local mdp6789="${KERNEL_DIR}/drivers/misc/mediatek/mdp/mdp_mt6789.c"
	if [ -f "$mdp6789" ] && grep -q "struct device \*larb2;" "$mdp6789"; then
		sed -i 's/struct device \*larb2;/struct device *larb2_mdp;/' "$mdp6789"
		sed -i 's/\blarb2\b/larb2_mdp/g' "$mdp6789"
		info "MDP6789: renamed larb2 -> larb2_mdp"
	fi
	# 7. Lens VCM driver (run #57): DW9763AF.c defines 'u8 read_data(u8 addr)'
	#    which collides with cam_cal's read_data(). DW9763AF.h does NOT declare
	#    it and no other file calls it. 'static' alone did NOT clear the
	#    duplicate (still global in LD even after the sed ran, seen in run
	#    #57/#59), so rename it to a file-unique symbol instead (same pattern
	#    as g_core_mask_table_dcs / larb2_mdp).
	local dw9763af="${KERNEL_DIR}/drivers/misc/mediatek/lens/vcm/proprietary/main/common/dw9763af/DW9763AF.c"
	if [ -f "$dw9763af" ] && grep -q "u8 read_data(u8 addr)" "$dw9763af"; then
		sed -i 's/u8 read_data(u8 addr)/static u8 dw9763af_read_data(u8 addr)/' "$dw9763af"
		sed -i 's/read_data(0x00)/dw9763af_read_data(0x00)/' "$dw9763af"
		info "DW9763AF: renamed read_data -> dw9763af_read_data (static)"
	fi

	# 8. Thermal/power (run #57): mtk_pbm.c and mtk_cm_mgr_common.c both define
	#    a global 'void tracepoint_cleanup(void)' -> duplicate symbol under lld.
	#    Only the PBM copy is made static. cm_mgr is a cross-subsystem power
	#    manager: keeping its copy static could leave an undefined reference
	#    from callers outside drivers/misc/mediatek. Static in PBM alone removes
	#    the collision; the definition is at line 677 of mtk_pbm.c and its only
	#    call is in the same file (line 947), so the regex below (anchored at
	#    line start) never touches the call.
	local pbm_c="${KERNEL_DIR}/drivers/misc/mediatek/pbm/mtk_pbm.c"
	if [ -f "$pbm_c" ] && grep -q "void tracepoint_cleanup(void)" "$pbm_c"; then
		sed -i 's/^void tracepoint_cleanup(void)/static void tracepoint_cleanup(void)/' "$pbm_c"
		info "PBM: made tracepoint_cleanup static (cm_mgr copy left untouched)"
	fi

	# 9. IOMMU debug (run #61/#62): iommu_debug.c (CONFIG_MTK_IOMMU_MISC_DBG)
	#    registers android vendor-hook tracepoints
	#    (register_trace_android_vh_iommu_iovad_alloc/free_iova) that are NOT
	#    generated on this 5.10 tree (CONFIG_ANDROID_VENDOR_HOOKS off) ->
	#    undefined symbols at LD. But mtk_iommu.c NEEDS MISC_DBG=y to compile
	#    (peri_* decls live in iommu_debug.h, included only when DBG is on).
	#    The hooks are pure debug tracing (alloc/free_iova_hook ->
	#    mtk_iova_dbg_*), so bypassing registration is safe and keeps the
	#    IOMMU subsystem intact. Idempotent: replaces the two-line call with
	#    a plain `ret = 0;` so the symbol reference disappears entirely.
	local iommu_dbg="${KERNEL_DIR}/drivers/misc/mediatek/iommu/iommu_debug.c"
	if [ -f "$iommu_dbg" ] && grep -q "register_trace_android_vh_iommu_iovad_alloc_iova" "$iommu_dbg"; then
		sed -i '/ret = register_trace_android_vh_iommu_iovad_alloc_iova(alloc_iova_hook,/,/"mtk_m4u_dbg_probe");/c	ret = 0; /* vendor-hook bypass (not generated on 5.10 GTA9) */' "$iommu_dbg"
		sed -i '/ret = register_trace_android_vh_iommu_iovad_free_iova(free_iova_hook,/,/"mtk_m4u_dbg_probe");/c	ret = 0; /* vendor-hook bypass (not generated on 5.10 GTA9) */' "$iommu_dbg"
		info "IOMMU_DBG: bypassed android_vh_iommu_iovad_* vendor-hook registration"
	fi

	# 10. Duplicate 'dev' (run #63): ccu_drv.c (ccu/isp6s) and
	#     mtk-mmdvfs-debug.c (mmdvfs) both declare a file-global
	#     'struct device *dev;' (mmdvfs-debug also 'struct regulator *reg;')
	#     -> duplicate symbol under lld at LD vmlinux. Both are internal to
	#     their file: no other CCU/mmdvfs object references them via extern
	#     (verified in ccu_hw/reg/kd_mailbox/imgsensor/qos/ipc/mva and the
	#     mmdvfs-debug.h header, which only declares the exported function).
	#     Make each global 'static' so the collision disappears (same pattern
	#     as tracepoint_cleanup in run #57). Idempotent.
	local ccu_drv="${KERNEL_DIR}/drivers/misc/mediatek/ccu/src/isp6s/ccu_drv.c"
	if [ -f "$ccu_drv" ] && grep -q "^struct device \*dev;$" "$ccu_drv"; then
		sed -i 's/^struct device \*dev;/static struct device *dev;/' "$ccu_drv"
		info "CCU_DRV: made global dev static (dup symbol vs mmdvfs-debug)"
	fi
	local mmdvfs_dbg="${KERNEL_DIR}/drivers/misc/mediatek/mmdvfs/mtk-mmdvfs-debug.c"
	if [ -f "$mmdvfs_dbg" ] && grep -q "^struct device \*dev;$" "$mmdvfs_dbg"; then
		sed -i -e 's/^struct device \*dev;/static struct device *dev;/' \
		       -e 's/^struct regulator \*reg;/static struct regulator *reg;/' "$mmdvfs_dbg"
		info "MMDVFS_DEBUG: made dev and reg static (dup symbol vs ccu_drv)"
	fi

	# 11. SCP rv/ must be linkable into vmlinux (run #60 -> #64). This is a trap:
	#     there is NO config value that works.
	#     drivers/misc/mediatek/scp/Makefile is only 'obj-y += rv/' with no config
	#     gate, while rv/Makefile compiles every object with '-fno-pic
	#     -mcmodel=large' and gates them on CONFIG_MTK_TINYSYS_SCP_SUPPORT.
	#       * =y -> the non-PIC objects land in drivers/built-in.a and lld rejects
	#         their R_AARCH64_MOVW_UABS_* absolute relocations against the PIE
	#         vmlinux (run #60).
	#       * =n -> nothing is built, so scp_ipidev, scp_A_{un,}register_notify and
	#         scp_get_reserve_mem_* become undefined, because the consumers
	#         (sensorhub/ipi_comm.o, sensorhub/ready.o, conap_scp_ipi.o) are NOT
	#         gated on the same symbol (run #64).
	#     Dropping -fno-pic -mcmodel=large makes the objects position independent,
	#     so lld can relax the relocations and they link into vmlinux normally.
	#     arm64 is PIC by default, so this is a no-op for codegen quality.
	local scp_rv_mk="${KERNEL_DIR}/drivers/misc/mediatek/scp/rv/Makefile"
	if [ -f "$scp_rv_mk" ] && grep -q -- '-fno-pic -mcmodel=large' "$scp_rv_mk"; then
		sed -i 's/ -fno-pic -mcmodel=large//' "$scp_rv_mk"
		info "SCP_RV: dropped -fno-pic -mcmodel=large so rv/ links into vmlinux"
	fi

	# 12. dma-buf heap duplicate (run #65): drivers/dma-buf/heaps/mtk_heap_priv.h
	#     *defines* dmabuf_release_check() in the header body with external
	#     linkage. Every .c that includes it -- mtk_sec_heap.c,
	#     mtk_heap_debug.c and system_heap.c -- therefore emits its own global
	#     copy, and lld reports "duplicate symbol: dmabuf_release_check" with
	#     three "defined at" sites. include/linux/dma-buf.h has no declaration
	#     for that name, so it is purely an MTK debug helper (it WARNs and dumps
	#     leftover dma-buf attachments), never a cross-module API. Giving each
	#     includer a private inline copy is behaviour-identical and removes the
	#     collision. 'inline' also avoids -Wunused-function in the translation
	#     units that include the header but never call it.
	local mtk_priv_h="${KERNEL_DIR}/drivers/dma-buf/heaps/mtk_heap_priv.h"
	if [ -f "$mtk_priv_h" ] && grep -q '^void dmabuf_release_check(' "$mtk_priv_h"; then
		sed -i 's/^void dmabuf_release_check(/static inline void dmabuf_release_check(/' "$mtk_priv_h"
		info "DMABUF_HEAP: made header-defined dmabuf_release_check static inline (dup symbol)"
	fi

	# 13. monitor_hang_regist_ldt (run #66). mt-plat/aee.h is included by ~18
	#     objects and switches on this symbol:
	#         #if IS_ENABLED(CONFIG_MTK_HANG_DETECT)
	#         void monitor_hang_regist_ldt(void (*fn)(void));   <- declaration
	#         #else
	#         void monitor_hang_regist_ldt(void (*fn)(void)) { } <- DEFINITION
	#         #endif
	#     So MTK_HANG_DETECT=n does not merely drop a driver: it turns the
	#     header into a definition site and every includer emits a global copy,
	#     giving a 18-way duplicate symbol. That is the *other* half of a
	#     two-sided trap -- MTK_HANG_DETECT=y leaves mrdump_regist_hang_bt
	#     undefined (its provider is under CONFIG_MTK_AEE_IPANIC, pinned n in
	#     EXTRA_DEFCONFIG because AEE does not compile on 5.10), and =n causes
	#     this duplicate. Keep the driver ON and fix the real problem:
	#     hang_detect.c guards its other mrdump calls with
	#     '#if IS_ENABLED(CONFIG_MTK_AEE_IPANIC)' (line 561, with a panic()
	#     fallback) but the two calls in monitor_hang_init/exit were missed by
	#     the vendor. Apply the same guard so the build matches the vendor's own
	#     intent.
	#     Anchored on a single leading tab: the already-guarded call at line 562
	#     is indented with two tabs and must not be matched.
	local hang_c="${KERNEL_DIR}/drivers/misc/mediatek/monitor_hang/hang_detect.c"
	if [ -f "$hang_c" ] && ! grep -q 'mrdump-regist-guard' "$hang_c" &&
		grep -q "^$(printf '\t')mrdump_regist_hang_bt(" "$hang_c"; then
		sed -i -E "s%^$(printf '\t')(mrdump_regist_hang_bt\((show_task_info|NULL)\);)\$%/* mrdump-regist-guard: provider is CONFIG_MTK_AEE_IPANIC, pinned n in EXTRA_DEFCONFIG */\n#if IS_ENABLED(CONFIG_MTK_AEE_IPANIC)\n$(printf '\t')\1\n#endif%" "$hang_c"
		local guards
		guards=$(grep -c 'mrdump-regist-guard' "$hang_c" || true)
		if [ "$guards" -eq 2 ]; then
			info "HANG_DETECT: guarded 2 unguarded mrdump_regist_hang_bt() calls"
		else
			warn "HANG_DETECT: expected 2 guards, found ${guards} -- check manually"
		fi
	fi

	# 14. register_mrdump_reset_delay (run #68). Provider is
	#     drivers/misc/mediatek/aee/mrdump/mrdump_panic.c, gated by
	#     CONFIG_MTK_AEE_IPANIC which 'depends on MTK_AEE_FEATURE' -- and we pin
	#     MTK_AEE_FEATURE=n in EXTRA_DEFCONFIG because the AEE stack does not
	#     compile on 5.10. So the pin is what makes the symbol unreachable.
	#     Un-pinning is the wrong direction: it drags in the whole AEE stack
	#     (MTK_AEE_AED, MTK_AEE_HANGDET) and, because MTK_AEE_FEATURE carries
	#     'select FTRACE', it would silently turn on FTRACE as a side effect.
	#     Only one call site exists, inside sec_reset_init(), and the symbol is
	#     declared by a file-local 'extern' in the same file, so guarding the
	#     call is safe: hard_reset_delay() stays defined and the panic path is
	#     untouched. The skipped call only ever widened the hard-reset window.
	#     Same technique already used for the iommu vendor hooks above.
	#     Written with awk, not sed: three separate sed attempts failed on
	#     quoting (a '|' delimiter collides with alternation, '#' collides with
	#     the literal "#endif" in the replacement, and \1 vs $1 backreference
	#     handling silently ate the indentation).
	local sec_reset_h="${KERNEL_DIR}/drivers/samsung/sec_hard_reset_hook.c"
	if [ -f "$sec_reset_h" ] && ! grep -q 'sec-mrdump-guard' "$sec_reset_h" &&
		grep -q "^$(printf '\t')register_mrdump_reset_delay(" "$sec_reset_h"; then
		awk '
			/^\tregister_mrdump_reset_delay\(hard_reset_delay\);$/ && !done {
				print "\t/* sec-mrdump-guard: provider is CONFIG_MTK_AEE_IPANIC,"
				print "\t * unreachable while CONFIG_MTK_AEE_FEATURE is pinned n in"
				print "\t * EXTRA_DEFCONFIG. This only widened the hard-reset window;"
				print "\t * the panic path is untouched."
				print "\t *"
				print "\t * This is ONE block comment on purpose. A \"/*\" at the start"
				print "\t * of a continuation line is a nested comment, and this tree"
				print "\t * rejects those as -Werror=comment. An earlier version of"
				print "\t * this patch did exactly that and cost runs #68 and #70. */"
				print "#if IS_ENABLED(CONFIG_MTK_AEE_IPANIC)"
				print "\tregister_mrdump_reset_delay(hard_reset_delay);"
				print "#endif"
				done = 1
				next
			}
			{ print }
		' "$sec_reset_h" >"$sec_reset_h.new" && mv "$sec_reset_h.new" "$sec_reset_h"
		# Diagnostic (run #70): this patch reported success and the file verified
		# clean, yet the compiler later rejected the SAME file at the SAME lines
		# with an unterminated block comment. One of those two facts has to be
		# wrong, and nothing in the log says which. Print the region verbatim so
		# the next run shows the bytes instead of requiring another guess.
		group "SEC_RESET: patched region (diagnostic)"
		sed -n '225,240p' "$sec_reset_h" | cat -A | sed 's/^/    /'
		endgroup
		# Verify the file still parses, not just that the marker landed. An
		# earlier version of this patch emitted a bare "\t/*" line, which opened
		# a block comment that was never closed; the following four lines were
		# swallowed and clang rejected the file with -Werror=comment, costing run
		# #68. Counting the marker alone could not see that, so check the actual
		# invariant: the file must not end inside a block comment.
		#
		# Note C comments do NOT nest, so "/*" seen while already inside a comment
		# is plain text. A naive grep -c counts those as new openers and reports
		# phantom unterminated comments.
		if [ "$(grep -c 'sec-mrdump-guard' "$sec_reset_h" || true)" -eq 1 ] &&
			[ "$(awk '
				{ line = $0
				  while (length(line) > 0) {
					if (!incomment) { o = index(line, "/*"); if (o == 0) break
						incomment = 1; line = substr(line, o + 2) }
					else { c = index(line, "*/"); if (c == 0) break
						incomment = 0; line = substr(line, c + 2) } } }
				END { print incomment ? 1 : 0 }' "$sec_reset_h")" = "0" ]; then
			info "SEC_RESET: guarded register_mrdump_reset_delay() call (provider pinned off)"
		else
			warn "SEC_RESET: guard did not verify -- file may be left inside a comment"
		fi
	fi

	# 15. ged tracepoint collides with an upstream event (run #72; CORRECTED in #80).
	#     drivers/gpu/mediatek/ged/include/ged_tracepoint.h declares
	#         TRACE_EVENT(tracing_mark_write, ...)
	#     but tracing_mark_write is ALSO declared upstream, in this tree in
	#     include/trace/events/tracing_mark_write.h compiled by
	#     kernel/trace/trace_tracing_mark_write.c. (MTK split it out of the
	#     upstream 5.10 location in trace_printk.c; sched.h here has no such
	#     event.) TRACE_SYSTEM does not appear in the symbol a TRACE_EVENT
	#     generates -- the name is always __tracepoint_<event> -- and
	#     TRACE_INCLUDE_FILE only names the generated *file*, not the symbol. So
	#     ged_log.o and kernel/built-in.a both emitted
	#     __tracepoint_tracing_mark_write:
	#         ld.lld: error: duplicate symbol: __tracepoint_tracing_mark_write
	#         >>> defined at trace_tracing_mark_write.c  kernel/built-in.a
	#         >>> defined at ged_log.c                   drivers/built-in.a
	#     and likewise __traceiter_ and __SCK__tp_func_ for the same event.
	#     This became visible in #72 only because run #68 turned on
	#     ENABLE_DEFAULT_TRACERS, which for the first time made CONFIG_TRACEPOINTS
	#     resolve to y and pulled kernel/trace/ into the build at all.
	#
	#     CORRECTION: the original comment here claimed "ged_log.c only includes
	#     the header and never calls the tracepoint, so renaming the event needs
	#     no call-site change". That was wrong, and run #79 paid for it --
	#     ld.lld: undefined symbol: trace_tracing_mark_write, referenced by
	#     ged_kpi.c and ged_eb.c. Four ged .c files do call it, 11 sites in
	#     total, and -Wno-error=implicit-function-declaration in KCFLAGS let
	#     every one of them through compile as an implicit declaration. The
	#     event is NOT a Kconfig problem: CONFIG_EVENT_TRACING=y already builds
	#     the provider, so there is nothing to configure. Both halves are needed
	#     and both are done here.
	local ged_tp="${KERNEL_DIR}/drivers/gpu/mediatek/ged/include/ged_tracepoint.h"
	if [ -f "$ged_tp" ] && grep -q '^TRACE_EVENT(tracing_mark_write,$' "$ged_tp"; then
		sed -i 's/^TRACE_EVENT(tracing_mark_write,$/TRACE_EVENT(ged_tracing_mark_write,/' "$ged_tp"
		if grep -q '^TRACE_EVENT(ged_tracing_mark_write,$' "$ged_tp"; then
			info "GED_TRACEPOINT: renamed tracing_mark_write -> ged_tracing_mark_write (upstream name clash)"
		else
			warn "GED_TRACEPOINT: rename did not verify"
		fi
	fi
	# Step 2, added in #80: the call sites. \b keeps this from touching the
	# upstream tracing_mark_begin/tracing_mark_end macros, which are macros and
	# therefore never appear as a literal call -- so only the ged driver's own
	# direct calls are rewritten.
	local ged_src f changed=0
	for ged_src in ged_kpi.c ged_dvfs.c ged_eb.c ged_notify_sw_vsync.c; do
		f="${KERNEL_DIR}/drivers/gpu/mediatek/ged/src/${ged_src}"
		[ -f "$f" ] || continue
		if grep -q '\btrace_tracing_mark_write(' "$f"; then
			sed -i 's/\btrace_tracing_mark_write(/trace_ged_tracing_mark_write(/g' "$f"
			changed=$((changed + 1))
		fi
	done
	if [ "$changed" -gt 0 ]; then
		info "GED_TRACEPOINT: rewrote the call sites in ${changed} ged source file(s)"
	else
		info "GED_TRACEPOINT: no ged call sites needed rewriting"
	fi

	# 16. mtk_pm_qos_update_request declared in two regulator drivers (run #73).
	#     drivers/regulator/mtk-vmm-trace.h lines 15-44 are a byte-identical
	#     copy-paste of mtk-dvfsrc-regulator-trace.h: the same
	#     DECLARE_EVENT_CLASS(mtk_pm_qos_request, ...) and the same
	#     DEFINE_EVENT(mtk_pm_qos_request, mtk_pm_qos_update_request, ...).
	#     Its include guard is _TRACE_ISPDVFS_EVENTS_H, which is not even its
	#     own name, more evidence it was pasted. Both headers have a correct and
	#     distinct TRACE_INCLUDE_FILE, so each legitimately generates its own
	#     tracepoint code -- and both use the same event name, so:
	#         ld.lld: error: duplicate symbol: __tracepoint_mtk_pm_qos_update_request
	#         >>> defined at mtk-dvfsrc-regulator.c  regulator/built-in.a
	#         >>> defined at mtk-vmm-regulator.c     regulator/built-in.a
	#
	#     Step 1: drop the copy from the vmm header. TRACE_INCLUDE_FILE,
	#     <trace/define_trace.h> and TRACE_EVENT(vmm__update_voltage) are
	#     outside the removed range and stay intact.
	#
	#     Step 2 (attempted in #74, DID NOT WORK -- reverted): include the dvfsrc
	#     header from the vmm .c, on the theory that its TRACE_HEADER_MULTI_READ
	#     style would yield a DECLARE_TRACE. It does not. TRACE_HEADER_MULTI_READ
	#     is defined nowhere in the sources; define_trace.h defines it itself at
	#     line 93, re-reads the header at line 95 to collect prototypes, then
	#     undefines it at line 118:
	#         include/trace/define_trace.h:93   #define TRACE_HEADER_MULTI_READ
	#         include/trace/define_trace.h:95   #include TRACE_INCLUDE(TRACE_INCLUDE_FILE)
	#         include/trace/define_trace.h:99   #define DECLARE_TRACE(name, proto, args)  <- no-op
	#         include/trace/define_trace.h:118  #undef TRACE_HEADER_MULTI_READ
	#     So the multi-read path is driven by define_trace.h re-including the
	#     header, not by the .c including it again. A second plain include skips
	#     the guarded body and the trailing define_trace.h re-emits the symbol from
	#     the still-defined CREATE_TRACE_POINTS. The linker confirmed it: the
	#     duplicate survived, still coming from mtk-vmm-regulator.o.
	#
	#     The obvious replacement is DECLARE_TRACE(...), available from
	#     include/linux/tracepoint.h:419, but two things must be checked first and
	#     both need a real preprocessor (there is no compiler on the workstation):
	#       a) TP_PROTO appears in no .c file anywhere -- it may only be valid
	#          inside TRACE_EVENT;
	#       b) define_trace.h:99 redefines DECLARE_TRACE to a no-op, so a
	#          DECLARE_TRACE placed after the vmm include may be silently dropped.
	#     Both are dumped below so the next run settles it with evidence.
	local vmm_tp="${KERNEL_DIR}/drivers/regulator/mtk-vmm-trace.h"
	if [ -f "$vmm_tp" ] && grep -q '^DECLARE_EVENT_CLASS(mtk_pm_qos_request,$' "$vmm_tp"; then
		# CORRECTED in #80. This used to delete the pasted block outright:
		#     sed -i "${vmm_start},$((vmm_end - 1))d" "$vmm_tp"
		# That cleared the duplicate, which is what run #76 verified, but it left
		# the call site in mtk-vmm-regulator.c:248 with nothing to resolve to:
		#     ld.lld: error: undefined symbol: trace_mtk_pm_qos_update_request
		#     >>> referenced by mtk-vmm-regulator.c  regulator/built-in.a
		#                             (ccu_set_voltage)
		# KCFLAGS carries -Wno-error=implicit-function-declaration, so the call
		# compiled happily as an implicit declaration and only failed at link.
		# BUILD_HISTORY §4b already suspected this call was a silent no-op; run
		# #79 proved it was worse than that.
		#
		# The right cut is to rename, not delete. The vmm regulator wants to
		# trace its own PM QoS request; the reason it collided with dvfsrc is
		# only that both picked the same event name. Give it a vmm-specific one
		# and the trace works again -- same approach as patch 15.
		sed -i 's/^DECLARE_EVENT_CLASS(mtk_pm_qos_request,$/DECLARE_EVENT_CLASS(mtk_vmm_pm_qos_request,/' "$vmm_tp"
		sed -i 's/^DEFINE_EVENT(mtk_pm_qos_request, mtk_pm_qos_update_request,$/DEFINE_EVENT(mtk_vmm_pm_qos_request, mtk_vmm_pm_qos_update_request,/' "$vmm_tp"
		if grep -q 'mtk_pm_qos_request' "$vmm_tp"; then
			warn "REGULATOR_TRACE: old event name still present in mtk-vmm-trace.h after rename"
		else
			info "REGULATOR_TRACE: renamed the vmm copy to mtk_vmm_pm_qos_request (was colliding with dvfsrc)"
		fi
		# The call site, which no Kconfig change and no header edit can fix.
		local vmm_c_call="${KERNEL_DIR}/drivers/regulator/mtk-vmm-regulator.c"
		if [ -f "$vmm_c_call" ] && grep -q '\btrace_mtk_pm_qos_update_request(' "$vmm_c_call"; then
			sed -i 's/\btrace_mtk_pm_qos_update_request(/trace_mtk_vmm_pm_qos_update_request(/g' "$vmm_c_call"
			if grep -q '\btrace_mtk_pm_qos_update_request(' "$vmm_c_call"; then
				warn "REGULATOR_TRACE: old call name still present in mtk-vmm-regulator.c"
			else
				info "REGULATOR_TRACE: call site now uses trace_mtk_vmm_pm_qos_update_request"
			fi
		fi
	fi

	# Diagnostic (run #75): settle the two open questions with evidence instead of
	# another guess. Preprocess the vmm regulator .c the same way the build does
	# and report what DECLARE_TRACE and the tracepoint call actually became.
	local vmm_c="${KERNEL_DIR}/drivers/regulator/mtk-vmm-regulator.c"
	if [ -f "$vmm_c" ] && is_true "${REGULATOR_TRACE_PROBE:-false}"; then
		group "REGULATOR_TRACE: preprocessor probe"
		local probe="${WORKSPACE}/vmm_probe.i"
		# Reuse the real build's include flags, otherwise asm/rwonce.h and the
		# rest of the arch headers are missing and the run aborts.
		# -imacros must be an ABSOLUTE path: run #75 and #76 passed "out/.config"
		# and got "fatal error: 'out/.config' file not found", because -imacros
		# is resolved against the include path rather than the working
		# directory. That silently dropped every CONFIG_* define, and the build
		# then reported 20 undefined symbols -- all of them guard-only core
		# kernel API such as video_device_alloc and v4l2_ctrl_handler_init_class.
		# Those 20 were an artefact of the broken probe, not a real regression.
		# Make the probe fail loudly instead of producing misleading output: if
		# .config is missing, say so and skip.
		if [ ! -f "${KERNEL_DIR}/out/.config" ]; then
			warn "REGULATOR_TRACE: ${KERNEL_DIR}/out/.config not found, skipping probe"
		elif (cd "$KERNEL_DIR" && "${CLANG_PATH:-clang}/clang" -E \
			-imacros "${KERNEL_DIR}/out/.config" \
			-I. -Iinclude -Iarch/arm64/include -Iarch/arm64/include/generated \
			-D__KERNEL__ -DKBUILD_MODNAME='"probe"' \
			"$vmm_c" >"$probe" 2>"${probe}.err"); then
			ok "preprocessed OK -> ${probe}"
			# Only ever read a complete file. Every count tolerates grep's exit 1
			# on no match: this script runs under `set -Eeuo pipefail` with an
			# ERR trap, so a bare `grep -c` that finds nothing kills the build.
			# That is what #75 did.
			echo "--- does the call site survive? ---"
			( grep -n "trace_mtk_pm_qos_update_request" "$probe" | head -5 || true ) | sed 's/^/      /'
			echo "--- DECLARE_TRACE directives still present? (0 = already a no-op) ---"
			echo "      count = $(grep -c 'define DECLARE_TRACE' "$probe" || true)"
			echo "--- __tracepoint_mtk_pm_qos_update_request occurrences ---"
			echo "      count = $(grep -o '__tracepoint_mtk_pm_qos_update_request' "$probe" | wc -l || true)"
		else
			warn "preprocess failed; first errors:"
			head -8 "${probe}.err" | sed 's/^/      /' || true
			# A failed -E still writes a partial .i. Reading it afterwards is what
			# produced the phantom 20 undefined symbols in #76 -- the file exists,
			# it is just missing every CONFIG_* define, so core kernel API guarded
			# by #ifdef looked "undefined". Never analyse a partial file.
			rm -f "$probe"
			info "REGULATOR_TRACE: discarded the partial preprocessor output"
		fi
		endgroup
	fi


	# 17. SCP 3-way semaphore notifier (run #77). scp_helper.c calls
	#     register_3way_semaphore_notifier() / unregister_3way_semaphore_notifier()
	#     from mtk-afe-external.h, but the only definition sits in
	#     sound/soc/mediatek/common/mtk-afe-external.c, which this Makefile
	#     hangs off obj-m:
	#         sound/soc/mediatek/common/Makefile:20   obj-m += mtk-afe-external.o
	#     CONFIG_MODULES is n in this tree, so obj-m builds nothing and the two
	#     symbols are undefined at link time. There is no second route to the
	#     file either: CONFIG_SOUND is off, so sound/Makefile never descends
	#     into soc/, soc/Makefile only enters mediatek/ under CONFIG_SND_SOC,
	#     and mediatek/Makefile only enters common/ under
	#     CONFIG_SND_SOC_MEDIATEK. Enabling the ALSA/SOC stack just for this
	#     58-line file is a far bigger change than the problem, so instead make
	#     the three directories reachable and build the object unconditionally.
	#     The file includes only its own header and <linux/module.h>, so it
	#     stands alone.
	local snd_mk="${KERNEL_DIR}/sound/Makefile"
	local soc_mk="${KERNEL_DIR}/sound/soc/Makefile"
	local mtk_soc_mk="${KERNEL_DIR}/sound/soc/mediatek/Makefile"
	local afe_mk="${KERNEL_DIR}/sound/soc/mediatek/common/Makefile"
	if [ -f "$snd_mk" ] && [ -f "$soc_mk" ] && [ -f "$mtk_soc_mk" ] && [ -f "$afe_mk" ]; then
		# Each of these is idempotent, and only ever adds the one directory
		# that Kbuild would otherwise skip. Every other entry in those three
		# Makefiles stays gated on its own CONFIG_*, so nothing else is built.
		grep -q '^obj-y += soc/$' "$snd_mk" || printf '\nobj-y += soc/\n' >>"$snd_mk"
		grep -q '^obj-y += mediatek/$' "$soc_mk" || printf '\nobj-y += mediatek/\n' >>"$soc_mk"
		grep -q '^obj-y += common/$' "$mtk_soc_mk" || printf '\nobj-y += common/\n' >>"$mtk_soc_mk"
		sed -i -E 's#^obj-m \+= mtk-afe-external\.o$#obj-y += mtk-afe-external.o#' "$afe_mk"
		info "AFE: built mtk-afe-external.o into vmlinux (was obj-m, dead with MODULES=n)"
	fi

	# 19. gpueb is hung off obj-m (run #79). drivers/gpu/mediatek/gpueb/Makefile:
	#         obj-m += gpueb.o
	#     CONFIG_MODULES is n in this tree, so scripts/Makefile.build discards
	#     obj-m outright and not one of the nine objects is compiled -- the log
	#     shows `AR drivers/gpu/mediatek/gpueb/built-in.a` with no `CC` for any
	#     of them. Eight symbols go missing, referenced from 20+ places across
	#     gpufreq_v2.c and ged_eb.c:
	#         get_gpueb_ipidev, gpueb_dump_status, gpueb_trigger_wdt,
	#         gpueb_get_{recv,send}_PIN_ID_by_name,
	#         gpueb_get_reserve_mem_{phys,virt,size}_by_name
	#     There is no Kconfig value that helps: CONFIG_MTK_TINYSYS_GPUEB_SUPPORT
	#     exists, defaults to n, and is referenced by nothing at all. Same
	#     treatment as patch 17 -- obj-m becomes obj-y. Disabling the consumer
	#     is not an option, it is the GPU driver itself.
	local gpueb_mk="${KERNEL_DIR}/drivers/gpu/mediatek/gpueb/Makefile"
	if [ -f "$gpueb_mk" ] && grep -q '^obj-m += gpueb\.o$' "$gpueb_mk"; then
		sed -i 's/^obj-m += gpueb\.o$/obj-y += gpueb.o/' "$gpueb_mk"
		if grep -q '^obj-y += gpueb\.o$' "$gpueb_mk"; then
			info "GPUEB: built into vmlinux (was obj-m, dead with MODULES=n)"
		else
			warn "GPUEB: obj-m -> obj-y did not verify"
		fi
	fi

	# 20. Secure CMDQ is gated on a make-level `ifeq ...,m` (run #79).
	#     drivers/misc/mediatek/cmdq/mailbox/Makefile guards the three objects
	#     that provide every cmdq_sec_* symbol with:
	#         ifeq ($(CONFIG_MTK_GZ_TZ_SYSTEM),m)
	#     CONFIG_MTK_GZ_TZ_SYSTEM is `m` in both defconfigs, so that is what the
	#     author wrote -- but with CONFIG_MODULES=n every `m` is transposed to
	#     `y` in include/config/auto.conf, the comparison goes false, and
	#     cmdq-sec-drv.o is added to no obj at all. Seven symbols then go missing
	#     from camera_fdvt.o: cmdq_sec_mbox_{enable,disable} and
	#     cmdq_sec_pkt_{set_data,set_mtee,set_secid,set_payload,write_reg}.
	#
	#     This is the same m->y trap as everywhere else, but a Kconfig edit
	#     cannot reach it: `ifeq` compares against a literal, while obj-$(CONFIG_X)
	#     only ever tests y/n and so survives transposition. Changing the guard
	#     to `ifneq ($(CONFIG_MTK_GZ_TZ_SYSTEM),)` tests "not off" instead of
	#     "exactly m", which is what a y/n world needs.
	#
	#     The consumer cannot be dropped instead: camera_fdvt.c:196 #defines
	#     FDVT_USE_GCE unconditionally, outside any #if, and the secure path is
	#     reached from FDVT_open/FDVT_release with only a NULL check. There is
	#     no config switch that turns it off in that file.
	local cmdq_mk="${KERNEL_DIR}/drivers/misc/mediatek/cmdq/mailbox/Makefile"
	if [ -f "$cmdq_mk" ] && grep -q 'ifeq ($(CONFIG_MTK_GZ_TZ_SYSTEM),m)' "$cmdq_mk"; then
		sed -i 's|ifeq ($(CONFIG_MTK_GZ_TZ_SYSTEM),m)|ifneq ($(CONFIG_MTK_GZ_TZ_SYSTEM),)|' "$cmdq_mk"
		if grep -q 'ifneq ($(CONFIG_MTK_GZ_TZ_SYSTEM),)' "$cmdq_mk"; then
			info "CMDQ: secure mailbox guard now accepts y (m is transposed to y here)"
		else
			warn "CMDQ: guard rewrite did not verify"
		fi
	fi

	# 21. Three file-global collisions, both sides newly built (run #80).
	#     With the 19 run-#80 symbols gone the link was clean of undefined
	#     references for the first time, and this is what was underneath:
	#         duplicate symbol: helper_fp
	#         >>> defined at cmdq-sec-mailbox.c  (patch 20 made this build)
	#         >>> defined at cmdq-util.c
	#         duplicate symbol: r_pos_debug
	#         duplicate symbol: log_ctl_debug
	#         >>> defined at gpueb_logger.c       (patch 19 made this build)
	#         >>> defined at scp_logger.c
	#     Same class as g_core_mask_table_dcs, larb2 and DW9763AF read_data:
	#     file-scope names with no prefix, in a tree that is linked
	#     monolithically so there is no module boundary to hide them. scp_logger.c
	#     and gpueb_logger.c are near-copies of each other, which is why they
	#     picked the same three names.
	#
	#     Renamed in the newly-built file only, so the older object keeps its
	#     symbol and nothing that already resolved changes. Verified before
	#     editing: no header in drivers/gpu/mediatek/gpueb/ or gpufreq/ declares
	#     either name extern, and no other gpueb source file mentions them, so
	#     both are genuinely local to gpueb_logger.c. Same check for helper_fp
	#     across cmdq-sec-helper.c, cmdq-sec-mtee.c and mtk-cmdq-ext.h.
	#
	#     The \b anchors are load-bearing in the cmdq case: the file also has a
	#     *type* called cmdq_sec_helper_fp, and '_' is a word character, so
	#     \bhelper_fp\b cannot match inside it. A plain s/helper_fp/.../ would
	#     have renamed the struct as well and broken the file.
	local gl_c="${KERNEL_DIR}/drivers/gpu/mediatek/gpueb/gpueb_logger.c"
	if [ -f "$gl_c" ] && grep -q '\br_pos_debug\b' "$gl_c"; then
		sed -i 's/\br_pos_debug\b/gpueb_r_pos_debug/g; s/\blog_ctl_debug\b/gpueb_log_ctl_debug/g' "$gl_c"
		if grep -q '\br_pos_debug\b\|\blog_ctl_debug\b' "$gl_c"; then
			warn "GPUEB_LOGGER: rename left the old name behind"
		else
			info "GPUEB_LOGGER: r_pos_debug/log_ctl_debug renamed to file-unique names (clash with scp_logger.c)"
		fi
	fi
	local cs_c="${KERNEL_DIR}/drivers/misc/mediatek/cmdq/mailbox/cmdq-sec-mailbox.c"
	if [ -f "$cs_c" ] && grep -q '\bhelper_fp\b' "$cs_c"; then
		sed -i 's/\bhelper_fp\b/cmdq_sec_helper_fp_inst/g' "$cs_c"
		if grep -q 'struct cmdq_sec_helper_fp cmdq_sec_helper_fp_inst' "$cs_c"; then
			info "CMDQ_SEC: helper_fp renamed to cmdq_sec_helper_fp_inst (clash with cmdq-util.c); the struct cmdq_sec_helper_fp type is untouched"
		else
			warn "CMDQ_SEC: rename did not verify"
		fi
	fi

	# 22. task_is_booster has no definition anywhere in this tree (run #81).
	#     block/elevator.c:767 forward-declares it and :774 calls it:
	#         bool task_is_booster(struct task_struct *tsk);
	#         ...
	#             if (task_is_booster(current))
	#                 return count;
	#         ld.lld: error: undefined symbol: task_is_booster
	#         >>> referenced by elevator.o:(elv_iosched_store) in archive block/built-in.a
	#
	#     The obvious suspect is the MTK scheduler, since CONFIG_MTK_SCHEDULER is
	#     one of our =n pins. It is not. Searched, and found nothing:
	#       - all 35 .c/.h files under kernel/sched/            no mention
	#       - all 19 files under drivers/misc/mediatek/sched/  no mention
	#         (common.c, fair.c, eas/{sched_main,sched_sys_common,eas_plus,
	#          topology,static_power,core_pause,rotate}.c, sugov/*, core_ctl/*)
	#       - include/linux/sched.h                            no mention
	#       - the only occurrences of the string "booster" anywhere in the
	#         repository are drivers/input/{evdev_booster.c,input_booster.c,
	#         input_booster_mtk.c}, include/linux/input/input_booster.h,
	#         drivers/regulator/stm32-booster.c and a stm32 devicetree binding
	#       - the only file-scope task_* definitions in kernel/sched/ are
	#         task_wants_autogroup, task_prio, task_can_attach, task_numa_free,
	#         task_numa_fault and task_cputime[_adjusted]
	#     CONFIG_MTK_TASK_TURBO is =m in the vendor defconfig but no file reads
	#     it in a way that would provide this symbol.
	#
	#     So this is a forward declaration to a function that does not exist in
	#     this kernel at all. No config value can bring it back, and leaving the
	#     call in place means the link can never succeed. The call is dead code
	#     here: there is no booster to consult, so removing it changes no
	#     behaviour, it only stops the linker looking for something absent.
	#
	#     Both the declaration and the call are removed together, and the
	#     verification checks that neither the declaration nor the symbol name
	#     survives, so a tree that *does* define it would be left alone.
	local elev_c="${KERNEL_DIR}/block/elevator.c"
	if [ -f "$elev_c" ] && grep -q '^bool task_is_booster(struct task_struct \*tsk);$' "$elev_c"; then
		sed -i '/^bool task_is_booster(struct task_struct \*tsk);$/d' "$elev_c"
		sed -i '/^\tif (task_is_booster(current))$/,+1d' "$elev_c"
		if grep -q 'task_is_booster' "$elev_c"; then
			warn "ELEVATOR: task_is_booster still referenced after removal"
		else
			info "ELEVATOR: removed the call to task_is_booster (no definition exists in this tree)"
		fi
	elif [ -f "$elev_c" ] && grep -q 'task_is_booster' "$elev_c"; then
		warn "ELEVATOR: task_is_booster present but the pattern did not match; leaving it alone"
	fi

	# 18. See every undefined symbol, not just the first 20 (run #78). lld stops
	#     after 20 errors and says so:
	#         ld.lld: error: too many errors emitted, stopping now
	#                 (use --error-limit=0 to see all errors)
	#     Runs #76, #77 and #78 each reported exactly 20, which read as
	#     "20 outstanding" and was wrong each time -- #78 fixed 17 and 17
	#     different ones appeared underneath. The real count has never been
	#     known. This is not instrumentation that can fail: it only changes how
	#     many errors the linker reports before stopping, and a successful link
	#     is unaffected. Keep it permanently.
	#
	#     ${LD} is invoked directly here, so the flag takes no -Wl, prefix.
	#     CONFIG_LTO_CLANG is off in this build (CONFIG_LTO_NONE=y), which is
	#     the branch this line is in; the other branch is the ${CC} one below it.
	local link_sh="${KERNEL_DIR}/scripts/link-vmlinux.sh"
	if [ -f "$link_sh" ] && grep -q 'error-limit' "$link_sh"; then
		info "LINK: --error-limit=0 already present"
	elif [ -f "$link_sh" ] && grep -q '${LD} ${KBUILD_LDFLAGS} ${LDFLAGS_vmlinux}' "$link_sh"; then
		sed -i 's|${LD} ${KBUILD_LDFLAGS} ${LDFLAGS_vmlinux}|& --error-limit=0|' "$link_sh"
		info "LINK: lld will now report every undefined symbol, not just 20"
	else
		warn "LINK: could not find the vmlinux link line in link-vmlinux.sh; the 20-error cap will stay"
	fi

	# Overlayfs backs KernelSU's module mounts and system-partition writes.
	is_true "${ADD_OVERLAYFS_CONFIG:-false}" && kconf_enable "$DEFCONFIG_PATH" CONFIG_OVERLAY_FS

	# Kept as a standalone switch for kernels that need kprobes for their own
	# reasons, independent of the hook mode.
	if is_true "${ADD_KPROBES_CONFIG:-false}"; then
		kconf_set_many "$DEFCONFIG_PATH" \
			CONFIG_MODULES=y CONFIG_KPROBES=y CONFIG_HAVE_KPROBES=y CONFIG_KPROBE_EVENTS=y
	fi

	if is_true "${DISABLE_LTO:-false}"; then
		kconf_set_many "$DEFCONFIG_PATH" \
			CONFIG_LTO=n CONFIG_LTO_CLANG=n CONFIG_LTO_CLANG_FULL=n \
			CONFIG_LTO_CLANG_THIN=n CONFIG_THINLTO=n CONFIG_LTO_NONE=y
	fi

	is_true "${DISABLE_CC_WERROR:-false}" && kconf_disable "$DEFCONFIG_PATH" CONFIG_CC_WERROR

	# Free-form extras: one CONFIG_x=y per line, or space separated.
	if [ -n "${EXTRA_DEFCONFIG:-}" ]; then
		local kv
		# shellcheck disable=SC2086
		for kv in $(printf '%s' "$EXTRA_DEFCONFIG" | tr '\n' ' '); do
			[ -n "$kv" ] || continue
			case "$kv" in
				*=*) kconf_set "$DEFCONFIG_PATH" "${kv%%=*}" "${kv#*=}" ;;
				*)   warn "ignoring malformed EXTRA_DEFCONFIG entry '${kv}' (want CONFIG_X=y)" ;;
			esac
		done
	fi

	# A stable LOCALVERSION keeps artifact names predictable. Without this the
	# tree appends "-dirty" as soon as any patch above touches a tracked file.
	if [ -n "${KERNEL_NAME:-}" ]; then
		kconf_set "$DEFCONFIG_PATH" CONFIG_LOCALVERSION "\"-${KERNEL_NAME}\""
		if [ -f "${KERNEL_DIR}/scripts/setlocalversion" ]; then
			sed -i 's/echo "\$res"/echo "\$res"/; s/-dirty//g' "${KERNEL_DIR}/scripts/setlocalversion"
		fi
	fi

	info "defconfig changes:"
	diff -u "${WORKSPACE}/defconfig.orig" "$DEFCONFIG_PATH" | sed -n '4,$p' | sed 's/^/    /' || true
	endgroup
}

# ----------------------------------------------------------------- build ---

make_args() {
	printf '%s' "O=out ARCH=${ARCH}"
	[ -n "${CUSTOM_CMDS:-}" ] && printf ' %s' "$CUSTOM_CMDS"
	[ -n "${EXTRA_CMDS:-}"  ] && printf ' %s' "$EXTRA_CMDS"
	[ -n "${GCC_64:-}"      ] && printf ' %s' "$GCC_64"
	[ -n "${GCC_32:-}"      ] && printf ' %s' "$GCC_32"
	if is_true "${USE_LLVM:-false}"; then
		printf ' LLVM=1 LLVM_IAS=1'
		[ -n "${GCC_64:-}" ] || printf ' CROSS_COMPILE=aarch64-linux-gnu-'
	fi
}

build_kernel() {
	group "Building kernel"
	export PATH="${CLANG_PATH:-}:${PATH}"
	export KBUILD_BUILD_HOST=${KBUILD_BUILD_HOST:-Github-Action}
	export KBUILD_BUILD_USER=${KBUILD_BUILD_USER:-kernelsu-action}

	# DISABLE_LTO is this action's boolean configuration switch, but several
	# Android kernel trees use the same Make variable for compiler flags (for
	# example, "-fno-lto").  Leaving our value in the environment makes a
	# non-LTO build invoke `clang ... false ...`, treating "false" as an input
	# file.  prepare_defconfig() has already consumed the action setting, so let
	# Kbuild own the name from this point on.
	unset DISABLE_LTO

	# Custom manager signature, when the user builds their own manager APK.
	if [ -n "${KSU_EXPECTED_SIZE:-}" ] && [ -n "${KSU_EXPECTED_HASH:-}" ]; then
		export KSU_EXPECTED_SIZE KSU_EXPECTED_HASH
		info "using custom manager signature (size=${KSU_EXPECTED_SIZE})"
	fi

	local cc="clang" args

	# KCFLAGS is a standalone config value, exported by config.sh like every other
	# key, and re-exported here so it survives into the kbuild sub-makes.
	#
	# It cannot live in EXTRA_CMDS: $args is deliberately expanded unquoted so it
	# word-splits into separate make variables, so any KCFLAGS value containing
	# spaces would be torn into several bogus arguments. The previous
	# comma-joined form in EXTRA_CMDS was rejected wholesale by clang:
	#   warning: unknown -Werror warning specifier: '-Wno-error,-Wno-error=...'
	# which means DISABLE_CC_WERROR had never actually applied since run #43.
	# Makefile:1090 consumes it as a single value: KBUILD_CFLAGS += $(KCFLAGS)
	if [ -n "${KCFLAGS:-}" ]; then
		export KCFLAGS
		info "KCFLAGS: ${KCFLAGS}"
	else
		unset KCFLAGS || true
	fi

	args=$(make_args)
	if is_true "${ENABLE_CCACHE:-true}" && command -v ccache >/dev/null; then
		cc="ccache clang"
		export CCACHE_DIR="${CCACHE_DIR:-${WORKSPACE}/.ccache}"
		info "ccache enabled (dir: ${CCACHE_DIR})"
	fi

	cd "$KERNEL_DIR"
	info "make ${args} ${KERNEL_CONFIG}"
	# shellcheck disable=SC2086
	make -j"$(nproc --all)" CC=clang $args "${KERNEL_CONFIG}" \
		|| die "defconfig generation failed"

	info "make ${args}"
	# Diagnostic (run #70): dump this file again, right before the compiler
	# sees it. If it differs from the dump taken just after patching, something
	# is rewriting it in between and that is the bug. If it matches, then the
	# patch step itself lied and the earlier dump is the one to distrust.
	if [ -f "${KERNEL_DIR}/drivers/samsung/sec_hard_reset_hook.c" ]; then
		group "SEC_RESET: same region, pre-compile (diagnostic)"
		sed -n '225,240p' "${KERNEL_DIR}/drivers/samsung/sec_hard_reset_hook.c" |
			cat -A | sed 's/^/    /'
		endgroup
	fi
	# shellcheck disable=SC2086
	make -j"$(nproc --all)" CC="$cc" $args \
		|| die "kernel build failed"

	endgroup
}

# --------------------------------------------------------------- verify ---

check_output() {
	group "Checking build output"
	local boot="${OUT}/arch/${ARCH}/boot"
	local image="${boot}/${KERNEL_IMAGE_NAME}"

	[ -f "$image" ] || die "expected kernel image not found: ${image}
       Built files: $(ls "$boot" 2>/dev/null | tr '\n' ' ')
       Check that KERNEL_IMAGE_NAME matches what your kernel produces."

	ok "kernel image: ${KERNEL_IMAGE_NAME} ($(du -h "$image" | cut -f1))"
	export_env CHECK_FILE_IS_OK true

	if is_true "${NEED_DTBO:-false}"; then
		[ -f "${boot}/dtbo.img" ] || die "NEED_DTBO=true but ${boot}/dtbo.img was not produced"
		export_env CHECK_DTBO_IS_OK true
		ok "dtbo.img present"
	fi

	# KPM rewrites the image in place, so it has to happen after the build and
	# before packaging.
	if is_true "${ENABLE_KPM:-false}"; then
		kpm_patch_image "$image"
	fi

	# Record the version string the kernel actually reports.
	if [ -f "${OUT}/include/generated/utsrelease.h" ]; then
		local rel
		rel=$(sed -nE 's/.*UTS_RELEASE[[:space:]]+"([^"]+)".*/\1/p' "${OUT}/include/generated/utsrelease.h")
		export_env KERNEL_RELEASE "$rel"
		ok "kernel release: ${rel}"
		summary "| Kernel release | \`${rel}\` |"
	fi
	endgroup
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
	case "${1:-all}" in
		defconfig) prepare_defconfig ;;
		compile)   build_kernel ;;
		check)     check_output ;;
		all)       prepare_defconfig; build_kernel; check_output ;;
		*) die "unknown build step '$1'" ;;
	esac
fi
