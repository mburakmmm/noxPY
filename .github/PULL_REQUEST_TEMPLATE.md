## Summary

<!-- Concise summary of what changed and which Phase / TODO item it addresses. -->

## Verification Evidence

- [ ] `python3.15 -m unittest discover -s tests/unit -v` passed
- [ ] `python3.15 -m unittest discover -s tests/codegen -v` passed
- [ ] CPython `3.15` and unforced `3.15t` (`not sys._is_gil_enabled()`) checks verified or explicitly noted
- [ ] Native `.abi3t.so` binaries pass `tests/abi/audit_binary.py` (no leaked `nox_*` exports, no private CPython imports)
- [ ] `python3 scripts/hooks/verify_rtk_enforcement.py` passed

## Compliance Checklist

- [ ] No modifications to upstream `nox-lang`
- [ ] No fake native functionality, guessed internal layouts, or global invocation locks
- [ ] `TODO.md`, `CHANGELOG.md`, `UPSTREAM_REQUIREMENTS.md`, and ADRs/evidence updated where applicable
