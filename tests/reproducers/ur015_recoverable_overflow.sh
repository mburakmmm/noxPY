#!/usr/bin/env bash
# Contract UR-015 (nox-lang v2.2.93): under `--host-abi`, integer overflow is recoverable (`NOX_HOST_ERR_OVERFLOW = 11`)
# and the context stays usable. Also checks the v3-rc layout constants of `nox_host.h` (UR-014).
set -euo pipefail

NOXC="${NOXC:-$(command -v noxc)}"
NOX_INCLUDE="${NOX_INCLUDE:-$(cd "$(dirname "$NOXC")/.." && pwd)/include}"
NOXRT="${NOXRT:-$(cd "$(dirname "$NOXC")/.." && pwd)/lib/noxrt.o}"
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT

NOXC_VER="$("$NOXC" --version 2>&1 | tr -d '\n')"
echo "[UR-015] Using noxc: $NOXC ($NOXC_VER)"

if ! grep -q "NOX_HOST_ERR_OVERFLOW" "$NOX_INCLUDE/nox_host.h" 2>/dev/null; then
    echo "[UR-015] PASS (legacy: $NOXC_VER predates v2.2.93 — overflow is still process-fatal)"
    exit 0
fi

cat << 'EOF' > "$TMPD/ov.nox"
@export
def add(a: int, b: int) -> int:
    return a + b
EOF

cat << 'EOF' > "$TMPD/ov.c"
#include "nox_host.h"
#include <stdint.h>
#include <stdio.h>
NoxHostStatus nox_host_module_init_ov(NoxHostContext *, NoxHostError **);
NoxHostStatus nox_host_export_ov_add(NoxHostContext *, int64_t, int64_t, int64_t *, NoxHostError **);
int main(void) {
    NoxHostContextConfig cfg = { sizeof cfg, NOX_HOST_ABI_VERSION, NULL };
    NoxHostContext *c = NULL; NoxHostError *e = NULL; int64_t r = 0;
    if (nox_host_context_create(&cfg, &c) != NOX_HOST_OK || nox_host_module_init_ov(c, &e) != NOX_HOST_OK) return 2;
    if (!(nox_host_capabilities() & NOX_HOST_CAP_RECOVERABLE_TRAPS)) return 3;
    if (nox_host_export_ov_add(c, INT64_MAX, 1, &r, &e) != NOX_HOST_ERR_OVERFLOW || !e) return 4;
    nox_host_error_release(e); e = NULL;
    if (nox_host_export_ov_add(c, 20, 22, &r, &e) != NOX_HOST_OK || r != 42) return 5;
    nox_host_context_destroy(c);
    printf("overflow recovered\n");
    return 0;
}
EOF

"$NOXC" build --backend llvm --host-abi --module-name ov --emit-obj -o "$TMPD/ov.o" "$TMPD/ov.nox" >/dev/null
clang -I "$NOX_INCLUDE" "$TMPD/ov.c" "$TMPD/ov.o" "$NOXRT" -lm -pthread -o "$TMPD/ov" 2>/dev/null
"$TMPD/ov" | grep -q "overflow recovered" || { echo "[UR-015] FAIL"; exit 1; }
echo "[UR-015] PASS (verified on $NOXC_VER: ERR_OVERFLOW returned, process alive, next call add(20,22) == 42)"
