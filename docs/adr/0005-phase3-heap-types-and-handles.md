# ADR-0005: CPython 3.15 `abi3t` Heap Types, Per-Instance `NoxHostHandle` Ownership, and GC Lifecycle (Phase 3)

- **Status:** Accepted (Phase 3 Foundation)
- **Date:** 2026-11-26
- **Related:** `AGENTS.md` §5, §9, §10, §12; `UR-006`; `UR-009`; `docs/architecture/threading.md`

## Context

Phase 3 introduces stateful native Python classes wrapping Nox objects exposed over `include/nox_host.h` (`NoxHostHandle`). Under `Py_TARGET_ABI3T=0x030F0000`, `_Py_OPAQUE_PYOBJECT` hides all `PyObject` and `PyTypeObject` internals, and dual `abi3`/`abi3t` execution requires strict separation between interpreter-local types, Nox handle ownership, CPython GC traversal, and free-threaded `3.15t` concurrency.

## Decision

1. **Interpreter-Local Heap Types (`PyType_FromModuleAndSpec`):**
   - Every native class is created per module execution in `Py_mod_exec` via `PyType_FromModuleAndSpec`, using flags `Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE` (no subclassing by default; no static `PyTypeObject` globals).
   - Native instance state `NoxPyObjectData` is stored using PEP 697 negative `basicsize = -(Py_ssize_t)sizeof(NoxPyObjectData)` and accessed strictly via `PyObject_GetTypeData(self, cls)` during normal calls and `PyObject_GetTypeData_DuringGC(self, cls)` inside `Py_tp_traverse` / `Py_tp_clear`.

2. **Dedicated Context-Per-Instance Ownership (`owner_ctx` + `NoxHostHandle`):**
   - Upstream `include/nox_host.h` (`v1`/`v2`) binds every `NoxHostHandle` strictly to the `NoxHostContext` that created it, and guards each `NoxHostContext` with a single-call context lock (`host-abi.md:71`).
   - Therefore, giving each Python wrapper instance its own initialized `NoxHostContext *owner_ctx` + `NoxHostHandle handle` achieves **100% lock-free parallelism across distinct instances** on `python3.15t` while preserving generation-checked handle safety.
   - Because handles cannot cross `NoxHostContext` boundaries, Phase 3 wrapper classes treat `owner_ctx` as the exclusive owner of `handle`, paired with a per-instance `pthread_mutex_t` for `instance_locked` access and safe `close()` serialization.

3. **GC & Idempotent Shutdown Contracts (`AGENTS.md` §9, §10.5):**
   - Each live instance holds a strong reference to `owner_module` (`Py_NewRef`), exposed via `Py_tp_traverse` (`Py_VISIT(Py_TYPE(self)); Py_VISIT(data->owner_module);`) and cleared in `Py_tp_clear`.
   - `noxpy_obj_close(data)` swaps `(owner_ctx, handle, owner_module, host_ops)` out under `data->lock`, marks `closed = 1`, releases `data->lock`, and **outside any lock** invokes `handle_release`, `module_fini` + `context_destroy`, and `Py_DECREF(owner_module)`.
   - `noxpy_obj_dealloc` untracks (`PyObject_GC_UnTrack`), closes idempotently, destroys `pthread_mutex_t`, frees via `PyType_GetSlot(cls, Py_tp_free)`, and calls `Py_DECREF(cls)`.

4. **Thread Policies (`NOXPY_POLICY_*`):**
   - `parallel` (`0u`): distinct instances execute in parallel without blocking each other.
   - `instance_locked` (`1u`): default for mutable classes; serializes concurrent mutations on the *same* Python instance via `data->lock`.
   - `thread_affine` (`2u`): records `owner_tid` on `__init__` and rejects calls from any other OS thread with `RuntimeError`.

5. **Method & Protocol Safety Boundaries:**
   - Equality (`__eq__`/`__ne__`) returns `NotImplemented` (via `Py_GetConstantBorrowed(Py_CONSTANT_NOT_IMPLEMENTED)`) for mismatched types, raises on closed objects or native errors where explicit comparison is invoked, and keeps mutable instances unhashable (`Py_tp_hash = PyObject_HashNotImplemented`).
   - Resource-owning classes expose idempotent `close()` plus context-manager (`__enter__`/`__exit__`) closing on both normal exit and exceptions.
