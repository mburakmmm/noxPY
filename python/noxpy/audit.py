"""Reusable CPython 3.15 dual `abi3t` binary symbol & dependency auditor (`AGENTS.md` §5, §15.3)."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import DiagnosticLocation, NoxPyError

ALLOWED_C_PY_IMPORTS = frozenset(
    {
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
        "PyTuple_GetItem",
        "PyTuple_Size",
        "PyType_FastSubclass",
        "PyType_GetFlags",
        "PyType_IsSubtype",
        "PyUnicode_AsUTF8AndSize",
        "PyUnicode_CompareWithASCIIString",
        "PyUnicode_DecodeUTF8",
        "PyUnicode_Type",
        "Py_GetConstantBorrowed",
        "Py_IS_TYPE",
        "Py_NoneStruct",
        "Py_TYPE",
    }
)


@dataclass(frozen=True)
class AuditResult:
    path: Path
    sha256: str
    exports: tuple[str, ...]
    py_symbols: tuple[str, ...]
    dependencies: tuple[str, ...]


def _run_tool(cmd: list[str], loc: DiagnosticLocation) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise NoxPyError(
            "NPY-E-ABI",
            f"Binary inspection tool failed ({' '.join(cmd)}): {res.stderr.strip() or res.stdout.strip()}",
            location=loc,
        )
    return res.stdout


def _audit_darwin(so_path: Path, expected_symbol: str, loc: DiagnosticLocation) -> tuple[list[str], list[str], list[str]]:
    exports = [
        line.strip().split()[-1]
        for line in _run_tool(["nm", "-gU", str(so_path)], loc).splitlines()
        if line.strip()
    ]
    undefs = [
        line.strip().split()[-1]
        for line in _run_tool(["nm", "-u", str(so_path)], loc).splitlines()
        if line.strip()
    ]
    py_c_syms = []
    for sym in undefs:
        c_sym = sym[1:] if sym.startswith("_") else sym
        if c_sym.startswith("Py") or c_sym.startswith("_Py"):
            py_c_syms.append(c_sym)

    otool_lines = [line.strip() for line in _run_tool(["otool", "-L", str(so_path)], loc).splitlines()[1:] if line.strip()]
    deps = [line.split()[0] for line in otool_lines]
    bad_deps = [d for d in deps if "nox" in d.lower() or not d.startswith("/usr/lib/")]
    if bad_deps:
        raise NoxPyError(
            "NPY-E-ABI",
            f"Unexpected external dynamic library dependency: {bad_deps}",
            location=loc,
        )

    expected_macho = expected_symbol if expected_symbol.startswith("_") else f"_{expected_symbol}"
    if exports != [expected_macho]:
        raise NoxPyError(
            "NPY-E-ABI",
            f"Exported symbols mismatch: expected [{expected_macho}], got {exports}",
            location=loc,
        )
    return exports, py_c_syms, deps


def _audit_linux(so_path: Path, expected_symbol: str, loc: DiagnosticLocation) -> tuple[list[str], list[str], list[str]]:
    expected_c = expected_symbol[1:] if expected_symbol.startswith("_PyModExport_") else expected_symbol
    exports = [
        line.strip().split()[-1]
        for line in _run_tool(["nm", "-D", "--defined-only", str(so_path)], loc).splitlines()
        if line.strip()
    ]
    undefs = []
    for line in _run_tool(["nm", "-D", "-u", str(so_path)], loc).splitlines():
        if not line.strip():
            continue
        tok = line.strip().split()[-1].split("@", 1)[0]
        undefs.append(tok)

    py_c_syms = [s for s in undefs if s.startswith("Py") or s.startswith("_Py")]

    ldd_lines = [line.strip() for line in _run_tool(["ldd", str(so_path)], loc).splitlines() if line.strip()]
    deps = [line.split()[0] for line in ldd_lines]
    bad_deps = [d for d in deps if "nox" in d.lower()]
    if bad_deps:
        raise NoxPyError(
            "NPY-E-ABI",
            f"Unexpected external Nox shared library dependency: {bad_deps}",
            location=loc,
        )

    if exports != [expected_c]:
        raise NoxPyError(
            "NPY-E-ABI",
            f"Exported symbols mismatch: expected [{expected_c}], got {exports}",
            location=loc,
        )
    return exports, py_c_syms, deps


def audit_extension_binary(so_path: str | Path, expected_export_symbol: str) -> AuditResult:
    """Audit a compiled `.abi3t.so` extension against NoxPy §5/§6 ABI & closure contracts."""
    path = Path(so_path)
    loc = DiagnosticLocation(file=str(path))
    if not path.is_file():
        raise NoxPyError("NPY-E-ABI", f"Compiled extension binary not found: {path}", location=loc)

    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    if sys.platform == "darwin":
        exports, py_c_syms, deps = _audit_darwin(path, expected_export_symbol, loc)
    elif sys.platform.startswith("linux"):
        exports, py_c_syms, deps = _audit_linux(path, expected_export_symbol, loc)
    else:
        raise NoxPyError("NPY-E-ABI", f"Unsupported platform for binary audit: {sys.platform}", location=loc)

    disallowed = []
    for c_sym in py_c_syms:
        if c_sym.startswith("_Py"):
            disallowed.append(f"{c_sym} (private CPython internal symbol)")
        elif c_sym not in ALLOWED_C_PY_IMPORTS:
            disallowed.append(f"{c_sym} (not in audited abi3t allowlist)")

    if disallowed:
        raise NoxPyError(
            "NPY-E-ABI",
            f"Disallowed CPython imports: {disallowed}",
            location=loc,
        )

    return AuditResult(
        path=path,
        sha256=sha256,
        exports=tuple(exports),
        py_symbols=tuple(sorted(set(py_c_syms))),
        dependencies=tuple(deps),
    )
