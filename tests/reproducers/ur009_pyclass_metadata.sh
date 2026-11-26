#!/usr/bin/env bash
# Contract & Blocker UR-009:
# 1) Confirms `noxc 2.2.87` already supports class instances via top-level @export functions (`marshal: "handle"`, `NoxHostHandle`).
# 2) Reproduces that first-class `@pyclass` classes & methods are not yet emitted by `noxc expand --json` (`compiler/main.zig`).
set -euo pipefail

NOXC="${NOXC:-$(command -v noxc)}"
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT

cat << 'EOF' > "$TMPD/cls_handle.nox"
class Counter:
    value: int

    def __init__(self, start: int) -> None:
        self.value = start

@export
def make_counter(start: int) -> Counter:
    return Counter(start)

@export
def counter_val(c: Counter) -> int:
    return c.value
EOF

cat << 'EOF' > "$TMPD/cls_pyclass.nox"
class Widget:
    value: int

    def __init__(self, start: int) -> None:
        self.value = start

    def get(self) -> int:
        return self.value
EOF

NOXC_VER="$("$NOXC" --version 2>&1 | tr -d '\n')"
echo "[UR-009] Using noxc: $NOXC ($NOXC_VER)"

HANDLE_JSON="$("$NOXC" expand --json --module cls_handle "$TMPD/cls_handle.nox")"
echo "$HANDLE_JSON" | grep -q '"return_marshal": "handle"' || { echo "[UR-009] FAIL: expected return_marshal=handle"; exit 1; }
echo "$HANDLE_JSON" | grep -q '"marshal": "handle"' || { echo "[UR-009] FAIL: expected param marshal=handle"; exit 1; }
echo "[UR-009] Verified top-level @export handle metadata present on $NOXC_VER"

PYCLASS_JSON="$("$NOXC" expand --json --module cls_pyclass "$TMPD/cls_pyclass.nox")"
if echo "$PYCLASS_JSON" | grep -q '"kind": "class"'; then
    echo "[UR-009] PASS: upstream now emits first-class class metadata in expand --json! Ready for Phase 3 Slice 3B auto-codegen."
    exit 0
fi

echo "$PYCLASS_JSON" | grep -q '"exports": \[' || { echo "[UR-009] UNEXPECTED expand JSON: $PYCLASS_JSON"; exit 1; }
echo "[UR-009] PASS (confirmed handle ABI ready + @pyclass class metadata not yet emitted by $NOXC_VER)"
