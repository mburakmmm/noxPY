#!/usr/bin/env python3
"""Verify NoxPy release gate invariants (`AGENTS.md` §19, §20)."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

PEP440_TAG_RE = re.compile(
    r"^v(?P<ver>(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?P<pre>(?:a|b|rc)\d+|\.dev\d+)?)$"
)
INIT_VER_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def extract_changelog_section(changelog_text: str, version: str) -> str | None:
    pattern = re.compile(
        rf"^##\s+\[{re.escape(version)}\][^\n]*\n(.*?)(?=^##\s+\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(changelog_text)
    if not m:
        return None
    body = m.group(1).strip()
    return body if body else None


def evaluate_release_gate(root: Path, tag: str) -> dict[str, object]:
    m = PEP440_TAG_RE.match(tag)
    if not m:
        raise ValueError(f"Tag {tag!r} does not match PEP 440 release format vX.Y.Z[aN|bN|rcN|.devN]")

    version = m.group("ver")
    major = int(m.group("major"))
    minor = int(m.group("minor"))
    is_prerelease = m.group("pre") is not None or (major == 0 and minor == 0)
    phase = 0 if (major == 0 and minor == 0) else (7 if major >= 1 else minor)
    pypi_allowed = (major, minor) >= (0, 1)

    pyproject_p = root / "pyproject.toml"
    if not pyproject_p.is_file():
        raise ValueError(f"Missing {pyproject_p}")
    py_data = tomllib.loads(pyproject_p.read_text(encoding="utf-8"))
    pkg_ver = (py_data.get("project") or {}).get("version")
    if pkg_ver != version:
        raise ValueError(f"Version mismatch: tag={version!r} != pyproject.toml [project].version={pkg_ver!r}")

    req_py = (py_data.get("project") or {}).get("requires-python")
    if not isinstance(req_py, str) or "3.15" not in req_py:
        raise ValueError(f"pyproject.toml requires-python must target >=3.15, got {req_py!r}")

    init_p = root / "python" / "noxpy" / "__init__.py"
    if not init_p.is_file():
        raise ValueError(f"Missing {init_p}")
    im = INIT_VER_RE.search(init_p.read_text(encoding="utf-8"))
    init_ver = im.group(1) if im else None
    if init_ver != version:
        raise ValueError(f"Version mismatch: tag={version!r} != __init__.py __version__={init_ver!r}")

    changelog_p = root / "CHANGELOG.md"
    if not changelog_p.is_file():
        raise ValueError(f"Missing {changelog_p}")
    notes = extract_changelog_section(changelog_p.read_text(encoding="utf-8"), version)
    if not notes:
        raise ValueError(f"CHANGELOG.md missing non-empty `## [{version}]` section")

    ev_dir = root / "docs" / "evidence"
    ev_files = sorted(p.name for p in ev_dir.glob("*.md")) if ev_dir.is_dir() else []
    if not ev_files:
        raise ValueError("docs/evidence/ must contain at least one verification evidence markdown file")

    if pypi_allowed:
        todo_p = root / "TODO.md"
        if todo_p.is_file():
            todo_txt = todo_p.read_text(encoding="utf-8")
            sec_m = re.search(
                r"^###\s+0\.4\s+Mandatory Phase 0 Exit Gates(.*?)(?=^##|\Z)",
                todo_txt,
                re.MULTILINE | re.DOTALL,
            )
            if sec_m and "- [ ]" in sec_m.group(1):
                raise ValueError(
                    f"Phase {phase} release ({tag}) blocked: TODO.md section 0.4 still has unchecked Phase 0 exit gates"
                )

    return {
        "tag": tag,
        "version": version,
        "phase": phase,
        "prerelease": is_prerelease,
        "pypi_allowed": pypi_allowed,
        "evidence_files": ev_files,
        "notes": notes,
    }


def build_artifact_manifest(artifacts_dir: Path, tag: str, version: str) -> dict[str, object]:
    entries = []
    for path in sorted(artifacts_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name in ("SHA256SUMS.txt", "release-manifest.json"):
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(
            {
                "file": path.relative_to(artifacts_dir).as_posix(),
                "size": path.stat().st_size,
                "sha256": digest,
            }
        )
    if not entries:
        raise ValueError(f"No release artifacts found under {artifacts_dir}")
    return {"tag": tag, "version": version, "artifacts": entries}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate NoxPy release gates.")
    parser.add_argument("--tag", required=True, help="Release tag, e.g. v0.0.1a0 or v0.1.0a1")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--extract-notes", help="Write extracted CHANGELOG section to file.")
    parser.add_argument("--artifacts-dir", help="Directory containing built wheels/sdists for manifest.")
    parser.add_argument("--write-manifest", help="Write JSON manifest (requires --artifacts-dir).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = evaluate_release_gate(Path(args.root), args.tag)
        if args.extract_notes:
            Path(args.extract_notes).write_text(str(result["notes"]) + "\n", encoding="utf-8")
        if args.write_manifest:
            if not args.artifacts_dir:
                raise ValueError("--write-manifest requires --artifacts-dir")
            manifest = build_artifact_manifest(Path(args.artifacts_dir), args.tag, str(result["version"]))
            Path(args.write_manifest).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except ValueError as exc:
        sys.stderr.write(f"RELEASE GATE FAILED: {exc}\n")
        return 1

    if args.json:
        out = {k: v for k, v in result.items() if k != "notes"}
        sys.stdout.write(json.dumps(out, indent=2) + "\n")
    else:
        print(
            f"RELEASE GATE PASS: tag={result['tag']} phase={result['phase']} "
            f"prerelease={result['prerelease']} pypi_allowed={result['pypi_allowed']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
