# UPSTREAM_REQUIREMENTS.md — Nox Compiler & Runtime Requirements for NoxPy

> **Current Installed & CI-Verified Upstream Baseline:** `nox-lang v2.2.95` (`noxc 2.2.95`, upstream `v2.2.75`–`v2.2.95`)
> **Initial Observed Baseline (Historical):** `nox-lang v2.2.70` (commit `4dc061f8c8443f8575092c2652d1109b6f4697e9`)
> **Installed Toolchain Verified (2026-11-26):**
> - `noxc`: `/Users/melihburakmemis/.nox-lang/bin/noxc` (`noxc 2.2.95`, SHA-256: `fd607819da6e536bd2e70e495af103f18b7c7c0debe47a59e874209f53d57829`)
> - `noxrt.o`: `/Users/melihburakmemis/.nox-lang/lib/noxrt.o` (SHA-256: `dcf9e8f9d3c87374dda1f6eb229c83647c9230b30a22c3b6086f0e911b3a675a`)
> - `nox_host.h`: `/Users/melihburakmemis/.nox-lang/include/nox_host.h` (`NOX_HOST_ABI_VERSION = 3u`, `v3-rc`)

Per [`AGENTS.md`](AGENTS.md) §2 and §6, NoxPy tracks all compiler/runtime requirements needed from upstream `nox-lang` with executable verification scripts in [`tests/reproducers/`](tests/reproducers/).

---

## Delivery Batches (`nox-lang` v2.2.75 – v2.2.87 Status)

| Batch | Unlocks | Requirements | Upstream Version | Verification Status (`v2.2.87`) |
|---|---|---|---|---|
| **Batch A** | **Phase 0 Exit** | `UR-001`, `UR-002`, `UR-003`, `UR-004`, `UR-005` | `v2.2.75`, `v2.2.78` | **Resolved & Verified** (`tests/reproducers/verify_v2281_contracts.sh`, `experiments/phase0_poc/build_and_test.sh`) |
| **Batch B** | **Phase 1 (Functions & Wheel)** | `UR-007`, `UR-006` (part 1: `str`/`bytes` spans) | `v2.2.76`, `v2.2.77`, `v2.2.78` | **Resolved & Verified** (`noxc expand --json` parsed by [`src/cli/main.nox`](src/cli/main.nox), `\0`-safe `str`/`bytes` round-trip) |
| **Batch C** | **Phase 3 (Native Classes)** | `UR-006` (part 2: opaque handles) | `v2.2.78` | **Resolved & Verified** (`NoxHostHandle` retain/release/invalid/cross-context checks in [`c_host_test.c`](experiments/phase0_poc/c_host_test.c)) |
| **Batch D** | **Phase 2 Exit (Containers, Optional, Defaults)** | `UR-008` | `v2.2.85`, `v2.2.86` | **Resolved & Verified** (`NoxHostValue` ABI v2 + canonical types + constant defaults in `tests/packaging/test_phase2_packaging.py` & `ur008_host_abi_containers.sh`) |
| **Batch E–H** | **Phases 3–7 (Classes, Callbacks, Async, Buffers, Allocator, Freeze)** | `UR-009` … `UR-015` | `v2.2.89` – `v2.2.93` | **Implemented upstream; Host ABI frozen as `v3-rc`** (C-host + `expand --json` + layout-gate tests in `nox-lang` `tests/compat/host_abi/`; NoxPy end-to-end validation pending → `v3-stable`). See [`UR-009` … `UR-015` section](#ur-009--ur-015--implemented-in-nox-lang-v2289v2293-host-abi-v3-rc) |

---

## Summary Matrix

| ID | Batch | Requirement | Resolved In | Status on `noxc 2.2.81` | Notes / Contract Boundaries |
|---|---|---|---|---|---|
| `UR-001` | Batch A | Desktop PIC library emission (`--emit-obj`/`--emit-static-lib`/`--emit-shared-lib`) & PIC `noxrt.o` | `v2.2.75` (`164b7448`) | **Resolved** | Desktop targets emit PIC objects/archives/shared libs without `main`; `--selfhost-obj` bridge retired. |
| `UR-002` | Batch A | Versioned Host Export ABI (`include/nox_host.h`) & `--host-abi` wrappers | `v2.2.78` (`0bbb969c`) | **Resolved (Experimental v1)** | Synchronized in [`native/include/nox_host.h`](native/include/nox_host.h). Emits `nox_host_module_init/fini_<mod>` and `nox_host_export_<mod>_<fn>`. |
| `UR-003` | Batch A | Shared-library symbol isolation & multi-extension safety | `v2.2.78` (`0bbb969c`) | **Resolved** | `--emit-shared-lib --host-abi` exports strictly `nox_host_*`; two extensions with overlapping class tags run cleanly concurrently (`libpoc_a` + `libpoc_b`, `_noxpy_poc` + `_noxpy_poc_b`). Static archives keep runtime symbols local via extension link scripts. |
| `UR-004` | Batch A | Foreign-thread concurrency contract without 64-worker cap | `v2.2.78` (`0bbb969c`) | **Resolved** | `runtime/host.zig` locks per `NoxHostContext` with `handOverArcOwner`; NoxPy pools per-thread `NoxHostContext`s in [`noxpy_shim.c`](native/cpython/noxpy_shim.c) for parallel `3.15t` execution (`peak_in_flight=8`, 80 sequential thread churn verified). |
| `UR-005` | Batch A | Exception containment & public exception inspection API | `v2.2.78` (`0bbb969c`) | **Resolved (Bounded Fatal Limit)** | Nox exceptions return `NOX_HOST_ERR_EXCEPTION` + `nox_host_error_{class_name,message,line,release}`. Per `nox_host.h` and `host-abi.md`, runtime-internal OOM remains process-fatal (no unwind tables). **Update (`v2.2.93`, `UR-015`):** integer overflow (plain `int` `+ - *` and fixed-width traps) is now recoverable under `--host-abi` (`NOX_HOST_ERR_OVERFLOW = 11`). |
| `UR-006` | Batch B/C | Length-aware UTF-8/bytes spans & generation-checked `NoxHostHandle` | `v2.2.77` + `v2.2.78` | **Resolved** | `str` (`nox_host_utf8_ok` / `NoxHostBuffer`) and built-in `bytes` preserve embedded `\0`; `NoxHostHandle` rejects released, forged, and cross-context handles with `NOX_HOST_ERR_INVALID_HANDLE`. |
| `UR-007` | Batch B | Compiler-owned typed export metadata (`noxc expand --json`) | `v2.2.76` (`eaa0e670`) | **Resolved** | Emits schema v1 JSON consumed directly by [`src/codegen/ir.nox`](src/codegen/ir.nox) and [`python/noxpy/ir.py`](python/noxpy/ir.py). |
| `UR-008` | Batch D | Host ABI `T \| None`, `list[T]`, `dict[K, V]`, `tuple[...]` marshaling + canonical tuple IR type names + default values | `v2.2.85`–`v2.2.86` (`3d825c65`, `603f6ffe`) | **Resolved (Documented Container Limits)** | `NoxHostValue` (ABI v2, backward-compatible with v1), canonical `expand --json` types (`tuple[...]`, `marshal`, `default`), and end-to-end CPython `3.15` / `3.15t` execution verified. Remaining upstream limits per `docs/apis/host-abi.md:59`: no class instances or `int\|float\|bool \| None` elements inside containers (`str \| None` and container `\| None` work); `b"..."` parameter defaults are accepted since `v2.2.93` (`UR-014`; `default: {"type": "bytes", "hex": ...}`). Class instances inside containers are supported since `v2.2.89` (`NOX_HOST_VALUE_HANDLE`). |

---

## `UR-008` — Resolved in `nox-lang v2.2.85`–`v2.2.86` (Verified on `v2.2.87`)

1. **`v2.2.85` (`3d825c65`):** `noxc expand --json` emits canonical surface spellings (`tuple[int, str]`, nested `list[tuple[int, str]]`, `dict[str, tuple[int, float]]`) and constant literal `default` objects (`int`, `float`, `bool`, `str`, `None`; `bytes` schema defined though checker restricts default params to `int/float/bool/str/None`).
2. **`v2.2.86` (`603f6ffe`):** `include/nox_host.h` (`NOX_HOST_ABI_VERSION 2u`) adds opaque, generation-checked `NoxHostValue` tree APIs (`nox_host_value_*`), `compiler/host_adapt.zig` synthesizes safe pre-compile adapters for containers/`T | None`/`tuple`, and `expand --json` emits `marshal`/`return_marshal`.
3. **Verification:** [`ur008_host_abi_containers.sh`](tests/reproducers/ur008_host_abi_containers.sh) + [`examples/mixed_package`](examples/mixed_package/) end-to-end dual-mode `3.15.2` & `3.15.0b3t` suite in [`test_phase2_packaging.py`](tests/packaging/test_phase2_packaging.py).

---

## Phase 3–7 Complete Upstream Requirements (`nox_host.h` v3 One-Shot Freeze Plan)

> **Single-Pass Upstream Freeze Spec:** All remaining compiler & runtime primitives needed for NoxPy Phases 3–7 (`1.0`) are specified together in [`docs/upstream/nox-host-abi-v3-freeze-spec.md`](docs/upstream/nox-host-abi-v3-freeze-spec.md) (`NOX_HOST_ABI_VERSION = 3u`, append-only backward-compatible with v1/v2) so `nox-lang` can stabilize its Zig Host ABI once before self-hosting bootstrap.

| ID | Batch | Unlocks Phase | Requirement | Status on `noxc 2.2.93` |
|---|---|---|---|---|
| `UR-009` | Batch E | **Phase 3 (`0.3`: Native Classes)** | `@export class` / methods / properties / static & classmethods / custom exceptions in `noxc expand --json` + `--host-abi`, `nox_host_handle_type_name`, `NOX_HOST_VALUE_HANDLE` ([`ur009_pyclass_metadata.sh`](tests/reproducers/ur009_pyclass_metadata.sh)) | **Resolved in `v2.2.89`** (`@export class`, `nox_host_handle_type_name`, `NOX_HOST_VALUE_HANDLE`; handle class/generation/context validation + multi-handle methods in `v2.2.92`) |
| `UR-010` | Batch F | **Phase 4 (`0.4`: Callbacks & `PyObject`)** | `NoxHostExternalRef`, `NoxHostCallbackOps`, **owner-thread reentrant suspended state** (`owner_tid`, `call_depth`, `suspended_for_callback`; foreign threads wait), `NOX_HOST_ERR_HOST_EXCEPTION` token preservation | **Resolved in `v2.2.90`** (callbacks) + **`v2.2.92`** (re-entrant owner-thread lock, foreign threads wait, destroy-from-callback deferred) |
| `UR-011` | Batch F | **Phase 4 (`0.4`: Async Tasks)** | `@export async def` C start/cancel/take-result/release API (`NoxHostTask`), `NOX_HOST_ERR_CANCELLED`, recoverable scheduler deadlock | **Resolved in `v2.2.91`** (`@pyiterator` needs only `UR-009`) |
| `UR-012` | Batch G | **Phase 5 & 6 (`0.5/0.6`: Buffers, NumPy, Atlas, Arrow)** | Pinned zero-copy contiguous views (`nox_host_handle_pin_buffer` / `unpin`, `NOX_HOST_ERR_BUSY`) + bulk `NOX_HOST_VALUE_TYPED_ARRAY` (`NoxHostDType`) | **Resolved in `v2.2.92`** |
| `UR-013` | Batch G/H | **Phase 4/6/7 (`0.4/0.6/1.0`: Allocator, Subinterpreters, Windows P0)** | Wire `NoxHostContextConfig.allocator` (`NoxHostAllocator`), `nox_host_capabilities()`, subinterpreter global isolation, Windows COFF `.dll`/`.obj` `--host-abi` parity | **Resolved in `v2.2.93`** (Windows DLL exports exactly `nox_host_*`; real-Windows `.pyd` load still to be validated by NoxPy) |
| `UR-014` | Batch H | **Phase 7 (`1.0`: Staged Freeze `v3-rc` -> `v3-stable`)** | Stage 1 (`v3-rc`): C11 `_Static_assert` ABI layout gate + append-only freeze + `b"..."` default literal; Stage 2 (`v3-stable`): promote in `docs/apis/stability.md` after Phases 3–6 validation | **Stage 1 (`v3-rc`) done in `v2.2.93`**; Stage 2 (`v3-stable`) **waits for NoxPy Phases 3–6** |
| `UR-015` | Batch H | **Phase 7 (`1.0`: Hosted Recoverable Trap Containment)** | `--host-abi` recoverable integer overflow (`NOX_HOST_ERR_OVERFLOW = 11`) and OOM (`NOX_HOST_ERR_NOMEM = 2`) containment + `NOX_HOST_CAP_RECOVERABLE_TRAPS` instead of `haltProcess` | **Overflow resolved in `v2.2.93`**; **OOM partly open** (runtime-internal OOM still ends the process; needs a guarded-stack/`setjmp` barrier decision) |

---

## `UR-009` … `UR-015` — Implemented in `nox-lang v2.2.89`–`v2.2.93` (Host ABI `v3-rc`)

Source of truth: `nox-lang` [`include/nox_host.h`](https://github.com/mburakmmm/nox-lang) (`NOX_HOST_ABI_VERSION 3u`, accepts `abi_version` 1..3), `docs/apis/host-abi.md`, `docs/apis/stability.md`, spec §3.522–§3.527. Each release was verified by C-host tests in `tests/compat/host_abi/`.

| UR | Release | What shipped | Boundaries to know |
|---|---|---|---|
| `UR-009` | `v2.2.89` | `@export class` → `"kind": "class"` (`constructor`, `methods[]` with `method_kind` `instance\|staticmethod\|classmethod\|property_get\|property_set\|dunder`, `has_self`, `c_symbol`) and `"kind": "exception"`; C symbols `nox_host_export_<mod>_<Class>___new__`, `_<Class>_<m>`, `_<Class>_get_<p>` / `_set_<p>`; `nox_host_handle_type_name`; `NOX_HOST_VALUE_HANDLE` (`nox_host_value_handle` / `_get_handle`) so `Class \| None` / `list[Class]` cross the boundary. | `@dataclass`/generic/enum classes rejected; dunders exported: `__repr__ __str__ __eq__ __len__ __iter__ __next__ __enter__ __exit__`; inside containers only the exact class (no subclasses). Property symbol naming (`_get_`/`_set_`) is a nox-lang decision. |
| `UR-010` | `v2.2.90` (+ `v2.2.92` fix) | `NoxHostExternalRef`, `NoxHostCallbackOps` (40 bytes), `nox_host_context_set_callbacks`, `NOX_HOST_ERR_HOST_EXCEPTION = 8` + `nox_host_error_host_token`, `NOX_HOST_VALUE_EXTERNAL_REF = 11`; Nox side `nox.host` (`HostObject`, `HostArgs`, `HostResult`, `HostError`); `expand --json` `marshal: "external_ref"`. **Addendum implemented:** the context lock is *re-entrant* (owner thread re-enters from a callback, foreign threads wait; `nox_host_context_destroy` from a callback is deferred) — the lock is **not** dropped around callbacks. | The error-message buffer is borrowed until `invoke` returns; the `+1` token is released when the `NoxHostError` is released (read the token first). Do not call `destroy` from the task worker thread. |
| `UR-011` | `v2.2.91` | `nox_host_async_start_<mod>_<fn>`, `NoxHostTask`, `NoxHostTaskCallback`, `nox_host_task_cancel/take_result/release`, `NOX_HOST_ERR_CANCELLED = 9`; async instance methods too; scheduler deadlock → task finishes `ERR_STATE` + `RuntimeError("deadlock in async task")` (process survives, context accepts no new tasks). | One lazy worker thread per context with its own Nox scheduler; lock held while Nox code runs, released while the scheduler waits and during `on_done`. Cancel is cooperative (next `await`). Result is always a `NoxHostValue`. Keep `NoxHostExternalRef` arguments alive until `on_done`. No tasks on wasm/freestanding. |
| `UR-012` | `v2.2.92` | `NOX_HOST_VALUE_TYPED_ARRAY = 10` (`nox_host_value_typed_array` / `_get_typed_array`, `NoxHostDType`), `nox_host_handle_pin_buffer` / `nox_host_buffer_unpin`, `NoxHostBufferView` (176 bytes), `NOX_HOST_ERR_BUSY = 10`; Nox classes `nox.host.F64Buffer/I64Buffer/I32Buffer/U8Buffer`. | Typed arrays are an *input* format (`list[int\|float\|bool]` params accept them); Nox results stay `list`. Pinning works on the four `nox.host` buffer classes only (other classes → `ERR_INVAL`); growing the list from `as_list()` while pinned is a contract violation. |
| `UR-013` | `v2.2.93` | `NoxHostContextConfig.allocator` (`NoxHostAllocator`, shared with `nox_embed.h`, needs `abi_version >= 3`), `nox_host_capabilities()` (`NOX_HOST_CAP_*` bits 0..8), Windows `--host-abi` DLL exporting exactly `nox_host_*`, context-isolation audit + concurrent-context test. | Stdlib modules with process-wide caches (`nox.http`/`tls`/`sqlite`/`postgres`/`terminal`/`signal`) are not subinterpreter-safe. Windows was cross-built and its PE export table verified; a real-Windows `.pyd` load is still ours to validate. |
| `UR-014` | `v2.2.93` | Staged freeze: **`v3-rc`** — C11 `_Static_assert` layout gate (`tests/compat/host_abi/abi_layout.c`, also run in `scripts/precheck.sh`), append-only; `b"..."` default literal accepted (`expand --json` `hex`). `v3-stable` only after NoxPy Phases 3–6 validate it. | |
| `UR-015` | `v2.2.93` | `--host-abi`: plain `int` `+ - *` and fixed-width traps are *checked* and return `NOX_HOST_ERR_OVERFLOW = 11` (`OverflowError`); the context stays healthy (`add(20, 22) == 42` afterwards); `NOX_HOST_CAP_RECOVERABLE_TRAPS`. | Standalone programs keep wrapping `int`. The trap is not catchable by Nox `except` (`finally` runs). **Runtime-internal OOM still ends the process** (no unwind tables); fixing it needs a guarded-stack or `setjmp` barrier — an open design decision. Overflow in module top-level initialisers is still fatal. |
