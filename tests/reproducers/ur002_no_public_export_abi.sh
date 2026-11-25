#!/usr/bin/env bash
# Reproducer UR-002: no public host export ABI (include/nox_host.h missing; functions emit private hidden rt param).
set -euo pipefail

NOXC="${NOXC:-$(command -v noxc)}"
NOX_SRC="${NOX_SRC:-/Users/melihburakmemis/Documents/nox-lang}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

if git -C "$NOX_SRC" cat-file -e v2.2.70:include/nox_host.h 2>/dev/null; then
    echo "[UR-002] UNEXPECTED: include/nox_host.h exists in v2.2.70"
    exit 1
fi
echo "[UR-002] Confirmed: include/nox_host.h absent at v2.2.70"

"$NOXC" build --backend llvm -o "$TMPDIR_WORK/min_add" "$SCRIPT_DIR/min_add.nox" >/dev/null
LL_FILE="$TMPDIR_WORK/min_add.ll"
if [ ! -f "$LL_FILE" ]; then
    echo "[UR-002] ERROR: expected LLVM IR at $LL_FILE"
    exit 1
fi

SIG="$(grep -E '^define .*@nox_add\(' "$LL_FILE" || true)"
echo "[UR-002] Emitted LLVM signature: $SIG"
echo "$SIG" | grep -q 'i64 %rt' || {
    echo "[UR-002] UNEXPECTED: no private hidden %rt parameter in signature"
    exit 1
}

echo "[UR-002] PASS (blocker reproduced)"
