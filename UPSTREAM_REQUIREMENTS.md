# UPSTREAM_REQUIREMENTS.md — Nox Compiler & Runtime Requirements for NoxPy

> **Current Verified Upstream Baseline:** `nox-lang v2.2.87` (`noxc 2.2.87`, commit `22a4d80b`, upstream `v2.2.75`–`v2.2.87`)
> **Initial Observed Baseline (Historical):** `nox-lang v2.2.70` (commit `4dc061f8c8443f8575092c2652d1109b6f4697e9`)
> **Installed Toolchain Verified (2026-11-26):**
> - `noxc`: `/Users/melihburakmemis/.nox-lang/bin/noxc` (`noxc 2.2.87`, SHA-256: `e86c99d2603986d33e17a147cb1a41c618cc8d94daa2d7f22f54ba22ac4df3b2`)
> - `noxrt.o`: `/Users/melihburakmemis/.nox-lang/lib/noxrt.o` (SHA-256: `198aef640c7361ef0b8efd2ac11db46cbec738e266cc096ed5d0ed1057a5ae98`)
> - `nox_host.h`: `/Users/melihburakmemis/.nox-lang/include/nox_host.h` (`NOX_HOST_ABI_VERSION = 2u`)

Per [`AGENTS.md`](AGENTS.md) §2 and §6, NoxPy tracks all compiler/runtime requirements needed from upstream `nox-lang` with executable verification scripts in [`tests/reproducers/`](tests/reproducers/).

---

## Delivery Batches (`nox-lang` v2.2.75 – v2.2.87 Status)

| Batch | Unlocks | Requirements | Upstream Version | Verification Status (`v2.2.87`) |
|---|---|---|---|---|
| **Batch A** | **Phase 0 Exit** | `UR-001`, `UR-002`, `UR-003`, `UR-004`, `UR-005` | `v2.2.75`, `v2.2.78` | **Resolved & Verified** (`tests/reproducers/verify_v2281_contracts.sh`, `experiments/phase0_poc/build_and_test.sh`) |
| **Batch B** | **Phase 1 (Functions & Wheel)** | `UR-007`, `UR-006` (part 1: `str`/`bytes` spans) | `v2.2.76`, `v2.2.77`, `v2.2.78` | **Resolved & Verified** (`noxc expand --json` parsed by [`src/cli/main.nox`](src/cli/main.nox), `\0`-safe `str`/`bytes` round-trip) |
| **Batch C** | **Phase 3 (Native Classes)** | `UR-006` (part 2: opaque handles) | `v2.2.78` | **Resolved & Verified** (`NoxHostHandle` retain/release/invalid/cross-context checks in [`c_host_test.c`](experiments/phase0_poc/c_host_test.c)) |
| **Batch D** | **Phase 2 Exit (Containers, Optional, Defaults)** | `UR-008` | `v2.2.85`, `v2.2.86` | **Resolved & Verified** (`NoxHostValue` ABI v2 + canonical types + constant defaults in `tests/packaging/test_phase2_packaging.py` & `ur008_host_abi_containers.sh`) |

---

## Summary Matrix

| ID | Batch | Requirement | Resolved In | Status on `noxc 2.2.81` | Notes / Contract Boundaries |
|---|---|---|---|---|---|
| `UR-001` | Batch A | Desktop PIC library emission (`--emit-obj`/`--emit-static-lib`/`--emit-shared-lib`) & PIC `noxrt.o` | `v2.2.75` (`164b7448`) | **Resolved** | Desktop targets emit PIC objects/archives/shared libs without `main`; `--selfhost-obj` bridge retired. |
| `UR-002` | Batch A | Versioned Host Export ABI (`include/nox_host.h`) & `--host-abi` wrappers | `v2.2.78` (`0bbb969c`) | **Resolved (Experimental v1)** | Synchronized in [`native/include/nox_host.h`](native/include/nox_host.h). Emits `nox_host_module_init/fini_<mod>` and `nox_host_export_<mod>_<fn>`. |
| `UR-003` | Batch A | Shared-library symbol isolation & multi-extension safety | `v2.2.78` (`0bbb969c`) | **Resolved** | `--emit-shared-lib --host-abi` exports strictly `nox_host_*`; two extensions with overlapping class tags run cleanly concurrently (`libpoc_a` + `libpoc_b`, `_noxpy_poc` + `_noxpy_poc_b`). Static archives keep runtime symbols local via extension link scripts. |
| `UR-004` | Batch A | Foreign-thread concurrency contract without 64-worker cap | `v2.2.78` (`0bbb969c`) | **Resolved** | `runtime/host.zig` locks per `NoxHostContext` with `handOverArcOwner`; NoxPy pools per-thread `NoxHostContext`s in [`noxpy_shim.c`](native/cpython/noxpy_shim.c) for parallel `3.15t` execution (`peak_in_flight=8`, 80 sequential thread churn verified). |
| `UR-005` | Batch A | Exception containment & public exception inspection API | `v2.2.78` (`0bbb969c`) | **Resolved (Bounded Fatal Limit)** | Nox exceptions return `NOX_HOST_ERR_EXCEPTION` + `nox_host_error_{class_name,message,line,release}`. Per `nox_host.h:25` and `host-abi.md:56`, OOM and fixed-width overflow traps remain process-fatal (no unwind tables; documented transparently per `AGENTS.md` §6). |
| `UR-006` | Batch B/C | Length-aware UTF-8/bytes spans & generation-checked `NoxHostHandle` | `v2.2.77` + `v2.2.78` | **Resolved** | `str` (`nox_host_utf8_ok` / `NoxHostBuffer`) and built-in `bytes` preserve embedded `\0`; `NoxHostHandle` rejects released, forged, and cross-context handles with `NOX_HOST_ERR_INVALID_HANDLE`. |
| `UR-007` | Batch B | Compiler-owned typed export metadata (`noxc expand --json`) | `v2.2.76` (`eaa0e670`) | **Resolved** | Emits schema v1 JSON consumed directly by [`src/codegen/ir.nox`](src/codegen/ir.nox) and [`python/noxpy/ir.py`](python/noxpy/ir.py). |
| `UR-008` | Batch D | Host ABI `T \| None`, `list[T]`, `dict[K, V]`, `tuple[...]` marshaling + canonical tuple IR type names + default values | `v2.2.85`–`v2.2.86` (`3d825c65`, `603f6ffe`) | **Resolved (Documented Container Limits)** | `NoxHostValue` (ABI v2, backward-compatible with v1), canonical `expand --json` types (`tuple[...]`, `marshal`, `default`), and end-to-end CPython `3.15` / `3.15t` execution verified. Remaining upstream limits per `docs/apis/host-abi.md:59`: no class instances or `int\|float\|bool \| None` elements inside containers (`str \| None` and container `\| None` work); `b"..."` parameter defaults rejected by checker. |

---

## `UR-008` — Resolved in `nox-lang v2.2.85`–`v2.2.86` (Verified on `v2.2.87`)

1. **`v2.2.85` (`3d825c65`):** `noxc expand --json` emits canonical surface spellings (`tuple[int, str]`, nested `list[tuple[int, str]]`, `dict[str, tuple[int, float]]`) and constant literal `default` objects (`int`, `float`, `bool`, `str`, `None`; `bytes` schema defined though checker restricts default params to `int/float/bool/str/None`).
2. **`v2.2.86` (`603f6ffe`):** `include/nox_host.h` (`NOX_HOST_ABI_VERSION 2u`) adds opaque, generation-checked `NoxHostValue` tree APIs (`nox_host_value_*`), `compiler/host_adapt.zig` synthesizes safe pre-compile adapters for containers/`T | None`/`tuple`, and `expand --json` emits `marshal`/`return_marshal`.
3. **Verification:** [`ur008_host_abi_containers.sh`](tests/reproducers/ur008_host_abi_containers.sh) + [`examples/mixed_package`](examples/mixed_package/) end-to-end dual-mode `3.15.2` & `3.15.0b3t` suite in [`test_phase2_packaging.py`](tests/packaging/test_phase2_packaging.py).

---

## Future Phase Upstream Tracking (Non-Blocking for Phase 0/1/2 Tooling)

Future phases (`Phase 3+`: method/property metadata in `expand --json`, `Phase 4+`: reentrant host callbacks / `async` / subinterpreters, long-term recoverable OOM or custom allocator hooks via `NoxHostContextConfig.allocator`) will be tracked incrementally as new `UR-009+` requirements when their respective phases are entered. See `docs/apis/host-abi.md` for currently documented `v1` scope.
