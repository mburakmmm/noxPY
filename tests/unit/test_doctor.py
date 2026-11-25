"""Unit tests for `noxpy.doctor`, `noxpy check`, `noxpy new`, and fail-closed `build` when `noxc` missing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestDoctorCLI(unittest.TestCase):
    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "python")
        return env

    def test_doctor_json(self) -> None:
        res = subprocess.run(
            [sys.executable, "-m", "noxpy", "doctor", "--json"],
            capture_output=True,
            text=True,
            env=self._env(),
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertTrue(data["python"]["meets_315_floor"])
        self.assertTrue(data["python"]["abi3t_header_ready"])
        self.assertIn("phase0_exit_ready", data["phase_gate"])
        self.assertIn("phase1_ready", data["phase_gate"])
        self.assertEqual(data["phase_gate"]["current_phase"], 1 if data["phase_gate"]["phase1_ready"] else 0)

    def test_build_fails_closed_when_noxc_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            (td_p / "pyproject.toml").write_text(
                '[project]\nname="demo"\nversion="0.1.0a1"\nrequires-python=">=3.15"\n'
                '[tool.noxpy]\nmodule-name="demo"\nsource="src/lib.nox"\n',
                encoding="utf-8",
            )
            (td_p / "src").mkdir()
            (td_p / "src" / "lib.nox").write_text("@export\ndef add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")

            env = self._env()
            env["PATH"] = os.defpath
            env["HOME"] = str(td_p)
            env.pop("NOXC", None)
            res = subprocess.run(
                [sys.executable, "-m", "noxpy", "build", str(td_p)],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("NPY-E-UPSTREAM", res.stderr)

    def test_new_command_and_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "sample_mod"
            res = subprocess.run(
                [sys.executable, "-m", "noxpy", "new", "sample_mod", "--dir", str(proj), "--json"],
                capture_output=True,
                text=True,
                env=self._env(),
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue((proj / "pyproject.toml").is_file())
            self.assertTrue((proj / "src" / "lib.nox").is_file())
            self.assertTrue((proj / "tests" / "test_basic.py").is_file())

            res2 = subprocess.run(
                [sys.executable, "-m", "noxpy", "new", "sample_mod", "--dir", str(proj)],
                capture_output=True,
                text=True,
                env=self._env(),
            )
            self.assertNotEqual(res2.returncode, 0)
            self.assertIn("NPY-E-CONFIG", res2.stderr)

    def test_check_command_with_pyproject_and_ir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            (td_p / "pyproject.toml").write_text(
                '[project]\nname="demo"\nversion="0.1.0"\nrequires-python=">=3.15"\n'
                '[tool.noxpy]\nmodule-name="demo._native"\nsource="src/lib.nox"\n',
                encoding="utf-8",
            )
            (td_p / "ir.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "compiler": {"name": "noxc", "version": "2.2.81", "commit": "abc"},
                        "module": {"name": "demo._native", "source": "src/lib.nox"},
                        "exports": [],
                    }
                ),
                encoding="utf-8",
            )
            res = subprocess.run(
                [sys.executable, "-m", "noxpy", "check", str(td_p / "pyproject.toml"), "--ir", str(td_p / "ir.json"), "--json"],
                capture_output=True,
                text=True,
                env=self._env(),
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            out = json.loads(res.stdout)
            self.assertEqual(out["status"], "ok")
            self.assertEqual(out["module"], "demo._native")


if __name__ == "__main__":
    unittest.main()
