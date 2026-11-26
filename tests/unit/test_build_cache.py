"""Unit tests for deterministic build cache key completeness (`python/noxpy/build.py`)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.build import _collect_project_nox_sources, _compute_cache_key  # noqa: E402


class TestBuildCache(unittest.TestCase):
    def _base_key(self, proj: Path, *, cc_version: str = "clang 19.0", host_h: bytes = b"/*v2*/") -> str:
        entry = proj / "src" / "lib.nox"
        return _compute_cache_key(
            noxc_sha="noxc-sha",
            noxrt_sha="noxrt-sha",
            cc_version=cc_version,
            upstream_host_h_bytes=host_h,
            pyproject_bytes=b"[project]\nname='demo'\n",
            nox_sources=_collect_project_nox_sources(proj, entry),
            lock_digest="lock-digest",
            native_root=ROOT / "native",
            release=True,
            plat_tag="macosx_14_0_arm64",
        )

    def test_collect_and_invalidate_on_transitive_nox_cc_and_host_h(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "demo"
            (proj / "src" / "helpers").mkdir(parents=True)
            (proj / "src" / "lib.nox").write_text("from helpers.math import sq\n", encoding="utf-8")
            (proj / "src" / "helpers" / "math.nox").write_text("def sq(x: int) -> int:\n    return x * x\n", encoding="utf-8")
            (proj / ".noxpy-cache" / "old").mkdir(parents=True)
            (proj / ".noxpy-cache" / "old" / "ignored.nox").write_text("ignored", encoding="utf-8")
            (proj / ".venv").mkdir()
            (proj / ".venv" / "ignored.nox").write_text("ignored", encoding="utf-8")

            sources = _collect_project_nox_sources(proj, proj / "src" / "lib.nox")
            self.assertEqual([rel for rel, _ in sources], ["src/helpers/math.nox", "src/lib.nox"])

            k1 = self._base_key(proj)
            (proj / ".noxpy-cache" / "old" / "ignored.nox").write_text("changed-junk", encoding="utf-8")
            self.assertEqual(self._base_key(proj), k1)

            # Mutating a transitive imported .nox file must invalidate cache key
            (proj / "src" / "helpers" / "math.nox").write_text("def sq(x: int) -> int:\n    return x * x + 1\n", encoding="utf-8")
            k_transitive = self._base_key(proj)
            self.assertNotEqual(k_transitive, k1)

            # Changing C compiler version or upstream host header must also invalidate
            self.assertNotEqual(self._base_key(proj, cc_version="clang 20.0"), k_transitive)
            self.assertNotEqual(self._base_key(proj, host_h=b"/*v3*/"), k_transitive)


if __name__ == "__main__":
    unittest.main()
