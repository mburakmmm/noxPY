"""Reusable CPython 3.15 dual `abi3t` binary symbol & dependency auditor (`AGENTS.md` §5, §15.3)."""

from __future__ import annotations

import hashlib
import os
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
        "PyBuffer_IsContiguous",
        "PyBuffer_Release",
        "PyBytes_AsStringAndSize",
        "PyBytes_FromStringAndSize",
        "PyDict_GetItemStringRef",
        "PyDict_Items",
        "PyDict_New",
        "PyDict_SetItem",
        "PyDict_Size",
        "PyDict_Type",
        "PyErr_Clear",
        "PyErr_ExceptionMatches",
        "PyErr_Format",
        "PyErr_GetRaisedException",
        "PyErr_NewExceptionWithDoc",
        "PyErr_NoMemory",
        "PyErr_Occurred",
        "PyErr_SetRaisedException",
        "PyErr_SetString",
        "PyEval_RestoreThread",
        "PyEval_SaveThread",
        "PyExc_AssertionError",
        "PyExc_AttributeError",
        "PyExc_BufferError",
        "PyExc_Exception",
        "PyExc_IndexError",
        "PyExc_KeyError",
        "PyExc_MemoryError",
        "PyExc_NotImplementedError",
        "PyExc_OverflowError",
        "PyExc_RecursionError",
        "PyExc_RuntimeError",
        "PyExc_StopIteration",
        "PyExc_SystemError",
        "PyExc_TypeError",
        "PyExc_ValueError",
        "PyExc_ZeroDivisionError",
        "PyFloat_AsDouble",
        "PyFloat_FromDouble",
        "PyFloat_Type",
        "PyGILState_Ensure",
        "PyGILState_Release",
        "PyImport_ImportModule",
        "PyInterpreterState_Get",
        "PyInterpreterState_GetID",
        "PyList_New",
        "PyList_SetItem",
        "PyList_Size",
        "PyList_Type",
        "PyLong_AsInt64",
        "PyLong_FromInt64",
        "PyLong_Type",
        "PyMemoryView_FromObject",
        "PyModule_AddObjectRef",
        "PyModule_GetState",
        "PyObject_Call",
        "PyObject_CallFunctionObjArgs",
        "PyObject_CallNoArgs",
        "PyObject_CheckBuffer",
        "PyObject_GC_UnTrack",
        "PyObject_GetAttrString",
        "PyObject_GetBuffer",
        "PyObject_GetTypeData",
        "PyObject_GetTypeData_DuringGC",
        "PyObject_HashNotImplemented",
        "PyObject_IsTrue",
        "PyObject_Str",
        "PyObject_TypeCheck",
        "PySequence_GetItem",
        "PySequence_Tuple",
        "PyTuple_GetItem",
        "PyTuple_New",
        "PyTuple_SetItem",
        "PyTuple_Size",
        "PyTuple_Type",
        "PyType_FastSubclass",
        "PyType_FromModuleAndSpec",
        "PyType_GetFlags",
        "PyType_GetModule",
        "PyType_GetSlot",
        "PyType_IsSubtype",
        "PyUnicode_AsUTF8AndSize",
        "PyUnicode_CompareWithASCIIString",
        "PyUnicode_DecodeUTF8",
        "PyUnicode_FromFormat",
        "PyUnicode_FromString",
        "PyUnicode_Type",
        "Py_DecRef",
        "Py_GetConstantBorrowed",
        "Py_IS_TYPE",
        "Py_IncRef",
        "Py_NoneStruct",
        "Py_TYPE",
    }
)


# CPython 3.15 limited API / abi3t inline `Py_DECREF` / `Py_INCREF` expand to these
# documented runtime thunks (`include/cpython/...` / `refcount.h:329-335`).
LIMITED_ABI_REFCOUNT_THUNKS = frozenset({"_Py_DecRef", "_Py_IncRef"})


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
    max_glibc_raw = os.environ.get("NOXPY_MAX_GLIBC", "").strip()
    max_glibc: tuple[int, ...] | None = None
    if max_glibc_raw:
        try:
            max_glibc = tuple(int(p) for p in max_glibc_raw.split("."))
        except ValueError as exc:
            raise NoxPyError(
                "NPY-E-CONFIG",
                f"Invalid NOXPY_MAX_GLIBC version specifier: {max_glibc_raw!r}",
                location=loc,
            ) from exc

    bad_glibc: list[str] = []
    for line in _run_tool(["nm", "-D", "-u", str(so_path)], loc).splitlines():
        if not line.strip():
            continue
        raw_tok = line.strip().split()[-1]
        if "@GLIBC_" in raw_tok and max_glibc is not None:
            ver_str = raw_tok.split("@GLIBC_", 1)[1].lstrip("@")
            try:
                ver_tup = tuple(int(p) for p in ver_str.split("."))
            except ValueError:
                ver_tup = ()
            if ver_tup and ver_tup > max_glibc:
                bad_glibc.append(f"{raw_tok} (> GLIBC_{max_glibc_raw})")
        tok = raw_tok.split("@", 1)[0]
        undefs.append(tok)

    if bad_glibc:
        raise NoxPyError(
            "NPY-E-ABI",
            f"Binary requires newer glibc symbols than allowed ({max_glibc_raw}): {bad_glibc[:8]}",
            location=loc,
        )

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
        if c_sym in LIMITED_ABI_REFCOUNT_THUNKS:
            continue
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
