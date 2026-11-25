"""Unit tests for `noxpy.config` (`AGENTS.md` §5, §15.1)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.config import load_pyproject  # noqa: E402
from noxpy.errors import NoxPyError  # noqa: E402


def _write_toml(tmp: Path, body: str) -> Path:
    p = tmp / "pyproject.toml"
    p.write_text(body, encoding="utf-8")
    return p


class TestConfig(unittest.TestCase):
    def test_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_toml(
                Path(td),
                """
[project]
name = "fast-math"
version = "0.1.0"
requires-python = ">=3.15"

[tool.noxpy]
module-name = "fast_math._native"
source = "src/lib.nox"
backend = "llvm"
abi = "abi3t"
free-threaded = true
generate-stubs = true
""",
            )
            cfg = load_pyproject(p)
            self.assertEqual(cfg.distribution_name, "fast-math")
            self.assertEqual(cfg.module_name, "fast_math._native")
            self.assertEqual(cfg.package_parts, ("fast_math", "_native"))
            self.assertEqual(cfg.leaf_name, "_native")
            self.assertEqual(cfg.abi, "abi3t")
            self.assertTrue(cfg.free_threaded)

    def test_rejects_python_below_315(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_toml(
                Path(td),
                """
[project]
name = "bad"
version = "0.1.0"
requires-python = ">=3.14"

[tool.noxpy]
module-name = "bad"
source = "src/lib.nox"
""",
            )
            with self.assertRaises(NoxPyError) as ctx:
                load_pyproject(p)
            self.assertEqual(ctx.exception.code, "NPY-E-ABI")

    def test_rejects_qbe_backend(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_toml(
                Path(td),
                """
[project]
name = "bad"
version = "0.1.0"
requires-python = ">=3.15"

[tool.noxpy]
module-name = "bad"
source = "src/lib.nox"
backend = "qbe"
""",
            )
            with self.assertRaises(NoxPyError) as ctx:
                load_pyproject(p)
            self.assertEqual(ctx.exception.code, "NPY-E-CONFIG")

    def test_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_toml(
                Path(td),
                """
[project]
name = "bad"
version = "0.1.0"
requires-python = ">=3.15"

[tool.noxpy]
module-name = "bad"
source = "../outside.nox"
""",
            )
            with self.assertRaises(NoxPyError) as ctx:
                load_pyproject(p)
            self.assertEqual(ctx.exception.code, "NPY-E-CONFIG")

    def test_rejects_unknown_tool_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_toml(
                Path(td),
                """
[project]
name = "bad"
version = "0.1.0"
requires-python = ">=3.15"

[tool.noxpy]
module-name = "bad"
source = "src/lib.nox"
mystery = 123
""",
            )
            with self.assertRaises(NoxPyError) as ctx:
                load_pyproject(p)
            self.assertEqual(ctx.exception.code, "NPY-E-CONFIG")


if __name__ == "__main__":
    unittest.main()
