"""Optional Arrow C Data & C Stream interface adapter (`AGENTS.md` §3, §20, §23; `ADR-0008`).

Provides zero-copy Arrow PyCapsule interoperability (`__arrow_c_schema__`,
`__arrow_c_array__`, `__arrow_c_stream__`) over NoxPy PEP 3118 pinnable buffers
(`F64Buffer`, `I64Buffer`, `I32Buffer`, `U8Buffer`) and standard Python buffers using
only documented CPython C APIs (`PyObject_GetBuffer`, `PyBuffer_Release`, `PyCapsule_*`).

This module has ZERO compile-time or link-time dependency on `pyarrow` and preserves
free-threaded execution (`Py_MOD_GIL_NOT_USED` on CPython 3.15t).
"""

from __future__ import annotations

import ctypes
import sys
import threading
import weakref
from typing import Any

# ============================================================================
# Apache Arrow C Data / C Stream ABI Structures (stable across Arrow C ABI)
# ============================================================================


class ArrowSchema(ctypes.Structure):
    pass


ArrowSchema._fields_ = [
    ("format", ctypes.c_char_p),
    ("name", ctypes.c_char_p),
    ("metadata", ctypes.c_char_p),
    ("flags", ctypes.c_int64),
    ("n_children", ctypes.c_int64),
    ("children", ctypes.POINTER(ctypes.POINTER(ArrowSchema))),
    ("dictionary", ctypes.POINTER(ArrowSchema)),
    ("release", ctypes.c_void_p),
    ("private_data", ctypes.c_void_p),
]


class ArrowArray(ctypes.Structure):
    pass


ArrowArray._fields_ = [
    ("length", ctypes.c_int64),
    ("null_count", ctypes.c_int64),
    ("offset", ctypes.c_int64),
    ("n_buffers", ctypes.c_int64),
    ("n_children", ctypes.c_int64),
    ("buffers", ctypes.POINTER(ctypes.c_void_p)),
    ("children", ctypes.POINTER(ctypes.POINTER(ArrowArray))),
    ("dictionary", ctypes.POINTER(ArrowArray)),
    ("release", ctypes.c_void_p),
    ("private_data", ctypes.c_void_p),
]


class ArrowArrayStream(ctypes.Structure):
    pass


ArrowArrayStream._fields_ = [
    ("get_schema", ctypes.c_void_p),
    ("get_next", ctypes.c_void_p),
    ("get_last_error", ctypes.c_void_p),
    ("release", ctypes.c_void_p),
    ("private_data", ctypes.c_void_p),
]


class _PyBuffer(ctypes.Structure):
    _fields_ = [
        ("buf", ctypes.c_void_p),
        ("obj", ctypes.c_void_p),
        ("len", ctypes.c_ssize_t),
        ("itemsize", ctypes.c_ssize_t),
        ("readonly", ctypes.c_int),
        ("ndim", ctypes.c_int),
        ("format", ctypes.c_char_p),
        ("shape", ctypes.POINTER(ctypes.c_ssize_t)),
        ("strides", ctypes.POINTER(ctypes.c_ssize_t)),
        ("suboffsets", ctypes.POINTER(ctypes.c_ssize_t)),
        ("internal", ctypes.c_void_p),
    ]


if ctypes.sizeof(ctypes.c_void_p) == 8:
    assert ctypes.sizeof(ArrowSchema) == 72
    assert ctypes.sizeof(ArrowArray) == 80
    assert ctypes.sizeof(ArrowArrayStream) == 40

# ============================================================================
# CPython public PyCapsule & PEP 3118 C-API bindings (private handles)
# ============================================================================

_PYBUF_RECORDS_RO = 0x001C

_PyCapsule_New = ctypes.PYFUNCTYPE(
    ctypes.py_object, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p
)(ctypes.cast(ctypes.pythonapi.PyCapsule_New, ctypes.c_void_p).value)

_PyCapsule_IsValid = ctypes.PYFUNCTYPE(
    ctypes.c_int, ctypes.py_object, ctypes.c_char_p
)(ctypes.cast(ctypes.pythonapi.PyCapsule_IsValid, ctypes.c_void_p).value)

_PyCapsule_GetPointer = ctypes.PYFUNCTYPE(
    ctypes.c_void_p, ctypes.py_object, ctypes.c_char_p
)(ctypes.cast(ctypes.pythonapi.PyCapsule_GetPointer, ctypes.c_void_p).value)

_PyCapsule_GetName = ctypes.PYFUNCTYPE(
    ctypes.c_char_p, ctypes.py_object
)(ctypes.cast(ctypes.pythonapi.PyCapsule_GetName, ctypes.c_void_p).value)

_PyCapsule_SetName = ctypes.PYFUNCTYPE(
    ctypes.c_int, ctypes.py_object, ctypes.c_char_p
)(ctypes.cast(ctypes.pythonapi.PyCapsule_SetName, ctypes.c_void_p).value)

# Raw PyObject* variants for PyCapsule destructors (never INCREF dying object).
_PyCapsule_GetName_raw = ctypes.PYFUNCTYPE(
    ctypes.c_char_p, ctypes.c_void_p
)(ctypes.cast(ctypes.pythonapi.PyCapsule_GetName, ctypes.c_void_p).value)

_PyCapsule_GetPointer_raw = ctypes.PYFUNCTYPE(
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p
)(ctypes.cast(ctypes.pythonapi.PyCapsule_GetPointer, ctypes.c_void_p).value)

_PyObject_GetBuffer = ctypes.PYFUNCTYPE(
    ctypes.c_int, ctypes.py_object, ctypes.POINTER(_PyBuffer), ctypes.c_int
)(ctypes.cast(ctypes.pythonapi.PyObject_GetBuffer, ctypes.c_void_p).value)

_PyBuffer_Release = ctypes.PYFUNCTYPE(
    None, ctypes.POINTER(_PyBuffer)
)(ctypes.cast(ctypes.pythonapi.PyBuffer_Release, ctypes.c_void_p).value)

_PyMemoryView_FromMemory = ctypes.PYFUNCTYPE(
    ctypes.py_object, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int
)(ctypes.cast(ctypes.pythonapi.PyMemoryView_FromMemory, ctypes.c_void_p).value)

_PYBUF_READ = 0x0100

_SCHEMA_RELEASE_CB_TYPE = ctypes.CFUNCTYPE(None, ctypes.POINTER(ArrowSchema))
_ARRAY_RELEASE_CB_TYPE = ctypes.CFUNCTYPE(None, ctypes.POINTER(ArrowArray))
_STREAM_GET_SCHEMA_CB_TYPE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ArrowArrayStream), ctypes.POINTER(ArrowSchema))
_STREAM_GET_NEXT_CB_TYPE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ArrowArrayStream), ctypes.POINTER(ArrowArray))
_STREAM_GET_LAST_ERROR_CB_TYPE = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.POINTER(ArrowArrayStream))
_STREAM_RELEASE_CB_TYPE = ctypes.CFUNCTYPE(None, ctypes.POINTER(ArrowArrayStream))
_CAPSULE_DESTRUCTOR_CB_TYPE = ctypes.CFUNCTYPE(None, ctypes.c_void_p)

_SCHEMA_NAME_BYTES = b"arrow_schema"
_USED_SCHEMA_NAME_BYTES = b"used_arrow_schema"
_ARRAY_NAME_BYTES = b"arrow_array"
_USED_ARRAY_NAME_BYTES = b"used_arrow_array"
_STREAM_NAME_BYTES = b"arrow_array_stream"
_USED_STREAM_NAME_BYTES = b"used_arrow_array_stream"

# PEP 3118 single-char code <-> (Arrow format string, itemsize)
_PY_TO_ARROW_DTYPE: dict[str, tuple[str, int]] = {
    "d": ("g", 8),
    "f": ("f", 4),
    "q": ("l", 8),
    "i": ("i", 4),
    "h": ("s", 2),
    "b": ("c", 1),
    "Q": ("L", 8),
    "I": ("I", 4),
    "H": ("S", 2),
    "B": ("C", 1),
}
if ctypes.sizeof(ctypes.c_long) == 8:
    _PY_TO_ARROW_DTYPE["l"] = ("l", 8)
    _PY_TO_ARROW_DTYPE["L"] = ("L", 8)
elif ctypes.sizeof(ctypes.c_long) == 4:
    _PY_TO_ARROW_DTYPE["l"] = ("i", 4)
    _PY_TO_ARROW_DTYPE["L"] = ("I", 4)

_ARROW_TO_PY_DTYPE: dict[str, tuple[str, int]] = {
    "g": ("d", 8),
    "f": ("f", 4),
    "l": ("q", 8),
    "i": ("i", 4),
    "s": ("h", 2),
    "c": ("b", 1),
    "L": ("Q", 8),
    "I": ("I", 4),
    "S": ("H", 2),
    "C": ("B", 1),
}

# Thread-safe live state registry keyed by private_data token.
# Ensures ctypes structures & release callbacks stay pinned until Arrow release() fires.
_STATE_LOCK = threading.Lock()
_NEXT_TOKEN = 1
_LIVE_STATES: dict[int, Any] = {}


def _register_state(obj: Any) -> int:
    global _NEXT_TOKEN
    with _STATE_LOCK:
        tok = _NEXT_TOKEN
        _NEXT_TOKEN += 1
        _LIVE_STATES[tok] = obj
    return tok


def _pop_state(tok: int | None) -> Any | None:
    if not tok:
        return None
    with _STATE_LOCK:
        return _LIVE_STATES.pop(tok, None)


def _get_state(tok: int | None) -> Any | None:
    if not tok:
        return None
    with _STATE_LOCK:
        return _LIVE_STATES.get(tok)


def _normalize_py_format(raw_fmt: str | None) -> str:
    fmt = raw_fmt or "B"
    if fmt.startswith(("=", "@")):
        fmt = fmt[1:]
    elif fmt.startswith("<"):
        if sys.byteorder != "little":
            raise ValueError(f"non-native little-endian PEP 3118 format {raw_fmt!r} on {sys.byteorder} host")
        fmt = fmt[1:]
    elif fmt.startswith((">", "!")):
        if sys.byteorder != "big":
            raise ValueError(f"non-native big-endian PEP 3118 format {raw_fmt!r} on {sys.byteorder} host")
        fmt = fmt[1:]
    if fmt not in _PY_TO_ARROW_DTYPE:
        raise ValueError(f"unsupported PEP 3118 buffer format for Arrow export: {raw_fmt!r}")
    return fmt


def _schema_release_impl(schema_ptr: Any) -> None:
    if not schema_ptr:
        return
    schema = schema_ptr.contents
    if not schema.release:
        return
    schema.release = None
    tok = schema.private_data
    schema.private_data = None
    _pop_state(tok)


_SCHEMA_RELEASE_CB = _SCHEMA_RELEASE_CB_TYPE(_schema_release_impl)
_SCHEMA_RELEASE_ADDR = ctypes.cast(_SCHEMA_RELEASE_CB, ctypes.c_void_p).value


def _array_release_impl(array_ptr: Any) -> None:
    if not array_ptr:
        return
    arr = array_ptr.contents
    if not arr.release:
        return
    arr.release = None
    tok = arr.private_data
    arr.private_data = None
    state = _pop_state(tok)
    if state is not None:
        state.release_pin()


_ARRAY_RELEASE_CB = _ARRAY_RELEASE_CB_TYPE(_array_release_impl)
_ARRAY_RELEASE_ADDR = ctypes.cast(_ARRAY_RELEASE_CB, ctypes.c_void_p).value


def _stream_get_schema_impl(stream_ptr: Any, out_schema_ptr: Any) -> int:
    if not stream_ptr or not out_schema_ptr:
        return 22  # EINVAL
    st = _get_state(stream_ptr.contents.private_data)
    if st is None or st.released:
        return 22
    st.build_schema_into(out_schema_ptr.contents)
    return 0


def _stream_get_next_impl(stream_ptr: Any, out_array_ptr: Any) -> int:
    if not stream_ptr or not out_array_ptr:
        return 22
    st = _get_state(stream_ptr.contents.private_data)
    if st is None or st.released:
        return 22
    st.pop_next_into(out_array_ptr.contents)
    return 0


def _stream_get_last_error_impl(stream_ptr: Any) -> bytes | None:
    if not stream_ptr:
        return None
    st = _get_state(stream_ptr.contents.private_data)
    if st is None or not st.last_error:
        return None
    return st.last_error


def _stream_release_impl(stream_ptr: Any) -> None:
    if not stream_ptr:
        return
    st_struct = stream_ptr.contents
    if not st_struct.release:
        return
    st_struct.release = None
    tok = st_struct.private_data
    st_struct.private_data = None
    state = _pop_state(tok)
    if state is not None:
        state.close_unpopped()


_STREAM_GET_SCHEMA_CB = _STREAM_GET_SCHEMA_CB_TYPE(_stream_get_schema_impl)
_STREAM_GET_NEXT_CB = _STREAM_GET_NEXT_CB_TYPE(_stream_get_next_impl)
_STREAM_GET_LAST_ERROR_CB = _STREAM_GET_LAST_ERROR_CB_TYPE(_stream_get_last_error_impl)
_STREAM_RELEASE_CB = _STREAM_RELEASE_CB_TYPE(_stream_release_impl)

_STREAM_GET_SCHEMA_ADDR = ctypes.cast(_STREAM_GET_SCHEMA_CB, ctypes.c_void_p).value
_STREAM_GET_NEXT_ADDR = ctypes.cast(_STREAM_GET_NEXT_CB, ctypes.c_void_p).value
_STREAM_GET_LAST_ERROR_ADDR = ctypes.cast(_STREAM_GET_LAST_ERROR_CB, ctypes.c_void_p).value
_STREAM_RELEASE_ADDR = ctypes.cast(_STREAM_RELEASE_CB, ctypes.c_void_p).value


def _schema_capsule_dtor_impl(cap_ptr: int | None) -> None:
    if not cap_ptr:
        return
    name = _PyCapsule_GetName_raw(cap_ptr)
    if name != _SCHEMA_NAME_BYTES:
        return
    raw_ptr = _PyCapsule_GetPointer_raw(cap_ptr, _SCHEMA_NAME_BYTES)
    if raw_ptr:
        sp = ctypes.cast(raw_ptr, ctypes.POINTER(ArrowSchema))
        if sp.contents.release:
            rel_fn = _SCHEMA_RELEASE_CB_TYPE(sp.contents.release)
            rel_fn(sp)


def _array_capsule_dtor_impl(cap_ptr: int | None) -> None:
    if not cap_ptr:
        return
    name = _PyCapsule_GetName_raw(cap_ptr)
    if name != _ARRAY_NAME_BYTES:
        return
    raw_ptr = _PyCapsule_GetPointer_raw(cap_ptr, _ARRAY_NAME_BYTES)
    if raw_ptr:
        ap = ctypes.cast(raw_ptr, ctypes.POINTER(ArrowArray))
        if ap.contents.release:
            rel_fn = _ARRAY_RELEASE_CB_TYPE(ap.contents.release)
            rel_fn(ap)


def _stream_capsule_dtor_impl(cap_ptr: int | None) -> None:
    if not cap_ptr:
        return
    name = _PyCapsule_GetName_raw(cap_ptr)
    if name != _STREAM_NAME_BYTES:
        return
    raw_ptr = _PyCapsule_GetPointer_raw(cap_ptr, _STREAM_NAME_BYTES)
    if raw_ptr:
        sp = ctypes.cast(raw_ptr, ctypes.POINTER(ArrowArrayStream))
        if sp.contents.release:
            rel_fn = _STREAM_RELEASE_CB_TYPE(sp.contents.release)
            rel_fn(sp)


_SCHEMA_CAPSULE_DTOR = _CAPSULE_DESTRUCTOR_CB_TYPE(_schema_capsule_dtor_impl)
_ARRAY_CAPSULE_DTOR = _CAPSULE_DESTRUCTOR_CB_TYPE(_array_capsule_dtor_impl)
_STREAM_CAPSULE_DTOR = _CAPSULE_DESTRUCTOR_CB_TYPE(_stream_capsule_dtor_impl)

_SCHEMA_DTOR_ADDR = ctypes.cast(_SCHEMA_CAPSULE_DTOR, ctypes.c_void_p).value
_ARRAY_DTOR_ADDR = ctypes.cast(_ARRAY_CAPSULE_DTOR, ctypes.c_void_p).value
_STREAM_DTOR_ADDR = ctypes.cast(_STREAM_CAPSULE_DTOR, ctypes.c_void_p).value


class _ExportedSchemaState:
    def __init__(self, arrow_fmt: str, name: str) -> None:
        self.fmt_bytes = arrow_fmt.encode("ascii")
        self.name_bytes = name.encode("utf-8") if name else b""
        self.schema = ArrowSchema()
        self.schema.format = ctypes.c_char_p(self.fmt_bytes)
        self.schema.name = ctypes.c_char_p(self.name_bytes) if self.name_bytes else None
        self.schema.metadata = None
        self.schema.flags = 0
        self.schema.n_children = 0
        self.schema.children = None
        self.schema.dictionary = None
        self.schema.release = _SCHEMA_RELEASE_ADDR
        self.token = _register_state(self)
        self.schema.private_data = self.token


class _ExportedArrayState:
    def __init__(self, buf_obj: Any, expected_py_fmt: str | None = None) -> None:
        self.view = _PyBuffer()
        self._pinned = False
        if _PyObject_GetBuffer(buf_obj, ctypes.byref(self.view), _PYBUF_RECORDS_RO) != 0:
            raise TypeError(f"object {type(buf_obj).__name__!r} does not export a PEP 3118 buffer")
        self._pinned = True
        try:
            if self.view.ndim != 1:
                raise ValueError(f"Arrow export requires 1D buffer, got ndim={self.view.ndim}")
            raw_fmt = self.view.format.decode("ascii") if self.view.format else "B"
            py_fmt = _normalize_py_format(raw_fmt)
            if expected_py_fmt is not None and py_fmt != expected_py_fmt:
                raise ValueError(f"buffer format mismatch: expected {expected_py_fmt!r}, got {py_fmt!r}")
            arrow_fmt, expected_itemsize = _PY_TO_ARROW_DTYPE[py_fmt]
            if self.view.itemsize != expected_itemsize:
                raise ValueError(
                    f"itemsize mismatch for format {py_fmt!r}: expected {expected_itemsize}, got {self.view.itemsize}"
                )
            length = self.view.shape[0] if self.view.shape else (self.view.len // self.view.itemsize)
            if length < 0:
                raise ValueError(f"negative buffer length: {length}")
            if self.view.strides:
                stride = self.view.strides[0]
                if length > 1 and stride != self.view.itemsize:
                    raise ValueError(
                        f"Arrow export requires contiguous stride {self.view.itemsize}, got {stride}"
                    )
            if length > 0 and not self.view.buf:
                raise ValueError("non-empty buffer has NULL data pointer")
            if length * self.view.itemsize != self.view.len:
                raise ValueError("buffer byte length does not match shape * itemsize")

            self.py_format = py_fmt
            self.arrow_format = arrow_fmt
            self.itemsize = expected_itemsize
            self.length = int(length)
            self.buffers_arr = (ctypes.c_void_p * 2)(None, self.view.buf or 0)
            self.array = ArrowArray()
            self.array.length = self.length
            self.array.null_count = 0
            self.array.offset = 0
            self.array.n_buffers = 2
            self.array.n_children = 0
            self.array.buffers = ctypes.cast(self.buffers_arr, ctypes.POINTER(ctypes.c_void_p))
            self.array.children = None
            self.array.dictionary = None
            self.array.release = _ARRAY_RELEASE_ADDR
            self.token = _register_state(self)
            self.array.private_data = self.token
        except Exception:
            self.release_pin()
            raise

    def release_pin(self) -> None:
        if self._pinned:
            self._pinned = False
            _PyBuffer_Release(ctypes.byref(self.view))


class _ExportedStreamState:
    def __init__(self, buffers: list[Any], name: str) -> None:
        if not buffers:
            raise ValueError("export_arrow_stream requires at least one buffer")
        self.name = name
        self.last_error: bytes | None = None
        self.released = False
        self._lock = threading.Lock()
        first = _ExportedArrayState(buffers[0])
        self.py_format = first.py_format
        self.arrow_format = first.arrow_format
        self.chunks: list[_ExportedArrayState] = [first]
        try:
            for item in buffers[1:]:
                self.chunks.append(_ExportedArrayState(item, expected_py_fmt=self.py_format))
        except Exception:
            for st in self.chunks:
                _pop_state(st.token)
                st.release_pin()
            raise
        self.idx = 0
        self.stream = ArrowArrayStream()
        self.stream.get_schema = _STREAM_GET_SCHEMA_ADDR
        self.stream.get_next = _STREAM_GET_NEXT_ADDR
        self.stream.get_last_error = _STREAM_GET_LAST_ERROR_ADDR
        self.stream.release = _STREAM_RELEASE_ADDR
        self.token = _register_state(self)
        self.stream.private_data = self.token

    def build_schema_into(self, out_schema: ArrowSchema) -> None:
        s_state = _ExportedSchemaState(self.arrow_format, self.name)
        ctypes.memmove(ctypes.addressof(out_schema), ctypes.addressof(s_state.schema), ctypes.sizeof(ArrowSchema))
        s_state.schema.release = None
        s_state.schema.private_data = None

    def pop_next_into(self, out_array: ArrowArray) -> None:
        with self._lock:
            if self.idx >= len(self.chunks):
                ctypes.memset(ctypes.addressof(out_array), 0, ctypes.sizeof(ArrowArray))
                return
            chunk = self.chunks[self.idx]
            self.idx += 1
        ctypes.memmove(ctypes.addressof(out_array), ctypes.addressof(chunk.array), ctypes.sizeof(ArrowArray))
        chunk.array.release = None
        chunk.array.private_data = None

    def close_unpopped(self) -> None:
        with self._lock:
            if self.released:
                return
            self.released = True
            rem = self.chunks[self.idx :]
            self.idx = len(self.chunks)
        for chunk in rem:
            _pop_state(chunk.token)
            chunk.release_pin()


class ArrowArrayExport:
    """Zero-copy Arrow C Data / PyCapsule producer wrapping a PEP 3118 buffer."""

    def __init__(self, buf: Any, *, name: str = "") -> None:
        self._buf = buf
        self._name = name
        # Validate eagerly without leaking a pin on validation failure.
        probe = _ExportedArrayState(buf)
        self.py_format = probe.py_format
        self.arrow_format = probe.arrow_format
        self.length = probe.length
        _pop_state(probe.token)
        probe.release_pin()

    def __arrow_c_schema__(self) -> Any:
        s_state = _ExportedSchemaState(self.arrow_format, self._name)
        return _PyCapsule_New(ctypes.addressof(s_state.schema), _SCHEMA_NAME_BYTES, _SCHEMA_DTOR_ADDR)

    def __arrow_c_array__(self, requested_schema: Any = None) -> tuple[Any, Any]:
        del requested_schema
        s_state = _ExportedSchemaState(self.arrow_format, self._name)
        schema_cap = _PyCapsule_New(ctypes.addressof(s_state.schema), _SCHEMA_NAME_BYTES, _SCHEMA_DTOR_ADDR)
        a_state = _ExportedArrayState(self._buf)
        array_cap = _PyCapsule_New(ctypes.addressof(a_state.array), _ARRAY_NAME_BYTES, _ARRAY_DTOR_ADDR)
        return schema_cap, array_cap


class ArrowStreamExport:
    """Zero-copy Arrow C Stream / PyCapsule producer over multiple homogenous buffers."""

    def __init__(self, buffers: list[Any], *, name: str = "") -> None:
        self._buffers = list(buffers)
        self._name = name
        probe = _ExportedStreamState(self._buffers, name)
        self.py_format = probe.py_format
        self.arrow_format = probe.arrow_format
        self.chunk_count = len(self._buffers)
        _pop_state(probe.token)
        probe.close_unpopped()

    def __arrow_c_stream__(self, requested_schema: Any = None) -> Any:
        del requested_schema
        st_state = _ExportedStreamState(self._buffers, self._name)
        return _PyCapsule_New(ctypes.addressof(st_state.stream), _STREAM_NAME_BYTES, _STREAM_DTOR_ADDR)


class ImportedArrowBuffer:
    """Owned zero-copy view of an imported ArrowSchema + ArrowArray pair.

    Guarantees deterministic `release()` of both C structs on `close()` / context exit
    and blocks `close()` with `BufferError` while any `.memoryview()` backing array
    remains alive.
    """

    def __init__(self, schema: ArrowSchema, array: ArrowArray) -> None:
        self._schema = schema
        self._array = array
        self._closed = False
        self._open_views = 0
        self._view_lock = threading.Lock()
        try:
            if not schema.release:
                raise ValueError("ArrowSchema has NULL release callback (already released)")
            if not array.release:
                raise ValueError("ArrowArray has NULL release callback (already released)")
            if not schema.format:
                raise ValueError("ArrowSchema has NULL format string")
            arrow_fmt = schema.format.decode("ascii")
            if arrow_fmt not in _ARROW_TO_PY_DTYPE:
                raise ValueError(f"unsupported ArrowSchema format: {arrow_fmt!r}")
            if schema.n_children != 0 or schema.dictionary:
                raise ValueError("nested/dictionary Arrow types are unsupported in primitive 1D buffer import")
            if array.null_count != 0:
                raise ValueError(f"ArrowArray with non-zero nulls (null_count={array.null_count}) is unsupported")
            if array.length < 0 or array.offset < 0:
                raise ValueError(f"invalid ArrowArray bounds: length={array.length}, offset={array.offset}")
            if array.n_buffers < 2 or not array.buffers:
                raise ValueError(f"ArrowArray primitive buffer requires n_buffers >= 2, got {array.n_buffers}")

            py_fmt, itemsize = _ARROW_TO_PY_DTYPE[arrow_fmt]
            byte_len = array.length * itemsize
            offset_bytes = array.offset * itemsize
            if byte_len > sys.maxsize or offset_bytes > sys.maxsize or (offset_bytes + byte_len) > sys.maxsize:
                raise OverflowError("ArrowArray byte span overflows ssize_t")

            base_addr = int(array.buffers[1] or 0)
            if array.length > 0 and base_addr == 0:
                raise ValueError("non-empty ArrowArray has NULL data buffer[1]")

            self.arrow_format = arrow_fmt
            self.format = py_fmt
            self.itemsize = itemsize
            self.length = int(array.length)
            self.offset = int(array.offset)
            self.byte_len = int(byte_len)
            self.data_address = (base_addr + offset_bytes) if array.length > 0 else 0
            self.name = schema.name.decode("utf-8", errors="replace") if schema.name else ""
        except Exception:
            self.close()
            raise

    @property
    def is_closed(self) -> bool:
        return self._closed

    def __buffer__(self, flags: int) -> memoryview:
        del flags
        with self._view_lock:
            if self._closed:
                raise ValueError("cannot export memoryview from closed ImportedArrowBuffer")
            if self.length == 0:
                mv_empty = memoryview(b"")
                mv = mv_empty if self.format == "B" else mv_empty.cast(self.format)
            else:
                mv_raw = _PyMemoryView_FromMemory(self.data_address, self.byte_len, _PYBUF_READ)
                mv = mv_raw if self.format == "B" else mv_raw.cast(self.format)
            self._open_views += 1
            return mv

    def __release_buffer__(self, view: memoryview) -> None:
        view.release()
        with self._view_lock:
            if self._open_views > 0:
                self._open_views -= 1

    def memoryview(self) -> memoryview:
        if self._closed:
            raise ValueError("cannot create memoryview from closed ImportedArrowBuffer")
        return memoryview(self)

    def __len__(self) -> int:
        return self.length

    def __enter__(self) -> ImportedArrowBuffer:
        if self._closed:
            raise ValueError("cannot enter closed ImportedArrowBuffer")
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        with self._view_lock:
            if self._closed:
                return
            if self._open_views > 0:
                raise BufferError("cannot close ImportedArrowBuffer while an exported memoryview is still open")
            self._closed = True
        if self._array.release:
            rel_a = _ARRAY_RELEASE_CB_TYPE(self._array.release)
            rel_a(ctypes.byref(self._array))
            self._array.release = None
        if self._schema.release:
            rel_s = _SCHEMA_RELEASE_CB_TYPE(self._schema.release)
            rel_s(ctypes.byref(self._schema))
            self._schema.release = None

    def __del__(self) -> None:
        if not getattr(self, "_closed", True):
            self._closed = True
            if self._array.release:
                rel_a = _ARRAY_RELEASE_CB_TYPE(self._array.release)
                rel_a(ctypes.byref(self._array))
                self._array.release = None
            if self._schema.release:
                rel_s = _SCHEMA_RELEASE_CB_TYPE(self._schema.release)
                rel_s(ctypes.byref(self._schema))
                self._schema.release = None


def export_arrow_array(buf: Any, *, name: str = "") -> ArrowArrayExport:
    """Wrap a PEP 3118 buffer (e.g. Nox `F64Buffer`/`I64Buffer`/`I32Buffer`/`U8Buffer`) for zero-copy Arrow export."""
    return ArrowArrayExport(buf, name=name)


def _consume_schema_capsule(cap: Any) -> ArrowSchema:
    if not _PyCapsule_IsValid(cap, _SCHEMA_NAME_BYTES):
        actual = _PyCapsule_GetName(cap)
        raise TypeError(f"expected 'arrow_schema' PyCapsule, got {actual!r}")
    src_ptr = _PyCapsule_GetPointer(cap, _SCHEMA_NAME_BYTES)
    if not src_ptr:
        raise ValueError("arrow_schema PyCapsule has NULL pointer")
    out = ArrowSchema()
    ctypes.memmove(ctypes.addressof(out), src_ptr, ctypes.sizeof(ArrowSchema))
    # Clear source and mark capsule consumed so its destructor does not double-release.
    ctypes.cast(src_ptr, ctypes.POINTER(ArrowSchema)).contents.release = None
    _PyCapsule_SetName(cap, _USED_SCHEMA_NAME_BYTES)
    return out


def _consume_array_capsule(cap: Any) -> ArrowArray:
    if not _PyCapsule_IsValid(cap, _ARRAY_NAME_BYTES):
        actual = _PyCapsule_GetName(cap)
        raise TypeError(f"expected 'arrow_array' PyCapsule, got {actual!r}")
    src_ptr = _PyCapsule_GetPointer(cap, _ARRAY_NAME_BYTES)
    if not src_ptr:
        raise ValueError("arrow_array PyCapsule has NULL pointer")
    out = ArrowArray()
    ctypes.memmove(ctypes.addressof(out), src_ptr, ctypes.sizeof(ArrowArray))
    ctypes.cast(src_ptr, ctypes.POINTER(ArrowArray)).contents.release = None
    _PyCapsule_SetName(cap, _USED_ARRAY_NAME_BYTES)
    return out


def _copy_schema_struct(src: ArrowSchema) -> ArrowSchema:
    dup = ArrowSchema()
    ctypes.memmove(ctypes.addressof(dup), ctypes.addressof(src), ctypes.sizeof(ArrowSchema))
    return dup


def import_arrow_array(source: Any) -> ImportedArrowBuffer:
    """Import an `(arrow_schema, arrow_array)` pair or `__arrow_c_array__` provider zero-copy."""
    if isinstance(source, tuple) and len(source) == 2:
        schema_cap, array_cap = source
    elif hasattr(source, "__arrow_c_array__"):
        schema_cap, array_cap = source.__arrow_c_array__(None)
    else:
        raise TypeError("source must provide __arrow_c_array__() or be a (schema_capsule, array_capsule) tuple")

    schema = _consume_schema_capsule(schema_cap)
    try:
        array = _consume_array_capsule(array_cap)
    except Exception:
        if schema.release:
            _SCHEMA_RELEASE_CB_TYPE(schema.release)(ctypes.byref(schema))
        raise
    return ImportedArrowBuffer(schema, array)


def export_arrow_stream(buffers: list[Any], *, name: str = "") -> ArrowStreamExport:
    """Wrap one or more homogenous 1D PEP 3118 buffers as an Arrow C Stream producer."""
    return ArrowStreamExport(buffers, name=name)


def import_arrow_stream(source: Any) -> list[ImportedArrowBuffer]:
    """Consume an `arrow_array_stream` PyCapsule or `__arrow_c_stream__` provider into zero-copy chunks."""
    if hasattr(source, "__arrow_c_stream__"):
        cap = source.__arrow_c_stream__(None)
    else:
        cap = source
    if not _PyCapsule_IsValid(cap, _STREAM_NAME_BYTES):
        actual = _PyCapsule_GetName(cap) if hasattr(cap, "__class__") else None
        raise TypeError(f"expected 'arrow_array_stream' PyCapsule, got {actual!r}")
    src_ptr = _PyCapsule_GetPointer(cap, _STREAM_NAME_BYTES)
    if not src_ptr:
        raise ValueError("arrow_array_stream PyCapsule has NULL pointer")

    stream = ArrowArrayStream()
    ctypes.memmove(ctypes.addressof(stream), src_ptr, ctypes.sizeof(ArrowArrayStream))
    ctypes.cast(src_ptr, ctypes.POINTER(ArrowArrayStream)).contents.release = None
    _PyCapsule_SetName(cap, _USED_STREAM_NAME_BYTES)

    chunks: list[ImportedArrowBuffer] = []
    try:
        if not stream.release or not stream.get_schema or not stream.get_next:
            raise ValueError("invalid or already-released ArrowArrayStream")
        get_schema_fn = _STREAM_GET_SCHEMA_CB_TYPE(stream.get_schema)
        get_next_fn = _STREAM_GET_NEXT_CB_TYPE(stream.get_next)
        last_err_fn = (
            _STREAM_GET_LAST_ERROR_CB_TYPE(stream.get_last_error) if stream.get_last_error else None
        )

        base_schema = ArrowSchema()
        rc = get_schema_fn(ctypes.byref(stream), ctypes.byref(base_schema))
        if rc != 0:
            detail = last_err_fn(ctypes.byref(stream)) if last_err_fn else None
            raise RuntimeError(f"ArrowArrayStream.get_schema failed (errno={rc}): {detail!r}")
        try:
            if not base_schema.release or not base_schema.format:
                raise ValueError("ArrowArrayStream returned invalid schema")
            fmt = base_schema.format.decode("ascii")
            fmt_bytes = fmt.encode("ascii")
            name_bytes = bytes(base_schema.name) if base_schema.name else b""
            while True:
                arr = ArrowArray()
                rc_n = get_next_fn(ctypes.byref(stream), ctypes.byref(arr))
                if rc_n != 0:
                    detail = last_err_fn(ctypes.byref(stream)) if last_err_fn else None
                    raise RuntimeError(f"ArrowArrayStream.get_next failed (errno={rc_n}): {detail!r}")
                if not arr.release:
                    break
                s_state = _ExportedSchemaState(fmt_bytes.decode("ascii"), name_bytes.decode("utf-8", errors="replace"))
                chunks.append(ImportedArrowBuffer(_copy_schema_struct(s_state.schema), arr))
                s_state.schema.release = None
                s_state.schema.private_data = None
        finally:
            if base_schema.release:
                _SCHEMA_RELEASE_CB_TYPE(base_schema.release)(ctypes.byref(base_schema))
                base_schema.release = None
    except Exception:
        for ch in chunks:
            try:
                ch.close()
            except BufferError:
                pass
        raise
    finally:
        if stream.release:
            _STREAM_RELEASE_CB_TYPE(stream.release)(ctypes.byref(stream))
            stream.release = None
    return chunks
