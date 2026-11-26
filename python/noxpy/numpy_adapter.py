"""Optional zero-copy NumPy interop adapter (`AGENTS.md` §3, §15; `ADR-0008`).

This module NEVER imports NumPy at NoxPy core import/build time. It is only imported
explicitly by user code that already depends on NumPy.
"""

from __future__ import annotations

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


@contextmanager
def writable_array(buf: Any) -> Iterator[Any]:
    """Acquire an exclusive zero-copy writable `numpy.ndarray` view over a Nox buffer.

    Guarantees deterministic unpinning on exit (so subsequent Nox `resize()` or new
    pins never see leaked exclusive locks). Raises `BufferError` if another view is
    currently pinned.
    """
    np = _require_numpy()
    proxy = buf.writable()
    mv = memoryview(proxy)
    try:
        arr = np.frombuffer(mv, dtype=np.dtype(mv.format))
        yield arr
    finally:
        mv.release()


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
