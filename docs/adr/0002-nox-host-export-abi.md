# ADR-0002: Nox Host Export ABI (`nox_host.h` v1) & Phase 0 Verification

- **Status:** Accepted — Verified on `nox-lang v2.2.81` (`2dcee335`) with CPython `3.15.2` and unforced `3.15t`
- **Date:** 2026-11-25 (updated for `v2.2.81` upstream resolution)

## Context

On the initial baseline (`nox-lang v2.2.70`), desktop `--emit-obj`/`--emit-shared-lib`, a public `nox_host.h` host contract, binary-safe spans, typed JSON export metadata, and exception inspection were absent (`UR-001`–`UR-007`), requiring a temporary gated Phase 0 bridge (`nox_v2270_bridge`).

Upstream `nox-lang v2.2.75`–`v2.2.81` resolved `UR-001`–`UR-007`:
1. Desktop PIC `--emit-obj`, `--emit-static-lib`, `--emit-shared-lib` (`v2.2.75`).
2. `noxc expand --json [--module <name>]` typed export metadata `schema_version = 1` (`v2.2.76`).
3. Built-in `bytes` and length-aware binary-safe `str` (`v2.2.77`).
4. Versioned `include/nox_host.h` (experimental `v1`), `--host-abi`, per-context lock + parallel per-thread `NoxHostContext` support, contained exception inspection (`nox_host_error_*`), and generation-checked `NoxHostHandle` (`v2.2.78`).

## Decision

1. Synchronize [`native/include/nox_host.h`](../../native/include/nox_host.h) with upstream `include/nox_host.h` `v1` and retire the obsolete `v2.2.70` `--selfhost-obj` bridge completely.
2. Bind per-extension host function pointers into [`NoxPyHostOps`](../../native/include/noxpy_abi.h) (`module_init`, `module_fini`, `context_create`, `context_destroy`, `buffer_release`, `error_*`) with hidden internal visibility and `-Wl,-exported_symbols_list` / `--version-script` exposing only `PyModExport_<leaf>`.
3. Implement a lock-minimal, bounded per-module `NoxHostContext` pool (`NOXPY_CTX_POOL_CAP = 64`) in [`native/cpython/noxpy_shim.c`](../../native/cpython/noxpy_shim.c) using C11 `atomic_flag` so concurrent Python threads execute in parallel across independent `NoxHostContext` instances with zero global invocation lock (`AGENTS.md` §10).
4. Always inspect and release `NoxHostError *` while its owning `NoxHostContext *` remains acquired (`noxpy_set_exception_from_host`), and release all `NoxHostBuffer` allocations cleanly via `nox_host_buffer_release`.
5. Keep fatal runtime limits transparent per `nox_host.h` and `AGENTS.md` §6: recoverable Nox exceptions return `NOX_HOST_ERR_EXCEPTION`, whereas OOM and fixed-width integer overflow traps terminate the process without unwind tables.

## Alternatives Rejected

- **Sharing one `NoxHostContext` across all Python threads:** Serializes parallel calls on `Context.lock` (`SpinLock`). Rejected by `AGENTS.md` §10.4.
- **Continuing with `--selfhost-obj` internal symbols:** Prohibited by `AGENTS.md` §2 and §6 now that `--host-abi` and `include/nox_host.h` exist.

## Consequences

- Phase 0 exit gates (`UR-001`–`UR-007` + unforced `python3.15t` dual-mode import + C host multi-thread + `audit_binary.py`) are satisfied on `noxc 2.2.81` (`2dcee335`) and CPython `3.15.2` / `3.15.0b3t`.
- Production CPython codegen in [`src/codegen/emit_c.nox`](../../src/codegen/emit_c.nox) and [`python/noxpy/codegen.py`](../../python/noxpy/codegen.py) now targets real `nox_host.h` `v1` directly, unblocking Phase 1 wheel builder implementation.
