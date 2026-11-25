# NoxPy

[![CI](https://github.com/mburakmmm/noxPY/actions/workflows/ci.yml/badge.svg)](https://github.com/mburakmmm/noxPY/actions/workflows/ci.yml)
[![Release](https://github.com/mburakmmm/noxPY/actions/workflows/release.yml/badge.svg)](https://github.com/mburakmmm/noxPY/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.15+](https://img.shields.io/badge/Python-3.15%2B-3776AB.svg)](https://docs.python.org/3.15/)
[![ABI: abi3t](https://img.shields.io/badge/ABI-abi3t%20%28free--threaded%29-2EA44F.svg)](docs/adr/0001-cpython-315-dual-abi3t.md)
[![Phase: 0 (Feasibility)](https://img.shields.io/badge/Phase-0%20Feasibility-orange.svg)](TODO.md)

**Native Python extensions written in [Nox](https://github.com/mburakmmm/nox-lang). Free-threaded first (`abi3t`), safe by design.**

NoxPy combines a native CPython 3.15+ binding layer and a PEP 517/660 build pipeline so developers can author typed Nox modules, compile platform wheels targeting `cp315-abi3.abi3t`, and import them in both ordinary CPython 3.15 and free-threaded CPython `3.15t` without requiring wheel consumers to install `noxc`, Zig, or a C compiler.

> **Current Status (Phase 0 — Compiler & Runtime Feasibility):**
> NoxPy is in **Phase 0**. Core CPython 3.15 `abi3t` shims, deterministic config/IR/codegen tooling, and an isolated Phase 0 PoC on `noxc v2.2.70` are implemented and verified on CPython 3.15.2 (macOS arm64). **Phase 1 stays gated** until upstream `nox-lang` resolves [`UR-001`–`UR-007`](UPSTREAM_REQUIREMENTS.md) and unforced `python3.15t` CI/local execution is green.

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
| Config, Binding IR, Stub & C Codegen ([`python/noxpy/`](python/noxpy/)) | **Tested** | 14 unit tests + 2 golden/clang `-Werror` codegen tests |
| Phase 0 Experimental PoC ([`experiments/phase0_poc/`](experiments/phase0_poc/)) | **Experimental (Gated)** | Genuine `noxc 2.2.70` modules running concurrently across `1/2/4/8` threads (`peak_in_flight=8`), dual-library isolation, empty-stderr teardown |
| Upstream Blocker Reproducers ([`tests/reproducers/`](tests/reproducers/)) | **Verified (`7/7`)** | `UR-001`–`UR-007` reproduced against `noxc v2.2.70` (`4dc061f8`) |
| Free-Threaded `python3.15t` Unforced Run | **Pending CI / Local `3.15t`** | [`test_freethreaded_315t.py`](tests/abi/test_freethreaded_315t.py) ready; CI workflow tests both `3.15` and `3.15t` on Ubuntu/macOS |
| Production Packaging & Wheels (Phases 1–7) | **Gated** | Blocked behind Batch A (`UR-001`–`UR-005`) and Batch B (`UR-007`, `UR-006`) |

---

## Quickstart (Local Verification)

Prerequisites: CPython `3.15+`, `clang`, [`rtk`](RTK.md), and (for Phase 0 PoC / reproducers) `noxc >= 2.2.70`.

```sh
# 1. Inspect toolchain, CPython 3.15 abi3t headers, and Phase 0 gate status
PYTHONPATH=python python3.15 -m noxpy doctor

# 2. Run isolated unit & golden codegen tests (includes compiling & auditing abi3t converters)
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
- [`python/noxpy/`](python/noxpy/) — Config validator, Binding IR, `.pyi`/C codegen, and `noxpy` CLI (`doctor`, `check`, `generate-stubs`).
- [`.github/workflows/`](.github/workflows/) — SHA-pinned CI and tag-gated release automation.

---

## Contributing, Security & License

- Contributions: see [`CONTRIBUTING.md`](CONTRIBUTING.md).
- Security & trust boundary: see [`SECURITY.md`](SECURITY.md).
- Release history: see [`CHANGELOG.md`](CHANGELOG.md).
- License: [MIT](LICENSE).
