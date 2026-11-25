#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export NOXC="${NOXC:-$(command -v noxc)}"
export NOXRT="${NOXRT:-$(cd "$(dirname "$NOXC")/.." && pwd)/lib/noxrt.o}"

NOXC_VER="$("$NOXC" --version 2>&1 | tr -d '\n')"
echo "=== Running NoxPy Upstream Blocker Reproducers ==="
echo "NOXC=$NOXC ($NOXC_VER)"
echo "NOXRT=$NOXRT"
echo

for script in \
    "$SCRIPT_DIR/ur001_desktop_emit_obj.sh" \
    "$SCRIPT_DIR/ur002_no_public_export_abi.sh" \
    "$SCRIPT_DIR/ur003_noxrt_symbol_visibility.sh" \
    "$SCRIPT_DIR/ur004_foreign_thread_contract.sh" \
    "$SCRIPT_DIR/ur005_exception_and_abort.sh" \
    "$SCRIPT_DIR/ur006_binary_safe_span_and_handles.sh" \
    "$SCRIPT_DIR/ur007_expand_no_typed_ir.sh"; do
    bash "$script"
    echo
done

echo "=== All 7 executable blocker reproducers confirmed on $NOXC_VER ==="
