"""Unit tests for `noxpy.ir` (`AGENTS.md` §7, `UR-007`)."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.errors import NoxPyError  # noqa: E402
from noxpy.ir import parse_upstream_metadata_json  # noqa: E402

SAMPLE_MANIFEST = {
    "schema_version": 1,
    "compiler": {"name": "noxc", "version": "2.2.71", "commit": "abc123"},
    "module": {"name": "fastmath._native", "source": "src/lib.nox"},
    "exports": [
        {
            "kind": "function",
            "id": "fastmath::zeta",
            "name": "zeta",
            "c_symbol": "nox_host_export_fastmath_zeta",
            "doc": None,
            "thread_policy": "parallel",
            "span": {"file": "src/lib.nox", "line": 10, "column": 1},
            "params": [{"name": "x", "type": "float"}],
            "return_type": "float",
        },
        {
            "kind": "function",
            "id": "fastmath::add",
            "name": "add",
            "c_symbol": "nox_host_export_fastmath_add",
            "doc": "Add two ints.",
            "thread_policy": "parallel",
            "span": {"file": "src/lib.nox", "line": 4, "column": 1},
            "params": [
                {"name": "a", "type": "int", "kind": "positional_or_keyword", "has_default": False},
                {"name": "b", "type": "int", "kind": "positional_or_keyword", "has_default": False},
            ],
            "return_type": "int",
        },
    ],
}


class TestIR(unittest.TestCase):
    def test_deterministic_sorting_and_leaf(self) -> None:
        ir = parse_upstream_metadata_json(SAMPLE_MANIFEST, expected_module_name="fastmath._native")
        self.assertEqual(ir.leaf_name, "_native")
        self.assertEqual([f.name for f in ir.functions], ["add", "zeta"])

    def test_rejects_duplicate_function_name(self) -> None:
        bad = copy.deepcopy(SAMPLE_MANIFEST)
        bad["exports"][0]["name"] = "add"  # type: ignore[index]
        with self.assertRaises(NoxPyError) as ctx:
            parse_upstream_metadata_json(bad)
        self.assertEqual(ctx.exception.code, "NPY-E-TYPE")

    def test_rejects_reserved_c_keyword(self) -> None:
        bad = copy.deepcopy(SAMPLE_MANIFEST)
        bad["exports"][0]["name"] = "return"  # type: ignore[index]
        with self.assertRaises(NoxPyError) as ctx:
            parse_upstream_metadata_json(bad)
        self.assertEqual(ctx.exception.code, "NPY-E-TYPE")

    def test_rejects_unsupported_type(self) -> None:
        bad = copy.deepcopy(SAMPLE_MANIFEST)
        bad["exports"][0]["return_type"] = "list[int]"  # type: ignore[index]
        with self.assertRaises(NoxPyError) as ctx:
            parse_upstream_metadata_json(bad)
        self.assertEqual(ctx.exception.code, "NPY-E-TYPE")

    def test_rejects_unsupported_thread_policy(self) -> None:
        bad = copy.deepcopy(SAMPLE_MANIFEST)
        bad["exports"][0]["thread_policy"] = "unsafe_global"  # type: ignore[index]
        with self.assertRaises(NoxPyError) as ctx:
            parse_upstream_metadata_json(bad)
        self.assertEqual(ctx.exception.code, "NPY-E-TYPE")


if __name__ == "__main__":
    unittest.main()
