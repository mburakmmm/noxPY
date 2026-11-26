#!/usr/bin/env bash
# Contract & Reproducer UR-008: Phase 2 containers, Optional (`T | None`), tuples, and constant defaults
# (`NoxHostValue` ABI v2 + canonical `expand --json` surface types, resolved in `noxc v2.2.85–v2.2.87`).
set -euo pipefail

NOXC="${NOXC:-$(command -v noxc)}"
NOX_INCLUDE="${NOX_INCLUDE:-$(cd "$(dirname "$NOXC")/.." && pwd)/include}"
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT

cat << 'EOF' > "$TMPD/p2_probe.nox"
@export
def opt_int(x: int | None = None) -> int | None:
    return x

@export
def sum_list(xs: list[int]) -> list[int]:
    return xs

@export
def map_dict(d: dict[str, int]) -> dict[str, int]:
    return d

@export
def swap_pair(p: tuple[int, str], scale: float = 1.5) -> tuple[str, int]:
    return (p[1], p[0])
EOF

NOXC_VER="$("$NOXC" --version 2>&1 | tr -d '\n')"
echo "[UR-008] Using noxc: $NOXC ($NOXC_VER)"

EXPAND_OUT="$("$NOXC" expand --json --module p2_probe "$TMPD/p2_probe.nox")"

if [ -f "$NOX_INCLUDE/nox_host.h" ] && grep -Eq "NOX_HOST_ABI_VERSION [23]u" "$NOX_INCLUDE/nox_host.h"; then
    echo "$EXPAND_OUT" | grep -q '"tuple\[int, str\]"' || { echo "[UR-008] FAIL: missing canonical tuple[int, str]"; exit 1; }
    echo "$EXPAND_OUT" | grep -q '"marshal": "value"' || { echo "[UR-008] FAIL: missing marshal=value"; exit 1; }
    echo "$EXPAND_OUT" | grep -q '"default": {"type": "float", "value": 1.5}' || { echo "[UR-008] FAIL: missing constant default JSON"; exit 1; }
    # UR-014 (v2.2.93+): `b"..."` constant defaults are accepted and reported as hex.
    printf '@export\ndef framed(payload: bytes = b"\\x00\\x01ab") -> int:\n    return len(payload)\n' > "$TMPD/bytes_default.nox"
    if "$NOXC" expand --json --module bd "$TMPD/bytes_default.nox" 2>/dev/null | grep -q '"hex": "00016162"'; then
        echo "[UR-008] bytes default literal: OK (UR-014)"
    else
        echo "[UR-008] note: this noxc predates UR-014 (b\"...\" defaults not accepted yet)"
    fi
    "$NOXC" build --backend llvm --host-abi --module-name p2_probe --emit-obj -o "$TMPD/p2.o" "$TMPD/p2_probe.nox" >/dev/null
    "$NOXC" build --backend llvm --host-abi --module-name p2_probe --emit-shared-lib -o "$TMPD/libp2.dylib" "$TMPD/p2_probe.nox" >/dev/null 2>&1 || \
        "$NOXC" build --backend llvm --host-abi --module-name p2_probe --emit-shared-lib -o "$TMPD/libp2.so" "$TMPD/p2_probe.nox" >/dev/null
    echo "[UR-008] PASS (verified resolved on $NOXC_VER: ABI v2 NoxHostValue + canonical types + defaults)"
    exit 0
fi

# Legacy blocker path on pre-v2.2.86 compilers
if echo "$EXPAND_OUT" | grep -q '"tuple__int_str"'; then
    echo "[UR-008] Confirmed expand --json emits internal tuple spelling: tuple__int_str"
else
    echo "[UR-008] UNEXPECTED: tuple spelling changed in expand --json; inspect output:"
    echo "$EXPAND_OUT"
    exit 1
fi

set +e
BUILD_ERR="$("$NOXC" build --backend llvm --host-abi --module-name p2_probe --emit-obj -o "$TMPD/p2.o" "$TMPD/p2_probe.nox" 2>&1)"
RC=$?
set -e

if [ "$RC" -eq 0 ]; then
    echo "[UR-008] UNEXPECTED PASS: --host-abi compiled container/Optional exports! Re-evaluate UR-008!"
    exit 1
fi

echo "[UR-008] Confirmed --host-abi failure (exit=$RC): $BUILD_ERR"
echo "[UR-008] PASS (Phase 2 container/Optional Host ABI blocker reproduced)"
