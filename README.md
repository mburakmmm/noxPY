# NoxPy

[![CI](https://github.com/mburakmmm/noxPY/actions/workflows/ci.yml/badge.svg)](https://github.com/mburakmmm/noxPY/actions/workflows/ci.yml)
[![Release](https://github.com/mburakmmm/noxPY/actions/workflows/release.yml/badge.svg)](https://github.com/mburakmmm/noxPY/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.15+](https://img.shields.io/badge/Python-3.15%2B-3776AB.svg)](https://docs.python.org/3.15/)
[![ABI: abi3t](https://img.shields.io/badge/ABI-abi3t%20%28free--threaded%29-2EA44F.svg)](docs/adr/0001-cpython-315-dual-abi3t.md)
[![Phase: 0 Complete -> 1 Ready](https://img.shields.io/badge/Phase-0%20Complete%20%E2%86%92%201%20Ready-brightgreen.svg)](TODO.md)

**Native Python extensions written in [Nox](https://github.com/mburakmmm/nox-lang). Free-threaded first (`abi3t`), safe by design.**

NoxPy combines a native CPython 3.15+ binding layer and a PEP 517/660 build pipeline so developers can author typed Nox modules, compile platform wheels targeting `cp315-abi3.abi3t`, and import them in both ordinary CPython 3.15 and free-threaded CPython `3.15t` without requiring wheel consumers to install `noxc`, Zig, or a C compiler.

> **Current Status (Phase 0 Exit Verified on `noxc v2.2.81` + `CPython 3.15.2` & unforced `3.15t`):**
> All 7 upstream requirements ([`UR-001`–`UR-007`](UPSTREAM_REQUIREMENTS.md)) are resolved in `nox-lang v2.2.75`–`v2.2.81` (`include/nox_host.h` v1, `--host-abi`, `--emit-obj`/`--emit-shared-lib`, `bytes`, `noxc expand --json`). The experimental `--selfhost-obj` bridge is retired, bounded per-module `NoxHostContext` pooling runs in parallel across `3.15.2` and unforced `3.15t`, and **Phase 1 (`0.1 alpha`: PEP 517 wheel builder)** is ready to begin.

---

## Architecture

```text
Python package / user code
  -> CPython native module (.abi3t.so / .pyd) via PyModExport_<leaf>
     -> deterministic C adapters + audited abi3t shim (native/cpython/noxpy_shim.c)
        -> versioned Nox Host Export ABI (native/include/nox_host_abi.h)
           -> compiled Nox modules + matching hidden-visibility Nox runtime
```

Design charter & normative invariants live in [`AGENTS.md`](AGENTS.md):
- **Minimum Python:** CPython `3.15+` (`Py_TARGET_ABI3T=0x030F0000`, `_Py_OPAQUE_PYOBJECT`, `PyModExport_*`, `Py_MOD_GIL_NOT_USED`).
- **True concurrency:** Zero process-global invocation mutexes; per-module atomic lifecycle leases (`NEW -> INITIALIZING -> READY -> CLOSING -> CLOSED`).
- **Strict conversions:** `bool` never silently coerces to `int`; `__index__`/`__float__` are never implicitly invoked; `int64` overflow raises `OverflowError`; UTF-8 `str` and `bytes` spans preserve embedded `\0`.
- **Clean upstream boundary:** `nox-lang` is strictly read-only here; missing compiler/runtime primitives are tracked with executable reproducers in [`UPSTREAM_REQUIREMENTS.md`](UPSTREAM_REQUIREMENTS.md).

---

## Phase & Verification Matrix

| Component | State | Verified Scope |
|---|---|---|
| CPython 3.15 `abi3t` core shim ([`noxpy_shim.c`](native/cpython/noxpy_shim.c)) | **Tested** | Compiled `-Wall -Wextra -Werror` (`Py_TARGET_ABI3T=0x030F0000`), symbol-audited, tested (`int64`, `bool`, `float`, `\0`-safe `str`/`bytes`) on CPython 3.15.2 |
| Config, Binding IR, Stub & C Codegen ([`src/`](src/), [`python/noxpy/`](python/noxpy/)) | **Tested** | Genuine Nox core (`nox.json`, `src/{core,codegen,builder,cli}/*.nox`) verified via `noxc test tests/nox` (`3/3`), Python `tests/unit/` (`24/24`), and byte-for-byte differential + `clang -Werror` CPython 3.15 header check (`tests/codegen/`, `4/4`) |
| Phase 0 End-to-End Host ABI Suite ([`experiments/phase0_poc/`](experiments/phase0_poc/)) | **Verified** | Genuine `@export` Nox modules built via `--host-abi` (`include/nox_host.h`), concurrent `1/2/4/8` threads (`peak_in_flight=8`), dual-library isolation, `\0`-safe `str`/`bytes`, `ValueError` inspection, `NoxHostHandle` lifecycle |
| Upstream Contract Verification ([`tests/reproducers/`](tests/reproducers/)) | **Verified (`7/7`)** | `UR-001`–`UR-007` positively verified against `noxc v2.2.81` (`2dcee335`) |
| Free-Threaded `python3.15t` Unforced Run | **Verified** | [`test_freethreaded_315t.py`](tests/abi/test_freethreaded_315t.py) passes locally on `3.15.0b3t` (`gil_enabled=False`, `peak_in_flight=8`) and in CI |
| Production Packaging & Wheels (Phase 1 — `0.1 alpha`) | **Unlocked (Next)** | Phase 0 exit gates satisfied; `python/noxpy/build.py` PEP 517 wheel builder next |

---

## Quickstart (Local Verification)

Prerequisites: CPython `3.15+`, `clang`, [`rtk`](RTK.md), and (for Phase 0 PoC / reproducers) `noxc >= 2.2.70`.

```sh
# 1. Inspect toolchain, CPython 3.15 abi3t headers, and Phase 0 gate status
PYTHONPATH=python python3.15 -m noxpy doctor

# 2. Run genuine Nox unit/codegen tests & Python unit/differential tests
noxc check src/cli/main.nox
noxc test tests/nox
python3.15 -m unittest discover -s tests/unit -v
python3.15 -m unittest discover -s tests/codegen -v

# 3. Run the isolated Phase 0 native C + CPython 3.15 PoC
bash experiments/phase0_poc/build_and_test.sh

# 4. Verify all 7 upstream requirements against installed noxc
bash tests/reproducers/run_all.sh

# 5. Validate strict RTK hooks & release gate dry-run
python3 scripts/hooks/verify_rtk_enforcement.py
python3.15 scripts/release/prepare_phase_release.py --dry-run
```

---

## Repository Map

- [`AGENTS.md`](AGENTS.md) — Normative engineering charter, CPython 3.15 `abi3t` rules, and phase gates.
- [`TODO.md`](TODO.md) — Phased implementation checklist (Phase 0 through Phase 7).
- [`UPSTREAM_REQUIREMENTS.md`](UPSTREAM_REQUIREMENTS.md) — Prioritized upstream `nox-lang` batches (`Batch A/B/C`) with runnable reproducers.
- [`docs/RELEASE.md`](docs/RELEASE.md) — Phase-by-phase release runbook, immutable artifact pipeline, and OIDC publishing.
- [`docs/adr/`](docs/adr/) & [`docs/evidence/`](docs/evidence/) — Architectural Decision Records and recorded verification evidence.
- [`native/`](native/) — Target `nox_host_abi.h` contract and audited CPython 3.15 `abi3t` C shim.
- [`src/`](src/) & [`nox.json`](nox.json) — Upstream-independent NoxPy core written in genuine Nox (`errors`, `ir`, `.pyi`/C codegen, `pyproject.toml` config validator, and `noxpy` Nox CLI).
- [`python/noxpy/`](python/noxpy/) — Python PEP 517 bootstrap adapter, `pyproject.toml` validator, Binding IR, `.pyi`/C codegen, and `noxpy` CLI (`doctor`, `check`, `generate-stubs`).
- [`.github/workflows/`](.github/workflows/) — SHA-pinned CI and tag-gated release automation.

---

## Contributing, Security & License

- Contributions: see [`CONTRIBUTING.md`](CONTRIBUTING.md).
- Security & trust boundary: see [`SECURITY.md`](SECURITY.md).
- Release history: see [`CHANGELOG.md`](CHANGELOG.md).
- License: [MIT](LICENSE).
