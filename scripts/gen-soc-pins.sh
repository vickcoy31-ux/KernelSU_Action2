#!/usr/bin/env bash
# Report what gen-soc-pins.sh would do, and refuse the answer.
#
# The script's premise was wrong and this exists to say so in the repo rather
# than only in a commit message. The tablet's config turns on 191 =m options
# that name a SoC other than MT6789, and it looks like pinning all of them is
# the fix for run #107's duplicate symbols. It is not, for three separate
# reasons, and the third is the one that matters.
set -uo pipefail
REPO="/c/Users/User/Documents/GitHub/KernelSU_Action"
DEF="$REPO/config/gta9_stock_defconfig"
MINE=6789
cd "$REPO"

echo "== symbols that name a SoC but are NOT per-SoC drivers =="
echo "A SoC number in a symbol name does not mean the symbol belongs to that"
echo "SoC. These are shared components that happen to be named after a chip:"
echo
for s in MT6366 MT6375 MT6359P MT6397 MT6338 MT6360 MT6373 MT6660; do
  hits=$(grep -E "^CONFIG_[A-Z0-9_]*${s}[A-Z0-9_]*=m$" "$DEF" | head -4)
  if [ -n "$hits" ]; then
    echo "  ${s}:"
    echo "$hits" | sed 's/^/    /'
  fi
done

echo
echo "== what this device actually needs from those =="
echo "MT6789 is paired with the MT6366 PMIC, and the SM-X110 board uses the"
echo "MT6375 charger and fuel gauge. Pinning CONFIG_TCPC_MT6375,"
echo "CONFIG_CHARGER_MT6375 or CONFIG_MFD_MT6375 to n does not remove a"
echo "duplicate symbol; it stops the tablet charging."

echo
echo "== what run #107 actually needed =="
echo "14 duplicate symbols, from two places:"
echo "  cm_mgr  : mtk_cm_mgr_{mt6789,mt6855,mt6879,mt6893,mt6895}.o all built"
echo "  vdec    : the per-SoC COMMON_CLK_*_VCODEC and _VDECSYS"
echo "That is 4 CM_MGR pins and 5 clock pins. Nothing else."

echo
echo "== verdict =="
echo "Do not use gen-soc-pins.sh. The hand-written pins in config.env, which"
echo "cover only the 33 options that are genuinely per-SoC, are correct. This"
echo "script is kept because the reasoning behind rejecting it is worth more"
echo "than the script would have been."
