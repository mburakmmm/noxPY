#!/usr/bin/env bash
# Reproducer UR-003: noxrt.o exports global T _nox_* symbols and dispatch_registry uses process-global mutable vars.
set -euo pipefail

NOXC="${NOXC:-$(command -v noxc)}"
NOXRT="${NOXRT:-$(cd "$(dirname "$NOXC")/.." && pwd)/lib/noxrt.o}"
NOX_SRC="${NOX_SRC:-/Users/melihburakmemis/Documents/nox-lang}"

if [ ! -f "$NOXRT" ]; then
    echo "[UR-003] ERROR: noxrt.o not found at $NOXRT"
    exit 1
fi

GLOBAL_COUNT="$(nm -g "$NOXRT" | grep -c ' T _nox_' || true)"
echo "[UR-003] Global 'T _nox_*' symbols in $NOXRT: $GLOBAL_COUNT"
if [ "$GLOBAL_COUNT" -eq 0 ]; then
    echo "[UR-003] UNEXPECTED: zero global _nox_* symbols found"
    exit 1
fi

if git -C "$NOX_SRC" rev-parse v2.2.70 >/dev/null 2>&1; then
    GLOBALS="$(git -C "$NOX_SRC" show v2.2.70:runtime/alloc/dispatch_registry.zig | grep -n '^var g_' || true)"
    echo "[UR-003] dispatch_registry.zig process-global vars:"
    echo "$GLOBALS"
    [ -n "$GLOBALS" ] || exit 1
fi

echo "[UR-003] PASS (blocker reproduced)"
