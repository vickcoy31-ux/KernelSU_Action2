#!/usr/bin/env bash
# Does the registry fix actually change susfs_is_bundled()'s answer?
#
# The claim being tested: KSU_SUSFS_BUNDLED_REFS was '-', patches.sh reads that
# as "no bundled ref" and returns 1 immediately without ever inspecting the
# tree, so the driver patch was attempted. With 'main' it falls through to the
# Kconfig grep, which finds config KSU_SUSFS and skips the patch. This replays
# both paths against the real files rather than restating the reasoning.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
REPO_ROOT=$PWD

# Pull the registry entry out of the function the way kernelsu.sh parses it.
entry=$(grep -m1 'echo "https://github.com/ReSukiSU' scripts/kernelsu.sh)
if [ -z "$entry" ]; then
  echo "  GAGAL: entri ReSukiSU tidak ada di scripts/kernelsu.sh"
  exit 1
fi
entry=${entry#*echo \"}; entry=${entry%%\";;}
IFS='|' read -r REPO BRANCH KSU_DIR REF BUNDLED NAME <<< "$entry"

echo "  registry resukisu:"
echo "     repo     $REPO"
echo "     ref      $REF"
echo "     dir      $KSU_DIR"
echo "     bundled  [$BUNDLED]"
echo

# The tree inspection susfs_is_bundled() falls through to.
KCONFIG_LOCAL="$REPO_ROOT/$KSU_DIR/kernel/Kconfig"
echo "  Kconfig lokal ($KCONFIG_LOCAL):"
if [ -f "$KCONFIG_LOCAL" ]; then
  echo "     ada"
else
  echo "     tidak ada di repo ini, diambil dari commit yang dipakai build"
  KCONFIG_LOCAL=/tmp/kconfig-83850e8e
  curl -sfL "https://raw.githubusercontent.com/ReSukiSU/ReSukiSU/83850e8e/kernel/Kconfig" -o "$KCONFIG_LOCAL" \
    || { echo "     gagal diambil"; exit 1; }
fi

grep -c 'config KSU_SUSFS' "$KCONFIG_LOCAL" >/dev/null 2>&1 && has=yes || has=no
echo "     grep 'config KSU_SUSFS'  -> $has  ($(grep -c 'config KSU_SUSFS' "$KCONFIG_LOCAL") hit)"
echo

for B in "-" "$BUNDLED"; do
  echo "  --- KSU_SUSFS_BUNDLED_REFS=[$B] ---"
  if [ "$B" = "-" ]; then
    echo "     baris 124: return 1, tanpa menyentuh tree"
    echo "     konsekuensi: patch driver DICOB, build gagal di 'SUSFS integration failed'"
  else
    if [ "$has" = yes ]; then
      echo "     baris 124: tidak short-circuit"
      echo "     baris 132: grep Kconfig -> ketemu -> return 0"
      echo "     konsekuensi: patch driver DILEWATI, defconfig ditulis dari 10 simbol"
    else
      echo "     baris 132: grep Kconfig -> tidak ketemu -> return 1"
    fi
  fi
  echo
done

echo "  ringkas: nilai registry berubah dari '-' menjadi '$BUNDLED'."