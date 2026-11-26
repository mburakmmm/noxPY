"""Deterministic `nox.lock` parser & validator matching upstream schema (`docs/tools/packages.md`)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .errors import DiagnosticLocation, NoxPyError

HEX_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


@dataclass(frozen=True)
class LockedPackage:
    alias: str
    repo: str
    ref: str
    resolved: str


def parse_nox_lock_json(data: object, *, lock_path: str = "nox.lock") -> tuple[LockedPackage, ...]:
    loc = DiagnosticLocation(file=lock_path)
    if not isinstance(data, dict):
        raise NoxPyError("NPY-E-CONFIG", "nox.lock root must be a JSON object", location=loc)

    packages = data.get("packages")
    if packages is None:
        return ()
    if not isinstance(packages, list):
        raise NoxPyError("NPY-E-CONFIG", "nox.lock 'packages' field must be an array", location=loc)

    seen_aliases: set[str] = set()
    parsed: list[LockedPackage] = []
    for idx, entry in enumerate(packages):
        if not isinstance(entry, dict):
            raise NoxPyError("NPY-E-CONFIG", f"nox.lock package #{idx} must be an object", location=loc)
        alias = entry.get("alias")
        repo = entry.get("repo")
        ref = entry.get("ref")
        resolved = entry.get("resolved")
        if not isinstance(alias, str) or not alias or alias == "nox":
            raise NoxPyError("NPY-E-CONFIG", f"Invalid nox.lock package alias: {alias!r}", location=loc)
        if alias in seen_aliases:
            raise NoxPyError("NPY-E-CONFIG", f"Duplicate nox.lock package alias: {alias!r}", location=loc)
        seen_aliases.add(alias)
        if not isinstance(repo, str) or not repo or not isinstance(ref, str) or not ref:
            raise NoxPyError("NPY-E-CONFIG", f"Empty repo/ref for nox.lock package {alias!r}", location=loc)
        if not isinstance(resolved, str) or not HEX_SHA_RE.match(resolved):
            raise NoxPyError(
                "NPY-E-CONFIG",
                f"nox.lock package {alias!r} resolved commit must be hex SHA, got {resolved!r}",
                location=loc,
            )
        parsed.append(LockedPackage(alias=alias, repo=repo, ref=ref, resolved=resolved.lower()))
    return tuple(sorted(parsed, key=lambda p: p.alias))


def compute_lock_digest(project_root: Path) -> str:
    """Validate `<project_root>/nox.lock` if present and return canonical SHA-256 hex (or `'none'`)."""
    lock_file = project_root / "nox.lock"
    if not lock_file.is_file():
        return "none"
    loc = DiagnosticLocation(file=str(lock_file))
    try:
        raw_text = lock_file.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as exc:
        raise NoxPyError("NPY-E-CONFIG", f"Invalid nox.lock JSON: {exc}", location=loc) from exc

    pkgs = parse_nox_lock_json(raw, lock_path=str(lock_file))
    canonical = json.dumps(
        [{"alias": p.alias, "repo": p.repo, "ref": p.ref, "resolved": p.resolved} for p in pkgs],
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
