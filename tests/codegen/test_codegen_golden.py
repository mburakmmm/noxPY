"""Golden & clang syntax verification tests for `noxpy.codegen` (`AGENTS.md` §7, §17)."""

from __future__ import annotations

import subprocess
import sys
import sysconfig
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.codegen import generate_c_adapter, generate_pyi  # noqa: E402
from noxpy.ir import parse_upstream_metadata_json  # noqa: E402

MANIFEST = {
    "schema_version": 1,
    "compiler": {"name": "noxc", "version": "2.2.71", "commit": "abc123"},
    "module": {"name": "pkg._native", "source": "src/lib.nox"},
    "exports": [
        {
            "kind": "function",
            "id": "pkg::add",
            "name": "add",
            "c_symbol": "nox_host_export_pkg_add",
            "doc": 'Add "two" ints.\nLine 2.',
            "thread_policy": "parallel",
            "span": {"file": "src/lib.nox", "line": 3, "column": 1},
            "params": [
                {"name": "a", "type": "int", "kind": "positional_only", "has_default": False},
                {"name": "b", "type": "int", "kind": "positional_or_keyword", "has_default": False},
            ],
            "return_type": "int",
        },
        {
            "kind": "function",
            "id": "pkg::echo_bytes",
            "name": "echo_bytes",
            "c_symbol": "nox_host_export_pkg_echo_bytes",
            "doc": None,
            "thread_policy": "parallel",
            "span": {"file": "src/lib.nox", "line": 7, "column": 1},
            "params": [{"name": "payload", "type": "bytes", "kind": "keyword_only", "has_default": False}],
            "return_type": "bytes",
        },
        {
            "kind": "function",
            "id": "pkg::greet",
            "name": "greet",
            "c_symbol": "nox_host_export_pkg_greet",
            "doc": None,
            "thread_policy": "parallel",
            "span": {"file": "src/lib.nox", "line": 11, "column": 1},
            "params": [
                {"name": "name", "type": "str"},
                {"name": "flag", "type": "bool", "has_default": True, "default": {"type": "bool", "value": False}},
                {"name": "scale", "type": "float", "has_default": True, "default": {"type": "float", "value": 2.0}},
            ],
            "return_type": "None",
        },
        {
            "kind": "function",
            "id": "pkg::transform",
            "name": "transform",
            "c_symbol": "nox_host_export_pkg_transform",
            "doc": "Phase 2 containers and defaults.",
            "thread_policy": "parallel",
            "span": {"file": "src/lib.nox", "line": 16, "column": 1},
            "params": [
                {"name": "xs", "type": "list[tuple[int, str]]", "marshal": "value"},
                {"name": "opt", "type": "int | None", "marshal": "value", "has_default": True, "default": {"type": "None", "value": None}},
                {"name": "raw", "type": "bytes", "marshal": "span", "kind": "keyword_only", "has_default": True, "default": {"type": "bytes", "hex": "00ff"}},
            ],
            "return_marshal": "value",
            "return_type": "dict[str, list[str | None]]",
        },
    ],
}


class TestCodegenGolden(unittest.TestCase):
    def test_pyi_stub_deterministic_and_accurate(self) -> None:
        ir = parse_upstream_metadata_json(MANIFEST)
        pyi_1 = generate_pyi(ir)
        pyi_2 = generate_pyi(ir)
        self.assertEqual(pyi_1, pyi_2)
        self.assertIn("def add(a: int, /, b: int) -> int:", pyi_1)
        self.assertIn("def echo_bytes(*, payload: bytes) -> bytes: ...", pyi_1)
        self.assertIn("def greet(name: str, flag: bool = False, scale: float = 2.0) -> None: ...", pyi_1)
        self.assertIn(
            'def transform(xs: list[tuple[int, str]], opt: int | None = None, *, raw: bytes = b"\\x00\\xff") -> dict[str, list[str | None]]:',
            pyi_1,
        )

    def test_generated_c_compiles_warning_clean_against_315_abi3t(self) -> None:
        ir = parse_upstream_metadata_json(MANIFEST)
        c_src = generate_c_adapter(ir)
        self.assertIn("PyModExport__native(void)", c_src)
        self.assertIn("PySlot_STATIC_DATA(Py_mod_methods, noxpy_methods__native)", c_src)
        self.assertIn("noxpy_bytes_buffer_to_py(st->host_ops, &c_ret)", c_src)
        self.assertIn("nox_host_module_init__native", c_src)
        self.assertIn("METH_FASTCALL | METH_KEYWORDS", c_src)
        self.assertIn("noxpy_bind_fastcall_kwargs", c_src)
        self.assertIn("noxpy_py_to_host_value", c_src)
        self.assertIn("noxpy_host_value_to_py", c_src)
        self.assertIn('add($module, a, /, b)\\n--\\n\\nAdd \\"two\\" ints.\\nLine 2.', c_src)
        self.assertIn('echo_bytes($module, *, payload)\\n--\\n\\n"', c_src)
        self.assertIn('transform($module, xs, opt=None, *, raw=b\\"\\\\x00\\\\xff\\")\\n--\\n\\nPhase 2 containers and defaults.', c_src)

        py_inc = sysconfig.get_path("include")
        assert py_inc
        with tempfile.TemporaryDirectory() as td:
            c_path = Path(td) / "adapter.c"
            c_path.write_text(c_src, encoding="utf-8")
            res = subprocess.run(
                [
                    "clang",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fsyntax-only",
                    f"-I{ROOT / 'native' / 'include'}",
                    f"-I{py_inc}",
                    str(c_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, f"clang failed:\n{res.stderr}")


if __name__ == "__main__":
    unittest.main()
