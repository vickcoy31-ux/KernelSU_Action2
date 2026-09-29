#!/usr/bin/env bash
# Build a boot image that carries this build's kernel, for flashing with Odin.
#
# This is a different artefact from the AnyKernel3 zip, and the two are not
# interchangeable. AnyKernel3 needs a custom recovery to unpack, repack and
# write the partition, which this device does not have. Odin instead takes a
# whole partition image inside a Samsung firmware tar, so what it needs is a
# boot.img built from *this tablet's own* stock image with only the kernel
# replaced.
#
# The tool doing the repack is magiskboot, the same one Magisk uses. It is not
# published as a Linux binary by the community build releases (macOS, Windows,
# wasm and source only), so it is taken out of the Magisk APK instead, where
# Magisk ships it for every Android ABI: lib/<abi>/libmagiskboot.so. The
# x86_64 one is what a GitHub-hosted runner needs. It is an ELF executable, so
# unlike the Windows build there is no .exe suffix and it does need chmod +x.

set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

WORKSPACE=${WORKSPACE:-$(pwd)}
KERNEL_DIR=${KERNEL_DIR:-${WORKSPACE}/android-kernel}
ARCH=${ARCH:-arm64}
BOOT_OUT="${KERNEL_DIR}/out/arch/${ARCH}/boot"
OUT="${WORKSPACE}/odin"

# Every stage checks the magic bytes of the file it just produced. A repack
# that silently writes garbage still exits 0, and the failure would otherwise
# only show up as a tablet that does not boot, so each check is worth its line.
check_android_magic() {
	local f=$1 what=$2
	[ -s "$f" ] || die "${what} is missing or empty: ${f}"
	[ "$(head -c 8 "$f" | tr -d '\000')" = "ANDROID!" ] \
		|| die "${what} has no ANDROID! magic: ${f}"
}

# arm64 kernel images carry "ARM\x64" at offset 56. Anything else means the
# file is a compressed kernel, a gzip, or an image for another architecture.
check_arm64_image() {
	local f=$1
	[ -s "$f" ] || die "kernel image is missing or empty: ${f}"
	local magic
	magic=$(od -An -tx1 -j 56 -N 4 "$f" | tr -d ' \n')
	[ "$magic" = "41524d64" ] \
		|| die "not an arm64 kernel image (offset 56 = ${magic:-<none>}): ${f}"
}

# ------------------------------------------------------------- the kernel ---

# The kernel build leaves an uncompressed Image next to the compressed one
# that is actually uploaded, so prefer that and fall back to unpacking. The
# fallback is the interesting case: Image.gz is a gzip of the same Image, and
# magiskboot needs the kernel uncompressed, so the compressed artefact is
# enough on its own.
stage_kernel() {
	group "Staging the uncompressed kernel"
	mkdir -p "$OUT"

	local plain="${BOOT_OUT}/Image"
	if [ -s "$plain" ]; then
		cp "$plain" "${OUT}/Image"
		info "kernel: ${plain}"
	else
		local packed="${BOOT_OUT}/${KERNEL_IMAGE_NAME}"
		[ -s "$packed" ] || die "no kernel image at ${BOOT_OUT} (looked for Image and ${KERNEL_IMAGE_NAME})"
		info "no uncompressed Image; unpacking ${KERNEL_IMAGE_NAME}"
		case "$packed" in
			*.gz)  gzip -dc "$packed" > "${OUT}/Image" ;;
			*.lz4) lz4  -dc "$packed" > "${OUT}/Image" ;;
			*)     cp "$packed" "${OUT}/Image" ;;
		esac
	fi

	check_arm64_image "${OUT}/Image"
	ok "kernel staged: ${OUT}/Image ($(du -h "${OUT}/Image" | cut -f1))"
	endgroup
}

# ------------------------------------------------------------ magiskboot ---

stage_magiskboot() {
	group "Fetching magiskboot"
	local apk="${OUT}/magisk.apk" bin="${OUT}/magiskboot"
	local url=${MAGISK_APK_URL:?MAGISK_APK_URL required}

	fetch "$url" "$apk"
	# -p writes the single member to stdout, so nothing unpacks into the tree.
	unzip -p "$apk" lib/x86_64/libmagiskboot.so > "$bin" \
		|| die "lib/x86_64/libmagiskboot.so is not in ${url}"
	[ -s "$bin" ] || die "extracted magiskboot is empty"

	local magic
	magic=$(od -An -tx1 -N 4 "$bin" | tr -d ' \n')
	[ "$magic" = "7f454c46" ] || die "extracted magiskboot is not an ELF: ${magic:-<none>}"

	# No .exe on a Linux binary, and without this the runner refuses to exec it.
	chmod +x "$bin"

	ok "magiskboot ready ($(du -h "$bin" | cut -f1), ELF x86-64)"
	endgroup
}

# ------------------------------------------------------- stock boot image ---

stage_stock_boot() {
	group "Fetching the stock boot image"
	mkdir -p "$OUT"
	local lz4file="${OUT}/boot.img.lz4" boot="${OUT}/boot.img"

	local src=${ODIN_BOOT_SOURCE_URL:?ODIN_BOOT_SOURCE_URL required}
	fetch "$src" "$lz4file"

	# This is the check that matters most. Repacking a stock image taken from a
	# different firmware -- or a different device -- is how a tablet ends up in
	# a boot loop, and both files are called "boot.img" and both are valid
	# boot images, so nothing downstream can tell them apart. Pin the exact
	# firmware this profile is built against.
	if [ -n "${ODIN_BOOT_SOURCE_SHA256:-}" ]; then
		local got
		got=$(sha256sum "$lz4file" | cut -d' ' -f1)
		if [ "$got" != "${ODIN_BOOT_SOURCE_SHA256}" ]; then
			die "stock boot image does not match the pinned firmware.
    expected ${ODIN_BOOT_SOURCE_SHA256}
    got      ${got}
    Refusing to repack: this is not the firmware this build is pinned to."
		fi
		ok "stock boot image matches the pinned firmware"
	fi

	lz4 -d -f "$lz4file" "$boot" || die "lz4 could not decompress the stock boot image"
	check_android_magic "$boot" "decompressed stock boot image"
	ok "stock boot image ready ($(du -h "$boot" | cut -f1))"
	endgroup
}

# ----------------------------------------------------------------- repack ---

repack() {
	group "Repacking the boot image"
	cd "$OUT"

	# unpack writes the components into the current directory, and repack reads
	# them back from there, so both have to run in $OUT.
	./magiskboot unpack boot.img || die "magiskboot unpack failed"
	[ -s "${OUT}/kernel" ] || die "magiskboot unpack produced no kernel file"
	info "stock kernel: $(du -h "${OUT}/kernel" | cut -f1)"

	mv -f "${OUT}/Image" "${OUT}/kernel"
	info "new kernel:  $(du -h "${OUT}/kernel" | cut -f1)"

	./magiskboot repack boot.img || die "magiskboot repack failed"
	[ -s "${OUT}/new-boot.img" ] || die "magiskboot repack produced no new-boot.img"
	check_android_magic "${OUT}/new-boot.img" "repacked boot image"

	ok "new-boot.img built ($(du -h "${OUT}/new-boot.img" | cut -f1))"
	endgroup

	cp "${OUT}/new-boot.img" "${WORKSPACE}/new-boot.img"
	export_env ODIN_BOOT_IMAGE_IS_OK true
}

write_summary() {
	summary ""
	summary "### Odin boot image"
	summary ""
	summary "| File | Size |"
	summary "| --- | --- |"
	summary "| \`new-boot.img\` | $(du -h "${WORKSPACE}/new-boot.img" | cut -f1) |"
	summary ""
	summary "Built from the stock boot image pinned in \`config.env\`, with only"
	summary "the kernel replaced. It is not compressed: the stock firmware ships"
	summary "\`boot.img.lz4\`, so compressing this again is a deliberate step and"
	summary "not an assumption this script makes."
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
	rm -rf "$OUT"
	mkdir -p "$OUT"
	stage_kernel
	stage_magiskboot
	stage_stock_boot
	repack
	write_summary
	ok "done"
fi
