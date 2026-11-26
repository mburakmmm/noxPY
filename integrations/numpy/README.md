# NumPy Interop Integration (`noxpy.numpy_adapter`)

Optional zero-copy interop helpers connecting NoxPy's PEP 3118 pinnable buffers (`F64Buffer`, `I64Buffer`, `I32Buffer`, `U8Buffer`) and bulk `TYPED_ARRAY` parameter path with NumPy without adding any build or runtime dependency to core `noxpy`.

## Usage

```python
import numpy as np
from noxpy.numpy_adapter import as_ndarray, writable_array, as_typed_input
import atlas_demo

col = atlas_demo.make_f64_column(1000)

# Zero-copy read-only ndarray view (allows concurrent readers, blocks Nox resize/write pins)
ro = as_ndarray(col)
assert ro.flags.writeable is False
del ro

# Deterministic exclusive writable view
with writable_array(col) as arr:
    arr[:] = np.arange(1000, dtype=np.float64) * 2.0

# Bulk single-copy TYPED_ARRAY fast path into list[float] / list[int] / list[bool]
total = atlas_demo.neumaier_sum_bulk(as_typed_input(np.linspace(0.0, 1.0, 10_000)))
```

See [`docs/adr/0008-buffers-numpy-ownership.md`](../../docs/adr/0008-buffers-numpy-ownership.md) for aliasing, exclusivity, and lifetime guarantees.
