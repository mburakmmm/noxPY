# ADR-0002: Nox Host Export ABI (`nox_host_abi.h`) & Upstream Feasibility

- **Status:** Proposed (Phase 0 experimental bridge verified on `nox-lang v2.2.70`; upstream `nox_host.h` required before Phase 1)
- **Date:** 2026-11-25

## Context

Inspection of upstream `nox-lang v2.2.70` (`4dc061f8`) established that:
1. `docs/apis/internal-abi.md` declares generated symbols (`@nox_add`), `RuntimeState`, `nox_runtime_init`, and `nox_runtime_deinit` private internal ABI subject to change in patch releases.
2. `include/nox_nni.h` (NNI v1) models Nox calling C plugins, not C/Python hosts calling Nox functions.
3. `include/nox_embed.h` models a mobile single-app event loop (`nox_app_create`/`nox_app_post_event`/`nox_app_pump`), not multi-function typed exports or host-attached threads.
4. `noxc` rejects `--emit-obj` / `--emit-static-lib` / `--emit-shared-lib` on desktop targets (`compiler/main.zig:4270`), leaving only the undocumented `--selfhost-obj` flag.
5. `noxrt.o` exports `752` global `T _nox_*` symbols, `dispatch_registry.zig` holds process-global `var g_*_fn`, and `runtime/errors/handle.zig` terminates the process via `std.process.exit(1)` on OOM/overflow traps.

## Decision

1. Define a clean, C/C++-compatible host boundary header [`native/include/nox_host_abi.h`](../../native/include/nox_host_abi.h) (`NoxHostRuntimeOps`, `NoxHostStatus`, `NoxHostSpan`, `NoxHostError`, `NoxHostContext`) with `struct_size` + `abi_version` versioning and fixed-width integers.
2. Document all 7 upstream gaps in [`UPSTREAM_REQUIREMENTS.md`](../../UPSTREAM_REQUIREMENTS.md) (`UR-001`–`UR-007`) with runnable reproducers in `tests/reproducers/`.
3. For Phase 0 feasibility only, isolate the `v2.2.70` `--selfhost-obj` bridge inside [`experiments/phase0_poc/`](../../experiments/phase0_poc/) behind `-DNOXPY_PHASE0_EXPERIMENTAL_SELFHOST_BRIDGE=1`:
   - Never dereference private `RuntimeState` or exception struct layouts.
   - Use per-call isolated `RuntimeState` (`nox_runtime_init` -> `nox_lib_init` -> call -> `nox_exception_pending`/`take`/`release` -> `nox_lib_fini` -> `nox_runtime_deinit`), eliminating any global invocation mutex and proving multi-thread overlap on foreign OS threads.
   - Hide all Nox and `noxrt.o` symbols at link time using `-Wl,-dead_strip -Wl,-exported_symbols_list`.

## Alternatives Rejected

- **Global mutex around a single shared `RuntimeState`:** Explicitly forbidden by `AGENTS.md` §0 and §10.4.
- **Dereferencing internal `RuntimeState` or exception tag offsets:** Explicitly forbidden by `AGENTS.md` §2 and §5.
- **Overloading `include/nox_nni.h`:** Rejected because NNI v1 is reverse-direction (Nox hosting C plugins).

## Consequences

- **Positive:** Phase 0 feasibility is empirically demonstrated on genuine Nox code (`math_mod.nox`, `mod_b.nox`) running concurrently across 8 OS threads in C and CPython 3.15.2 with zero leaked symbols and zero stderr leaks.
- **Blockers:** Phase 1 MUST NOT start until upstream `nox-lang` delivers `UR-001`–`UR-006` (and `UR-007` for Phase 1 Binding IR) so the gated `nox_v2270_bridge` can be retired cleanly.
