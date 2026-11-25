#!/usr/bin/env bash
# Reproducer UR-001: noxc 2.2.70 rejects --emit-obj / --emit-static-lib / --emit-shared-lib on desktop targets.
set -euo pipefail

NOXC="${NOXC:-$(command -v noxc)}"
NOX_SRC="${NOX_SRC:-/Users/melihburakmemis/Documents/nox-lang}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

NOXC_VER="$("$NOXC" --version 2>&1 | tr -d '\n')"
echo "[UR-001] Using noxc: $NOXC ($NOXC_VER)"

for flag in --emit-obj --emit-static-lib --emit-shared-lib; do
    set +e
    OUT="$("$NOXC" build --backend llvm "$flag" -o "$TMPDIR_WORK/out" "$SCRIPT_DIR/min_add.nox" 2>&1)"
    RC=$?
    set -e
    if [ "$RC" -eq 0 ]; then
        echo "[UR-001] UNEXPECTED PASS: $flag succeeded on desktop!"
        exit 1
    fi
    echo "[UR-001] Confirmed failure (exit=$RC) for $flag: $OUT"
done

# Also verify build.zig at v2.2.70 omits .pic = true on desktop noxrt_mod
if git -C "$NOX_SRC" rev-parse v2.2.70 >/dev/null 2>&1; then
    PIC_LINES="$(git -C "$NOX_SRC" show v2.2.70:build.zig | sed -n '900,970p' | grep -c '\.pic\s*=\s*true' || true)"
    echo "[UR-001] build.zig:900-970 (desktop noxrt_mod) .pic = true count: $PIC_LINES"
fi

echo "[UR-001] PASS (blocker reproduced)"
