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

echo "=== NoxPy Phase 0 PoC Build ==="
echo "NOXC=$NOXC ($NOXC_VER)"
echo "NOXRT=$NOXRT"
echo "PYTHON315=$PYTHON315 ($("$PYTHON315" --version 2>&1))"
echo "PY_INC=$PY_INC"

mkdir -p "$BUILD_DIR"

# 1. Compile genuine Nox sources with installed noxc 2.2.70 (--selfhost-obj experimental spike)
"$NOXC" build --selfhost-obj -o "$BUILD_DIR/math_mod.o" "$POC_DIR/math_mod.nox"
"$NOXC" build --selfhost-obj -o "$BUILD_DIR/mod_b.o" "$POC_DIR/mod_b.nox"

CFLAGS_COMMON=(-std=c11 -O2 -Wall -Wextra -Werror -fPIC -fvisibility=hidden "-I$ROOT/native/include" "-I$POC_DIR")
CFLAGS_POC=("${CFLAGS_COMMON[@]}" -DNOXPY_PHASE0_EXPERIMENTAL_SELFHOST_BRIDGE=1)

# 2. Compile audited CPython abi3t shim & experimental v2.2.70 bridge
clang "${CFLAGS_COMMON[@]}" "-I$PY_INC" -c "$ROOT/native/cpython/noxpy_shim.c" -o "$BUILD_DIR/noxpy_shim.o"
clang "${CFLAGS_POC[@]}" -c "$POC_DIR/nox_v2270_bridge.c" -o "$BUILD_DIR/nox_v2270_bridge.o"

# 3. Build two independent C-host dylibs (strict exported symbol lists)
clang "${CFLAGS_POC[@]}" -c "$POC_DIR/lib_poc_a_entry.c" -o "$BUILD_DIR/lib_poc_a_entry.o"
clang "${CFLAGS_POC[@]}" -c "$POC_DIR/lib_poc_b_entry.c" -o "$BUILD_DIR/lib_poc_b_entry.o"

clang -dynamiclib -Wl,-dead_strip -Wl,-exported_symbols_list,"$POC_DIR/exports_lib_a.txt" \
    -o "$BUILD_DIR/libpoc_a.dylib" \
    "$BUILD_DIR/lib_poc_a_entry.o" "$BUILD_DIR/nox_v2270_bridge.o" "$BUILD_DIR/math_mod.o" "$NOXRT" -lm

clang -dynamiclib -Wl,-dead_strip -Wl,-exported_symbols_list,"$POC_DIR/exports_lib_b.txt" \
    -o "$BUILD_DIR/libpoc_b.dylib" \
    "$BUILD_DIR/lib_poc_b_entry.o" "$BUILD_DIR/nox_v2270_bridge.o" "$BUILD_DIR/mod_b.o" "$NOXRT" -lm

clang "${CFLAGS_COMMON[@]}" "$POC_DIR/c_host_test.c" -o "$BUILD_DIR/c_host_test"

# 4. Build two independent CPython 3.15 abi3t extension bundles
clang "${CFLAGS_POC[@]}" "-I$PY_INC" -c "$POC_DIR/py_poc_a.c" -o "$BUILD_DIR/py_poc_a.o"
clang "${CFLAGS_POC[@]}" "-I$PY_INC" -c "$POC_DIR/py_poc_b.c" -o "$BUILD_DIR/py_poc_b.o"

clang -bundle -undefined dynamic_lookup -Wl,-dead_strip -Wl,-exported_symbols_list,"$POC_DIR/exports_py_a.txt" \
    -o "$BUILD_DIR/_noxpy_poc.abi3t.so" \
    "$BUILD_DIR/py_poc_a.o" "$BUILD_DIR/noxpy_shim.o" "$BUILD_DIR/nox_v2270_bridge.o" "$BUILD_DIR/math_mod.o" "$NOXRT" -lm

clang -bundle -undefined dynamic_lookup -Wl,-dead_strip -Wl,-exported_symbols_list,"$POC_DIR/exports_py_b.txt" \
    -o "$BUILD_DIR/_noxpy_poc_b.abi3t.so" \
    "$BUILD_DIR/py_poc_b.o" "$BUILD_DIR/noxpy_shim.o" "$BUILD_DIR/nox_v2270_bridge.o" "$BUILD_DIR/mod_b.o" "$NOXRT" -lm

echo
echo "=== Artifact SHA-256 Digests ==="
shasum -a 256 "$NOXC" "$NOXRT" "$BUILD_DIR/libpoc_a.dylib" "$BUILD_DIR/libpoc_b.dylib" \
    "$BUILD_DIR/_noxpy_poc.abi3t.so" "$BUILD_DIR/_noxpy_poc_b.abi3t.so"

echo
echo "=== Running C Host Multi-Thread & Dual-Library Test ==="
"$BUILD_DIR/c_host_test" "$BUILD_DIR/libpoc_a.dylib" "$BUILD_DIR/libpoc_b.dylib"

echo
echo "=== Running ABI & Symbol Audit ==="
"$PYTHON315" "$ROOT/tests/abi/audit_binary.py" "$BUILD_DIR/_noxpy_poc.abi3t.so" "_PyModExport__noxpy_poc"
"$PYTHON315" "$ROOT/tests/abi/audit_binary.py" "$BUILD_DIR/_noxpy_poc_b.abi3t.so" "_PyModExport__noxpy_poc_b"

echo
echo "=== Running CPython 3.15 Integration & Concurrency Suite ==="
PYTHONPATH="$BUILD_DIR" "$PYTHON315" "$ROOT/tests/integration/test_phase0_poc.py"

echo
echo "=== Running Unforced Free-Threaded 3.15t Check ==="
PYTHONPATH="$BUILD_DIR" "$PYTHON315" "$ROOT/tests/abi/test_freethreaded_315t.py" || {
    RC=$?
    if [ "$RC" -eq 2 ]; then
        echo "NOTE: 3.15t test returned SKIPPED (exit 2) — free-threaded python3.15t is not installed."
    else
        exit "$RC"
    fi
}

echo
echo "=== Phase 0 PoC Build & Local Verification Complete ==="
