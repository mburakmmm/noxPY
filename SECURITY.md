# Security Policy

## Trust Boundary

NoxPy compiles and loads **native code inside the CPython process**. It is **not** a security sandbox (`AGENTS.md` §16). External `.nox` sources, dependencies in `nox.lock`, FFI declarations (`extern def`), and binary wheels execute with the full privileges of the host Python process.

Treat the following as security-sensitive boundaries:
- Build manifests (`pyproject.toml`, `UR-007` JSON IR) and relative path resolution (parent-directory traversal `..` is rejected).
- ABI compatibility, symbol visibility, and dependency closure of shipped `.abi3t.so` / `.pyd` binaries.
- Reference counting, opaque handle lifetimes, generation checks, lock ordering, and interpreter/module shutdown.
- C string/identifier escaping in generated C adapters.

Reproducible data races, use-after-free, double-free, refcount corruption, or ABI/symbol leaks are treated as release blockers.

## Supported Versions

| Version / Phase | Status | Security Fixes |
|---|---|---|
| `0.0.x` (Phase 0 feasibility) | Pre-release development | Best effort on `main` |
| `0.1.0a*` – `0.6.*` (Phases 1–6) | Pre-1.0 alphas/betas | Latest pre-release only |
| `1.0.x` (Phase 7) | Planned stable | Supported per release policy |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for suspected security vulnerabilities, memory corruption, or data races.

Report privately via GitHub Security Advisories:
- https://github.com/mburakmmm/noxPY/security/advisories/new

Include:
1. NoxPy commit/tag, `noxc --version`, Python version (`3.15` / `3.15t`), and OS/architecture.
2. Minimal reproducer (`.nox` / `.py` / `pyproject.toml`).
3. Observed impact (crash, race, leak, symbol exposure) and any sanitizer/debugger trace.
