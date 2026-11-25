---
trigger: always_on
---

# NoxPy Upstream Boundary & Workflow Rules

1. **NEVER modify, patch, commit, or write inside `/Users/melihburakmemis/Documents/nox-lang`.**
   The upstream `nox-lang` repository is owned and modified exclusively by the user. Agents may inspect it read-only to verify compiler/runtime capabilities and document blockers, never edit it.
2. **Requirement -> Upstream Solution -> Progress Loop:**
   - Complete everything feasible on the `noxPY` side using genuine Nox code, audited CPython 3.15 `abi3t` shims, deterministic IR/config/codegen/CLI tooling, and executable tests.
   - Record every missing compiler or runtime primitive in `UPSTREAM_REQUIREMENTS.md` with runnable reproducers in `tests/reproducers/`.
   - Wait for the user to solve requirements upstream in `nox-lang`, then re-run `tests/reproducers/run_all.sh` and advance phases only when exit criteria genuinely pass.
3. **Strict RTK & Phase Gate Discipline:**
   - All shell commands must follow `RTK.md` and pass `scripts/hooks/rtk_strict_guard.py`.
   - Never fake native functionality or proceed past Phase 0 before `UR-001`–`UR-007` and unforced `python3.15t` tests pass.
