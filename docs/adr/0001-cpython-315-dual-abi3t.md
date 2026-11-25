# ADR-0001: CPython 3.15+ Minimum Floor & Dual `abi3`/`abi3t` Extension ABI

- **Status:** Proposed (verified on CPython 3.15.2 regular GIL build; free-threaded `3.15t` runtime test pending local `python3.15t` availability)
- **Date:** 2026-11-25

## Context

NoxPy targets CPython 3.15+ (`AGENTS.md` §0, §5), requiring mandatory free-threaded support alongside ordinary CPython without shipping separate per-interpreter binaries where a single dual-compatible wheel suffices.

Inspection of the actual CPython 3.15.2 headers (`/opt/homebrew/opt/python@3.15/Frameworks/Python.framework/Versions/3.15/include/python3.15/`) confirmed:
- `pyabi.h`: Defining `Py_TARGET_ABI3T=0x030F0000` before `#include <Python.h>` automatically defines `_Py_OPAQUE_PYOBJECT` and sets `Py_LIMITED_API=0x030F0000`.
- `modsupport.h` & `slots.h` & `exports.h`: CPython 3.15 exports `PyModExport_<leaf>` via `PyMODEXPORT_FUNC` returning a `PySlot` array with `PyABIInfo_VAR(...)`, `PySlot_STATIC_DATA` (mandatory for `Py_mod_abi`, `Py_mod_name`, `Py_mod_doc`, `Py_mod_methods`), `PySlot_SIZE`, `PySlot_FUNC`, and `PySlot_DATA(Py_mod_gil, Py_MOD_GIL_NOT_USED)`.
- `importlib.machinery.EXTENSION_SUFFIXES` on CPython 3.15.2 macOS arm64 includes `.abi3t-darwin.so` and `.abi3t.so`.
- `longobject.h`: `PyLong_AsInt64` and `PyLong_FromInt64` are part of the 3.15 limited ABI, avoiding C `long` width ambiguities.

## Decision

1. Set `Requires-Python: >=3.15` across all NoxPy packages.
2. Compile the default native C shim (`native/include/noxpy_abi.h`, `native/cpython/noxpy_shim.c`) with `#define Py_TARGET_ABI3T 0x030F0000` and compile-time guards enforcing `PY_VERSION_HEX >= 0x030F0000` and `_Py_OPAQUE_PYOBJECT`.
3. Use `PyModExport_<leaf_module>` with per-module state (`Py_mod_state_size`, `Py_mod_exec`, `Py_mod_state_free`) and zero global mutable module/type state.
4. Declare `Py_mod_gil = Py_MOD_GIL_NOT_USED` only after auditing C symbols and verifying zero global invocation mutexes; declare `Py_mod_multiple_interpreters = Py_MOD_MULTIPLE_INTERPRETERS_NOT_SUPPORTED` until Phase 4 subinterpreter isolation is proven.
5. Enforce strict integer conversion (`PyBool_Check` rejected with `TypeError`, non-`int` rejected with `TypeError` without invoking `__index__`, `PyLong_AsInt64` overflow mapped to `OverflowError`).
6. Audit every produced `.abi3t.so` binary (`tests/abi/audit_binary.py`) so only `_PyModExport_<leaf>` is exported and zero `__Py*` private CPython imports or leaked `_nox_*` symbols appear.

## Consequences

- **Positive:** Single `.abi3t.so` binary targets both ordinary and free-threaded CPython 3.15+; strict opaque object discipline verified at compile time (`_Py_OPAQUE_PYOBJECT`) and link/audit time (`nm -u`).
- **Trade-offs / Risks:** Requires auditing `PySlot_STATIC_DATA` vs `PySlot_DATA` against real 3.15 headers (verified: `Py_mod_methods` failed at import with `SystemError` until switched to `PySlot_STATIC_DATA`). Dual-mode runtime verification remains incomplete until `python3.15t` is installed and tested unforced.

## Verification Evidence

- `native/cpython/noxpy_shim.c` compiled warning-clean (`-Wall -Wextra -Werror`) under `Py_TARGET_ABI3T=0x030F0000`.
- `_noxpy_poc.abi3t.so` and `_noxpy_poc_b.abi3t.so` passed `tests/abi/audit_binary.py` and `tests/integration/test_phase0_poc.py` on CPython 3.15.2 (see `docs/evidence/phase0-2026-11-25.md`).
