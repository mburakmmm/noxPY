"""Canonical `pyproject.toml` parser and validator (`AGENTS.md` §5, §15.1)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import DiagnosticLocation, NoxPyError

DIST_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REQ_PY_CLAUSE_RE = re.compile(r"^\s*(>=|==|~=|>|<|<=|!=)\s*(\d+)\.(\d+)(?:\.(\d+))?(?:\.\*)?\s*$")

ALLOWED_TOOL_KEYS = frozenset(
    {
        "module-name",
        "source",
        "backend",
        "abi",
        "free-threaded",
        "generate-stubs",
        "python-source",
    }
)


@dataclass(frozen=True)
class NoxPyProjectConfig:
    project_root: Path
    distribution_name: str
    version: str
    requires_python: str
    module_name: str
    package_parts: tuple[str, ...]
    leaf_name: str
    source_path: Path
    source_rel: str
    backend: str
    abi: str
    free_threaded: bool
    generate_stubs: bool
    python_source_dir: Path | None = None


def _parse_requires_python_floor(spec: str, loc: DiagnosticLocation) -> tuple[int, int]:
    """Validate `requires-python` enforces CPython >= 3.15 (`AGENTS.md` §5)."""
    clauses = [c for c in spec.split(",") if c.strip()]
    if not clauses:
        raise NoxPyError("NPY-E-ABI", "project.requires-python must not be empty", location=loc)

    floor: tuple[int, int] | None = None
    for clause in clauses:
        m = REQ_PY_CLAUSE_RE.match(clause)
        if not m:
            raise NoxPyError(
                "NPY-E-CONFIG",
                f"Unsupported requires-python specifier clause: {clause!r}",
                location=loc,
            )
        op, maj_s, min_s, _ = m.groups()
        ver = (int(maj_s), int(min_s))
        if op in (">=", "==", "~="):
            if floor is None or ver > floor:
                floor = ver
        elif op == ">":
            bumped = (ver[0], ver[1] + 1)
            if floor is None or bumped > floor:
                floor = bumped

    if floor is None or floor < (3, 15):
        raise NoxPyError(
            "NPY-E-ABI",
            f"NoxPy requires requires-python >= 3.15; got {spec!r}",
            location=loc,
            hint="Set `requires-python = \">=3.15\"` in [project].",
        )
    return floor


def _resolve_safe_relpath(project_root: Path, rel: str, field: str, loc: DiagnosticLocation) -> Path:
    if not isinstance(rel, str) or not rel.strip():
        raise NoxPyError("NPY-E-CONFIG", f"[tool.noxpy].{field} must be a non-empty relative path", location=loc)
    candidate = Path(rel)
    if candidate.is_absolute():
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"[tool.noxpy].{field} must be relative to project root, got {rel!r}",
            location=loc,
        )
    if ".." in candidate.parts:
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"[tool.noxpy].{field} must not traverse parent directories ('..'): {rel!r}",
            location=loc,
        )
    root_resolved = project_root.resolve()
    resolved = (root_resolved / candidate).resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"[tool.noxpy].{field} escapes project root: {rel!r}",
            location=loc,
        ) from exc
    return resolved


def load_pyproject(pyproject_path: str | Path) -> NoxPyProjectConfig:
    path = Path(pyproject_path)
    loc = DiagnosticLocation(file=str(path))
    if not path.is_file():
        raise NoxPyError("NPY-E-CONFIG", f"pyproject.toml not found: {path}", location=loc)

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise NoxPyError("NPY-E-CONFIG", f"Failed to parse pyproject.toml: {exc}", location=loc) from exc

    project = raw.get("project")
    if not isinstance(project, dict):
        raise NoxPyError("NPY-E-CONFIG", "Missing [project] table in pyproject.toml", location=loc)

    dist_name = project.get("name")
    if not isinstance(dist_name, str) or not DIST_NAME_RE.match(dist_name):
        raise NoxPyError("NPY-E-CONFIG", f"Invalid [project].name: {dist_name!r}", location=loc)

    version = project.get("version")
    if not isinstance(version, str) or not version.strip():
        raise NoxPyError("NPY-E-CONFIG", "Missing or invalid [project].version", location=loc)

    req_py = project.get("requires-python")
    if not isinstance(req_py, str):
        raise NoxPyError("NPY-E-ABI", "Missing [project].requires-python (must be >=3.15)", location=loc)
    _parse_requires_python_floor(req_py, loc)

    tool = raw.get("tool")
    noxpy_tbl = tool.get("noxpy") if isinstance(tool, dict) else None
    if not isinstance(noxpy_tbl, dict):
        raise NoxPyError("NPY-E-CONFIG", "Missing [tool.noxpy] table in pyproject.toml", location=loc)

    unknown = sorted(set(noxpy_tbl.keys()) - ALLOWED_TOOL_KEYS)
    if unknown:
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"Unknown key(s) in [tool.noxpy]: {', '.join(unknown)}",
            location=loc,
        )

    module_name = noxpy_tbl.get("module-name")
    if not isinstance(module_name, str) or not module_name:
        raise NoxPyError("NPY-E-CONFIG", "Missing [tool.noxpy].module-name", location=loc)
    parts = tuple(module_name.split("."))
    if any(not IDENT_RE.match(p) for p in parts):
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"Invalid Python module-name {module_name!r}; every dotted component must be an identifier",
            location=loc,
        )
    leaf_name = parts[-1]

    source_rel = noxpy_tbl.get("source")
    if not isinstance(source_rel, str):
        raise NoxPyError("NPY-E-CONFIG", "Missing [tool.noxpy].source", location=loc)
    project_root = path.parent.resolve()
    source_path = _resolve_safe_relpath(project_root, source_rel, "source", loc)

    backend = noxpy_tbl.get("backend", "llvm")
    if backend != "llvm":
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"Unsupported backend {backend!r}; NoxPy requires 'llvm' (`AGENTS.md` §0)",
            location=loc,
        )

    abi = noxpy_tbl.get("abi", "abi3t")
    if abi != "abi3t":
        raise NoxPyError(
            "NPY-E-ABI",
            f"Unsupported abi {abi!r}; default stable target must be 'abi3t'",
            location=loc,
        )

    free_threaded = noxpy_tbl.get("free-threaded", True)
    if not isinstance(free_threaded, bool) or not free_threaded:
        raise NoxPyError(
            "NPY-E-ABI",
            "[tool.noxpy].free-threaded must be true (`AGENTS.md` §0)",
            location=loc,
        )

    generate_stubs = noxpy_tbl.get("generate-stubs", True)
    if not isinstance(generate_stubs, bool):
        raise NoxPyError("NPY-E-CONFIG", "[tool.noxpy].generate-stubs must be a boolean", location=loc)

    py_src_raw = noxpy_tbl.get("python-source")
    py_src_dir = (
        _resolve_safe_relpath(project_root, py_src_raw, "python-source", loc)
        if py_src_raw is not None
        else None
    )

    return NoxPyProjectConfig(
        project_root=project_root,
        distribution_name=dist_name,
        version=version.strip(),
        requires_python=req_py.strip(),
        module_name=module_name,
        package_parts=parts,
        leaf_name=leaf_name,
        source_path=source_path,
        source_rel=source_rel,
        backend=backend,
        abi=abi,
        free_threaded=free_threaded,
        generate_stubs=generate_stubs,
        python_source_dir=py_src_dir,
    )
