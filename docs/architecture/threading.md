# NoxPy Free-Threaded Synchronization & Lock-Order Specification

> Normative reference per `AGENTS.md` §10.

---

## 1. No Process-Global Invocation Mutex

NoxPy never uses a process-global mutex to serialize calls into Nox. Unrelated modules, unrelated `NoxHostContext`s, and distinct native class instances execute concurrently on ordinary CPython 3.15 (inside `Py_BEGIN_ALLOW_THREADS`) and unforced free-threaded CPython `3.15t` (`Py_MOD_GIL_NOT_USED`).

## 2. Canonical Lock Hierarchy

To prevent deadlocks across Python reference drops, module shutdown, and instance access, locks are strictly ordered from outer to inner and **never** acquired in reverse or nested across Python callbacks:

1. **`NoxPyObjectData.lock` (`pthread_mutex_t`, per-instance):**
   - Protects `data->closed`, `data->handle`, `data->owner_ctx`, and serializes `instance_locked` native invocations on a single Python wrapper instance.
   - **NEVER block on `data->lock` while holding the CPython GIL:** `noxpy_obj_enter_call`, `noxpy_obj_is_closed`, and `noxpy_obj_close_method` ALWAYS release the GIL (`Py_BEGIN_ALLOW_THREADS` / `Py_END_ALLOW_THREADS`) before `pthread_mutex_lock`, preventing GIL <-> instance-mutex lock-inversion deadlocks on ordinary CPython 3.15 (`tp_clear`/`tp_dealloc` paths call non-detaching `noxpy_obj_close` under GC).
   - Released before returning from `noxpy_obj_leave_call` and **always released BEFORE** `Py_DECREF`, `noxpy_set_exception_from_host`, or any Python C API call that could trigger finalizers (`AGENTS.md` §9, §10.4).
2. **`NoxPyModuleState` lifecycle atomics (`lifecycle`, `active_calls`):**
   - Lock-free acquisition (`NOXPY_MOD_READY` lease check -> increment `active_calls` -> re-check `lifecycle`).
   - `noxpy_module_state_close` transitions `READY -> CLOSING` and drains `active_calls` to `0` before destroying pooled contexts.
3. **`NoxPyModuleState.pool_lock` (`atomic_flag`, leaf lock):**
   - Protects only `pool` and `pool_count`. Held solely for O(1) pointer pop/push; never held during context creation, destruction, or native calls.

## 3. Thread Policy Matrix

| Policy Constant | Value | Creation Thread | Foreign Thread Access | Concurrent Same-Instance Behavior |
|---|---|---|---|---|
| `NOXPY_POLICY_PARALLEL` | `0u` | Any attached Python thread | Allowed | Guarded per-instance context lock (distinct instances run fully concurrent) |
| `NOXPY_POLICY_INSTANCE_LOCKED` | `1u` | Any attached Python thread | Allowed | Serialized per Python instance via `NoxPyObjectData.lock`; distinct instances run concurrently |
| `NOXPY_POLICY_THREAD_AFFINE` | `2u` | Recorded (`owner_tid = noxpy_current_tid()`) | Rejected (`RuntimeError`) before native entry | Only `owner_tid` allowed; teardown/dealloc releases safely |

## 4. Shutdown & Cleanup Guarantees

- `noxpy_obj_enter_call` validates `initialized`, `type_tag`, `thread_policy` (`thread_affine` TID match), `closed == 0`, `handle_is_valid`, and module lease (`noxpy_module_lease_acquire`) while attached to a valid `PyThreadState`.
- Native execution detaches Python thread state (`Py_BEGIN_ALLOW_THREADS` ... `Py_END_ALLOW_THREADS`) and never manipulates `PyObject*` while detached.
- `close()`, `__exit__()`, `tp_clear()`, and `tp_dealloc()` funnel through idempotent `noxpy_obj_close()`.
