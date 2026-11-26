# ADR-0009: Standalone Embedding Policy & Cross-Platform Support Matrix

- **Status:** Accepted (Phase 6 — `0.6`, verified on `nox-lang v2.2.95`)
- **Date:** 2026-11-26
- **Related:** `AGENTS.md` §1, §5, §19, §20, §22; [`src/runtime/embed.nox`](../../src/runtime/embed.nox); [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)

## Context

NoxPy Phase 6 covers two release-architecture questions:
1. Whether standalone CPython embedding (a native Nox executable hosting `libpython`) should ship alongside extension wheels or remain an explicitly separated profile.
2. Which target platforms qualify as verified P0 vs P1/P2/P3 support in CI (`AGENTS.md` §19–§20).

## Decisions

### 1. Standalone CPython Embedding Is Gated and Deferred (`cp315-embed`, non-`abi3t`)
- Python extension modules built by NoxPy MUST remain `libpython`-free `cp315-abi3.abi3t` shared objects/extensions loaded into an existing Python host process.
- Standalone embedding is specified separately in [`src/runtime/embed.nox`](../../src/runtime/embed.nox) (`EMBED_PROFILE_STATUS = "experimental-deferred"`):
  - It forbids `"abi3"` and `"abi3t"` (raising `NPY-E-ABI`) and requires explicit `"cp315-embed"` + `link_libpython=True`.
  - Per `AGENTS.md` §20 Phase 6, deferring full standalone embedding runtime implementation preserves extension binary correctness and keeps the `0.6` focus on subinterpreter isolation, Arrow interop, and Noxen validation.

### 2. Platform Matrix and Honest Verification Tiers (`nox-lang v2.2.95`)

| Tier | Platform | Current Verified Coverage | Remaining Phase 7 Gate |
|---|---|---|---|
| **P0** | `macOS arm64` (`macosx_14_0_arm64`) | Full native `.abi3t.so` wheel E2E on `CPython 3.15` + unforced `3.15t` (`Phases 1–6`), UBSan, symbol/dep audit | Soak stress / 1.0 audit |
| **P0** | `Linux x86_64` (`ubuntu-latest`) | Full native `.abi3t.so` wheel E2E on `CPython 3.15` + unforced `3.15t` (`Phases 1–6`), ASan/UBSan, symbol/dep + `GLIBC` check hook (`NOXPY_MAX_GLIBC`) | `manylinux_2_28` container wheel build & audit in Phase 7 |
| **P0** | `Windows x86_64` (`windows-latest`) | Portable Win32 `CRITICAL_SECTION` / `SwitchToThread` / `GetCurrentThreadId` shim (`noxpy_abi.h` / `noxpy_shim.c`), `noxc v2.2.95` native smoke + `tests/nox` + `tests/unit` | Full `.pyd` wheel link (`noxc --emit-obj` + MSVC / MinGW COFF import library) in Phase 7 |
| **P1/P2** | `Linux aarch64`, `Windows arm64`, `macOS x86_64` | Not claimed until CI execution evidence exists | Future CI runners |

### 3. Pinned Upstream `nox-lang v2.2.95` Digests
- `nox-lang-v2.2.95-macos-arm64.tar.gz`: `f0a8e51edadbb8533153d0834334e397188f669cc8943dc9c5c09aab504d6234`
- `nox-lang-v2.2.95-linux-x64.tar.gz`: `8b49098bf6c5b6072e58a7246ced294dd7888ad614099920ba5cfd4190942539`
- `nox-lang-v2.2.95-windows-x64.zip`: `2e9ee9b66cc2ba6e96d85651fdbfb5996c90dd1e942c9e0d91a37be8a7bdc956`

## Consequences
- No undocumented platform claims: docs and `TODO.md` separate full Linux/macOS `.abi3t.so` wheel verification from Windows toolchain/unit CI until real `.pyd` execution evidence lands in Phase 7.
- Portable mutex primitives eliminate POSIX-only `pthread_mutex_t`/`sched_yield` compile blockers in [`native/include/noxpy_abi.h`](../../native/include/noxpy_abi.h) ahead of Windows `.pyd` bring-up.
