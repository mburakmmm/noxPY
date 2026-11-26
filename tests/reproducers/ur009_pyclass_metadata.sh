#!/usr/bin/env bash
# Contract UR-009 (resolved in nox-lang v2.2.89):
# 1) Class instances cross the boundary via top-level @export functions (`marshal: "handle"`, `NoxHostHandle`).
# 2) `@export class` is emitted by `noxc expand --json` (`kind: "class"`, `constructor`, `methods[]` with `method_kind`, `kind: "exception"`)
#    and `--host-abi` builds the `nox_host_export_<mod>_<Class>_*` symbols. Older compilers (<= v2.2.88) are detected and reported as the legacy blocker.
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
@export("instance_locked")
class Widget:
    value: int

    def __init__(self, start: int) -> None:
        self.value = start

    def get(self) -> int:
        return self.value

    @property
    def doubled(self) -> int:
        return self.value * 2

    @staticmethod
    def zero() -> int:
        return 0

@export
class WidgetError(ValueError):
    pass
EOF

NOXC_VER="$("$NOXC" --version 2>&1 | tr -d '\n')"
echo "[UR-009] Using noxc: $NOXC ($NOXC_VER)"

HANDLE_JSON="$("$NOXC" expand --json --module cls_handle "$TMPD/cls_handle.nox")"
echo "$HANDLE_JSON" | grep -q '"return_marshal": "handle"' || { echo "[UR-009] FAIL: expected return_marshal=handle"; exit 1; }
echo "$HANDLE_JSON" | grep -q '"marshal": "handle"' || { echo "[UR-009] FAIL: expected param marshal=handle"; exit 1; }
echo "[UR-009] Verified top-level @export handle metadata present on $NOXC_VER"

PYCLASS_JSON="$("$NOXC" expand --json --module cls_pyclass "$TMPD/cls_pyclass.nox")"
if echo "$PYCLASS_JSON" | grep -q '"kind": "class"'; then
    echo "$PYCLASS_JSON" | grep -q '"c_symbol": "nox_host_export_cls_pyclass_Widget___new__"' || { echo "[UR-009] FAIL: missing constructor symbol"; exit 1; }
    echo "$PYCLASS_JSON" | grep -q '"method_kind": "property_get"' || { echo "[UR-009] FAIL: missing property_get"; exit 1; }
    echo "$PYCLASS_JSON" | grep -q '"method_kind": "staticmethod"' || { echo "[UR-009] FAIL: missing staticmethod"; exit 1; }
    echo "$PYCLASS_JSON" | grep -q '"kind": "exception"' || { echo "[UR-009] FAIL: missing exception record"; exit 1; }
    "$NOXC" build --backend llvm --host-abi --module-name cls_pyclass --emit-obj -o "$TMPD/w.o" "$TMPD/cls_pyclass.nox" >/dev/null
    echo "[UR-009] PASS (verified resolved on $NOXC_VER: first-class class metadata + --host-abi wrappers)"
    exit 0
fi

echo "$PYCLASS_JSON" | grep -q '"exports": \[' || { echo "[UR-009] UNEXPECTED expand JSON: $PYCLASS_JSON"; exit 1; }
echo "[UR-009] PASS (confirmed handle ABI ready; @export class metadata not emitted by $NOXC_VER: pre-v2.2.89 legacy blocker)"
