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
        for bad_ty in ("set[int]", "CustomClass", "tuple__int_str", "list[int | None]", "dict[list[int], int]", "tuple[]"):
            bad = copy.deepcopy(SAMPLE_MANIFEST)
            bad["exports"][0]["return_type"] = bad_ty  # type: ignore[index]
            with self.assertRaises(NoxPyError) as ctx:
                parse_upstream_metadata_json(bad)
            self.assertEqual(ctx.exception.code, "NPY-E-TYPE")

    def test_accepts_phase2_containers_optionals_tuples_and_defaults(self) -> None:
        manifest = {
            "schema_version": 1,
            "compiler": {"name": "noxc", "version": "2.2.87", "commit": "22a4d80b"},
            "module": {"name": "pkg._p2", "source": "src/p2.nox"},
            "exports": [
                {
                    "kind": "function",
                    "id": "pkg::fn",
                    "name": "fn",
                    "c_symbol": "nox_host_export_pkg_fn",
                    "doc": "Phase 2 test.",
                    "thread_policy": "parallel",
                    "span": {"file": "src/p2.nox", "line": 1, "column": 1},
                    "params": [
                        {"name": "xs", "type": "list[tuple[int, str]]", "marshal": "value", "kind": "positional_or_keyword", "has_default": False, "default": None},
                        {"name": "opt", "type": "int | None", "marshal": "value", "kind": "positional_or_keyword", "has_default": True, "default": {"type": "None", "value": None}},
                        {"name": "scale", "type": "float", "marshal": "scalar", "kind": "keyword_only", "has_default": True, "default": {"type": "float", "value": 2.5}},
                        {"name": "tag", "type": "bytes", "marshal": "span", "kind": "keyword_only", "has_default": True, "default": {"type": "bytes", "hex": "00FF"}},
                    ],
                    "return_marshal": "value",
                    "return_type": "dict[str, list[str | None]]",
                }
            ],
        }
        ir = parse_upstream_metadata_json(manifest, expected_module_name="pkg._p2")
        fn = ir.functions[0]
        self.assertEqual(fn.return_marshal, "value")
        self.assertEqual(fn.params[0].marshal, "value")
        self.assertTrue(fn.params[1].has_default)
        self.assertEqual(fn.params[1].default_type, "None")
        self.assertEqual(fn.params[2].default_float, 2.5)
        self.assertEqual(fn.params[3].default_str, "00ff")

    def test_rejects_bad_defaults_and_positional_default_order(self) -> None:
        bad_missing_default = copy.deepcopy(SAMPLE_MANIFEST)
        bad_missing_default["exports"][0]["params"][0] = {"name": "x", "type": "float", "has_default": True, "default": None}  # type: ignore[index]
        with self.assertRaises(NoxPyError) as ctx1:
            parse_upstream_metadata_json(bad_missing_default)
        self.assertEqual(ctx1.exception.code, "NPY-E-TYPE")

        bad_type_default = copy.deepcopy(SAMPLE_MANIFEST)
        bad_type_default["exports"][0]["params"][0] = {"name": "x", "type": "float", "has_default": True, "default": {"type": "int", "value": 1}}  # type: ignore[index]
        with self.assertRaises(NoxPyError) as ctx2:
            parse_upstream_metadata_json(bad_type_default)
        self.assertEqual(ctx2.exception.code, "NPY-E-TYPE")

        bad_order = copy.deepcopy(SAMPLE_MANIFEST)
        bad_order["exports"][1]["params"] = [  # type: ignore[index]
            {"name": "a", "type": "int", "kind": "positional_or_keyword", "has_default": True, "default": {"type": "int", "value": 1}},
            {"name": "b", "type": "int", "kind": "positional_or_keyword", "has_default": False},
        ]
        with self.assertRaises(NoxPyError) as ctx3:
            parse_upstream_metadata_json(bad_order)
        self.assertEqual(ctx3.exception.code, "NPY-E-TYPE")

    def test_rejects_unsupported_thread_policy(self) -> None:
        bad = copy.deepcopy(SAMPLE_MANIFEST)
        bad["exports"][0]["thread_policy"] = "unsafe_global"  # type: ignore[index]
        with self.assertRaises(NoxPyError) as ctx:
            parse_upstream_metadata_json(bad)
        self.assertEqual(ctx.exception.code, "NPY-E-TYPE")

    def test_accepts_and_validates_phase3_classes_and_exceptions(self) -> None:
        manifest = {
            "schema_version": 1,
            "compiler": {"name": "noxc", "version": "2.2.90", "commit": "e297c4cf"},
            "module": {"name": "pkg._p3", "source": "src/p3.nox"},
            "exports": [
                {
                    "kind": "exception",
                    "id": "pkg::CounterError",
                    "name": "CounterError",
                    "base": "ValueError",
                    "doc": "Domain failure.",
                    "span": {"file": "src/p3.nox", "line": 1, "column": 1},
                },
                {
                    "kind": "class",
                    "id": "pkg::Counter",
                    "name": "Counter",
                    "doc": "Native counter.",
                    "thread_policy": "instance_locked",
                    "span": {"file": "src/p3.nox", "line": 5, "column": 1},
                    "constructor": {
                        "c_symbol": "nox_host_export_pkg_Counter___new__",
                        "params": [
                            {
                                "name": "start",
                                "type": "int",
                                "marshal": "scalar",
                                "has_default": True,
                                "default": {"type": "int", "value": 0},
                            }
                        ],
                        "doc": None,
                    },
                    "methods": [
                        {
                            "name": "value",
                            "method_kind": "property_get",
                            "has_self": True,
                            "c_symbol": "nox_host_export_pkg_Counter_get_value",
                            "thread_policy": "instance_locked",
                            "is_async": False,
                            "params": [],
                            "return_marshal": "scalar",
                            "return_type": "int",
                            "doc": None,
                            "span": {"file": "src/p3.nox", "line": 10, "column": 1},
                        },
                        {
                            "name": "value",
                            "method_kind": "property_set",
                            "has_self": True,
                            "c_symbol": "nox_host_export_pkg_Counter_set_value",
                            "thread_policy": "instance_locked",
                            "is_async": False,
                            "params": [{"name": "v", "type": "int", "marshal": "scalar", "has_default": False}],
                            "return_marshal": "none",
                            "return_type": "None",
                            "doc": None,
                            "span": {"file": "src/p3.nox", "line": 14, "column": 1},
                        },
                    ],
                },
            ],
        }
        ir = parse_upstream_metadata_json(manifest, expected_module_name="pkg._p3")
        self.assertEqual([e.name for e in ir.exceptions], ["CounterError"])
        self.assertEqual([c.name for c in ir.classes], ["Counter"])
        self.assertEqual([m.method_kind for m in ir.classes[0].methods], ["property_get", "property_set"])

        bad_setter = copy.deepcopy(manifest)
        bad_setter["exports"][1]["methods"] = [bad_setter["exports"][1]["methods"][1]]  # type: ignore[index]
        with self.assertRaises(NoxPyError) as ctx_s:
            parse_upstream_metadata_json(bad_setter)
        self.assertEqual(ctx_s.exception.code, "NPY-E-TYPE")

        bad_async = copy.deepcopy(manifest)
        bad_async["exports"][1]["methods"][0]["is_async"] = True  # type: ignore[index]
        with self.assertRaises(NoxPyError) as ctx_a:
            parse_upstream_metadata_json(bad_async)
        self.assertEqual(ctx_a.exception.code, "NPY-E-TYPE")


if __name__ == "__main__":
    unittest.main()
