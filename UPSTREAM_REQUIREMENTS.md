# UPSTREAM_REQUIREMENTS.md — Nox Compiler & Runtime Requirements for NoxPy

> **Observed Upstream Baseline:** `nox-lang v2.2.70` (commit `4dc061f8c8443f8575092c2652d1109b6f4697e9`)
> **Installed Toolchain Verified:**
> - `noxc`: `/Users/melihburakmemis/.nox-lang/bin/noxc` (`noxc 2.2.70`, SHA-256: `902f7c439b861282c1f7ce50b301e531c3842a079e09e53fa2a8a4ecd2a10acb`)
> - `noxrt.o`: `/Users/melihburakmemis/.nox-lang/lib/noxrt.o` (SHA-256: `926db2d8792ac36a8161766080be380157fb3dd75302454848ed6a1eef79e837`)

Per [`AGENTS.md`](AGENTS.md) §2 and §6, NoxPy MUST NOT rely on undocumented internal compiler flags (`--selfhost-obj`), private internal runtime ABIs (`docs/apis/internal-abi.md`), or guessed object layouts. Until the requirements below are implemented and released in upstream `nox-lang`, Phase 0 cannot graduate to Phase 1.

---

## Delivery Order for `nox-lang` (Requirement -> Solution -> Progress Loop)

> **Ownership boundary:** All items below are implemented by the maintainer inside `nox-lang`. NoxPy tracks each item's reproducer under `tests/reproducers/` and advances phases only after re-running `tests/reproducers/run_all.sh` and verifying exit gates.

| Batch | Unblocks | Requirements | Minimum Acceptance Gate |
|---|---|---|---|
| **Batch A** | **Phase 0 Exit** | `UR-001`, `UR-002`, `UR-003`, `UR-004`, `UR-005` | `noxc build --backend llvm --emit-obj` + `include/nox_host.h` + hidden-visibility PIC `noxrt.o` + foreign-thread attach + non-aborting exception containment/inspection. Retires `experiments/phase0_poc/nox_v2270_bridge.c`. |
| **Batch B** | **Phase 1 (Functions & Wheel)** | `UR-007`, `UR-006` (part 1: `str`/`bytes` spans) | `noxc expand --json` (or `export-metadata --json`) matches [`python/noxpy/ir.py`](python/noxpy/ir.py) schema v1 + length-aware UTF-8 `str` & `bytes` spans preserving embedded `\0`. |
| **Batch C** | **Phase 3 (Native Classes)** | `UR-006` (part 2: opaque handles) | Generation-checked, owner-validated `NoxHostHandle` retain/release/lookup APIs for Nox class instances. |

### Reference Target Contracts Already Prepared in NoxPy
- Target C host header contract: [`native/include/nox_host_abi.h`](native/include/nox_host_abi.h) (`NoxHostStatus`, `NoxHostSpan`, `NoxHostError`, `NoxHostContext`, `NoxHostRuntimeOps`).
- Target `UR-007` JSON manifest consumer: [`python/noxpy/ir.py`](python/noxpy/ir.py) (`parse_upstream_metadata_json`).
- Expected `UR-007` JSON shape (`schema_version = 1`):
  ```json
  {
    "schema_version": 1,
    "compiler": {"name": "noxc", "version": "2.2.71", "commit": "..."},
    "module": {"name": "fastmath", "source": "src/lib.nox"},
    "exports": [
      {
        "kind": "function",
        "id": "fastmath::add",
        "name": "add",
        "c_symbol": "nox_host_export_fastmath_add",
        "doc": "Add two integers.",
        "thread_policy": "parallel",
        "span": {"file": "src/lib.nox", "line": 4, "column": 1},
        "params": [
          {"name": "a", "type": "int", "kind": "positional_or_keyword", "has_default": false},
          {"name": "b", "type": "int", "kind": "positional_or_keyword", "has_default": false}
        ],
        "return_type": "int"
      }
    ]
  }
  ```

---

## Summary Matrix

| ID | Batch | Requirement | Blocking Phase | Reproducer | Status on `v2.2.70` |
|---|---|---|---|---|---|
| `UR-001` | Batch A | Desktop PIC library emission (`--emit-obj`/`--emit-static-lib`/`--emit-shared-lib`) & PIC `noxrt.o` | Phase 0 Exit | [`ur001_desktop_emit_obj.sh`](tests/reproducers/ur001_desktop_emit_obj.sh) | **Blocked** (mobile-only) |
| `UR-002` | Batch A | Versioned Host Export ABI (`include/nox_host.h`) & explicit function export | Phase 0 Exit | [`ur002_no_public_export_abi.sh`](tests/reproducers/ur002_no_public_export_abi.sh) | **Blocked** (missing) |
| `UR-003` | Batch A | Hidden runtime visibility & elimination of process-global mutable dispatch/error state | Phase 0 Exit | [`ur003_noxrt_symbol_visibility.sh`](tests/reproducers/ur003_noxrt_symbol_visibility.sh) | **Blocked** (global `var`, 752 `T` exports) |
| `UR-004` | Batch A | Supported foreign-thread attachment & host-context concurrency contract | Phase 0 Exit | [`ur004_foreign_thread_contract.sh`](tests/reproducers/ur004_foreign_thread_contract.sh) | **Blocked** (Nox-thread/internal only) |
| `UR-005` | Batch A | Non-aborting exception containment & public exception inspection API | Phase 0 Exit | [`ur005_exception_and_abort.sh`](tests/reproducers/ur005_exception_and_abort.sh) | **Blocked** (`exit(1)` on OOM/trap, opaque ptr) |
| `UR-006` | Batch B/C | Binary-safe length-aware UTF-8/bytes spans & generation-checked opaque handles | Phase 1 / Phase 3 | [`ur006_binary_safe_span_and_handles.sh`](tests/reproducers/ur006_binary_safe_span_and_handles.sh) | **Blocked** (NUL-terminated `nox_str_from_cstr` only) |
| `UR-007` | Batch B | Compiler-owned machine-readable typed export metadata (JSON/IR manifest) | Phase 1 Entry | [`ur007_expand_no_typed_ir.sh`](tests/reproducers/ur007_expand_no_typed_ir.sh) | **Blocked** (`noxc expand` text-only, untyped) |

---

## UR-001 — Desktop PIC Library Emission (`--emit-obj`, `--emit-static-lib`, `--emit-shared-lib`) & PIC `noxrt.o`

### 1. Exact Technical Blocker
`noxc` restricts `--emit-obj`, `--emit-static-lib`, and `--emit-shared-lib` exclusively to mobile targets (`ios-arm64`, `ios-simulator-arm64`, `android-arm64`, `android-x64`). Attempting to emit a library or object file on any desktop hosted target (`macos-arm64`, `macos-x64`, `linux-x64`, `linux-arm64`, `windows-x64`, or host native) fails with `--emit-obj, --emit-static-lib and --emit-shared-lib require a mobile --target`.

An undocumented internal flag `--selfhost-obj` exists solely for building `noxc-nightly`, but:
- It is explicitly marked hidden and not user-facing (`compiler/main.zig:2364`).
- It rejects `async` and `spawn` (`compiler/codegen_qbe/registration.zig:2558`).
- It rejects cross-compilation `--target` (`compiler/main.zig:4266`).
- Desktop `noxrt.o` in `build.zig:900-987` does not enable `.pic = true` (unlike mobile targets in `build.zig:426-448`), which breaks ELF shared-library linking (`-shared`) on Linux `x86_64` / `aarch64`.

### 2. Minimal Reproducible Example
- Script: [`tests/reproducers/ur001_desktop_emit_obj.sh`](tests/reproducers/ur001_desktop_emit_obj.sh)
- Source: [`tests/reproducers/min_add.nox`](tests/reproducers/min_add.nox)
- Observed output on `noxc 2.2.70`:
  ```sh
  noxc build --backend llvm --emit-obj -o min_add.o tests/reproducers/min_add.nox
  # exit 1: error: --emit-obj, --emit-static-lib and --emit-shared-lib require a mobile --target
  ```

### 3. Relevant Upstream Source Files (`nox-lang v2.2.70`)
- `compiler/main.zig:2364` (`host_obj` / `--selfhost-obj` internal-only comment)
- `compiler/main.zig:2546-2552, 2647` (CLI flag parsing for `--emit-obj`, `--emit-static-lib`, `--emit-shared-lib`)
- `compiler/main.zig:4256-4271` (mobile-target-only gate rejecting desktop targets)
- `compiler/main.zig:4368-4385` (`host_obj` LLVM `-fPIC` object compilation path)
- `compiler/codegen_qbe/registration.zig:2558, 2598-2672` (`lib_split` vs `library_mode` entry emission)
- `build.zig:426-448, 900-987` (`.pic = true` enabled for Android mobile targets, absent on desktop `noxrt_mod`)

### 4. Required Compiler / Runtime Changes
1. Allow `--emit-obj`, `--emit-static-lib`, and `--emit-shared-lib` on all P0 desktop hosted targets (`macos-arm64`, `macos-x64`, `linux-x64`, `linux-arm64`, `windows-x64`) under `--backend llvm` with `-fPIC` (or target-appropriate PIC).
2. In library modes, omit `$main` / `main` and `$nox_os_init`, and avoid executable startup assumptions.
3. Build desktop `noxrt.o` (or a dedicated `noxrt-pic.o` / `libnoxrt.a`) with `.pic = true` and `-ffunction-sections -fdata-sections` across all P0 desktop targets.

### 5. Expected ABI Contract
- `noxc build --backend llvm --emit-obj [--target <desktop-target>] -o <out.o> <file.nox>` emits a relocatable PIC object file without `main`, linkable into a shared library (`.so` / `.dylib` / `.pyd`) alongside a host-compatible PIC runtime archive/object.
- `noxc build --backend llvm --emit-static-lib ...` emits a self-contained static archive (or clearly documents runtime pairing).

### 6. Measurable Acceptance Tests
- `noxc build --backend llvm --emit-obj -o add.o min_add.nox` exits `0` on macOS arm64, Linux x86_64, and Windows x86_64.
- Linking `add.o` + matching PIC runtime + a C host shared library into a `.so` / `.dylib` / `.pyd` succeeds with `-Wl,-no_undefined` / `-Wl,--no-undefined` (except Python symbols when building a CPython extension) and no text-relocation errors on Linux ELF.

---

## UR-002 — Versioned Host Export ABI (`include/nox_host.h`) & Explicit Function Export Mechanism

### 1. Exact Technical Blocker
`nox-lang` has no supported, versioned C ABI for hosting and calling exported Nox functions from an external host (such as CPython):
- `docs/apis/internal-abi.md:1-23` explicitly warns that generated function symbol names, `RuntimeState`, and `nox_runtime_init`/`nox_runtime_deinit` are **private internal ABI** that may change in any patch release.
- `include/nox_nni.h` (NNI v1) only models Nox calling native plugins (`nox_plugin_init_v1`), not a native host calling Nox code.
- `include/nox_embed.h` (`NoxEmbedSlot`, `nox_app_create`, `nox_embed_bind`) is a mobile single-app event-loop contract (`nox_app_set_entry` + `nox_app_post_event`/`nox_app_pump`), not a multi-function typed call ABI.
- User-defined `def` symbols receive a hidden private first parameter `rt` (`compiler/codegen_qbe/registration.zig:2326-2334`) and have no explicit `@export` / symbol-visibility / C-wrapper contract.

### 2. Minimal Reproducible Example
- Script: [`tests/reproducers/ur002_no_public_export_abi.sh`](tests/reproducers/ur002_no_public_export_abi.sh)
- Observed on `v2.2.70`: `include/nox_host.h` does not exist; compiling `min_add.nox` emits `define i64 @add(i64 %rt, i64 %p_a, i64 %p_b)` with internal `rt` parameter and no versioned host entry table.

### 3. Relevant Upstream Source Files (`nox-lang v2.2.70`)
- `docs/apis/internal-abi.md:1-40` (declares generated symbols and `RuntimeState` private)
- `include/nox_embed.h:1-96` (mobile event-loop embed ABI only)
- `include/nox_nni.h:1-128` (reverse-direction plugin ABI only)
- `runtime/embed.zig:1-424` (mobile `App` state machine, single `entry`/`event_fn`)
- `compiler/codegen_qbe/registration.zig:2326-2334, 2598-2672` (`RT_PARAM` prepended to every `def`; `nox_lib_init`/`nox_lib_fini` undocumented `--selfhost-obj` internal symbols)

### 4. Required Compiler / Runtime Changes
1. Add a public header `include/nox_host.h` alongside `include/nox_nni.h` and `include/nox_embed.h`, defining a versioned `NoxHostApiV1` / `NoxHostContext` / `NoxHostModule` contract (`struct_size`, `abi_version`, fixed-width status codes).
2. Add an explicit export mechanism (e.g., `@export` or compiler-generated C-ABI thunks) so exported Nox functions get documented, stable, host-callable wrappers that accept `NoxHostContext *` (not private raw `RuntimeState *`), validate state, contain Nox exceptions, and leave internal functions hidden (`internal` / `hidden` visibility).
3. Support per-module initialization/teardown (`nox_host_module_init` / `nox_host_module_fini`) tied to a `NoxHostContext` rather than process-global symbols.

### 5. Expected ABI Contract
- Header: `include/nox_host.h` (C/C++ compatible, `extern "C"`, fixed-width `<stdint.h>`/`<stddef.h>` types only, `struct_size` + `abi_version` versioning).
- Every exported Nox function `foo(args...) -> R` has a documented C-callable wrapper taking `(NoxHostContext *ctx, ...args..., R *out, NoxHostError **out_err)` returning `NoxHostStatus`, never passing raw private `RuntimeState` pointers across undocumented symbols.

### 6. Measurable Acceptance Tests
- A standalone C host test compiling strictly against `#include "nox_host.h"` (zero forward-declarations of `nox_runtime_*` or raw `def` symbols) initializes a `NoxHostContext`, resolves and calls `add(20, 22) == 42`, and destroys the context cleanly with zero leaks.

---

## UR-003 — Runtime Symbol Visibility & Elimination of Process-Global Mutable Dispatch/Error State

### 1. Exact Technical Blocker
1. **`noxrt.o` exports hundreds of global `T` symbols** (`_nox_runtime_init`, `_nox_alloc`, `_nox_register_dispatch_table`, etc.), with no hidden-visibility library profile. When two independently compiled NoxPy extension modules load into the same CPython process without strict symbol hiding, or on ELF platforms with global symbol interposition, they risk symbol collisions across compiler patch versions.
2. **Process-global mutable state inside `runtime/`** prevents safe isolation of multiple independent Nox modules in one process:
   - `runtime/alloc/dispatch_registry.zig:33-37, 67-79`: `g_trace_fn`, `g_gc_free_fn`, `g_class_release_fn`, `g_class_name_fn`, `g_make_json_value_fn` are process-global `var`s overwritten by `nox_register_dispatch_table(...)`! If Module A and Module B share or resolve `nox_register_dispatch_table`, Module B's class release/name/trace dispatch table overwrites Module A's.
   - `runtime/errors/handle.zig:45-48`: `g_halt_provider` is process-global.
   - `docs/internals/global-state-audit.md:17-21`: `tl-errno` (`last_op_ok()` and stdin remainder) is thread-local outside fibers, so two runtimes on the same OS thread share it.

### 2. Minimal Reproducible Example
- Script: [`tests/reproducers/ur003_noxrt_symbol_visibility.sh`](tests/reproducers/ur003_noxrt_symbol_visibility.sh)
- Confirms `nm -g /Users/melihburakmemis/.nox-lang/lib/noxrt.o` exports `T _nox_*` globally and `runtime/alloc/dispatch_registry.zig` defines process-global `var g_trace_fn` etc.

### 3. Relevant Upstream Source Files (`nox-lang v2.2.70`)
- `runtime/alloc/dispatch_registry.zig:33-37, 67-79` (process-global function pointer table)
- `runtime/errors/handle.zig:45-48` (`g_halt_provider` process-global)
- `docs/internals/global-state-audit.md:1-25` (documents `tl-errno` open debt)
- `build.zig:900-987` (`noxrt` object build without `-fvisibility=hidden` / hidden export option)

### 4. Required Compiler / Runtime Changes
1. Move `dispatch_registry` function pointers and `halt_provider` out of process-global `var`s into `RuntimeState` / `NoxHostModule` context.
2. Move `tl-errno` (`last_op_ok()`) into `RuntimeState` / fiber state so multiple runtimes on the same OS thread never interfere.
3. Provide a hidden-visibility runtime build (or localized static linking contract where only `nox_host_*` / module-entry symbols have default visibility) so two extensions built with different `nox-lang` versions never collide in one Python process.

### 5. Expected ABI Contract
- Two independently compiled Nox libraries loaded concurrently in one process (even if built with different `noxc` versions or defining conflicting class tags / globals) have zero cross-module symbol resolution or shared mutable dispatch/error state.

### 6. Measurable Acceptance Tests
- Load two independently compiled Nox extensions containing classes with overlapping tag IDs (`tag = 1`) and exception handlers into one C/Python process; call both concurrently and on the same OS thread; verify each module resolves its own `classReleaseFn` / `classNameFn` and zero `nox_*` symbols leak from `nm -gU` on the `.abi3t.so`.

---

## UR-004 — Supported Foreign-Thread Attachment & Host-Context Concurrency Contract

### 1. Exact Technical Blocker
`nox-lang` currently distinguishes two incompatible worlds:
- Internal multi-runtime isolation (`runtime/multi_runtime_test.zig`), where each OS thread owns its own isolated `RuntimeState` via private `nox_runtime_init` / `nox_runtime_deinit`.
- Internal `WorkerPool` (`runtime/alloc/asap.zig:405-442, 505-536`), capped at `MAX_POOL_WORKERS = 64`, where `arc_owner_pool` asserts thread membership in Debug builds, `HotState.pending_exception` has a single slot for slot 0, and `nox_cycle_collect` requires stop-the-world worker barriers (notes at `asap.zig:409-424`: running `nox_cycle_collect` concurrently with ARC retain/release crashed with `SIGBUS`).
- Furthermore, `docs/language/ffi.md:165-171` explicitly states callbacks may ONLY run on Nox-created threads, never arbitrary host OS threads.

There is **no supported public host API** to attach/detach arbitrary CPython OS threads (which may exceed 64 threads over a process lifetime, be created/destroyed dynamically by `threading.Thread`, or reenter Nox) to a persistent `RuntimeState` or pool.

### 2. Minimal Reproducible Example
- Upstream source inspection: `runtime/alloc/asap.zig:187, 405-442, 457-474`, `runtime/errors/handle.zig:70-91`, `docs/language/ffi.md:165-171`.
- Creating a single shared `RuntimeState` and calling Nox functions from >1 arbitrary foreign OS threads hits:
  - In Debug `noxrt.o`: `arcOwnerThreadOk` failure (`capacity = 1` default) on `nox_rc_free_payload` when a second OS thread touches ARC objects.
  - Race on `HotState.pending_exception` if two foreign threads (`slot == 0`) raise/check exceptions simultaneously.
  - Race on `nox_cycle_collect` vs concurrent ARC mutations.

### 3. Relevant Upstream Source Files (`nox-lang v2.2.70`)
- `runtime/alloc/asap.zig:187, 405-442, 457-474, 505-536` (`MAX_POOL_WORKERS = 64`, `cycle_gc_lock` caveat, `arc_owner_pool`, `pool_ext`)
- `runtime/errors/handle.zig:70-91` (`pendingException` routing slot 0 to `HotState`)
- `runtime/multi_runtime_test.zig:77-160` (proves per-thread isolated `RuntimeState` works, not shared foreign-thread attachment)
- `docs/language/ffi.md:165-171` (foreign OS threads prohibited for stored callbacks)

### 4. Required Compiler / Runtime Changes
1. Define and test an explicit foreign-thread attachment contract in `nox_host.h`:
   - `nox_host_thread_attach(NoxHostContext *ctx, NoxHostThread **out_thread)` / `nox_host_thread_detach(NoxHostThread *t)` (or per-call lightweight execution context handle), supporting arbitrary OS thread creation/destruction without hitting a 64-slot permanent leak.
2. Route per-thread exception state (`pending_exception`) and allocator/pool caches to the attached `NoxHostThread`, not a single shared `HotState` slot 0.
3. Document whether persistent Nox heap objects (`Phase 3` classes) are thread-affine, synchronized via runtime locking, or share-safe with atomic ARC + safe cycle collection.

### 5. Expected ABI Contract
- Arbitrary foreign OS threads (e.g. CPython `threading.Thread` workers) can attach, execute independent Nox calls concurrently in parallel with zero global lock, handle thread-local exceptions independently, detach/exit, and allow >64 sequential threads over the process lifetime without exhausting fixed slot tables.

### 6. Measurable Acceptance Tests
- Spawn 100+ short-lived OS threads sequentially and 8–16 OS threads concurrently behind a barrier, attaching/detaching to a `NoxHostContext`, performing ARC allocations and raising/catching exceptions, with TSan and DebugAllocator reporting 0 data races and 0 memory leaks.

---

## UR-005 — Exception Containment, Non-Aborting Error Paths, & Public Exception Inspection

### 1. Exact Technical Blocker
Inside a Python process, killing the host interpreter on a native OOM or integer overflow violates `AGENTS.md` §6 and §11. Currently on `v2.2.70`:
1. `runtime/errors/handle.zig:50-61, 141-157, 222-231`: `nox_out_of_memory`, `nox_int_overflow_trap`, and `nox_unhandled_exception` invoke `haltProcess()`, which calls `std.process.exit(1)` by default (`g_halt_provider` is private internal Zig, not exposed in `include/nox_embed.h`).
2. `include/nox_embed.h:87-90` exposes `nox_exception_pending`, `nox_exception_take`, and `nox_exception_release`, which return an opaque `void *`, **but provide zero public functions to read the exception's class name, error message, or source line** without dereferencing private class-object layouts (`tag = *(int64_t *)obj` in `runtime/embed.zig:295`).

### 2. Minimal Reproducible Example
- Script: [`tests/reproducers/ur005_exception_and_abort.sh`](tests/reproducers/ur005_exception_and_abort.sh)
- Confirms `include/nox_embed.h` lacks any exception accessor (`class_name` / `message` / `line`) and `runtime/errors/handle.zig` calls `std.process.exit(1)` on `nox_out_of_memory` and `nox_int_overflow_trap`.

### 3. Relevant Upstream Source Files (`nox-lang v2.2.70`)
- `runtime/errors/handle.zig:38-61, 141-173, 222-231` (`HaltProvider`, `haltProcess()`, `nox_unhandled_exception`, `nox_out_of_memory`, `nox_int_overflow_trap`)
- `include/nox_embed.h:87-90` (opaque `nox_exception_take` without inspection accessors)
- `runtime/embed.zig:290-297` (`nox_exception_release` reads raw tag header internally)

### 4. Required Compiler / Runtime Changes
1. Add public host-mode error containment so recoverable OOM and fixed-width integer overflow return status/exception (`NOX_HOST_ERR_OOM` / `NOX_HOST_ERR_OVERFLOW` or raise a recoverable Nox exception) instead of calling `std.process.exit(1)` when running inside a host context.
2. Add public exception inspection functions in `nox_host.h` returning UTF-8 spans `(data, len)` for `class_name` and `message`, plus `int64_t line` and `NoxHostErrorKind`, without exposing internal object memory layouts.

### 5. Expected ABI Contract
- A Nox function raising `ValueError("bad input")` or overflowing a fixed-width integer returns `NOX_HOST_ERR_EXCEPTION` / `NOX_HOST_ERR_OVERFLOW` to the host; the host reads `class_name = "ValueError"`, `message = "bad input"`, `line`, releases the error object via `nox_host_error_release()`, and maps it cleanly to the corresponding Python exception without process termination.

### 6. Measurable Acceptance Tests
- Call a Nox function that raises `ValueError("division by zero")` and a function that triggers fixed-width integer overflow from CPython 3.15/3.15t; verify Python catches `ValueError` / `OverflowError` with the preserved message and the interpreter process remains alive and leak-free.

---

## UR-006 — Binary-Safe Length-Aware String/Bytes Spans & Opaque Managed Handle Table

### 1. Exact Technical Blocker
1. `include/nox_embed.h:84-86` provides only `char *nox_str_from_cstr(void *rt, const char *text)`, which relies on NUL-terminated `const char *` (cannot represent strings with embedded `\0` bytes, required by `AGENTS.md` §8) and offers no public byte-length or UTF-8 span accessor for returning Nox `str` or `bytes` to a host without reading private `str.zig` headers.
2. There is no public host-side opaque handle table (analogous to NNI's generation-counted `NoxHandle` in `include/nox_nni.h:32-33`, but for Nox-defined objects/classes) to retain, validate, and release Nox class instances owned by Python heap types (`Phase 3`) without exposing raw pointers or ARC headers.

### 2. Minimal Reproducible Example
- Inspection of `include/nox_embed.h:84-86`, `docs/apis/internal-abi.md:14-16`, and `include/nox_nni.h:32-33, 83-90`.
- Attempting to pass `"a\x00b"` (length 3) via `nox_str_from_cstr` truncates at index 1 (`"a"`).

### 3. Relevant Upstream Source Files (`nox-lang v2.2.70`)
- `include/nox_embed.h:84-86` (`nox_str_from_cstr` / `nox_str_release`)
- `runtime/str.zig` (`nox_str_from_bytes` exists internally, not in public header; `str` length header is private)
- `include/nox_nni.h:32-33, 83-90` (`NoxHandle` generation-counted pattern, limited to NNI plugins)

### 4. Required Compiler / Runtime Changes
1. Expose binary-safe `nox_host_str_from_utf8(ctx, const uint8_t *bytes, size_t len, ...)` (validating UTF-8 and preserving embedded NULs) and `nox_host_str_view(ctx, obj, const uint8_t **out_bytes, size_t *out_len)` + `bytes` equivalents in `nox_host.h`.
2. Expose a generation-counted, per-context opaque handle API (`NoxHostHandle`) with `retain`, `release`, and `is_valid` / owner validation for Phase 3 class instances.

### 5. Expected ABI Contract
- `str` values containing embedded `\0` round-trip byte-for-byte across the C/Python boundary.
- Released or forged `NoxHostHandle` values are deterministically rejected with `NOX_HOST_ERR_INVALID_HANDLE` rather than dereferencing freed memory.

### 6. Measurable Acceptance Tests
- Round-trip `"hello\x00world"` (`len == 11`) and multi-byte UTF-8 (`"ğüşiöç🚀"`) between Python and Nox without truncation.
- Close a `NoxHostHandle` and verify subsequent operations return `NOX_HOST_ERR_INVALID_HANDLE` cleanly.

---

## UR-007 — Compiler-Owned Machine-Readable Typed Export Metadata (JSON/Schema for Binding IR)

### 1. Exact Technical Blocker
`AGENTS.md` §3 requires `noxc` to own semantic type resolution and provide a supported typed export manifest rather than having NoxPy regex-parse `.nox` source files. Currently on `v2.2.70`:
- `noxc expand <file.nox>` (`compiler/main.zig:3725-3792`) only prints human-readable decorator names and literal decorator arguments (e.g., `@export()`).
- It emits **no** function signatures, resolved parameter types, parameter defaults, return types, docstrings, class fields/methods, source spans, or ABI symbols.
- `VERSIONING.md:48-54` explicitly states diagnostic and `--dump` text outputs are unstable.

### 2. Minimal Reproducible Example
- Script: [`tests/reproducers/ur007_expand_no_typed_ir.sh`](tests/reproducers/ur007_expand_no_typed_ir.sh)
- Observed on `noxc 2.2.70`:
  ```sh
  noxc expand tests/reproducers/min_add.nox
  # Output:
  # 1 decorator(s) found:
  #   @export()
  #     -> def nox_add
  ```
  (No parameter types, no return type, no JSON schema, no symbol name.)

### 3. Relevant Upstream Source Files (`nox-lang v2.2.70`)
- `compiler/main.zig:3725-3792` (`cmdExpand`)
- `compiler/frontend.zig` (`frontend.analyze` produces typed AST/checker state)
- `VERSIONING.md:48-54` (text output stability exclusions)
- `docs/tools/noxc.md:31` (`noxc expand` documentation)

### 4. Required Compiler / Runtime Changes
1. Add `noxc export-metadata --json <file.nox>` (or `noxc expand --json <file.nox>`) backed by `checker.Checker` / `frontend.analyze` semantic resolution.
2. Emit deterministic, schema-versioned JSON containing: schema version, `noxc` version, module name, exported functions/classes/methods/exceptions, resolved parameter names/kinds/types/defaults, resolved return types, docstrings, source locations `(file, line, col)`, decorator arguments, and exported C ABI wrapper symbol names.

### 5. Expected ABI Contract
- Versioned JSON schema (`"schema_version": 1`) emitted on `stdout` with exit code `0` on valid typed modules and nonzero exit code on type errors.

### 6. Measurable Acceptance Tests
- Run `noxc expand --json` (or `export-metadata --json`) on `min_add.nox`; parse JSON cleanly with `json.loads()`, assert `schema_version`, parameter `(name="a", type="int")`, `(name="b", type="int")`, return type `"int"`, and C export symbol exist.
