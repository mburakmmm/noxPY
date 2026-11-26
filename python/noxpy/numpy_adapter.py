"""Optional zero-copy NumPy interop adapter (`AGENTS.md` §3, §15; `ADR-0008`).

This module NEVER imports NumPy at NoxPy core import/build time. It is only imported
explicitly by user code that already depends on NumPy.
"""

from __future__ import annotations

import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


def _require_numpy() -> Any:
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "noxpy.numpy_adapter requires 'numpy' to be installed in the active Python environment"
        ) from exc
    return np


def as_ndarray(buf: Any) -> Any:
    """Return a zero-copy read-only `numpy.ndarray` view over a pinned Nox buffer.

    The returned ndarray shares the native Nox buffer storage directly without copying
    (`arr.flags.writeable == False`) and holds a PEP 3118 pin via its `base` `memoryview`
    until the array is garbage-collected.
    """
    np = _require_numpy()
    mv = memoryview(buf)
    arr = np.asarray(mv)
    arr.setflags(write=False)
    return arr


def as_writable_ndarray(buf: Any) -> Any:
    """Return an exclusive zero-copy writable `numpy.ndarray` view over a Nox buffer.

    Unlike `writable_array()`, the returned `ndarray` owns the PEP 3118 exclusive pin via
    its `base` `memoryview` until the array and all derived slices/views are garbage-collected.
    Raises `BufferError` if another view already pins the buffer.
    """
    np = _require_numpy()
    proxy = buf.writable()
    mv = memoryview(proxy)
    return np.frombuffer(mv, dtype=np.dtype(mv.format))


@contextmanager
def writable_array(buf: Any, *, strict: bool = False) -> Iterator[Any]:
    """Acquire an exclusive zero-copy writable `numpy.ndarray` view over a Nox buffer.

    Deterministically unpins on exit when no external reference to the `ndarray` or any
    derived slice/view escaped the `with` block. If an external view or slice remains
    alive on exit, the native buffer stays safely pinned (preventing use-after-free on
    subsequent `close()` or `resize()`); `arr.setflags(write=False)` is also applied on
    the yielded array, and `strict=True` raises `BufferError` on escape.
    """
    np = _require_numpy()
    proxy = buf.writable()
    mv = memoryview(proxy)
    arr = np.frombuffer(mv, dtype=np.dtype(mv.format))
    arr_wr = weakref.ref(arr)
    mv_wr = weakref.ref(mv)
    try:
        yield arr
    finally:
        arr.setflags(write=False)
        # Drop our own strong refs; if neither `arr` nor any derived view/slice escaped,
        # `arr_wr()` and `mv_wr()` immediately become None (releasing the PEP 3118 pin).
        # If any view or slice escaped, `mv` stays alive & pinned, preventing UAF on close/resize.
        del arr
        del mv
        del proxy
        escaped = (arr_wr() is not None) or (mv_wr() is not None)
        if strict and escaped:
            raise BufferError(
                "noxpy.numpy_adapter.writable_array: view or slice escaped context manager; "
                "buffer remains pinned until all views are released"
            )


def as_typed_input(arr: Any, *, allow_copy: bool = False) -> Any:
    """Validate/prepare a 1D C-contiguous native-endian buffer for bulk `TYPED_ARRAY` calls.

    By default (`allow_copy=False`), raises `ValueError` if `arr` is non-contiguous, not 1D,
    or non-native endianness rather than silently copying. With `allow_copy=True`, uses
    `np.ascontiguousarray` with native byte order when needed.
    """
    mv = memoryview(arr)
    if mv.ndim != 1:
        mv.release()
        raise ValueError(f"expected 1D buffer for TYPED_ARRAY input, got ndim={mv.ndim}")
    is_contig = mv.c_contiguous
    fmt = mv.format or "B"
    mv.release()

    if not is_contig:
        if not allow_copy:
            raise ValueError("typed input array is not C-contiguous (pass allow_copy=True to copy)")
        np = _require_numpy()
        return np.ascontiguousarray(arr)

    if fmt.startswith((">", "!")):
        # Swap only if host is little-endian
        import sys

        if sys.byteorder == "little":
            if not allow_copy:
                raise ValueError("typed input array has non-native endianness (pass allow_copy=True to normalize)")
            np = _require_numpy()
            return np.ascontiguousarray(arr, dtype=np.dtype(arr.dtype).newbyteorder("="))
    elif fmt.startswith("<"):
        import sys

        if sys.byteorder == "big":
            if not allow_copy:
                raise ValueError("typed input array has non-native endianness (pass allow_copy=True to normalize)")
            np = _require_numpy()
            return np.ascontiguousarray(arr, dtype=np.dtype(arr.dtype).newbyteorder("="))

    return arr
