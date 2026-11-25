# ADR-0004: Deterministic Binding IR (`schema_version = 1`) & Phase 1 Dual-Tag `cp315-abi3.abi3t` Wheels

- **Status:** Accepted — Implemented and verified on `noxc 2.2.81` + `CPython 3.15.2` / `3.15.0b3t`
- **Date:** 2026-11-25

## Context

Phase 1 (`0.1 alpha`) requires compiling typed Nox `@export` functions into standalone Python extension wheels (`AGENTS.md` §7, §15) without regex-parsing `.nox` source files, without requiring wheel consumers to install `noxc` or a C compiler, and supporting positional, positional-or-keyword, and keyword-only arguments cleanly under CPython 3.15 dual `abi3t`.

## Decision

1. **Compiler-Owned Metadata -> Binding IR Schema v1 (`src/codegen/ir.nox`, `python/noxpy/ir.py`):**
   - `noxpy.build` queries `noxc expand --json --module <leaf> <source>` rather than parsing `.nox` ASTs or syntax with regexes.
   - IR validation enforces schema version `1`, compiler identity (`noxc`), valid non-C-keyword identifiers, Phase 1 scalar/span types (`int`, `float`, `bool`, `str`, `bytes`, `None`), parameter ordering rules (`positional_only` -> `positional_or_keyword` -> `keyword_only`), unique IDs/symbols, and deterministic `(name, export_id)` sorting.
2. **Unified Codegen Across Nox & Python (`src/codegen/`, `python/noxpy/codegen.py`):**
   - Both the genuine Nox emitters and Python emitters generate byte-for-byte identical `.pyi` stubs (PEP 561) and CPython 3.15 `abi3t` C adapters targeting `nox_host.h` `v1` and `noxpy_abi.h`.
   - Zero-arg and multi-arg exports register with `METH_FASTCALL | METH_KEYWORDS`, routing parameter binding through `noxpy_bind_fastcall_kwargs` (`PyTuple_Size`, `PyTuple_GetItem`, `PyUnicode_CompareWithASCIIString`). Defaults remain explicitly rejected in IR v1 until default-expression lowering is specified.
3. **Audited Dual-Tag PEP 427 Wheels (`python/noxpy/build.py`, `python/noxpy/audit.py`):**
   - Every compiled `.abi3t.so` links `noxrt.o` statically with `-Wl,-dead_strip` / `-Wl,--gc-sections` and explicit export-list symbol hiding (`PyModExport_<leaf>` only), then passes `audit_extension_binary` before packaging.
   - Wheels use tag `cp315-abi3.abi3t-<platform>`, `Root-Is-Purelib: false`, `Requires-Python: >=3.15`, sorted entries, `SOURCE_DATE_EPOCH` determinism, and valid SHA-256 `RECORD` hashes.

## Consequences

- `examples/hello` and `examples/fastmath` build reproducibly via `noxpy build --release` and PEP 517 `noxpy.build:build_wheel`, and install/import cleanly via `uv` into isolated `CPython 3.15.2` and unforced `CPython 3.15.0b3t` virtualenvs with empty `PATH` and zero compilers installed.
- Note on `TODO.md` Phase 1 default-parameter clause: default parameter values remain rejected fail-closed by IR validation (`NPY-E-TYPE`) until a compiler-evaluated default representation is added in Phase 2 (`UR-007` schema evolution).
