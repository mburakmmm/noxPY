#!/usr/bin/env python3
"""Audit a built `.abi3t.so` extension binary against NoxPy Section 5 & 6 ABI rules."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from noxpy.audit import ALLOWED_C_PY_IMPORTS, audit_extension_binary  # noqa: E402,F401
from noxpy.errors import NoxPyError  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: audit_binary.py <path/to/mod.abi3t.so> <expected_export_symbol>\n")
        return 2

    so_path = sys.argv[1]
    expected_export = sys.argv[2]

    try:
        res = audit_extension_binary(so_path, expected_export)
    except NoxPyError as exc:
        sys.stderr.write(f"FAIL: {exc} in {so_path}\n")
        return 1

    print(f"AUDIT PASS: {res.path}")
    print(f"  sha256:  {res.sha256}")
    print(f"  exports: {list(res.exports)}")
    print(f"  py_syms: {list(res.py_symbols)}")
    print(f"  deps:    {list(res.dependencies)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
