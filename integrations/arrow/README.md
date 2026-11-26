# Arrow C Data & C Stream Integration (`noxpy.arrow_adapter`)

Optional zero-copy interoperability between NoxPy PEP 3118 pinnable buffers (`F64Buffer`, `I64Buffer`, `I32Buffer`, `U8Buffer`) / Python buffer objects and Apache Arrow consumers (`pyarrow`, `polars`, `duckdb`, etc.) via the Arrow C Data Interface, C Stream Interface, and Arrow PyCapsule protocol.

- **Zero core ABI pollution:** Uses only standard CPython public `PyCapsule` + PEP 3118 buffer APIs via `ctypes`; does **not** add any compile-time or link-time `pyarrow` dependency to `.abi3t.so` extensions and keeps `Py_MOD_GIL_NOT_USED` intact on free-threaded `3.15t`.
- **Zero-copy pin contract:** `export_arrow_array(buf)` pins the underlying Nox buffer via PEP 3118 (`active_pins > 0`). While exported, `buf.resize()` and `buf.close()` raise `BufferError`; calling the Arrow `release()` callback (or dropping an unconsumed capsule) unpins deterministically.

## Usage

```python
from noxpy.arrow_adapter import (
    export_arrow_array,
    import_arrow_array,
    export_arrow_stream,
    import_arrow_stream,
)
import atlas_demo

col = atlas_demo.make_f64_column(4)
with memoryview(col.writable()) as wmv:
    wmv[:] = atlas_demo.make_f64_column(4)  # or any sequence of 4 floats

exp = export_arrow_array(col, name="metric")
# Zero-copy import (works with any __arrow_c_array__ producer/consumer, including pyarrow.array(exp))
with import_arrow_array(exp) as view:
    mv = view.memoryview()
    assert list(mv) == [0.0, 0.0, 0.0, 0.0]

# Multi-chunk stream
stream = export_arrow_stream([col], name="metric")
chunks = import_arrow_stream(stream)
```

See [`docs/adr/0008-buffers-numpy-ownership.md`](../../docs/adr/0008-buffers-numpy-ownership.md) for ownership, `offset` slicing, `null_count == 0` checks, and `release()` idempotency rules.
