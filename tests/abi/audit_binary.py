#!/usr/bin/env python3
"""Audit a built `.abi3t.so` extension binary against NoxPy Section 5 & 6 ABI rules.

Supports macOS (Mach-O) and Linux (ELF):
  1. Exported defined symbols: ONLY the single expected `PyModExport_<leaf>` symbol
     (with Mach-O leading `_` on Darwin). Zero leaked `nox_*` symbols.
  2. Imported Python symbols: every imported `Py*` / `_Py*` C symbol must be in the
     audited CPython 3.15 abi3t allowlist; zero private `_Py*` C symbols.
  3. Linked dependencies: no external Nox runtime shared libraries.
  4. Emits SHA-256 hash.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys

# C symbol names (without Mach-O leading underscore)
ALLOWED_C_PY_IMPORTS = {
    "PyABIInfo_Check",
    "PyArg_CheckPositional",
    "PyBool_FromLong",
    "PyBool_Type",
    "PyBytes_AsStringAndSize",
    "PyBytes_FromStringAndSize",
    "PyErr_ExceptionMatches",
    "PyErr_Format",
    "PyErr_Occurred",
    "PyErr_SetString",
    "PyEval_RestoreThread",
    "PyEval_SaveThread",
    "PyExc_IndexError",
    "PyExc_KeyError",
    "PyExc_MemoryError",
    "PyExc_NotImplementedError",
    "PyExc_OverflowError",
    "PyExc_RuntimeError",
    "PyExc_SystemError",
    "PyExc_TypeError",
    "PyExc_ValueError",
    "PyExc_ZeroDivisionError",
    "PyFloat_AsDouble",
    "PyFloat_FromDouble",
    "PyFloat_Type",
    "PyLong_AsInt64",
    "PyLong_FromInt64",
    "PyLong_Type",
    "PyModule_GetState",
    "PyObject_TypeCheck",
    "PyType_FastSubclass",
    "PyType_GetFlags",
    "PyType_IsSubtype",
    "PyUnicode_AsUTF8AndSize",
    "PyUnicode_DecodeUTF8",
    "Py_GetConstantBorrowed",
    "Py_IS_TYPE",
    "Py_NoneStruct",
    "Py_TYPE",
}


def run_cmd(cmd: list[str]) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return res.stdout


def _audit_darwin(so_path: str, expected_export: str) -> tuple[list[str], list[str], list[str]]:
    exports = [line.strip().split()[-1] for line in run_cmd(["nm", "-gU", so_path]).splitlines() if line.strip()]
    undefs = [line.strip().split()[-1] for line in run_cmd(["nm", "-u", so_path]).splitlines() if line.strip()]
    py_c_syms = []
    for sym in undefs:
        c_sym = sym[1:] if sym.startswith("_") else sym
        if c_sym.startswith("Py") or c_sym.startswith("_Py"):
            py_c_syms.append(c_sym)

    otool_lines = [line.strip() for line in run_cmd(["otool", "-L", so_path]).splitlines()[1:] if line.strip()]
    deps = [line.split()[0] for line in otool_lines]
    bad_deps = [d for d in deps if "nox" in d.lower() or not d.startswith("/usr/lib/")]
    if bad_deps:
        raise AssertionError(f"unexpected external dynamic library dependency: {bad_deps}")

    if exports != [expected_export]:
        raise AssertionError(f"exported symbols mismatch: expected [{expected_export}], got {exports}")
    return exports, py_c_syms, deps


def _audit_linux(so_path: str, expected_export: str) -> tuple[list[str], list[str], list[str]]:
    expected_c = expected_export[1:] if expected_export.startswith("_PyModExport_") else expected_export
    exports = [
        line.strip().split()[-1]
        for line in run_cmd(["nm", "-D", "--defined-only", so_path]).splitlines()
        if line.strip()
    ]
    undefs = []
    for line in run_cmd(["nm", "-D", "-u", so_path]).splitlines():
        if not line.strip():
            continue
        tok = line.strip().split()[-1].split("@", 1)[0]
        undefs.append(tok)

    py_c_syms = [s for s in undefs if s.startswith("Py") or s.startswith("_Py")]

    ldd_lines = [line.strip() for line in run_cmd(["ldd", so_path]).splitlines() if line.strip()]
    deps = [line.split()[0] for line in ldd_lines]
    bad_deps = [d for d in deps if "nox" in d.lower()]
    if bad_deps:
        raise AssertionError(f"unexpected Nox shared library dependency: {bad_deps}")

    if exports != [expected_c]:
        raise AssertionError(f"exported symbols mismatch: expected [{expected_c}], got {exports}")
    return exports, py_c_syms, deps


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: audit_binary.py <path/to/mod.abi3t.so> <expected_export_symbol>\n")
        return 2

    so_path = sys.argv[1]
    expected_export = sys.argv[2]

    with open(so_path, "rb") as fh:
        sha256 = hashlib.sha256(fh.read()).hexdigest()

    try:
        if sys.platform == "darwin":
            exports, py_c_syms, deps = _audit_darwin(so_path, expected_export)
        elif sys.platform.startswith("linux"):
            exports, py_c_syms, deps = _audit_linux(so_path, expected_export)
        else:
            sys.stderr.write(f"FAIL: unsupported platform for binary audit: {sys.platform}\n")
            return 1
    except AssertionError as exc:
        sys.stderr.write(f"FAIL: {exc} in {so_path}\n")
        return 1

    disallowed = []
    for c_sym in py_c_syms:
        if c_sym.startswith("_Py"):
            disallowed.append(f"{c_sym} (private CPython internal symbol)")
        elif c_sym not in ALLOWED_C_PY_IMPORTS:
            disallowed.append(f"{c_sym} (not in audited abi3t allowlist)")

    if disallowed:
        sys.stderr.write(f"FAIL: disallowed CPython imports in {so_path}: {disallowed}\n")
        return 1

    print(f"AUDIT PASS: {so_path}")
    print(f"  sha256:  {sha256}")
    print(f"  exports: {exports}")
    print(f"  py_syms: {sorted(set(py_c_syms))}")
    print(f"  deps:    {deps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
