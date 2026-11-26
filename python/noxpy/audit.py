"""Reusable CPython 3.15 dual `abi3t` binary symbol & dependency auditor (`AGENTS.md` §5, §15.3)."""

from __future__ import annotations

import hashlib
import os
import struct
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
        "PyEval_AcquireThread",
        "PyEval_ReleaseThread",
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
        "PyThreadState_Clear",
        "PyThreadState_Delete",
        "PyThreadState_New",
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


def _pe_rva_to_offset(sections: list[tuple[int, int, int, int]], rva: int) -> int | None:
    for vaddr, vsize, raw_ptr, raw_size in sections:
        span = max(vsize, raw_size)
        if vaddr <= rva < vaddr + span:
            off = raw_ptr + (rva - vaddr)
            return off if off >= 0 else None
    return None


def _pe_read_cstr(data: bytes, offset: int | None) -> str:
    if offset is None or offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\x00", offset)
    if end < 0:
        end = len(data)
    return data[offset:end].decode("utf-8", errors="replace")


def _audit_windows(pyd_path: Path, expected_symbol: str, loc: DiagnosticLocation) -> tuple[list[str], list[str], list[str]]:
    data = pyd_path.read_bytes()
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise NoxPyError("NPY-E-ABI", f"Invalid PE binary (missing MZ header): {pyd_path}", location=loc)
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    if pe_off + 24 > len(data) or data[pe_off : pe_off + 4] != b"PE\x00\x00":
        raise NoxPyError("NPY-E-ABI", f"Invalid PE signature in {pyd_path}", location=loc)

    _machine, num_sections, _ts, _sym_ptr, _num_syms, opt_size, _chars = struct.unpack_from("<HHIIIHH", data, pe_off + 4)
    opt_off = pe_off + 24
    if opt_off + opt_size > len(data) or opt_size < 112:
        raise NoxPyError("NPY-E-ABI", f"Truncated PE optional header in {pyd_path}", location=loc)
    magic = struct.unpack_from("<H", data, opt_off)[0]
    if magic != 0x20B:
        raise NoxPyError("NPY-E-ABI", f"Expected PE32+ (64-bit, magic 0x20B), got 0x{magic:X}", location=loc)

    num_rvas = struct.unpack_from("<I", data, opt_off + 108)[0]
    dd_off = opt_off + 112
    exp_rva, exp_size = struct.unpack_from("<II", data, dd_off) if num_rvas >= 1 else (0, 0)
    imp_rva, imp_size = struct.unpack_from("<II", data, dd_off + 8) if num_rvas >= 2 else (0, 0)

    sec_off = opt_off + opt_size
    sections: list[tuple[int, int, int, int]] = []
    for i in range(num_sections):
        entry = sec_off + i * 40
        if entry + 40 > len(data):
            break
        vsize, vaddr, raw_size, raw_ptr = struct.unpack_from("<IIII", data, entry + 8)
        sections.append((vaddr, vsize, raw_ptr, raw_size))

    exports: list[str] = []
    if exp_rva and exp_size:
        eo = _pe_rva_to_offset(sections, exp_rva)
        if eo is not None and eo + 40 <= len(data):
            _num_funcs, num_names = struct.unpack_from("<II", data, eo + 20)
            names_rva = struct.unpack_from("<I", data, eo + 32)[0]
            names_off = _pe_rva_to_offset(sections, names_rva)
            if names_off is not None:
                for i in range(num_names):
                    if names_off + (i + 1) * 4 > len(data):
                        break
                    nrva = struct.unpack_from("<I", data, names_off + i * 4)[0]
                    name = _pe_read_cstr(data, _pe_rva_to_offset(sections, nrva))
                    if name:
                        exports.append(name)

    deps: list[str] = []
    py_c_syms: list[str] = []
    bad_py_dlls: list[str] = []
    if imp_rva and imp_size:
        ioff = _pe_rva_to_offset(sections, imp_rva)
        while ioff is not None and ioff + 20 <= len(data):
            orig_ft, _ts2, _fwd, name_rva, ft = struct.unpack_from("<IIIII", data, ioff)
            if orig_ft == 0 and name_rva == 0 and ft == 0:
                break
            dll_name = _pe_read_cstr(data, _pe_rva_to_offset(sections, name_rva))
            if dll_name:
                deps.append(dll_name)
                low_dll = dll_name.lower()
                if "nox" in low_dll:
                    raise NoxPyError(
                        "NPY-E-ABI",
                        f"Unexpected external Nox DLL dependency: {dll_name}",
                        location=loc,
                    )
                is_py_dll = low_dll.startswith("python") and low_dll.endswith(".dll")
                if is_py_dll and low_dll != "python3.dll":
                    bad_py_dlls.append(dll_name)

                thunk_rva = orig_ft if orig_ft else ft
                toff = _pe_rva_to_offset(sections, thunk_rva)
                while toff is not None and toff + 8 <= len(data):
                    entry_val = struct.unpack_from("<Q", data, toff)[0]
                    if entry_val == 0:
                        break
                    if (entry_val & (1 << 63)) == 0:
                        hint_name_rva = entry_val & 0x7FFFFFFF
                        h_off = _pe_rva_to_offset(sections, hint_name_rva)
                        if h_off is not None and h_off + 2 < len(data):
                            sym_name = _pe_read_cstr(data, h_off + 2)
                            if sym_name.startswith("Py") or sym_name.startswith("_Py"):
                                py_c_syms.append(sym_name)
                    toff += 8
            ioff += 20

    if bad_py_dlls:
        raise NoxPyError(
            "NPY-E-ABI",
            f"Windows abi3t extension must link python3.dll (stable ABI), not version-specific {bad_py_dlls}",
            location=loc,
        )

    expected_c = expected_symbol[1:] if expected_symbol.startswith("_PyModExport_") else expected_symbol
    if exports != [expected_c]:
        raise NoxPyError(
            "NPY-E-ABI",
            f"Exported symbols mismatch: expected [{expected_c}], got {exports}",
            location=loc,
        )
    return exports, py_c_syms, deps


def audit_extension_binary(so_path: str | Path, expected_export_symbol: str) -> AuditResult:
    """Audit a compiled `.abi3t.so` / `.pyd` extension against NoxPy §5/§6 ABI & closure contracts."""
    path = Path(so_path)
    loc = DiagnosticLocation(file=str(path))
    if not path.is_file():
        raise NoxPyError("NPY-E-ABI", f"Compiled extension binary not found: {path}", location=loc)

    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    if sys.platform == "darwin":
        exports, py_c_syms, deps = _audit_darwin(path, expected_export_symbol, loc)
    elif sys.platform.startswith("linux"):
        exports, py_c_syms, deps = _audit_linux(path, expected_export_symbol, loc)
    elif sys.platform == "win32":
        exports, py_c_syms, deps = _audit_windows(path, expected_export_symbol, loc)
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
