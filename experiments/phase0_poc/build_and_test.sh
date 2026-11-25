#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
POC_DIR="$ROOT/experiments/phase0_poc"
BUILD_DIR="$POC_DIR/build"

NOXC="${NOXC:-$(command -v noxc)}"
NOXRT="${NOXRT:-$(cd "$(dirname "$NOXC")/.." && pwd)/lib/noxrt.o}"
PYTHON315="${PYTHON315:-/opt/homebrew/bin/python3.15}"

if [ ! -x "$NOXC" ]; then
    echo "ERROR: noxc not found at $NOXC" >&2
    exit 1
fi
if [ ! -f "$NOXRT" ]; then
    echo "ERROR: noxrt.o not found at $NOXRT" >&2
    exit 1
fi
if [ ! -x "$PYTHON315" ]; then
    echo "ERROR: python3.15 not found at $PYTHON315" >&2
    exit 1
fi

PY_INC="$("$PYTHON315" -c "import sysconfig; print(sysconfig.get_path('include'))")"
NOXC_VER="$("$NOXC" --version 2>&1 | tr -d '\n')"

echo "=== NoxPy Phase 0 Verification Build (upstream nox_host.h v1, no --selfhost-obj) ==="
echo "NOXC=$NOXC ($NOXC_VER)"
echo "NOXRT=$NOXRT"
echo "PYTHON315=$PYTHON315 ($("$PYTHON315" --version 2>&1))"
echo "PY_INC=$PY_INC"

mkdir -p "$BUILD_DIR"

# 1. Build two standalone host-ABI shared libraries for C host dlopen/dlsym verification
"$NOXC" build --backend llvm --host-abi --module-name poc_a --emit-shared-lib -o "$BUILD_DIR/libpoc_a.dylib" "$POC_DIR/math_mod.nox"
"$NOXC" build --backend llvm --host-abi --module-name poc_b --emit-shared-lib -o "$BUILD_DIR/libpoc_b.dylib" "$POC_DIR/mod_b.nox"

# 2. Build relocatable PIC object files with --host-abi for CPython 3.15 abi3t extensions
"$NOXC" build --backend llvm --host-abi --module-name _noxpy_poc --emit-obj -o "$BUILD_DIR/math_mod.o" "$POC_DIR/math_mod.nox"
"$NOXC" build --backend llvm --host-abi --module-name _noxpy_poc_b --emit-obj -o "$BUILD_DIR/mod_b.o" "$POC_DIR/mod_b.nox"

CFLAGS_COMMON=(-std=c11 -O2 -Wall -Wextra -Werror -fPIC -fvisibility=hidden "-I$ROOT/native/include")

# 3. Compile audited CPython abi3t shim & standalone C host harness (includes ONLY nox_host.h)
clang "${CFLAGS_COMMON[@]}" "-I$PY_INC" -c "$ROOT/native/cpython/noxpy_shim.c" -o "$BUILD_DIR/noxpy_shim.o"
clang "${CFLAGS_COMMON[@]}" "$POC_DIR/c_host_test.c" -o "$BUILD_DIR/c_host_test"

# 4. Build two independent CPython 3.15 abi3t extension bundles (ONLY PyModExport_* exported)
clang "${CFLAGS_COMMON[@]}" "-I$PY_INC" -c "$POC_DIR/py_poc_a.c" -o "$BUILD_DIR/py_poc_a.o"
clang "${CFLAGS_COMMON[@]}" "-I$PY_INC" -c "$POC_DIR/py_poc_b.c" -o "$BUILD_DIR/py_poc_b.o"

clang -bundle -undefined dynamic_lookup -Wl,-dead_strip -Wl,-exported_symbols_list,"$POC_DIR/exports_py_a.txt" \
    -o "$BUILD_DIR/_noxpy_poc.abi3t.so" \
    "$BUILD_DIR/py_poc_a.o" "$BUILD_DIR/noxpy_shim.o" "$BUILD_DIR/math_mod.o" "$NOXRT" -lm

clang -bundle -undefined dynamic_lookup -Wl,-dead_strip -Wl,-exported_symbols_list,"$POC_DIR/exports_py_b.txt" \
    -o "$BUILD_DIR/_noxpy_poc_b.abi3t.so" \
    "$BUILD_DIR/py_poc_b.o" "$BUILD_DIR/noxpy_shim.o" "$BUILD_DIR/mod_b.o" "$NOXRT" -lm

echo
echo "=== Artifact SHA-256 Digests ==="
shasum -a 256 "$NOXC" "$NOXRT" "$BUILD_DIR/libpoc_a.dylib" "$BUILD_DIR/libpoc_b.dylib" \
    "$BUILD_DIR/_noxpy_poc.abi3t.so" "$BUILD_DIR/_noxpy_poc_b.abi3t.so"

echo
echo "=== Running C Host Multi-Thread, Dual-Library, Handle & Error Test (nox_host.h) ==="
"$BUILD_DIR/c_host_test" "$BUILD_DIR/libpoc_a.dylib" "$BUILD_DIR/libpoc_b.dylib"

echo
echo "=== Running ABI & Symbol Audit ==="
"$PYTHON315" "$ROOT/tests/abi/audit_binary.py" "$BUILD_DIR/_noxpy_poc.abi3t.so" "_PyModExport__noxpy_poc"
"$PYTHON315" "$ROOT/tests/abi/audit_binary.py" "$BUILD_DIR/_noxpy_poc_b.abi3t.so" "_PyModExport__noxpy_poc_b"

echo
echo "=== Running CPython 3.15 Integration & Concurrency Suite ==="
PYTHONPATH="$BUILD_DIR" "$PYTHON315" "$ROOT/tests/integration/test_phase0_poc.py"

echo
echo "=== Running Unforced Free-Threaded 3.15t Verification Suite ==="
PYTHONPATH="$BUILD_DIR" "$PYTHON315" "$ROOT/tests/abi/test_freethreaded_315t.py"

echo
echo "=== Phase 0 Build & Dual-Mode (3.15 + 3.15t) Verification Complete ==="
