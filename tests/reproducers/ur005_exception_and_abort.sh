#!/usr/bin/env bash
# Reproducer UR-005: nox_embed.h lacks exception inspection accessors and handle.zig calls std.process.exit(1).
set -euo pipefail

NOX_SRC="${NOX_SRC:-/Users/melihburakmemis/Documents/nox-lang}"

EMBED_H="$(git -C "$NOX_SRC" show v2.2.70:include/nox_embed.h)"
if echo "$EMBED_H" | grep -qE 'nox_exception_(message|class|line)'; then
    echo "[UR-005] UNEXPECTED: exception inspection API found in include/nox_embed.h"
    exit 1
fi
echo "[UR-005] Confirmed: include/nox_embed.h has no exception class/message/line inspection API"

HANDLE_ZIG="$(git -C "$NOX_SRC" show v2.2.70:runtime/errors/handle.zig)"
EXIT_LINES="$(echo "$HANDLE_ZIG" | grep -n 'std\.process\.exit(1)' || true)"
echo "[UR-005] runtime/errors/handle.zig std.process.exit(1) occurrences:"
echo "$EXIT_LINES"
[ -n "$EXIT_LINES" ] || exit 1

echo "[UR-005] PASS (blocker reproduced)"
