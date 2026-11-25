# Changelog

All notable changes to **NoxPy** are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [PEP 440](https://peps.python.org/pep-0440/).

## [Unreleased] — Phase 0 Exit Complete (`noxc v2.2.81` + CPython `3.15.2` / `3.15t`)

### Changed / Verified
- Migrated from the temporary `v2.2.70` `--selfhost-obj` bridge to upstream [`native/include/nox_host.h`](native/include/nox_host.h) (`v1`, `--host-abi`, `--emit-obj`, `--emit-shared-lib`), deleting obsolete `nox_v2270_bridge.*` files.
- Upgraded [`native/include/noxpy_abi.h`](native/include/noxpy_abi.h) and [`native/cpython/noxpy_shim.c`](native/cpython/noxpy_shim.c) with bounded per-module `NoxHostContext` pooling (`NOXPY_CTX_POOL_CAP = 64`), `NoxPyHostOps`, `NoxHostBuffer` cleanup converters, and `NoxHostError` inspection.
- Aligned both [`src/codegen/emit_c.nox`](src/codegen/emit_c.nox) and [`python/noxpy/codegen.py`](python/noxpy/codegen.py) to emit `nox_host.h` `v1` wrappers byte-for-byte identically.
- Verified all 7 resolved upstream contracts (`UR-001`–`UR-007`) against `noxc 2.2.81` (`2dcee335`) and verified the same `.abi3t.so` binaries on CPython `3.15.2` and unforced free-threaded `3.15.0b3t` (`gil_enabled=False`, `peak_in_flight=8`), recorded in [`docs/evidence/phase0-2026-11-25-v2.2.81.md`](docs/evidence/phase0-2026-11-25-v2.2.81.md).

### Added (Earlier Phase 0 Baseline)
- Engineering charter ([`AGENTS.md`](AGENTS.md)), phased roadmap ([`TODO.md`](TODO.md)), and prioritized upstream requirements ([`UPSTREAM_REQUIREMENTS.md`](UPSTREAM_REQUIREMENTS.md)) anchored on `nox-lang v2.2.70` (`4dc061f8`).
- Executable upstream blocker reproducers (`tests/reproducers/ur001`–`ur007` + `run_all.sh`).
- Versioned target C host export contract ([`native/include/nox_host_abi.h`](native/include/nox_host_abi.h)) and audited CPython 3.15 `abi3t` core shim ([`native/include/noxpy_abi.h`](native/include/noxpy_abi.h), [`native/cpython/noxpy_shim.c`](native/cpython/noxpy_shim.c)) supporting strict `int64`, `bool`, `float`, and binary-safe `\0`-preserving UTF-8 `str`/`bytes` conversions.
- Isolated Phase 0 native PoC ([`experiments/phase0_poc/`](experiments/phase0_poc/)) demonstrating genuine `noxc 2.2.70` modules executing concurrently across 8 threads in C and CPython 3.15.2 with dual-library symbol isolation.
- Genuine Nox upstream-independent core package ([`nox.json`](nox.json), [`src/core/errors.nox`](src/core/errors.nox), [`src/codegen/ir.nox`](src/codegen/ir.nox), [`src/codegen/emit_pyi.nox`](src/codegen/emit_pyi.nox), [`src/codegen/emit_c.nox`](src/codegen/emit_c.nox), [`src/builder/config.nox`](src/builder/config.nox), [`src/cli/main.nox`](src/cli/main.nox)), Nox test suite ([`tests/nox/`](tests/nox/)), and byte-for-byte differential + `clang -Werror` verification ([`tests/codegen/test_nox_vs_python_codegen.py`](tests/codegen/test_nox_vs_python_codegen.py)).
- Pure-stdlib NoxPy tooling foundations ([`python/noxpy/`](python/noxpy/)): `pyproject.toml` validator, deterministic `ModuleIR` schema v1, `.pyi` and `PyModExport` C codegen, `noxpy doctor`, `noxpy check`, and fail-closed `noxpy build`.
- Cross-platform ABI binary auditor ([`tests/abi/audit_binary.py`](tests/abi/audit_binary.py)), unit tests (`tests/unit/`), and golden/clang codegen tests (`tests/codegen/`).
- Strict RTK hooks & enforcement verifier ([`RTK.md`](RTK.md), [`scripts/hooks/`](scripts/hooks/)).
- SHA-pinned GitHub Actions CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)), phase-by-phase Release pipeline ([`.github/workflows/release.yml`](.github/workflows/release.yml)), release gates ([`scripts/release/`](scripts/release/)), and [`docs/RELEASE.md`](docs/RELEASE.md).
- ADRs [`0001`](docs/adr/0001-cpython-315-dual-abi3t.md), [`0002`](docs/adr/0002-nox-host-export-abi.md) and Phase 0 evidence [`docs/evidence/phase0-2026-11-25.md`](docs/evidence/phase0-2026-11-25.md).
