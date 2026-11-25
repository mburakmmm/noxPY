#!/usr/bin/env bash
# Reproducer UR-007: noxc expand prints unstructured text without resolved parameter/return types or JSON.
set -euo pipefail

NOXC="${NOXC:-$(command -v noxc)}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

set +e
JSON_OUT="$("$NOXC" expand --json "$SCRIPT_DIR/min_add.nox" 2>&1)"
JSON_RC=$?
set -e
if [ "$JSON_RC" -eq 0 ]; then
    echo "[UR-007] UNEXPECTED: noxc expand --json succeeded: $JSON_OUT"
    exit 1
fi
echo "[UR-007] Confirmed: noxc expand --json unsupported (exit=$JSON_RC)"

EXPAND_OUT="$("$NOXC" expand "$SCRIPT_DIR/min_add.nox" 2>&1)"
echo "[UR-007] noxc expand output:"
echo "$EXPAND_OUT"

if echo "$EXPAND_OUT" | grep -qE '"schema_version"|-> int'; then
    echo "[UR-007] UNEXPECTED: typed signature or JSON schema found in expand output"
    exit 1
fi

echo "[UR-007] PASS (blocker reproduced)"
