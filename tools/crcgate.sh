#!/usr/bin/env bash
# Wait for a run to finish, pull Module.symvers, and check CRC against the
# tablet's own 202 vendor modules.
#
# The second bootloop was found this way and not guessed at: 155 of those modules
# import __stack_chk_guard, and when it was absent insmod refused every one of
# them, cqhci went with them, and init died with no storage and nowhere to log
# it. So the CRC check is the gate that decides whether a flash is worth trying,
# and it can be answered from the build alone, without the tablet.
#
# The SUSFS patch rewrites prototypes in 24 kernel files, so it can shift a CRC
# on its own. That has never been checked for a SUSFS build.
set -uo pipefail

REPO=vickcoy31-ux/KernelSU_Action2
RUN=${1:-}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRCS=${2:-/c/Users/User/bootwork/gta9/vendor-crcs.tsv}
OUT=${3:-/c/Users/User/bootwork/gta9/run-$RUN}

[ -n "$RUN" ] || { echo "  usage: crcgate.sh <run-id> [vendor-crcs.tsv] [outdir]"; exit 2; }

echo "  run     : $RUN"
echo "  vendor  : $CRCS"
[ -f "$CRCS" ] || { echo "  vendor-crcs.tsv tidak ada"; exit 2; }
echo "  simbol  : $(wc -l < "$CRCS")"
echo

echo "=== menunggu run selesai ==="
while :; do
  json=$(gh run view "$RUN" --repo "$REPO" --json status,conclusion 2>/dev/null) || {
    sleep 30; continue; }
  status=$(printf '%s' "$json" | sed -nE 's/.*"status":"([^"]*)".*/\1/p')
  concl=$(printf '%s' "$json" | sed -nE 's/.*"conclusion":"([^"]*)".*/\1/p')
  echo "  $(date +%H:%M:%S)  $status ${concl:-$concl}"
  [ "$status" = "completed" ] && break
  sleep 60
done
echo "  selesai: $concl"
echo

if [ "$concl" != "success" ]; then
  echo "  run gagal. undefined symbols yang dilaporkan:"
  gh run view "$RUN" --repo "$REPO" --log-failed 2>/dev/null \
    | grep -oE 'undefined symbol: \S+' | awk '{print $3}' | sort -u | sed 's/^/    /'
  exit 1
fi

mkdir -p "$OUT"
echo "=== mengunduh artefak ==="
gh run download "$RUN" --repo "$REPO" --dir "$OUT" 2>&1 | sed 's/^/  /'
SYMV=$(find "$OUT" -name 'Module.symvers' | head -1)
echo
if [ -z "$SYMV" ]; then
  echo "  Module.symvers tidak ada di artefak:"
  find "$OUT" -type f | head -20 | sed 's/^/    /'
  exit 1
fi
echo "  Module.symvers : $SYMV  ($(wc -l < "$SYMV") simbol)"
echo

echo "=== crccheck ==="
python3 "$HERE/scripts/crccheck.py" compare "$CRCS" "$SYMV" "$OUT/crc-report.txt" 2>&1 | sed 's/^/  /'
rc=$?
echo
echo "  report: $OUT/crc-report.txt"
exit $rc