# ADR-0008: Buffers, NumPy, and Ownership Contracts

- **Status:** Accepted (Phase 5 — `0.5`, verified on `nox-lang v2.2.95`)
- **Date:** 2026-11-26
- **Related:** `AGENTS.md` §5, §8, §9, §12; `UPSTREAM_REQUIREMENTS.md` (`UR-012`); [`src/buffer/spec.nox`](../../src/buffer/spec.nox); [`native/include/noxpy_abi.h`](../../native/include/noxpy_abi.h); [`native/cpython/noxpy_shim.c`](../../native/cpython/noxpy_shim.c); [`python/noxpy/numpy_adapter.py`](../../python/noxpy/numpy_adapter.py)

## Context

Phase 5 introduces native buffer interoperation, PEP 3118 `memoryview` / NumPy adapters, and bulk `NOX_HOST_VALUE_TYPED_ARRAY` input marshaling for Python 3.15 (`3.15` and free-threaded `3.15t`) under `Py_TARGET_ABI3T=0x030F0000`.

Two constraints govern the design:
1. CPython objects (`memoryview`, `numpy.ndarray`, `bytearray`) and Nox buffers (`nox.host.F64Buffer`, `I64Buffer`, `I32Buffer`, `U8Buffer`) live in separate memory domains with distinct lifetimes.
2. Under free-threaded CPython (`3.15t`), multiple Python threads can simultaneously export `memoryview` objects, call `.writable()`, call `.close()`, or pass PEP 3118 buffers into Nox functions.

## Decisions

### 1. Zero-Copy Read/Write Export via PEP 3118 Owner Pinning (`F64/I64/I32/U8Buffer`)
- Generated wrappers expose upstream `nox.host.{F64,I64,I32,U8}Buffer` handles as `abi3t` heap types (`Py_bf_getbuffer`, `Py_bf_releasebuffer`, `Py_sq_length`) using ONLY documented `abi3t` C APIs (`PyBuffer_IsContiguous`, `PyBuffer_Release`, `PyObject_CheckBuffer`, `PyObject_GetBuffer`, `PyMemoryView_FromObject`, `PyExc_BufferError`).
- Default `memoryview(buf)` is **read-only** (`readonly = 1`). Every `bf_getbuffer` acquires an upstream pin via `nox_host_handle_pin_buffer` and increments `NoxPyObjectData.active_pins`.
- Exclusive mutable access requires calling `buf.writable()`, which returns a `_WritableBufferView` heap-type proxy holding a dedicated strong reference to the parent buffer (`obj_ref`). While `_WritableBufferView` exists or any PEP 3118 export is live (`active_pins > 0`), `buf.close()` and `__exit__` fail loudly with `BufferError` (matching CPython `bytearray` semantics) instead of leaving dangling pointers, and simultaneous mutable exports are rejected with `BufferError`.
- Each `bf_releasebuffer` releases the corresponding upstream pin (`nox_host_buffer_unpin`) and decrements `active_pins`. Nox-side mutations while pinned (`resize` / invalid handle access) return `NOX_HOST_ERR_BUSY`, mapped to `PyExc_BufferError`.
- On invalid or non-contiguous upstream `NoxHostBufferView` metadata (`itemsize`, `ndim`, negative/overflowing byte lengths or non-matching `strides[0]`), `bf_getbuffer` unpins immediately and raises `BufferError`.

### 2. Bulk Typed Inputs vs Zero-Copy Boundary Semantics
- Passing a 1D C-contiguous PEP 3118 buffer (`memoryview`, `bytes`, `bytearray`, NumPy 1D array) to a Nox parameter typed `list[float]`, `list[int]`, or `list[bool]` uses `NOX_HOST_VALUE_TYPED_ARRAY` when its format (`d`/`q`/`l`/`i`/`B`/`?` with native endianness and `itemsize` match) and contiguous byte size pass overflow validation.
- **Honest copy accounting:** `NOX_HOST_VALUE_TYPED_ARRAY` avoids creating millions of temporary Python `PyFloatObject`/`PyLongObject` heap boxes (O(1) Python boxing overhead across the C boundary), performing **one bulk memcpy** into the Nox host context. True zero-copy aliasing happens exclusively through `F64Buffer`/`I64Buffer`/`I32Buffer`/`U8Buffer` PEP 3118 views (`memoryview(buf)` and `buf.writable()`).
- Contiguous typed inputs returning from Nox functions remain standard Python `list[...]` values per upstream `v2.2.95` contract; explicit zero-copy outputs use the `nox.host.*Buffer` classes.

### 3. Shared Context Lifetime for Buffer Factory Functions
- Top-level Nox functions and `@export class` methods that construct and return `nox.host.*Buffer` handles allocate into a `NoxHostContext` stored inside a `NoxPySharedCtx` refcounted holder (`noxpy_buf_new_from_shared_handle`) rather than releasing the context back to the module pool while the handle is alive. The shared context is destroyed only when the last referencing Python buffer handle closes or is garbage-collected.

### 4. Optional NumPy Integration Without Core ABI Pollution
- [`python/noxpy/numpy_adapter.py`](../../python/noxpy/numpy_adapter.py) provides `as_typed_input(obj)`, `from_buffer(buf, copy=False)`, and `writable_array(buf)` on top of standard PEP 3118 `memoryview` / `np.asarray`.
- Core `.abi3t.so` binaries have zero compile-time or link-time NumPy dependency (`import numpy` is lazy and optional), preserving dual `3.15` / `3.15t` compatibility and `Py_MOD_GIL_NOT_USED`.

### 5. Addendum (Phase 6 — Arrow C Data & C Stream PyCapsule Ownership)
- [`python/noxpy/arrow_adapter.py`](../../python/noxpy/arrow_adapter.py) implements the Arrow C Data Interface, C Stream Interface, and `__arrow_c_schema__` / `__arrow_c_array__` / `__arrow_c_stream__` PyCapsule protocol using `ctypes` over CPython's public `PyObject_GetBuffer` / `PyBuffer_Release` / `PyCapsule_*` APIs (`zero` compile/link `pyarrow` dependency).
- **Producer Pinning (`export_arrow_array`, `export_arrow_stream`):** Keeps a live PEP 3118 `_PyBuffer` pin on the exported Nox/Python buffer in a thread-safe registry until either the consumer invokes `ArrowArray.release(&arr)` or the unconsumed `PyCapsule` destructor runs (`used_arrow_*` rename avoids double release after ownership move).
- **Consumer Validation (`import_arrow_array`, `import_arrow_stream`):** Rejects already-released structs, non-zero `null_count`, unsupported dtypes, NULL data buffers, or overflowing `offset + length`, slices zero-copy read-only views accurately at `buffers[1] + offset * itemsize`, and blocks `close()` with `BufferError` while any exported `memoryview()` backing array stays live.

## Consequences
- Positive: True zero-copy read/write aliasing with Python `memoryview` and NumPy arrays (`copy=False`) without `ob_refcnt` hacks or disabling `Py_MOD_GIL_NOT_USED`.
- Positive: Bulk `list[float]` / `list[int]` calls accept `memoryview`/`array`/`np.ndarray` seamlessly without boxing every element into Python objects.
- Constraint: Calling `resize()` on a Nox buffer while exported via PEP 3118 fails with `BufferError`; callers must release all `memoryview` / `_WritableBufferView` references before `close()` or resizing.
