# UR-016 — Universal Nox Package Bindings & Package-Relative Imports (`nox-lang` Upstream Spec)

> **Purpose:** Standalone upstream specification for `nox-lang` enabling NoxPy to bind **existing, unmodified Nox packages** (such as `atlas`, `noxen`, and `nova`) into CPython 3.15 `abi3t` extension wheels without requiring `@export` annotations or manual wrapper rewrites.
>
> - **Observed Upstream Baseline:** `nox-lang v2.2.95` – `v2.2.99` (`NOX_HOST_ABI_VERSION = 3u`, `v3-rc`)
> - **C ABI Compatibility:** **Zero breaking changes** to `include/nox_host.h` (`v3`). All requirements below are compiler/CLI/resolution additions (`noxc`) emitting standard Host ABI v3 wrappers and metadata.
> - **Reproducer:** [`tests/reproducers/ur016_universal_package_bindings.sh`](../../tests/reproducers/ur016_universal_package_bindings.sh)

---

## Summary of Sub-Requirements

| Sub-ID | Scope | Priority | Blocker For |
|---|---|---|---|
| `UR-016.1` | Package-relative intra-package import resolution (`import errors` inside dependency `atlas.values`) | **P0** | Unmodified `atlas` (and any multi-file Nox package) |
| `UR-016.2` | Compiler-owned public API discovery (`noxc expand --public-api --json`) | **P0** | Replacing regex scanner `pkg_bridge.py` with semantic compiler IR |
| `UR-016.3` | Direct public export compilation (`--export-public`, `--export-modules`, `--export-select`, `--strict-export`) | **P1** | Eliminating generated `src/lib.nox` forwarding shims |
| `UR-016.4` | Concrete generic monomorphization (`--specialize`) | **P1** | Generic functions & classes (`Series[float]`, `sum_slice[int]`) |
| `UR-016.5` | Public non-generic class & exception export completeness | **P1** | Unmodified classes (`UserValidator`, `Table`, `Series`, `FieldError`) |

---

## `UR-016.1` — Package-Relative Intra-Package Import Resolution (P0)

### Problem Statement

When a consumer project `pkg_b` declares an unmodified Nox package `pkg_a` (e.g. `atlas`) in `pkg_b/nox.json` (`requires`), fetches it via `noxc fetch`, and imports a submodule such as:

```nox
import atlas.values
```

bare intra-package imports inside `atlas/values.nox`:

```nox
import errors
from errors import TypeConversionError, ShapeError
```

currently fail or resolve relative to `pkg_b`'s working directory instead of `atlas`'s own package root (`~/.nox/cache/.../atlas/`). Today this blocks binding `atlas` even via thin wrapper packages (`noxen.validators` only works because it imports `nox.*` stdlib modules exclusively).

### Required Compiler Semantics

When compiling or type-checking a module `M` whose source file belongs to package `P` (determined by the `nox.json` owning `M`, whether the root project or a fetched dependency in `nox.lock` / `~/.nox/cache`):

1. **Resolution Precedence inside `M`:**
   1. `nox.*` standard library modules (always resolved from toolchain stdlib).
   2. Explicit package aliases declared in **`P`'s own `nox.json` `requires`** (transitive package dependencies).
   3. **Package-relative modules inside `P`** (e.g. `import errors` inside `atlas/values.nox` resolves to `<P_root>/errors.nox` or `<P_root>/src/errors.nox`, and `import sub.mod` resolves to `<P_root>/sub/mod.nox` or `<P_root>/src/sub/mod.nox`).
   4. Never fall back to the *consumer* project's CWD for bare imports originating inside a dependency package `P`.
2. **Symbol Isolation:** Sibling packages `P1` and `P2` both having an internal `errors.nox` must receive distinct internal module namespaces so C/LLVM symbols never collide during `--emit-obj --host-abi` or `noxc build`.
3. **Applicable Commands:** `noxc check`, `noxc build`, `noxc test`, and `noxc expand`.

---

## `UR-016.2` — Compiler-Owned Public API Discovery (`noxc expand --public-api --json`) (P0)

### Problem Statement

NoxPy currently uses a regex-based source scanner (`python/noxpy/pkg_bridge.py`) to inspect unmodified packages. Regex scanning cannot reliably resolve type aliases, imported return/parameter types, `/` and `*` parameter separators, nested module exports, or transitive stdlib subinterpreter safety. Per `AGENTS.md` §3, the compiler must own semantic type resolution.

### Required CLI Contract

```text
noxc expand --public-api --json [--package <dir>] [--modules <m1,m2,...>] <entry_or_package>
```

- Works on a single `.nox` file OR a package directory containing `nox.json` (discovering all public non-test `.nox` modules unless filtered by `--modules`).
- Does **not** require `@export` decorators on upstream symbols.

### Required JSON Schema (Additive on `expand --json`)

```json
{
  "schema_version": 2,
  "compiler": "noxc 2.2.x",
  "package": "atlas",
  "version": "0.1.0",
  "modules": ["values", "table", "errors"],
  "subinterpreter_safe": true,
  "unsafe_stdlib_imports": [],
  "symbols": [
    {
      "kind": "function",
      "module": "values",
      "name": "checked_add_int64",
      "qualified_name": "atlas.values.checked_add_int64",
      "is_async": false,
      "is_generic": false,
      "type_params": [],
      "params": [
        {"name": "a", "type": "int", "kind": "positional_or_keyword", "default": null},
        {"name": "b", "type": "int", "kind": "positional_or_keyword", "default": null},
        {"name": "context_msg", "type": "str", "kind": "positional_or_keyword", "default": {"type": "str", "value": ""}}
      ],
      "return_type": "int",
      "compatibility": "exportable",
      "reason_code": null,
      "doc": "...",
      "span": {"file": "values.nox", "line": 15, "column": 1}
    },
    {
      "kind": "class",
      "module": "errors",
      "name": "FieldError",
      "is_generic": false,
      "is_exception": false,
      "thread_policy": "instance_locked",
      "fields": [{"name": "path", "type": "str"}],
      "constructor": {"params": ["..."]},
      "methods": ["..."],
      "compatibility": "exportable",
      "reason_code": null
    }
  ]
}
```

Required fields per symbol:
- `compatibility`: `"exportable"` | `"needs_specialization"` | `"unsupported"`.
- `reason_code`: `null` when exportable, else deterministic machine code (e.g. `"GENERIC_REQUIRES_SPECIALIZATION"`, `"UNSUPPORTED_PARAM_TYPE"`, `"VARARGS_UNSUPPORTED"`, `"NON_LITERAL_DEFAULT"`).
- `params[].kind`: `"positional_only"` | `"positional_or_keyword"` | `"keyword_only"` | `"var_positional"` | `"var_keyword"`.
- `unsafe_stdlib_imports`: transitive list of process-global stdlib modules (`nox.http`, `nox.tls`, `nox.sqlite`, `nox.postgres`, `nox.terminal`, `nox.signal`) reachable from the inspected modules.

---

## `UR-016.3` — Direct Public Export Compilation (`--export-public`) (P1)

```text
noxc build --backend llvm --host-abi --export-public \
  [--export-modules mod1,mod2] \
  [--export-select mod1.fn1,mod2.ClassA] \
  [--strict-export] \
  --module-name <leaf> --emit-obj -o <out.o> <entry.nox>
```

- Compiles all public non-`_` symbols (or the filtered subset from `--export-modules` / `--export-select`) into Host ABI v3 (`nox_host_export_<leaf>_*`) wrappers without editing the source `.nox` files.
- Default behavior: skip `unsupported` / `needs_specialization` symbols with structured JSON diagnostics; `--strict-export` turns any skipped selected symbol into a build error.
- `noxc expand --public-api --host-abi --json` emits the corresponding Host ABI v3 `exports[]` manifest so `noxpy.build` can feed it straight into `codegen.py`.

---

## `UR-016.4` — Concrete Generic Monomorphization (`--specialize`) (P1)

```text
noxc build ... --specialize "Series[float]=FloatSeries" --specialize "sum_slice[int]=sum_i64"
```

- Allows consumers/NoxPy to monomorphize generic Nox functions and classes under explicit Python-safe export names without modifying the upstream generic definition.
- `noxc expand --public-api --json --specialize ...` emits the specialized concrete signature as `"kind": "function"` / `"kind": "class"` with `"compatibility": "exportable"`.

---

## `UR-016.5` — Public Non-Generic Class & Exception Export Completeness (P1)

- Plain public classes (e.g. `noxen.errors.FieldError`, `atlas.table.Table` where Host-ABI-compatible) and custom `Exception` subclasses (e.g. `ValidationError`, `ShapeError`) are exported identically to `@export class` (`UR-009`) when selected via `--export-public`.
- Default `thread_policy` for unannotated public classes under `--export-public`: `"instance_locked"` (per-instance locking in `nox_host` / `noxpy_shim.c`), never `"thread_safe"` unless explicitly annotated.

---

## Acceptance Criteria for Closing `UR-016`

1. `tests/reproducers/ur016_universal_package_bindings.sh` exits `0`.
2. Unmodified `atlas` (`values.nox` with `import errors`) compiles and passes `noxc check` / `noxc build --host-abi` as a fetched `nox.json` dependency of an external wrapper package.
3. Unmodified `noxen` (`validators.nox` + `errors.nox`) and `atlas` produce valid `noxc expand --public-api --json` output with accurate `params[].kind`, class methods/properties, and `subinterpreter_safe` flags.
4. No changes to `include/nox_host.h` ABI v3 struct layouts (`abi_layout.c` gate continues to pass).
