# Nox Host ABI v3 Addendum (`UR-015` + Critical v3 Delta for `nox-lang`)

> Token-minimal delta brief on top of `docs/upstream/nox-host-abi-v3-freeze-spec.md` (`v2.2.87` baseline).

## 1. `UR-015` — Hosted Recoverable Integer Overflow & OOM Containment (`--host-abi`)

### Problem
In `v2.2.87`, `runtime/errors/handle.zig` (`nox_int_overflow_trap` at `:232`, `nox_out_of_memory` at `:227`) calls `haltProcess()` -> `std.process.exit(1)`. Inside a CPython extension (`--host-abi`), `add(INT64_MAX, 1)` or allocator exhaustion inside Nox **terminates the entire host Python process** instead of returning a recoverable C status (`OverflowError` / `MemoryError`).

### Required C ABI (`include/nox_host.h`, append-only)
Reuse existing `#define NOX_HOST_ERR_NOMEM 2` (do not duplicate OOM codes) and add:
```c
#define NOX_HOST_ERR_OVERFLOW           11 /* Hosted checked integer overflow trap */
#define NOX_HOST_CAP_RECOVERABLE_TRAPS  (1ULL << 8)
```

### Runtime / Codegen Contract (Respecting Nox Principle #3 — No Unwind Tables)
1. **Overflow (`nox_int_overflow_trap` / checked integer ops under `--host-abi`):**
   - Instead of calling `haltProcess()`, record a pending overflow trap on `RuntimeState` / `host_ctx` (via implicit error-return propagation `nox_exception_pending`, returning `NOX_HOST_ERR_OVERFLOW` from `nox_host_capture`).
   - Calling context remains healthy for subsequent calls (`add(20, 22) == 42`), OR if an unrecoverable internal state occurs, poisons *only* that `NoxHostContext` (`NOX_HOST_ERR_STATE` on reuse) without touching other contexts or exiting the host process.
2. **OOM (`nox_out_of_memory` / `NoxHostAllocator` returns `NULL` under `--host-abi`):**
   - When `rtState(rt).host_ctx != null`, MUST NOT call `std.process.exit(1)`. Return `NOX_HOST_ERR_NOMEM` cleanly to the host C wrapper; never kill the CPython process or corrupt unrelated `NoxHostContext`s.
3. **Standalone CLI / freestanding binaries:** Keep existing `haltProcess()` behavior unchanged outside `--host-abi`.
4. **Files:** `include/nox_host.h`, `runtime/host.zig`, `runtime/errors/handle.zig`, plus codegen `--host-abi` trap lowering.

### Required C Host Tests (`tests/compat/host_abi/`)
- Checked `add(INT64_MAX, 1)` returns `NOX_HOST_ERR_OVERFLOW` (process survives; next call `add(20, 22)` returns `42`).
- Failing `NoxHostAllocator` returns `NOX_HOST_ERR_NOMEM` without calling `abort`/`exit` or tainting a second `NoxHostContext`.

---

## 2. Critical Delta on Existing v3 Freeze Spec (`UR-009`, `UR-010`, `UR-014`)

1. **`UR-010` Reentrancy Fix (DO NOT globally unlock `ctx.lock` around `cb_ops.invoke`):**
   - Simply unlocking `ctx.lock` lets a *foreign* OS thread enter `ctx` and corrupt suspended Nox stack/ARC state.
   - Instead track `owner_tid`, `call_depth`, and `suspended_for_callback` on `NoxHostContext`:
     - Only `tid == owner_tid` while `suspended_for_callback == true` may reenter `ctx` (increments `call_depth`).
     - Foreign threads (`tid != owner_tid`) MUST block on condition variable/futex until `call_depth == 0` (or return `NOX_HOST_ERR_BUSY`).
     - `nox_host_context_destroy` rejects (`NOX_HOST_ERR_BUSY`) or defers while `call_depth > 0`.
     - Deferred ARC `cb_ops.release` MUST follow the exact same rule.
2. **`UR-014` Staged Freeze (`v3-rc` -> `v3-stable`):**
   - Freeze struct layouts (`_Static_assert` on `sizeof`/`offsetof`, append-only codes `0..11`) as **`v3-rc`** for Zig self-hosting bootstrap now; promote `docs/apis/stability.md` to **`v3-stable`** only after NoxPy Phases 3–6 validate the ABI end-to-end.
3. **`UR-009` Multi-Handle Same-Context Support:**
   - Generated class method wrappers MUST accept `>= 2` `NoxHostHandle` parameters belonging to the same `NoxHostContext` (e.g., `a.dot(b)`) with per-handle generation/type validation, keeping future **shared owner-context / arena** mode compatible without ABI changes.
