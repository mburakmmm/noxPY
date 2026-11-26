# Nox Host ABI v3 One-Shot Freeze Specification (`nox-lang` Baseline for Self-Hosting Bootstrap)

> **Purpose:** Complete, single-pass upstream requirements specification for `nox-lang` covering **all remaining phases of NoxPy (Phase 3 -> Phase 7 / 1.0)**. Implementing `UR-009` through `UR-014` in `nox-lang`'s Zig compiler/runtime promotes `include/nox_host.h` from Experimental (`v2`) to **Stable (append-only, like NNI v1)** so the Zig bootstrap host can freeze cleanly.

- **Current Verified Baseline:** `nox-lang v2.2.87` (`include/nox_host.h` v2, `NOX_HOST_ABI_VERSION = 2u`)
- **Target Frozen ABI Version:** `NOX_HOST_ABI_VERSION = 3u` (100% backward-compatible with v1 & v2 hosts: `cfg.abi_version` in `1..3`)
- **Non-Negotiable Invariant (`AGENTS.md` §2 & §6):** Nox heap object layouts, ARC headers, and mangled internal symbols NEVER leak across the C ABI boundary.

---

## Summary Roadmap of Remaining Upstream Batches

| ID | Unlocks NoxPy Phase | Scope | Blocking? |
|---|---|---|---|
| `UR-009` | **Phase 3 (`0.3`: Native Classes)** | `@export class` / methods / properties / static & classmethods / custom exceptions in `expand --json` + `--host-abi` | Yes for Phase 3 Slice 3B+ auto-codegen (`NoxHostHandle` runtime works in `v2.2.87`) |
| `UR-010` | **Phase 4 (`0.4`: Callbacks & Python Objects)** | `NoxHostExternalRef` + `NoxHostCallbackOps` + unlock-around-callback reentrancy + Python exception token preservation | Yes for Phase 4 callbacks |
| `UR-011` | **Phase 4 (`0.4`: Async Tasks)** | `@export async def` C task handles (`NoxHostTask`), completion callback, cancellation (`NOX_HOST_ERR_CANCELLED`), deadlock containment | Yes for Phase 4 `@pyasync` (`@pyiterator` needs only `UR-009`) |
| `UR-012` | **Phase 5 & 6 (`0.5/0.6`: Buffers, NumPy, Atlas, Arrow)** | Pinned contiguous zero-copy buffers (`nox_host_handle_pin_buffer` / `unpin`) + bulk typed array values (`NoxHostDType`) | Yes for zero-copy buffers / NumPy / Atlas / Arrow |
| `UR-013` | **Phase 4/6/7 (`0.4/0.6/1.0`: Allocator, Subinterpreters, Windows P0)** | Wire `NoxHostAllocator`, `nox_host_capabilities()`, subinterpreter global-state isolation audit, Windows COFF `.dll`/`.obj` `--host-abi` parity | Yes for subinterpreters, Windows P0, and custom allocators |
| `UR-014` | **Phase 7 (`1.0`: Zig Bootstrap Freeze)** | Freeze `nox_host.h` v3 in `docs/apis/stability.md`, add C ABI layout `_Static_assert` gate, allow `b"..."` default literal in checker | Yes for `nox-lang` self-hosting ABI freeze |

*(Language note: `list[int | None]` / boxed-optional scalar inside containers can remain a documented typechecker limit in Phase 2/3, OR be lifted independently without changing `nox_host.h`.)*

---

## `UR-009` — First-Class Classes, Methods, Properties, & Exceptions (Phase 3)

### 1. Why Existing v2 Is Not Enough
In `v2.2.87`, `noxc expand --json` (`compiler/main.zig:3958`) only iterates top-level `func_def` nodes with `@export`, ignoring classes, methods, properties, and custom `Exception` subclasses. Automatic NoxPy binding generation (`@pyclass` -> CPython `PyType_FromModuleAndSpec`) needs compiler-resolved class metadata and `--host-abi` dispatchers.

### 2. Required `noxc expand --json` Additions (Additive on Schema v1)
1. Support `@export class Counter:` (or class decorated `@export`, `@export("instance_locked")`, `@export("thread_affine")`) plus optional method decorators `@property`, `@staticmethod`, `@classmethod`, and `class MyError(ValueError):` marked `@export`.
2. In `exports[]`:
   - For an exported class (`"kind": "class"`):
     ```json
     {
       "kind": "class",
       "id": "fastmath::Counter",
       "name": "Counter",
       "doc": "...",
       "thread_policy": "instance_locked",
       "span": {"file": "...", "line": 10, "column": 1},
       "constructor": {
         "c_symbol": "nox_host_export_fastmath_Counter___new__",
         "params": [ ... ],
         "doc": "..."
       },
       "methods": [
         {
           "name": "inc",
           "method_kind": "instance",
           "c_symbol": "nox_host_export_fastmath_Counter_inc",
           "thread_policy": "instance_locked",
           "is_async": false,
           "params": [ ... ],
           "return_type": "int",
           "return_marshal": "scalar",
           "doc": "...",
           "span": { ... }
         }
       ]
     }
     ```
     Where `method_kind` is one of `"instance" | "staticmethod" | "classmethod" | "property_get" | "property_set" | "dunder"` (`dunder` covers `__repr__`, `__str__`, `__eq__`, `__len__`, `__iter__`, `__next__`, `__enter__`, `__exit__`).
   - For an exported exception (`"kind": "exception"`):
     ```json
     {
       "kind": "exception",
       "id": "fastmath::CounterError",
       "name": "CounterError",
       "base": "ValueError",
       "doc": "...",
       "span": { ... }
     }
     ```

### 3. Required `--host-abi` & `runtime/host.zig` Additions
1. `compiler/codegen_qbe/host_abi.zig` + `compiler/host_adapt.zig`: emit `nox_host_export_<mod>_<Class>___new__(ctx, ..., NoxHostHandle *out, err)` and `nox_host_export_<mod>_<Class>_<method>(ctx, NoxHostHandle self, ..., err)` using exact `marshal` rules (including `NoxHostValue` containers via `host_adapt.zig`).
2. `runtime/host.zig` / `include/nox_host.h`:
   - `NoxHostStatus nox_host_handle_type_name(NoxHostContext *ctx, NoxHostHandle h, NoxHostSpan *out);` (borrowed class name while `h` is valid; allows runtime type verification across module boundaries).
   - Add `#define NOX_HOST_VALUE_HANDLE 9` (`nox_host_value_handle(ctx, NoxHostHandle h, NoxHostValue *out)` & `nox_host_value_get_handle(ctx, NoxHostValue v, NoxHostHandle *out)`) so `Class | None` or containers of handles can traverse `NoxHostValue` cleanly without exposing class layouts.

---

## `UR-010` — Managed Host Object Handles & Reentrant Callbacks (Phase 4)

### 1. Design Contract
Nox code inside an extension must be able to hold and invoke opaque host objects (`PyObject` wrapped safely by NoxPy on the CPython side) without Nox ever dereferencing `PyObject*` or deadlocking `NoxHostContext.lock`.

### 2. Required C ABI (`include/nox_host.h`)
```c
typedef uint64_t NoxHostExternalRef; /* opaque host-managed object token; 0 = invalid */
#define NOX_HOST_ERR_HOST_EXCEPTION 8 /* callback raised a host (Python) exception */

typedef struct NoxHostCallbackOps {
    uint32_t struct_size;
    void *user_data;
    /* Called with ctx->lock UNLOCKED so reentrant host -> Nox calls cannot deadlock.
     * `args_tuple` is a borrowed NOX_HOST_VALUE_TUPLE; callback owns `*out_result` if set.
     * If host raises an exception, return NOX_HOST_ERR_HOST_EXCEPTION, set `*out_err_token`
     * (opaque host handle kept until `error_release`), and optional UTF-8 `*out_err_msg`. */
    NoxHostStatus (*invoke)(
        void *user_data,
        NoxHostContext *ctx,
        NoxHostExternalRef target,
        NoxHostValue args_tuple,
        NoxHostValue *out_result,
        uint64_t *out_err_token,
        NoxHostBuffer *out_err_msg);
    void (*retain)(void *user_data, NoxHostExternalRef ref);
    void (*release)(void *user_data, NoxHostExternalRef ref);
} NoxHostCallbackOps;

NoxHostStatus nox_host_context_set_callbacks(NoxHostContext *ctx, const NoxHostCallbackOps *ops);
uint64_t nox_host_error_host_token(const NoxHostError *err); /* 0 if Nox-originated exception */
```
Also add `#define NOX_HOST_VALUE_EXTERNAL_REF 11` (`nox_host_value_external_ref` / `nox_host_value_get_external_ref`) if external refs pass inside `NoxHostValue` containers.

### 3. Critical Reentrancy Rule (`runtime/host.zig`)
When Nox invokes a host callback through the runtime helper (`nox_host_call_external`):
1. Validate `ctx.callback_ops != null` (else raise Nox `RuntimeError`).
2. Marshall Nox arguments into temporary `NoxHostValue` tuple under `ctx.lock`.
3. **Unlock `ctx.lock`** -> call `cb_ops.invoke(...)` -> **re-lock `ctx.lock`** and re-establish `handOverArcOwner(ctx.rt)`.
4. If `invoke` returns `NOX_HOST_ERR_HOST_EXCEPTION`: propagate `*out_err_token` so if uncaught in Nox, `nox_host_capture` returns `NOX_HOST_ERR_HOST_EXCEPTION` and `nox_host_error_host_token(err)` lets NoxPy restore the original Python exception + traceback verbatim (`AGENTS.md` §11).
5. When Nox destroys a retained external ref via ARC (`releaseObject`), invoke `cb_ops.release` with `ctx.lock` unlocked.

### 4. `noxc expand --json` Support
Emit `"marshal": "external_ref"` / `"return_marshal": "external_ref"` for opaque host object parameters/returns (and corresponding C parameter/return `NoxHostExternalRef`).

---

## `UR-011` — Async Tasks & Cancellation (Phase 4)

*(Note: Iterators `@pyiterator` require NO new async primitive — `@export` `__iter__`/`__next__` or `T | None` next method works cleanly once `UR-009` is in place.)*

### 1. Design Contract
CPython `asyncio` cannot block the event loop waiting for Nox work (`AGENTS.md` §13.2). Nox must expose start/complete/cancel primitives on `NoxHostContext` (safe to drive from `async def` -> `loop.create_future()` + `loop.call_soon_threadsafe(...)`).

### 2. Required C ABI (`include/nox_host.h`)
```c
typedef uint64_t NoxHostTask;
#define NOX_HOST_ERR_CANCELLED 9

typedef void (*NoxHostTaskCallback)(
    void *user_data,
    NoxHostContext *ctx,
    NoxHostTask task,
    NoxHostStatus final_status); /* OK, ERR_EXCEPTION, ERR_HOST_EXCEPTION, or ERR_CANCELLED */

NoxHostStatus nox_host_task_cancel(NoxHostContext *ctx, NoxHostTask task);
NoxHostStatus nox_host_task_take_result(
    NoxHostContext *ctx,
    NoxHostTask task,
    NoxHostValue *out_val, /* NULL if None return; host releases */
    NoxHostError **out_err);
NoxHostStatus nox_host_task_release(NoxHostContext *ctx, NoxHostTask task);
```
And for every `@export async def foo(a: int, ...) -> T`, `--host-abi` generates:
```c
NoxHostStatus nox_host_async_start_<mod>_foo(
    NoxHostContext *ctx,
    <params...>,
    NoxHostTaskCallback on_done,
    void *user_data,
    NoxHostTask *out_task,
    NoxHostError **err);
```

### 3. Runtime Guarantees (`runtime/host.zig` + `async_rt/bridge_native.zig`)
- Run the async body on a worker/context scheduler without blocking the caller's thread.
- Guarantee `on_done` is invoked **at most and at least once** per successfully started task (`out_task != 0`).
- Scheduler deadlock (`error.Deadlock`) must NOT abort the Python interpreter process — transition the task to `NOX_HOST_ERR_STATE` + `NoxHostError("RuntimeError", "deadlock in async task")`.
- Emit `"is_async": true` in `noxc expand --json` (`compiler/main.zig`).

---

## `UR-012` — Pinned Zero-Copy Buffers & Bulk Typed Arrays (Phase 5 & Phase 6: PEP 3118 / NumPy / Atlas / Arrow)

### 1. Design Contract (`AGENTS.md` §14)
Python `memoryview`, NumPy (`__array_interface__` / buffer protocol), `Atlas` kernels, and Arrow C Data Interface require either:
- **Zero-copy pinned views** with strict lifetime + resize prohibition while pinned, OR
- **Single-memcpy bulk contiguous buffers** instead of element-by-element `NoxHostValue` boxing.

### 2. Required C ABI (`include/nox_host.h`)
```c
typedef enum NoxHostDType {
    NOX_HOST_DTYPE_U8 = 1,
    NOX_HOST_DTYPE_I8 = 2,
    NOX_HOST_DTYPE_U16 = 3,
    NOX_HOST_DTYPE_I16 = 4,
    NOX_HOST_DTYPE_U32 = 5,
    NOX_HOST_DTYPE_I32 = 6,
    NOX_HOST_DTYPE_U64 = 7,
    NOX_HOST_DTYPE_I64 = 8,
    NOX_HOST_DTYPE_F32 = 9,
    NOX_HOST_DTYPE_F64 = 10,
    NOX_HOST_DTYPE_BOOL = 11
} NoxHostDType;

#define NOX_HOST_ERR_BUSY 10 /* buffer has active conflicting pins (resize/exclusive write rejected) */

#define NOX_HOST_BUF_READONLY 0u
#define NOX_HOST_BUF_WRITABLE 1u
#define NOX_HOST_BUF_CONTIGUOUS_C 2u

typedef struct NoxHostBufferView {
    uint32_t struct_size;
    int32_t dtype;
    void *data;
    size_t byte_len;
    size_t itemsize;
    int32_t ndim;       /* 1..8 */
    uint32_t flags;     /* NOX_HOST_BUF_* bitmask */
    int64_t shape[8];
    int64_t strides[8]; /* in bytes */
    uint64_t pin_id;    /* pass to nox_host_buffer_unpin; retains backing storage until unpinned */
} NoxHostBufferView;

NoxHostStatus nox_host_handle_pin_buffer(
    NoxHostContext *ctx,
    NoxHostHandle h,
    uint32_t req_flags, /* 0 = readonly, NOX_HOST_BUF_WRITABLE = exclusive writable */
    NoxHostBufferView *out_view,
    NoxHostError **err);
NoxHostStatus nox_host_buffer_unpin(NoxHostContext *ctx, uint64_t pin_id);

/* Bulk contiguous typed array value (avoids N boxing calls for large vectors/tensors) */
#define NOX_HOST_VALUE_TYPED_ARRAY 10
NoxHostStatus nox_host_value_typed_array(
    NoxHostContext *ctx, int32_t dtype, const void *data, size_t count, NoxHostValue *out);
NoxHostStatus nox_host_value_get_typed_array(
    NoxHostContext *ctx, NoxHostValue v, int32_t *out_dtype, const void **out_data, size_t *out_count);
```

### 3. Runtime Guarantees
- Pinning increments a pin count on the backing Nox buffer/object (`NoxHostBufferView.pin_id`); multiple concurrent `READONLY` pins are allowed, at most one exclusive `WRITABLE` pin is allowed (returns `NOX_HOST_ERR_BUSY` otherwise).
- Resizing or freeing a pinned buffer in Nox while `pin_count > 0` MUST raise a recoverable `RuntimeError` (never UAF or relocate memory out from under `Py_buffer`).

---

## `UR-013` — Custom Allocator, Subinterpreter Isolation, Windows COFF P0, & Capability Bits (Phase 4/6/7)

1. **Allocator hook (`NoxHostContextConfig.allocator`):**
   - Reuse the exact `NoxHostAllocator` layout from `include/nox_embed.h:36-41` (`uint32_t size; void *ctx; void *(*alloc)(void*, size_t, size_t); void (*free)(void*, void*, size_t, size_t);`) so extensions can plug CPython / PyMem / Arena allocators per `NoxHostContext` (with `NULL` still defaulting to the system allocator).
2. **Runtime capability probing:**
   ```c
   #define NOX_HOST_CAP_VALUE_TREE        (1ULL << 0)
   #define NOX_HOST_CAP_CLASSES           (1ULL << 1)
   #define NOX_HOST_CAP_CALLBACKS         (1ULL << 2)
   #define NOX_HOST_CAP_ASYNC_TASKS       (1ULL << 3)
   #define NOX_HOST_CAP_PINNED_BUFFERS    (1ULL << 4)
   #define NOX_HOST_CAP_TYPED_ARRAYS      (1ULL << 5)
   #define NOX_HOST_CAP_CUSTOM_ALLOCATOR  (1ULL << 6)
   #define NOX_HOST_CAP_SUBINTERPRETERS   (1ULL << 7)
   uint64_t nox_host_capabilities(void);
   ```
3. **Subinterpreter safety (`NOX_HOST_CAP_SUBINTERPRETERS`):**
   - Audit `runtime/host.zig`, `runtime/alloc/dispatch_registry.zig`, and `runtime/str.zig` so two independent `NoxHostContext`s created inside two separate CPython subinterpreters share ZERO mutable global state (all module-level Nox globals live strictly in `RuntimeState`, destroyed per `NoxHostContext`).
4. **Windows x86-64 COFF P0 (`AGENTS.md` §5, §15.5):**
   - Verify `noxc build --backend llvm --host-abi --emit-obj` and `--emit-shared-lib` (`.dll` + import `.lib`) on `windows-latest`, exporting strictly `nox_host_*` (`dumpbin /exports`) cleanly linkable into CPython `.pyd` extensions with `clang`/`lld-link`.

---

## `UR-014` — Zig Host ABI Freeze Gate (`1.0` Readiness)

1. **`docs/apis/stability.md` promotion:** Promote `Host ABI (nox_host.h)` from **experimental** to **stable (append-only, same promise as NNI v1)**: existing struct sizes, enum IDs, status codes (`0..10`), and v1/v2/v3 function signatures are frozen; future additions only append new functions/fields/capabilities.
2. **ABI Freeze Regression Test (`tests/compat/host_abi/`):** Add a C11 `_Static_assert` test pinning `sizeof`/`offsetof` for `NoxHostContextConfig`, `NoxHostSpan`, `NoxHostBuffer`, `NoxHostCallbackOps`, `NoxHostBufferView`, and `NOX_HOST_ABI_VERSION`.
3. **Minor Phase 2 checker polish:** Allow constant `b"..."` bytes literals as default parameter values in the typechecker (currently `writeDefaultJson` in `compiler/main.zig:3883` already serializes `.bytes_lit` to JSON, only the checker error message rejects it).

---

## Exact `nox-lang` Files Involved (Implementation Checklist for One-Shot Freeze)

| `nox-lang` Path | Role in v3 Freeze |
|---|---|
| `include/nox_host.h` | Add `NOX_HOST_ABI_VERSION 3u`, handle type name, callback/task/buffer/capability declarations (append-only) |
| `runtime/host.zig` | Handle type-name helper, `cb_ops` unlock-around-invoke, async task table/scheduler bridge, buffer pin table, typed array nodes, custom allocator routing, capability bitmask |
| `compiler/main.zig` | `expand --json` `kind: "class"` / `"exception"`, `is_async`, `"external_ref"` marshal; optional `b"..."` default literal acceptance |
| `compiler/host_adapt.zig` | Method/constructor/`@pyclass` container adapters + `async def` task trampoline synthesis |
| `compiler/codegen_qbe/host_abi.zig` | Exported class constructor/method/property/dunder/async C wrappers |
| `docs/apis/host-abi.md` & `docs/apis/stability.md` | Document v3 contracts and freeze `nox_host.h` as append-only stable |
| `tests/compat/host_abi/` & `tests/cli/expand_json_test.zig` | C host tests (callbacks + reentrancy, async + cancel, pinned buffer + busy, subinterpreter isolation, `_Static_assert` ABI layout) |
