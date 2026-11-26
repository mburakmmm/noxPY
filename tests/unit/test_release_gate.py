"""Unit tests for `scripts/release/check_release_gate.py` (`AGENTS.md` §19, §20)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "release"))

from check_release_gate import evaluate_release_gate  # noqa: E402


def _make_fake_repo(
    tmp: Path,
    *,
    version: str,
    changelog_version: str | None = None,
    with_evidence: bool = True,
    with_unchecked_phase0: bool = False,
) -> None:
    (tmp / "pyproject.toml").write_text(
        f'[project]\nname="noxpy"\nversion="{version}"\nrequires-python=">=3.15"\n',
        encoding="utf-8",
    )
    init_dir = tmp / "python" / "noxpy"
    init_dir.mkdir(parents=True, exist_ok=True)
    (init_dir / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")

    ch_ver = changelog_version if changelog_version is not None else version
    (tmp / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{ch_ver}]\n- Verified release notes.\n",
        encoding="utf-8",
    )
    if with_evidence:
        ev = tmp / "docs" / "evidence"
        ev.mkdir(parents=True, exist_ok=True)
        (ev / "phase.md").write_text("# Evidence\n", encoding="utf-8")

    gate_box = "- [ ]" if with_unchecked_phase0 else "- [x]"
    (tmp / "TODO.md").write_text(
        f"### 0.4 Mandatory Phase 0 Exit Gates\n{gate_box} UR-001 resolved.\n\n## Phase 1\n",
        encoding="utf-8",
    )


class TestReleaseGate(unittest.TestCase):
    def test_phase0_prerelease_allows_github_blocks_pypi(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_repo(root, version="0.0.1a0", with_unchecked_phase0=True)
            res = evaluate_release_gate(root, "v0.0.1a0")
            self.assertEqual(res["phase"], 0)
            self.assertTrue(res["prerelease"])
            self.assertFalse(res["pypi_allowed"])

    def test_phase1_blocked_when_phase0_gates_unchecked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_repo(root, version="0.1.0a1", with_unchecked_phase0=True)
            with self.assertRaises(ValueError) as ctx:
                evaluate_release_gate(root, "v0.1.0a1")
            self.assertIn("unchecked Phase 0 exit gates", str(ctx.exception))

    def test_phase1_allowed_when_phase0_gates_complete(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_repo(root, version="0.1.0a1", with_unchecked_phase0=False)
            res = evaluate_release_gate(root, "v0.1.0a1")
            self.assertEqual(res["phase"], 1)
            self.assertTrue(res["prerelease"])
            self.assertTrue(res["pypi_allowed"])

    def test_stable_1_0_release_gate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_repo(root, version="1.0.0", with_unchecked_phase0=False)
            res = evaluate_release_gate(root, "v1.0.0")
            self.assertEqual(res["phase"], 7)
            self.assertFalse(res["prerelease"])
            self.assertTrue(res["pypi_allowed"])

    def test_rejects_version_and_changelog_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_repo(root, version="0.1.0", changelog_version="0.0.9", with_unchecked_phase0=False)
            with self.assertRaises(ValueError):
                evaluate_release_gate(root, "v0.1.0")

    def test_rejects_missing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_repo(root, version="0.0.1a0", with_evidence=False, with_unchecked_phase0=True)
            with self.assertRaises(ValueError):
                evaluate_release_gate(root, "v0.0.1a0")

    def test_phase1_blocked_when_todo_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_repo(root, version="0.1.0a1", with_unchecked_phase0=False)
            (root / "TODO.md").unlink()
            with self.assertRaises(ValueError) as ctx:
                evaluate_release_gate(root, "v0.1.0a1")
            self.assertIn("missing required", str(ctx.exception))

    def test_phase1_blocked_when_phase0_section_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_repo(root, version="0.1.0a1", with_unchecked_phase0=False)
            (root / "TODO.md").write_text("# TODO\n\n## Phase 1\n- [x] Done\n", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                evaluate_release_gate(root, "v0.1.0a1")
            self.assertIn("Mandatory Phase 0 Exit Gates", str(ctx.exception))

    def test_phase1_blocked_when_phase0_section_has_no_checked_items(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_repo(root, version="0.1.0a1", with_unchecked_phase0=False)
            (root / "TODO.md").write_text(
                "### 0.4 Mandatory Phase 0 Exit Gates\nNothing completed yet.\n\n## Phase 1\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                evaluate_release_gate(root, "v0.1.0a1")
            self.assertIn("no completed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
