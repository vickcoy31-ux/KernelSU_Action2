#!/usr/bin/env bash
# Resolve the build configuration, validate it, and export it to later steps.
#
# Resolution order (last wins):
#   1. built-in defaults below
#   2. the config file named by CONFIG_ENV (default: config.env)
#   3. workflow_dispatch inputs, passed in as IN_<KEY> environment variables
#
# The old workflow parsed config.env with
#     grep -w "$KEY" config.env | head -n1 | cut -d= -f2
# which truncates any value containing '=', matches commented-out lines, and
# matches a key that merely appears as a substring of a comment. That is why
# EXTRA_CMDS had to use a ':' separator. Both forms are still accepted here,
# but parsing is now anchored and comment-aware, so '=' in values is fine.

set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CONFIG_FILE=${CONFIG_ENV:-config.env}

# --------------------------------------------------------------- defaults ---

declare -A DEFAULTS=(
	[KERNEL_SOURCE]=""
	[KERNEL_SOURCE_BRANCH]=""
	[KERNEL_CONFIG]=""
	[KERNEL_IMAGE_NAME]="Image.gz-dtb"
	[ARCH]="arm64"
	[KERNEL_NAME]=""
	[KERNEL_LOCALVERSION]=""
	[NO_TREE_PATCHES]="false"
	[PATCH_WHITELIST]=""
	[ADD_LOCALVERSION_TO_FILENAME]="false"
	[EXTRA_CMDS]=""
	[CUSTOM_CMDS]=""
	[KCFLAGS]=""

	# Toolchain
	[USE_CUSTOM_CLANG]="false"
	[CUSTOM_CLANG_SOURCE]=""
	[CUSTOM_CLANG_BRANCH]=""
	[CLANG_BRANCH]="main-kernel-2025"
	[CLANG_VERSION]="r547379"
	[USE_LLVM]="false"
	[ENABLE_GCC_ARM64]="false"
	[ENABLE_GCC_ARM32]="false"
	[USE_CUSTOM_GCC_64]="false"
	[CUSTOM_GCC_64_SOURCE]=""
	[CUSTOM_GCC_64_BRANCH]=""
	[CUSTOM_GCC_64_BIN]="aarch64-linux-android-"
	[USE_CUSTOM_GCC_32]="false"
	[CUSTOM_GCC_32_SOURCE]=""
	[CUSTOM_GCC_32_BRANCH]=""
	[CUSTOM_GCC_32_BIN]="arm-linux-androideabi-"

	# KernelSU
	[KSU_VARIANT]="none"
	[KSU_REF]=""
	[KSU_HOOK_MODE]="auto"
	[KSU_EXPECTED_SIZE]=""
	[KSU_EXPECTED_HASH]=""

	# Patches
	[ENABLE_SUSFS]="false"
	[SUSFS_REPO]="https://gitlab.com/simonpunk/susfs4ksu.git"
	[SUSFS_BRANCH]="auto"
	[ENABLE_PATH_UMOUNT]="false"
	[ENABLE_HIDE_STUFF]="false"
	[ENABLE_KPM]="false"

	# Kconfig tweaks
	[ADD_KPROBES_CONFIG]="false"
	[ADD_OVERLAYFS_CONFIG]="false"
	[DISABLE_LTO]="false"
	[DISABLE_CC_WERROR]="false"
	[REGULATOR_TRACE_PROBE]="false"
	[EXTRA_DEFCONFIG]=""

	# Packaging
	[USE_CUSTOM_ANYKERNEL3]="false"
	[CUSTOM_ANYKERNEL3_SOURCE]=""
	[CUSTOM_ANYKERNEL3_BRANCH]=""
	[NEED_DTBO]="false"
	[BUILD_BOOT_IMG]="false"
	[SOURCE_BOOT_IMAGE]=""

	# Odin: repack the stock boot image with this build's kernel
	[BUILD_ODIN_BOOT_IMG]="false"
	[ODIN_BOOT_SOURCE_URL]=""
	[ODIN_BOOT_SOURCE_SHA256]=""
	[MAGISK_APK_URL]=""

	# Runner
	[ENABLE_CCACHE]="true"
	[REMOVE_UNUSED_PACKAGES]="true"
)

# Legacy spellings that must keep working for existing forks' config.env files.
declare -A ALIASES=(
	[DISABLE-LTO]="DISABLE_LTO"
	[KERNELSU_TAG]="KSU_REF"
	[APPLY_KSU_PATCH]="_LEGACY_APPLY_KSU_PATCH"
	[ENABLE_KERNELSU]="_LEGACY_ENABLE_KERNELSU"
)

# ----------------------------------------------------------------- parsing ---

# cfg_read FILE KEY -- first non-comment "KEY=value" or "KEY:value" line.
cfg_read() {
	local file=$1 key=$2
	[ -f "$file" ] || return 0
	sed -nE "s/\r$//; s/^[[:space:]]*${key}[[:space:]]*[=:][[:space:]]*(.*)$/\1/p" "$file" \
		| head -n1 \
		| sed -E 's/[[:space:]]+$//'
}

report_unknown_keys() {
	local file=${1:-$CONFIG_FILE}
	[ -f "$file" ] || return 0

	local key unknown=0
	# Only assignment lines count. Comments and blank lines are the majority of
	# these files, and a key mentioned inside a comment is not being set.
	while IFS= read -r key; do
		case "$key" in
		''|\#*) continue ;;
		esac
		# Leading whitespace before KEY= is accepted by cfg_read, so accept it
		# here too rather than reporting a key that is in fact being read.
		key=${key%%=*}
		key=$(printf '%s' "$key" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')
		[ -n "$key" ] || continue
		# CONFIG_ENV is a real key of this system, but it is read from the
		# environment or the workflow input, before DEFAULTS is walked, and it
		# names the file being read -- so a `CONFIG_ENV=...` line inside that
		# file does nothing. config.env carries one. It is accepted rather than
		# reported so the warning stays about keys nothing reads at all.
		if [ -z "${DEFAULTS[$key]+set}" ] && [ "$key" != "CONFIG_ENV" ]; then
			warn "config: '${key}' is set in ${file} but is not a known key -- it will be ignored"
			unknown=$((unknown + 1))
		fi
	done < <(grep -E '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' "$file" 2>/dev/null || true)

	if [ "$unknown" -gt 0 ]; then
		warn "config: ${unknown} key(s) in ${file} are not read by anything."
		warn "config: A key that is misspelled, or that a script expects but this file"
		warn "config: does not declare, produces a build that succeeds and is wrong."
	fi
	return 0
}

resolve() {	local key val
	for key in "${!DEFAULTS[@]}"; do
		val=${DEFAULTS[$key]}

		local from_file
		from_file=$(cfg_read "$CONFIG_FILE" "$key")
		[ -n "$from_file" ] && val=$from_file

		# Workflow inputs win over the file, but only when actually provided.
		#
		# The sentinel "config" means "leave the config file's value alone".
		# The dispatch form needs it because a GitHub boolean input always has
		# a concrete value: with a plain checkbox defaulting to false, a user
		# who set ENABLE_SUSFS=true in their profile and then ran the form
		# without touching anything would silently get SUSFS turned back off.
		local in_var="IN_${key}"
		local in_val=${!in_var:-}
		if [ -n "$in_val" ] && [ "$in_val" != "config" ]; then
			val=$in_val
		fi

		CFG[$key]=$val
	done

	# Fold legacy keys in only where the modern key was not already set.
	local legacy modern
	for legacy in "${!ALIASES[@]}"; do
		modern=${ALIASES[$legacy]}
		local lv
		lv=$(cfg_read "$CONFIG_FILE" "$legacy")
		[ -n "$lv" ] || continue
		case "$modern" in
			_LEGACY_*) CFG[$modern]=$lv ;;
			*)
				# Only honour the legacy spelling when the modern one is absent
				# from the file and no input overrode it.
				local mv in_var="IN_${modern}"
				mv=$(cfg_read "$CONFIG_FILE" "$modern")
				if [ -z "$mv" ] && [ -z "${!in_var:-}" ]; then
					CFG[$modern]=$lv
					debug "legacy key ${legacy} -> ${modern}=${lv}"
				fi
				;;
		esac
	done
}

# --------------------------------------------- legacy compatibility bridge ---

# Old config.env used ENABLE_KERNELSU=true plus KERNELSU_TAG to mean
# "install tiann/KernelSU". Translate that into the new variant selector so
# existing forks keep building without editing anything.
apply_legacy_bridge() {
	local legacy_enable=${CFG[_LEGACY_ENABLE_KERNELSU]:-}
	local legacy_patch=${CFG[_LEGACY_APPLY_KSU_PATCH]:-}

	if [ "${CFG[KSU_VARIANT]}" = "none" ] && is_true "$legacy_enable"; then
		CFG[KSU_VARIANT]="kernelsu"
		warn "config.env uses the legacy ENABLE_KERNELSU flag; treating it as KSU_VARIANT=kernelsu."
		warn "Set KSU_VARIANT explicitly to pick a fork (kernelsu-next, sukisu-ultra, resukisu, ...)."
	fi

	# APPLY_KSU_PATCH used to mean "run the bundled sed script to add manual
	# hooks". That is now the 'manual' hook mode.
	if is_true "$legacy_patch" && [ "${CFG[KSU_HOOK_MODE]}" = "auto" ]; then
		CFG[KSU_HOOK_MODE]="manual"
		warn "config.env uses the legacy APPLY_KSU_PATCH flag; treating it as KSU_HOOK_MODE=manual."
	fi

	unset 'CFG[_LEGACY_ENABLE_KERNELSU]' 'CFG[_LEGACY_APPLY_KSU_PATCH]'
}

# -------------------------------------------------------------- validation ---

validate() {
	local errors=0
	_err() { warn "config: $*"; errors=$((errors + 1)); }

	[ -n "${CFG[KERNEL_SOURCE]}" ]        || _err "KERNEL_SOURCE is required"
	[ -n "${CFG[KERNEL_SOURCE_BRANCH]}" ] || _err "KERNEL_SOURCE_BRANCH is required"
	[ -n "${CFG[KERNEL_CONFIG]}" ]        || _err "KERNEL_CONFIG is required"
	[ -n "${CFG[KERNEL_IMAGE_NAME]}" ]    || _err "KERNEL_IMAGE_NAME is required"

	case "${CFG[ARCH]}" in
		arm64 | arm | x86_64 | riscv) ;;
		*) _err "ARCH must be one of arm64/arm/x86_64/riscv (got '${CFG[ARCH]}')" ;;
	esac

	case "${CFG[KSU_VARIANT]}" in
		none | kernelsu | kernelsu-next | sukisu-ultra | resukisu | rsuntk | backslashxx) ;;
		*) _err "unknown KSU_VARIANT '${CFG[KSU_VARIANT]}'" ;;
	esac

	case "${CFG[KSU_HOOK_MODE]}" in
		auto | kprobes | manual | tracepoint | syscall | none) ;;
		*) _err "unknown KSU_HOOK_MODE '${CFG[KSU_HOOK_MODE]}'" ;;
	esac

	if [ "${CFG[KSU_VARIANT]}" = "none" ]; then
		is_true "${CFG[ENABLE_SUSFS]}" &&
			_err "ENABLE_SUSFS requires a KSU_VARIANT other than 'none'"
		is_true "${CFG[ENABLE_KPM]}" &&
			_err "ENABLE_KPM requires KSU_VARIANT=sukisu-ultra"
	fi

	# KPM is a SukiSU-Ultra feature and its patch_linux tool is 64-bit only.
	if is_true "${CFG[ENABLE_KPM]}"; then
		[ "${CFG[KSU_VARIANT]}" = "sukisu-ultra" ] ||
			_err "ENABLE_KPM is only supported with KSU_VARIANT=sukisu-ultra (got '${CFG[KSU_VARIANT]}')"
		[ "${CFG[ARCH]}" = "arm64" ] ||
			_err "ENABLE_KPM requires ARCH=arm64"
	fi

	if is_true "${CFG[BUILD_BOOT_IMG]}" && [ -z "${CFG[SOURCE_BOOT_IMAGE]}" ]; then
		_err "BUILD_BOOT_IMG=true requires SOURCE_BOOT_IMAGE"
	fi

	# A repack is only safe against the exact firmware it was verified on, so
	# the source image and its checksum are both required, not merely advised.
	if is_true "${CFG[BUILD_ODIN_BOOT_IMG]}"; then
		[ -n "${CFG[ODIN_BOOT_SOURCE_URL]}" ] ||
			_err "BUILD_ODIN_BOOT_IMG=true requires ODIN_BOOT_SOURCE_URL"
		[ -n "${CFG[ODIN_BOOT_SOURCE_SHA256]}" ] ||
			_err "BUILD_ODIN_BOOT_IMG=true requires ODIN_BOOT_SOURCE_SHA256 (sha256 of that image)"
		[ -n "${CFG[MAGISK_APK_URL]}" ] ||
			_err "BUILD_ODIN_BOOT_IMG=true requires MAGISK_APK_URL (magiskboot is taken from the APK)"
	fi

	if is_true "${CFG[USE_CUSTOM_CLANG]}" && [ -z "${CFG[CUSTOM_CLANG_SOURCE]}" ]; then
		_err "USE_CUSTOM_CLANG=true requires CUSTOM_CLANG_SOURCE"
	fi

	if is_true "${CFG[USE_CUSTOM_ANYKERNEL3]}" && [ -z "${CFG[CUSTOM_ANYKERNEL3_SOURCE]}" ]; then
		_err "USE_CUSTOM_ANYKERNEL3=true requires CUSTOM_ANYKERNEL3_SOURCE"
	fi

	[ "$errors" -eq 0 ] || die "${errors} configuration error(s); fix ${CONFIG_FILE} or the workflow inputs"
}

# ------------------------------------------------------------------- main ---

declare -A CFG
resolve
apply_legacy_bridge
validate

# ------------------------------------------------------------ unknown keys ---
# resolve() walks DEFAULTS and nothing else, so a key the config file sets but
# DEFAULTS does not declare is dropped without a word. That cost run 36723484934
# a full 55-minute build: config/gta9_vermagic.env set KERNEL_LOCALVERSION,
# build.sh knew what to do with it, and the variable never reached the runner
# because no one had told config.sh the key existed. The kernel built clean and
# still carried the wrong release string, which on this device means no module
# will load -- and the only symptom was a value that had silently reverted.
#
# A profile is a list of settings nobody re-reads after writing it, and a
# misspelled or unregistered key there is indistinguishable from a typo in a
# shell script except that it costs an hour. So name every one of them.
report_unknown_keys

# Derive the device label from the defconfig name, as before.
DEVICE=$(printf '%s' "${CFG[KERNEL_CONFIG]}" | sed 's!.*/!!; s/_defconfig$//; s/_user$//; s/-perf$//')
[ -n "${CFG[KERNEL_NAME]}" ] && DEVICE=${CFG[KERNEL_NAME]}
CFG[DEVICE]=$DEVICE

summary "### Build configuration"
summary ""
summary "| Item | Value |"
summary "| --- | --- |"

group "Resolved configuration"
for key in $(printf '%s\n' "${!CFG[@]}" | sort); do
	printf '  %-32s = %s\n' "$key" "${CFG[$key]}"
	export_env "$key" "${CFG[$key]}"
done
endgroup

export_env BUILD_TIME "$(TZ=${BUILD_TZ:-Asia/Shanghai} date '+%Y%m%d%H%M')"

ok "configuration resolved (device: ${DEVICE})"
