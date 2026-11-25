#!/usr/bin/env bash
# Reproducer UR-004: no public foreign-thread attachment contract for shared/persistent RuntimeState.
set -euo pipefail

NOX_SRC="${NOX_SRC:-/Users/melihburakmemis/Documents/nox-lang}"

if git -C "$NOX_SRC" cat-file -e v2.2.70:include/nox_host.h 2>/dev/null; then
    if git -C "$NOX_SRC" show v2.2.70:include/nox_host.h | grep -q 'nox_host_thread_attach'; then
        echo "[UR-004] UNEXPECTED: nox_host_thread_attach exists at v2.2.70"
        exit 1
    fi
fi
echo "[UR-004] Confirmed: no public nox_host_thread_attach / detach API at v2.2.70"

ASAP_ZIG="$(git -C "$NOX_SRC" show v2.2.70:runtime/alloc/asap.zig)"
MAX_LINE="$(echo "$ASAP_ZIG" | grep -n 'MAX_POOL_WORKERS' | head -n 1 || true)"
CAP_LINE="$(echo "$ASAP_ZIG" | grep -n 'capacity: usize = 1' | head -n 1 || true)"
echo "[UR-004] runtime/alloc/asap.zig limits: $MAX_LINE | $CAP_LINE"
[ -n "$MAX_LINE" ] && [ -n "$CAP_LINE" ] || exit 1

FFI_MD="$(git -C "$NOX_SRC" show v2.2.70:docs/language/ffi.md)"
echo "$FFI_MD" | grep -q 'never from an OS thread the native library created itself' || {
    echo "[UR-004] UNEXPECTED: foreign OS thread restriction missing from docs/language/ffi.md"
    exit 1
}
echo "[UR-004] Confirmed: docs/language/ffi.md forbids foreign OS threads for stored callbacks"
echo "[UR-004] PASS (blocker reproduced)"
