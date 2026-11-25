"""Unit tests for `noxpy.doctor`, `noxpy check`, and fail-closed `noxpy build`."""

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
        self.assertEqual(data["phase_gate"]["current_phase"], 0)
        self.assertFalse(data["phase_gate"]["phase1_ready"])

    def test_build_fails_closed_with_upstream_error(self) -> None:
        res = subprocess.run(
            [sys.executable, "-m", "noxpy", "build"],
            capture_output=True,
            text=True,
            env=self._env(),
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("NPY-E-UPSTREAM", res.stderr)

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
                        "compiler": {"name": "noxc", "version": "2.2.71", "commit": "abc"},
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
