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

	# 5. IMGSENSOR frame-sync (run #55/#56): the legacy src/ tree builds its
	#    own frame_monitor.o + frame_sync_algo.o via
	#    src/common/v1_1/n3d_fsync/Makefile, and src-v4l2/frame-sync builds the
	#    same-named objects again -> every frm_*/fs_*/FrameSync* symbol collides
	#    under lld. GTA9 uses the v4l2 path, so drop the n3d_fsync include from
	#    src/isp6s/Makefile; the rest of the legacy imgsensor tree stays intact.
	local n3d_mk="${KERNEL_DIR}/drivers/misc/mediatek/imgsensor/src/isp6s/Makefile"
	if [ -f "$n3d_mk" ] && grep -q "n3d_fsync/Makefile" "$n3d_mk"; then
		sed -i -E '\#include .*n3d_fsync/Makefile#d' "$n3d_mk"
		info "IMGSENSOR: dropped n3d_fsync frame-sync include (GTA9 uses src-v4l2)"
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
