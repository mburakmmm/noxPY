#!/usr/bin/env bash
# Reproducer UR-006: no binary-safe UTF-8/bytes span or generation-checked NoxHostHandle in public headers.
set -euo pipefail

NOX_SRC="${NOX_SRC:-/Users/melihburakmemis/Documents/nox-lang}"

EMBED_H="$(git -C "$NOX_SRC" show v2.2.70:include/nox_embed.h)"
echo "$EMBED_H" | grep -q 'nox_str_from_cstr' || {
    echo "[UR-006] UNEXPECTED: nox_str_from_cstr missing from include/nox_embed.h"
    exit 1
}
if echo "$EMBED_H" | grep -qE 'nox_str_from_utf8|nox_str_view|nox_host_handle'; then
    echo "[UR-006] UNEXPECTED: binary-safe span or handle API found in include/nox_embed.h"
    exit 1
fi
echo "[UR-006] Confirmed: include/nox_embed.h only exposes NUL-terminated nox_str_from_cstr (no length-aware span or NoxHostHandle)"
echo "[UR-006] PASS (blocker reproduced)"
