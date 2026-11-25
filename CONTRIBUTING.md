# Contributing to NoxPy

Thank you for contributing to **NoxPy** (`https://github.com/mburakmmm/noxPY`). Before opening a pull request, please read [`AGENTS.md`](AGENTS.md).

## Core Engineering Rules

1. **Upstream Boundary (`nox-lang`):** Never modify or submit patches against `/Users/melihburakmemis/Documents/nox-lang` from NoxPy tasks. Record missing compiler or runtime primitives in [`UPSTREAM_REQUIREMENTS.md`](UPSTREAM_REQUIREMENTS.md) with an executable reproducer under `tests/reproducers/`.
2. **Minimum Python & `abi3t`:** All code targets CPython `3.15+`. C shims must compile cleanly under `Py_TARGET_ABI3T=0x030F0000` (`_Py_OPAQUE_PYOBJECT`) with `-Wall -Wextra -Werror` and pass [`tests/abi/audit_binary.py`](tests/abi/audit_binary.py).
3. **No Fake Implementations or Global Locks:** Never fake native Nox execution, rely on private runtime struct layouts, or serialize independent calls behind a global mutex.
4. **Phase Gate Discipline:** Respect phase boundaries in [`TODO.md`](TODO.md). Features belonging to later phases stay gated behind explicit `NPY-E-*` diagnostics until exit criteria pass.
5. **Strict RTK Command Usage:** Follow [`RTK.md`](RTK.md) and verify local hooks with `python3 scripts/hooks/verify_rtk_enforcement.py`.

## Local Development & Testing

Run all checks locally before opening a PR:

```sh
python3.15 -m unittest discover -s tests/unit -v
python3.15 -m unittest discover -s tests/codegen -v
bash experiments/phase0_poc/build_and_test.sh
bash tests/reproducers/run_all.sh
python3 scripts/hooks/verify_rtk_enforcement.py
```

## Architecture Decisions & Evidence

- Significant architectural choices require an ADR under [`docs/adr/NNNN-title.md`](docs/adr/).
- Phase completions and releases require an executed evidence log under [`docs/evidence/`](docs/evidence/) following `AGENTS.md` §22.
