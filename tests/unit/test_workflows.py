"""Policy tests for `.github/workflows/*.yml` (`AGENTS.md` §16, §19)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USES_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)")
SHA_PIN_RE = re.compile(r"^[^@]+@[0-9a-f]{40}$")


class TestWorkflowSecurityAndGates(unittest.TestCase):
    def _workflow_files(self) -> list[Path]:
        wf_dir = ROOT / ".github" / "workflows"
        files = sorted(wf_dir.glob("*.yml"))
        self.assertTrue(files, "Expected workflow files under .github/workflows/")
        return files

    def test_all_actions_pinned_by_commit_sha(self) -> None:
        for wf in self._workflow_files():
            text = wf.read_text(encoding="utf-8")
            uses_matches = []
            for lineno, line in enumerate(text.splitlines(), start=1):
                m = USES_RE.match(line)
                if m:
                    ref = m.group(1)
                    uses_matches.append(ref)
                    self.assertRegex(
                        ref,
                        SHA_PIN_RE,
                        f"{wf.name}:{lineno} must pin action by 40-hex commit SHA, got {ref!r}",
                    )
            self.assertTrue(uses_matches, f"{wf.name} should declare at least one `uses:` step")

    def test_top_level_least_privilege_permissions(self) -> None:
        for wf in self._workflow_files():
            text = wf.read_text(encoding="utf-8")
            self.assertRegex(
                text,
                re.compile(r"^permissions:\s*\n\s+contents:\s+read\b", re.MULTILINE),
                f"{wf.name} must restrict top-level permissions to `contents: read`",
            )

    def test_no_blanket_continue_on_error(self) -> None:
        for wf in self._workflow_files():
            text = wf.read_text(encoding="utf-8")
            self.assertNotIn(
                "continue-on-error: true",
                text,
                f"{wf.name} must not set `continue-on-error: true` (`AGENTS.md` §19)",
            )

    def test_release_workflow_never_runs_on_pr_and_defaults_dry_run(self) -> None:
        rel_txt = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertNotIn("pull_request", rel_txt)
        self.assertIn('tags:\n      - "v*"', rel_txt)
        self.assertIn("default: true", rel_txt)
        self.assertIn("pypi_allowed", rel_txt)


if __name__ == "__main__":
    unittest.main()
