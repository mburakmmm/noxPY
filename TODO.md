# NoxPy Roadmap & Master TODO List

> Native Python extensions written in Nox. Free-threaded first, safe by design.
>
> Aligned with [`AGENTS.md`](AGENTS.md) §19–§20. Checkboxes reflect **verified reality** only — never mark an item done without executed evidence.

---

## Hard Phase Gate Rule

**Phase 0 Exit Gates Satisfied (`2026-11-25` on `noxc v2.2.81` + `CPython 3.15.2` & unforced `3.15.0b3t`).** `UR-001`–`UR-007` are resolved, the experimental `--selfhost-obj` / `nox_v2270_bridge` is retired in favor of [`native/include/nox_host.h`](native/include/nox_host.h), and dual-mode unforced `python3.15t` execution is verified locally and in CI. **Ready for Phase 1 (`0.1 alpha`).**

---

## Phase 0 — Compiler / Runtime Feasibility (Complete)

### 0.1 Upstream Baseline & Blocker Audit
- [x] Inspect upstream `nox-lang` source, `AGENTS.md`, `VERSIONING.md`, `include/nox_embed.h`, `include/nox_nni.h`, `docs/apis/internal-abi.md`, `docs/language/ffi.md`, `docs/tools/noxc.md`, `docs/internals/runtime.md`, `docs/stdlib/thread.md`.
- [x] Record exact upstream baseline (`v2.2.70`, commit `4dc061f8c8443f8575092c2652d1109b6f4697e9`, installed `/Users/melihburakmemis/.nox-lang/bin/noxc` & `/Users/melihburakmemis/.nox-lang/lib/noxrt.o` SHA-256 digests).
- [x] Create `UPSTREAM_REQUIREMENTS.md` covering all blockers (`UR-001`–`UR-007`) with Batch A/B/C delivery order, reproducers, upstream source refs, compiler/runtime changes, ABI contracts, and acceptance tests.
- [x] Run all 7 executable blocker reproducers (`tests/reproducers/run_all.sh`) and confirm all 7 reproduce on `v2.2.70`.
- [x] Enforce permanent read-only `nox-lang` boundary across `.agents/rules/noxpy-workflow.md`, `CLAUDE.md`, and `scripts/hooks/session_rules.sh`.

### 0.1b Strict RTK Tooling & Hook Enforcement
- [x] Initialize official Antigravity RTK plugin (`.agents/plugins/rtk/`) via `rtk init --agent antigravity`.
- [x] Author `RTK.md`, `CLAUDE.md`, `.agents/hooks.json`, `.claude/settings.json`, `scripts/hooks/rtk_strict_guard.py`, `scripts/hooks/session_rules.sh`, `scripts/hooks/token_audit_hook.py`.
- [x] Verify all 29 strict RTK checks pass (`scripts/hooks/verify_rtk_enforcement.py`).

### 0.2 Target Host Contract, Audited CPython 3.15 `abi3t` Core Shim, & Isolated IR/Config/Codegen
- [x] Define C/C++-compatible `native/include/nox_host_abi.h` (version + struct_size, opaque context, fixed-width `NoxHostStatus`, `NoxHostSpan`, `NoxHostError`, `NoxHostRuntimeOps`, compile-time `_Static_assert` checks).
- [x] Define `native/include/noxpy_abi.h` enforcing `Py_TARGET_ABI3T=0x030F0000`, `PY_VERSION_HEX >= 0x030F0000`, `_Py_OPAQUE_PYOBJECT`, per-module `NoxPyModuleState` lifecycle (`NEW -> INITIALIZING -> READY -> CLOSING -> CLOSED`), and strict conversion/error APIs.
- [x] Implement & test `native/cpython/noxpy_shim.c` (`int64`, `bool`, `float`, binary-safe UTF-8 `str`, `bytes`) using strictly `abi3t` opaque APIs (`tests/unit/test_shim_converters.py` + `tests/abi/audit_binary.py`, warning-clean `-Wall -Wextra -Werror`).
- [x] Implement isolated NoxPy-side `python/noxpy/` foundations (`errors.py`, `config.py`, `ir.py`, `codegen.py`, `doctor.py`, `__main__.py`) with fail-closed `noxpy build` (`NPY-E-UPSTREAM`), `tests/unit/`, and `tests/codegen/` (`clang -Werror` check).
- [x] Implement upstream-independent NoxPy core in genuine Nox (`nox.json`, `src/core/errors.nox`, `src/codegen/{ir,emit_pyi,emit_c}.nox`, `src/builder/config.nox`, `src/cli/main.nox`), runnable Nox test suite (`tests/nox/*_test.nox` via `noxc test tests/nox`), and byte-for-byte differential + `clang -Werror` verification (`tests/codegen/test_nox_vs_python_codegen.py`).
- [x] Create GitHub repository documentation (`README.md`, `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `pyproject.toml`), `.github/` templates (`CODEOWNERS`, PR/issue templates, `dependabot.yml`), SHA-pinned CI (`.github/workflows/ci.yml`), and phase-by-phase Release pipeline (`.github/workflows/release.yml`, `scripts/release/`, `docs/RELEASE.md`).

### 0.3 Isolated Phase 0 PoC Spike (`experiments/phase0_poc/`, gated behind `NOXPY_PHASE0_EXPERIMENTAL_SELFHOST_BRIDGE`)
- [x] Compile genuine Nox functions (`math_mod.nox`: `nox_add`, `nox_fib`, `nox_work_loop`, `nox_checked_div`, `nox_alloc_checksum`; `mod_b.nox`: `nox_mul`, `nox_sub`) with `noxc 2.2.70`.
- [x] Build experimental `v2.2.70` bridge (`nox_v2270_bridge.{h,c}`) with per-call isolated `RuntimeState` and documented `nox_embed.h` exception handling (`nox_exception_pending`/`take`/`release`).
- [x] Build two independent shared libraries (`libpoc_a.dylib`, `libpoc_b.dylib`) with `-Wl,-exported_symbols_list` hiding internal symbols, and pass `c_host_test` across 1/2/4/8 `pthread` foreign OS threads (`peak_in_flight=8`, zero leaks).
- [x] Build two CPython 3.15 `abi3t` extension bundles (`_noxpy_poc.abi3t.so`, `_noxpy_poc_b.abi3t.so`) using `PyModExport_*`, `PyABIInfo_VAR`, `PySlot` (`PySlot_STATIC_DATA` for static metadata), `Py_MOD_GIL_NOT_USED`, and `Py_MOD_MULTIPLE_INTERPRETERS_NOT_SUPPORTED`.
- [x] Verify CPython 3.15.2 import, strict type/overflow/bool/no-`__index__` rejection, genuine Nox exception containment, concurrent barrier overlap (`peak_in_flight=8`), multi-module isolation, and subprocess teardown with zero stderr leak reports.
- [x] Audit `.abi3t.so` binaries (`tests/abi/audit_binary.py`): exported symbols allowlist, imported CPython symbol allowlist against `abi3t`, `otool -L` dependency closure, and SHA-256 digests.
- [x] Create Phase 0 ADRs (`docs/adr/0001-cpython-315-dual-abi3t.md`, `docs/adr/0002-nox-host-export-abi.md`) and evidence record (`docs/evidence/phase0-2026-11-25.md`).

### 0.4 Mandatory Phase 0 Exit Gates (Verified Complete on `noxc 2.2.81` + `python3.15t`)
- [x] **Verified (`uv` `python3.15t` `3.15.0b3`):** Exact same `.abi3t.so` binaries executed on free-threaded CPython `3.15t` in a clean subprocess without `-X gil=0`, confirming `not sys._is_gil_enabled()` before and after import plus 8-thread concurrent overlap (`peak_in_flight=8`).
- [x] **Resolved (`UR-001` in `v2.2.75`):** Upstream `noxc` supports desktop PIC `--emit-obj` / `--emit-static-lib` / `--emit-shared-lib` and PIC `noxrt.o`; `--selfhost-obj` workaround completely removed from `experiments/phase0_poc/`.
- [x] **Resolved (`UR-002` in `v2.2.78`):** Upstream ships versioned `include/nox_host.h` (`v1`) and `--host-abi` (`nox_host_module_init/fini`, `nox_host_export_*`); synchronized in [`native/include/nox_host.h`](native/include/nox_host.h) and targeted by [`src/codegen/emit_c.nox`](src/codegen/emit_c.nox) + [`python/noxpy/codegen.py`](python/noxpy/codegen.py).
- [x] **Resolved (`UR-003` in `v2.2.78`):** Shared libraries via `--emit-shared-lib --host-abi` export strictly `nox_host_*`; two independently built Nox libraries (`libpoc_a`/`libpoc_b`, `_noxpy_poc`/`_noxpy_poc_b`) with overlapping class tags run concurrently with zero `_nox_*` symbol leakage.
- [x] **Resolved (`UR-004` in `v2.2.78`):** Arbitrary foreign OS threads run safely via independent per-thread `NoxHostContext` pooling in [`native/cpython/noxpy_shim.c`](native/cpython/noxpy_shim.c) (`peak_in_flight=8`, 80-thread churn verified, zero 64-worker cap).
- [x] **Resolved (`UR-005` in `v2.2.78`, bounded fatal limit documented):** Recoverable Nox exceptions return `NOX_HOST_ERR_EXCEPTION` with public `class_name`/`message`/`line` inspection; OOM and fixed-width overflow traps remain process-fatal per `nox_host.h:25` and `AGENTS.md` §6.
- [x] **Resolved (`UR-006` in `v2.2.77`+`v2.2.78`):** Length-aware UTF-8/`bytes` spans (`\0`-safe) and generation-checked opaque `NoxHostHandle` verified end-to-end.
- [x] **Resolved (`UR-007` in `v2.2.76`):** Upstream `noxc expand --json` emits typed schema v1 metadata verified directly against [`src/cli/main.nox`](src/cli/main.nox).

---

## Phase 1 — `0.1 alpha`: Functions and Wheel (Complete on macOS arm64; Linux/Windows matrix ongoing)
- [x] Replace `experiments/phase0_poc/` experimental bridge with upstream `nox_host.h` production runtime adapter (`native/include/nox_host.h`, `native/cpython/noxpy_shim.c`, `src/codegen/emit_c.nox`).
- [x] Implement deterministic Binding IR schema v1 (`src/codegen/ir.nox`, `python/noxpy/ir.py`, [`ADR-0004`](docs/adr/0004-binding-ir-and-phase1-wheels.md)) fed by upstream `noxc expand --json` (`UR-007`).
- [x] Implement primitive type conversions (`int`, `float`, `bool`, `str` with length-aware UTF-8 & embedded NUL, `bytes`, `None`) with exact overflow/strictness checks (`native/cpython/noxpy_shim.c`).
- [x] Support positional-only (`/`), positional-or-keyword, and keyword-only (`*`) argument binding via `noxpy_bind_fastcall_kwargs` (`METH_FASTCALL | METH_KEYWORDS`); reject default values (`NPY-E-TYPE`) and unsupported combinations cleanly at build time.
- [x] Implement module init/lifecycle/context-pool/error mapping (`NoxPyHostOps`, `noxpy_module_state_init/close`, `noxpy_set_exception_from_host`).
- [x] Implement minimal PEP 517 backend (`python/noxpy/build.py`), ABI auditor (`python/noxpy/audit.py`), and CLI (`noxpy new`, `noxpy doctor`, `noxpy check`, `noxpy build`, `noxpy develop`).
- [x] Package audited dual-tag wheel (`cp315-abi3.abi3t-macosx_14_0_arm64.whl`) with `.abi3t.so`, `.pyi`, and `Requires-Python: >=3.15` (verified on macOS `arm64`; Linux/Windows wheel build testing follows in CI/Phase 2+).
- [x] Ship [`examples/hello`](examples/hello/) and [`examples/fastmath`](examples/fastmath/); verify clean `uv` wheel install and unforced dual-mode (`3.15.2` & `3.15.0b3t`) import outside repo with no compiler on `PATH` (`docs/evidence/phase1-2026-11-25.md`).

---

## Phase 2 — `0.2`: Types and Packaging (Complete — `2026-11-26` on `noxc v2.2.87` + `CPython 3.15.2` & `3.15.0b3t`)
- [x] **Verified (`UR-008` resolved in `noxc v2.2.85–v2.2.87`):** End-to-end `NoxHostValue` (ABI v2) marshaling for `T | None`, `list[T]`, `dict[K, V]`, `tuple[...]`, nested combinations (`list[tuple[int, str]]`, `dict[str, list[str | None]]`), constant parameter defaults (`int`, `float`, `bool`, `str`, `None`), cycle/depth guards (`NOXPY_MAX_CONV_DEPTH = 32`), atomic container snapshots (`PySequence_Tuple`, `PyDict_Items`), and 8-thread `3.15t` stress + concurrent mutation safety ([`native/cpython/noxpy_shim.c`](native/cpython/noxpy_shim.c), [`examples/mixed_package/`](examples/mixed_package/), [`docs/evidence/phase2-2026-11-26.md`](docs/evidence/phase2-2026-11-26.md)).
- [x] Generate deterministic `.pyi` type stubs and `py.typed` (PEP 561) from Binding IR + `noxpy generate-stubs` (including `--check`).
- [x] Support docstrings, `__text_signature__` (`inspect.signature` PEP 457 format with defaults), and mixed Python/native package layouts ([`examples/mixed_package`](examples/mixed_package/)).
- [x] Complete PEP 517 (`build_sdist`, `get_requires_*`, `prepare_metadata_*`) and PEP 660 (`build_editable`, `get_requires_for_build_editable`, `prepare_metadata_for_build_editable`).
- [x] Implement `nox.lock` validation ([`src/builder/lockfile.nox`](src/builder/lockfile.nox), [`python/noxpy/lockfile.py`](python/noxpy/lockfile.py)), deterministic `.noxpy-cache`, `SOURCE_DATE_EPOCH` reproducibility, `noxpy check`, `noxpy init`, `noxpy clean`.
- [x] Add seeded fuzz/property tests for converters, IR parser, lockfile, and `pyproject.toml` (`tests/fuzz/`) plus isolated `sdist -> wheel` & PEP 660 editable tests in `uv` (`tests/packaging/test_phase2_packaging.py`).

---

## Phase 3 — `0.3`: Native Objects & Classes (Complete — `2026-11-26` on `noxc v2.2.90` + `CPython 3.15.2` & `3.15.0b3t`)
- [x] Implement CPython 3.15 heap types (`PyType_FromModuleAndSpec` + `Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE` + PEP 697 negative `basicsize`) with interpreter-local type objects (`ADR-0005`).
- [x] Implement generation-checked opaque handle table (`UR-006`, `NoxHostHandle` + dedicated `owner_ctx` + `noxpy_obj_new_from_handle`) rejecting cross-interpreter/foreign-context use and use-after-close.
- [x] Implement `@export` native classes, methods, `@property` getters/setters, `@staticmethod`, `@classmethod`, custom `@export` exceptions (`PyErr_NewExceptionWithDoc` + `noxpy_set_exception_from_host_ex`), constructors, `__repr__`, `__str__`, `__len__`, and deterministic `.pyi` + `abi3t` C codegen (`python/noxpy/ir.py`, `python/noxpy/codegen.py`).
- [x] Enforce thread policies (`parallel`, `instance_locked`, `thread_affine`) and lock-order rules (`docs/architecture/threading.md`, `ADR-0005`, `tests/unit/test_phase3_heap_types.py`).
- [x] Implement GC `tp_traverse` / `tp_clear`, cyclic cleanup tests, context-manager (`close` idempotency, `__enter__`/`__exit__`), and unhashable/immutable checks.
- [x] Ship [`examples/classes`](examples/classes/) and [`examples/parallel`](examples/parallel/); pass same-instance contention (`8x250 == 2000`) and distinct-instance overlap tests on `CPython 3.15.2` & unforced `3.15.0b3t` ([`tests/packaging/test_phase3_classes.py`](tests/packaging/test_phase3_classes.py), [`docs/evidence/phase3-2026-11-26.md`](docs/evidence/phase3-2026-11-26.md)).

---

## Phase 4 — `0.4`: Callbacks, Async, and Isolation (Core Verified on `noxc v2.2.92`; Subinterpreter Pending `v2.2.93`)
- [x] Implement managed `PyObject` handle abstraction (`NoxPyExtRefTable` + `NoxHostExternalRef`, interpreter-affine, generation-checked, strong-ref tracked, never exposed as raw `PyObject*`).
- [x] Support synchronous reentrant callbacks and safe worker-thread callback routing (`PyGILState_Ensure`, `NoxHostCallbackOps`, original Python exception restoration across `NOX_HOST_ERR_HOST_EXCEPTION`).
- [x] Implement `asyncio.Future` async bridge (`@export async def` + `async` class methods, `loop.call_soon_threadsafe` completion, cooperative cancellation, deadlock `RuntimeError` containment, deferred instance close).
- [x] Implement iterator protocol (`__iter__` / `__next__`, `NoxPySharedCtx` lifetime preservation, clean `StopIteration` vs domain exception distinction).
- [x] Prove subinterpreter isolation (`Py_mod_multiple_interpreters = Py_MOD_PER_INTERPRETER_GIL_SUPPORTED`), cross-interpreter rejection (`owner_interp_id` checks on module lease, handle calls & buffers + `NOX_HOST_CAP_SUBINTERPRETERS`), and clean teardown across dual `3.15` / `3.15t` `concurrent.interpreters` tests on `noxc v2.2.95` (`UR-013`).

---

## Phase 5 — `0.5`: Native Data, Buffers, and Atlas (Verified on `noxc v2.2.95`)
- [x] Implement `src/buffer/` (`F64Buffer`/`I64Buffer`/`I32Buffer`/`U8Buffer`): PEP 3118 `Py_buffer` protocol with owner pinning (`active_pins` + `NOX_HOST_ERR_BUSY`), readonly/exclusive `writable()` proxy checks, stride/overflow validation, and bulk `NOX_HOST_VALUE_TYPED_ARRAY` fast input path.
- [x] Implement optional NumPy adapter (`integrations/numpy/`, `noxpy.numpy_adapter`) without polluting core ABI or re-enabling GIL.
- [x] Build benchmark harness (`benchmarks/bench_phase5.py`) reporting warmups, median, IQR/p95/p99 tail dispersion, scaling across 1/2/4/8 cores, and native vs Python/C baselines.
- [x] Integrate a real `Atlas` operation (`examples/atlas`) and prove zero-copy aliasing (`memoryview` + `writable()`) / single-copy (`TYPED_ARRAY`) semantics honestly.

---

## Phase 6 — `0.6`: Arrow, Noxen, Subinterpreters & Cross-Platform CI (Verified on `noxc v2.2.95`)
- [x] Implement Arrow C Data / C Stream interface adapter ([`python/noxpy/arrow_adapter.py`](python/noxpy/arrow_adapter.py), [`integrations/arrow/`](integrations/arrow/)) with schema/offset/release validation and zero-copy PyCapsule PEP 3118 buffer pinning.
- [x] Build realistic `Noxen` validation example ([`examples/noxen/`](examples/noxen/), `noxpy-noxen 0.6.0a1`) verified end-to-end across dual `3.15` / `3.15t` virtualenvs and concurrent subinterpreters.
- [x] Evaluate and gate standalone CPython embedding profile ([`src/runtime/embed.nox`](src/runtime/embed.nox), [`ADR-0009`](docs/adr/0009-embedding-and-platform-matrix.md): explicit non-`abi3t` `cp315-embed` profile marked `experimental-deferred` to keep extension wheels `libpython`-free).
- [x] Expand cross-platform CI matrix ([`.github/workflows/ci.yml`](.github/workflows/ci.yml): full native `abi3t` wheel E2E on `ubuntu-latest` + `macos-15`, plus `windows-latest` `noxc v2.2.95` toolchain smoke + `tests/nox` + `tests/unit`; full Windows `.pyd` wheel link & `manylinux_2_28` container wheel builds tracked for Phase 7 `1.0`).

---

## Phase 6.5 — `0.6.5`: P0/P1 Hardening, Real NumPy/PyArrow & Universal Nox Package Bindings (Verified on `noxc v2.2.95`)
- [x] Fix P0-1 subinterpreter callback/async thread-state (`PyThreadState_New` + `PyEval_AcquireThread`/`ReleaseThread`/`Clear`/`Delete` + deferred `Py_DECREF` draining), verified with synchronous callbacks, exceptions, and `asyncio.run(...)` inside concurrent `3.15` / `3.15t` `concurrent.interpreters`.
- [x] Fix P0-2 async premature loop-close retirement queue (`NoxPyRetiredCtx` drained safely outside worker thread) and P0-3 atomic lock-ordered `close()` vs `bf_getbuffer` + `writable_array` deterministic unpin/read-only guard.
- [x] Implement P1-1 bulk C-pack `TYPED_ARRAY` fast path for plain Python `list[float]` / `list[int]` / `list[bool]` and P1-2 real `numpy` (`2.5.5`) + `pyarrow` (`26.0.1`) zero-copy end-to-end verification.
- [x] Implement Universal Nox Package Bindings bridge ([`python/noxpy/pkg_bridge.py`](python/noxpy/pkg_bridge.py), `noxpy check --compatibility`, `noxpy init --from-nox-package`, `[tool.noxpy] export = "public"` across Python & Nox) verified with unmodified git Nox packages + real sibling `noxen` / `atlas` (`UR-016` documented for upstream bare intra-package imports & compiler-owned `--export-public`).

---

## Phase 7A — Hardening, Windows `.pyd`, `manylinux` & Universal Package Bindings v2 (Verified on `noxc 2.2.99`, pending CI)
- [x] Author isolated `UR-016` upstream specification ([`docs/upstream/ur016-universal-package-bindings.md`](docs/upstream/ur016-universal-package-bindings.md)) and executable reproducer ([`tests/reproducers/ur016_universal_package_bindings.sh`](tests/reproducers/ur016_universal_package_bindings.sh)).
- [x] Eliminate NumPy `writable_array()` UAF on escaped views/slices via `weakref`-guarded pin retention + `as_writable_ndarray()` ([`python/noxpy/numpy_adapter.py`](python/noxpy/numpy_adapter.py), [`ADR-0008`](docs/adr/0008-buffers-numpy-ownership.md)).
- [x] Remove `PyGILState_Ensure()` fallback in `noxpy_interp_attach()` and add per-interpreter `atexit` shutdown barrier (`active_attaches` + `active_async_ops`) in [`native/cpython/noxpy_shim.c`](native/cpython/noxpy_shim.c), verified by [`tests/stress/test_phase7a_soak.py`](tests/stress/test_phase7a_soak.py).
- [x] Verify real `NumPy` (`2.5.5`) + `PyArrow` (`26.0.1`, incl. `pa.chunked_array` C Stream) interop (`3.15` & `3.15t` NumPy + Arrow PyCapsule, with honest local `3.15.0b3t` `pyarrow.lib` import probe).
- [x] Upgrade Universal Package Bindings v2 (`pkg_bridge.py`: `--portable`, `--repo-url`, `--multi-module`, nested modules, `/` & `*` parameter kinds, and automatic non-generic public class wrappers).
- [x] Add pure-Python PE32+ Windows `.pyd` auditor ([`python/noxpy/audit.py`](python/noxpy/audit.py)), Windows `.pyd` linking & `NOXPY_PLATFORM_TAG` (`manylinux`) in [`python/noxpy/build.py`](python/noxpy/build.py), and upgrade [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (Windows `noxrt.o` link parity tracked in [`UR-017`](UPSTREAM_REQUIREMENTS.md)).

---

## Phase 7 — `1.0` Stable Release (Locked behind Phase 6 / `UR-016`)
- [ ] Finalize stable config, Binding IR schema, and dual `abi3`/`abi3t` policy; publish compatibility matrix.
- [ ] Complete `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, API docs, and migration guides.
- [ ] Run full Section 19 CI pipeline (formatting, unit, codegen, upstream contract, native build, ABI/dependency audit, wheel install, thread/GC/stress/fuzz, P0 matrix).
- [ ] Verify Section 19 Release Checklist:
  - [ ] Version, changelog, migration notes, support matrix, and feature status updated.
  - [ ] Exact upstream compiler/runtime digests and build configuration recorded.
  - [ ] All tests pass on ordinary 3.15 and unforced free-threaded 3.15t.
  - [ ] Stable ABI/dependency audit succeeds on every shipped wheel.
  - [ ] Same dual-tag wheel installs/imports in both modes without compiler on PATH.
  - [ ] Sdist and PEP 517/660 hooks verified; stubs match implementation.
  - [ ] No known critical memory-safety/data-race/ABI/security issues.
  - [ ] Licenses/SBOM, checksums, and provenance complete.
  - [ ] PyPI distribution name verified, dry-run verified, maintainer approved.
  - [ ] Release evidence archived in `docs/evidence/`.
