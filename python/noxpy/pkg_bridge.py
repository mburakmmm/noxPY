"""Universal Nox Package Bindings bridge (`AGENTS.md` §3; `UPSTREAM_REQUIREMENTS.md` `UR-016`).

Scans an existing unmodified Nox package (`nox.json` + public `.nox` modules), classifies
functions/classes into exportable Host ABI v3 targets, generic specializations, or
unsupported domain signatures, and scaffolds thin wrapper extension projects that depend
on the unmodified upstream package via `nox.json` `requires` + `nox.lock`.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .build import IDENT_RE, normalize_dist_name
from .errors import DiagnosticLocation, NoxPyError

_UNSAFE_SUBINTERP_STDLIB = (
    "nox.http",
    "nox.tls",
    "nox.sqlite",
    "nox.postgres",
    "nox.terminal",
    "nox.signal",
)

_PRIMITIVE_TYPES = frozenset(
    {
        "int",
        "float",
        "bool",
        "str",
        "bytes",
        "None",
        "F64Buffer",
        "I64Buffer",
        "I32Buffer",
        "U8Buffer",
    }
)

_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*\])?\s*\((.*?)\)\s*(?:->\s*([^:]+?))?\s*:\s*(?:#.*)?$"
)
_CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*\])?(?:\(([^)]*)\))?\s*:")
_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_.]*)")
_ALL_RE = re.compile(r"__all__\s*(?::\s*[^=]+)?=\s*\[(.*?)\]", re.DOTALL)


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    depth = 0
    in_sq = False
    in_dq = False
    esc = False
    for ch in text:
        if esc:
            cur.append(ch)
            esc = False
            continue
        if ch == "\\" and (in_sq or in_dq):
            cur.append(ch)
            esc = True
            continue
        if ch == "'" and not in_dq:
            in_sq = not in_sq
            cur.append(ch)
        elif ch == '"' and not in_sq:
            in_dq = not in_dq
            cur.append(ch)
        elif not in_sq and not in_dq:
            if ch in "([{" :
                depth += 1
                cur.append(ch)
            elif ch in ")]}":
                depth = max(0, depth - 1)
                cur.append(ch)
            elif ch == sep and depth == 0:
                item = "".join(cur).strip()
                if item:
                    parts.append(item)
                cur = []
                continue
            else:
                cur.append(ch)
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def _split_top_level_union(t: str) -> list[str] | None:
    parts = _split_top_level(t, "|")
    if len(parts) <= 1:
        return None
    return parts


def _is_supported_abi_type(raw_type: str, *, for_return: bool = False) -> tuple[bool, str]:
    t = " ".join(raw_type.strip().split())
    if not t:
        return False, "missing type annotation"
    if t == "None":
        return (True, "") if for_return else (False, "None parameter without Optional union")
    if t in _PRIMITIVE_TYPES:
        return True, ""

    union_parts = _split_top_level_union(t)
    if union_parts is not None:
        if len(union_parts) != 2 or "None" not in union_parts:
            return False, f"unsupported union type {t!r}"
        inner = union_parts[0] if union_parts[1] == "None" else union_parts[1]
        ok, reason = _is_supported_abi_type(inner, for_return=False)
        return (ok, reason)

    if t.startswith("list[") and t.endswith("]"):
        return _is_supported_abi_type(t[5:-1], for_return=False)
    if t.startswith("dict[") and t.endswith("]"):
        kv = _split_top_level(t[5:-1], ",")
        if len(kv) != 2:
            return False, f"invalid dict type {t!r}"
        ok_k, rk = _is_supported_abi_type(kv[0], for_return=False)
        if not ok_k:
            return False, f"dict key type: {rk}"
        return _is_supported_abi_type(kv[1], for_return=False)
    if t.startswith("tuple[") and t.endswith("]"):
        elems = _split_top_level(t[6:-1], ",")
        if not elems:
            return False, f"empty tuple type {t!r}"
        for el in elems:
            ok_e, re_s = _is_supported_abi_type(el, for_return=False)
            if not ok_e:
                return False, f"tuple element type: {re_s}"
        return True, ""

    return False, f"custom/domain type {t!r} requires explicit @export class or specialization"


def _strip_default_literal(raw_default: str) -> str | None:
    d = raw_default.strip()
    if d in ("True", "False", "None"):
        return d
    if re.fullmatch(r"-?\d+", d):
        return d
    if re.fullmatch(r"-?\d+\.\d*", d):
        return d
    if (d.startswith('"') and d.endswith('"')) or (d.startswith("'") and d.endswith("'")):
        return d
    return None


_SKIP_SCAN_DIRS = frozenset(
    {
        ".git",
        ".noxpy-cache",
        "tests",
        "examples",
        "benchmarks",
        "docs",
        "scripts",
        "build",
        "dist",
        "project",
    }
)

_SUPPORTED_DUNDERS = frozenset(
    {"__repr__", "__str__", "__eq__", "__len__", "__iter__", "__next__", "__enter__", "__exit__"}
)


@dataclass(frozen=True)
class _ParsedParam:
    name: str
    type_name: str
    default_literal: str | None
    kind: str = "positional_or_keyword"


@dataclass(frozen=True)
class _ParsedFunc:
    module: str
    name: str
    params: tuple[_ParsedParam, ...]
    return_type: str
    is_async: bool
    is_generic: bool
    reason: str | None


@dataclass(frozen=True)
class _ParsedMethod:
    name: str
    method_kind: str  # constructor | instance | staticmethod | classmethod | property_get | dunder
    params: tuple[_ParsedParam, ...]
    return_type: str
    is_async: bool


@dataclass(frozen=True)
class _ParsedClass:
    module: str
    name: str
    is_generic: bool
    is_exception: bool
    constructor: _ParsedMethod | None
    methods: tuple[_ParsedMethod, ...]
    reason: str | None


def _discover_module_files(
    pkg_root: Path, entry_rel: str, only_modules: list[str] | None
) -> list[tuple[str, Path]]:
    discovered: dict[str, Path] = {}
    entry_norm = Path(entry_rel).as_posix()

    def _walk(base: Path) -> None:
        if not base.is_dir():
            return
        for p in sorted(base.rglob("*.nox")):
            rel_parts = p.relative_to(base).parts
            if any(part.startswith("_") or part in _SKIP_SCAN_DIRS for part in rel_parts):
                continue
            if p.name.endswith("_test.nox"):
                continue
            if p.relative_to(pkg_root).as_posix() == entry_norm and len(rel_parts) == 1:
                continue
            mod_dotted = ".".join(p.relative_to(base).with_suffix("").parts)
            discovered.setdefault(mod_dotted, p)

    _walk(pkg_root)
    src_dir = pkg_root / "src"
    if src_dir.is_dir():
        _walk(src_dir)

    if len(discovered) > 1:
        entry_stem = Path(entry_rel).stem
        if entry_stem in discovered and Path(entry_rel).name == entry_rel:
            discovered.pop(entry_stem, None)

    items = sorted(discovered.items(), key=lambda kv: kv[0])
    if only_modules is not None:
        wanted = {m.strip() for m in only_modules if m.strip()}
        items = [(m, p) for m, p in items if m in wanted]
    return items


def _parse_params_list(params_str: str, *, allow_self: bool = False) -> tuple[list[_ParsedParam], str | None]:
    if not params_str.strip():
        return [], None
    raw_items = _split_top_level(params_str, ",")
    has_slash = "/" in raw_items
    cur_kind = "positional_only" if has_slash else "positional_or_keyword"
    parsed: list[_ParsedParam] = []
    seen_self = False

    for p_item in raw_items:
        if p_item == "/":
            cur_kind = "positional_or_keyword"
            continue
        if p_item == "*":
            cur_kind = "keyword_only"
            continue
        if p_item.startswith("*"):
            return [], f"varargs/kwargs parameter {p_item!r} not supported"
        eq_parts = _split_top_level(p_item, "=")
        decl = eq_parts[0].strip()
        def_lit = _strip_default_literal(eq_parts[1]) if len(eq_parts) == 2 else None
        if len(eq_parts) > 2 or (len(eq_parts) == 2 and def_lit is None):
            return [], f"non-literal default on parameter {decl!r}"

        if allow_self and not seen_self and decl in ("self", "cls"):
            seen_self = True
            continue
        if ":" not in decl:
            return [], f"unannotated parameter {decl!r}"
        pname, ptype = decl.split(":", 1)
        pname = pname.strip()
        ptype = " ".join(ptype.strip().split())
        if allow_self and not seen_self and pname in ("self", "cls"):
            seen_self = True
            continue
        if not IDENT_RE.match(pname):
            return [], f"invalid parameter name {pname!r}"
        ok_t, r_t = _is_supported_abi_type(ptype, for_return=False)
        if not ok_t:
            return [], f"param '{pname}': {r_t}"
        parsed.append(_ParsedParam(name=pname, type_name=ptype, default_literal=def_lit, kind=cur_kind))
    return parsed, None


def _scan_module(mod_name: str, mod_path: Path) -> tuple[list[_ParsedFunc], list[str], list[str], list[_ParsedClass]]:
    text = mod_path.read_text(encoding="utf-8")

    all_match = _ALL_RE.search(text)
    explicit_all: set[str] | None = None
    if all_match:
        names = re.findall(r"""["']([A-Za-z_][A-Za-z0-9_]*)["']""", all_match.group(1))
        explicit_all = set(names)

    unsafe_imports: list[str] = []
    intra_imports: list[str] = []
    funcs: list[_ParsedFunc] = []
    classes: list[_ParsedClass] = []

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        imp_m = _IMPORT_RE.match(raw_line)
        if imp_m:
            mod_imp = imp_m.group(1)
            if mod_imp == "nox" or mod_imp.startswith("nox."):
                for unsafe in _UNSAFE_SUBINTERP_STDLIB:
                    if mod_imp == unsafe or mod_imp.startswith(unsafe + "."):
                        if mod_imp not in unsafe_imports:
                            unsafe_imports.append(mod_imp)
            else:
                if mod_imp not in intra_imports:
                    intra_imports.append(mod_imp)

        # Only top-level definitions (indent == 0)
        if raw_line and not raw_line[0].isspace():
            cls_m = _CLASS_RE.match(raw_line)
            if cls_m:
                cname = cls_m.group(1)
                is_gen_cls = bool(cls_m.group(2))
                bases_str = (cls_m.group(3) or "").strip()
                is_exc_cls = "Exception" in bases_str or "Error" in bases_str
                # Collect indented class body
                body_lines: list[str] = []
                j = i + 1
                while j < len(lines):
                    bl = lines[j]
                    if not bl.strip() or bl.lstrip().startswith("#") or bl[0].isspace():
                        body_lines.append(bl)
                        j += 1
                    else:
                        break
                i = j - 1

                if not cname.startswith("_") and (explicit_all is None or cname in explicit_all):
                    if is_gen_cls:
                        classes.append(
                            _ParsedClass(
                                module=mod_name,
                                name=f"{cname}{cls_m.group(2)}",
                                is_generic=True,
                                is_exception=is_exc_cls,
                                constructor=None,
                                methods=(),
                                reason="generic class requires specialization",
                            )
                        )
                    elif is_exc_cls:
                        classes.append(
                            _ParsedClass(
                                module=mod_name,
                                name=cname,
                                is_generic=False,
                                is_exception=True,
                                constructor=None,
                                methods=(),
                                reason="custom exception class requires UR-016.5 compiler support",
                            )
                        )
                    else:
                        ctor: _ParsedMethod | None = None
                        methods: list[_ParsedMethod] = []
                        cls_reason: str | None = None
                        pending_dec: str | None = None
                        k = 0
                        while k < len(body_lines):
                            bl_strip = body_lines[k].strip()
                            if bl_strip in ("@property", "@staticmethod", "@classmethod"):
                                pending_dec = bl_strip[1:]
                                k += 1
                                continue
                            if bl_strip.startswith("def ") or bl_strip.startswith("async def "):
                                msig = bl_strip
                                pbal = msig.count("(") - msig.count(")")
                                while pbal > 0 and k + 1 < len(body_lines):
                                    k += 1
                                    msig += " " + body_lines[k].strip()
                                    pbal = msig.count("(") - msig.count(")")
                                mm = _DEF_RE.match(msig)
                                if mm:
                                    mname = mm.group(1)
                                    m_is_async = msig.startswith("async ")
                                    m_is_gen = bool(mm.group(2))
                                    mparams_s = mm.group(3).strip()
                                    mret_s = (mm.group(4) or "None").strip()
                                    if mname == "__init__":
                                        mp, mr = _parse_params_list(mparams_s, allow_self=True)
                                        if mr is None and not m_is_gen and not m_is_async:
                                            ctor = _ParsedMethod("__init__", "constructor", tuple(mp), "None", False)
                                        else:
                                            cls_reason = mr or "unsupported __init__ signature"
                                    elif (not mname.startswith("_") or mname in _SUPPORTED_DUNDERS) and not m_is_gen:
                                        mkind = "instance"
                                        if pending_dec == "property":
                                            mkind = "property_get"
                                        elif pending_dec == "staticmethod":
                                            mkind = "staticmethod"
                                        elif pending_dec == "classmethod":
                                            mkind = "classmethod"
                                        elif mname in _SUPPORTED_DUNDERS:
                                            mkind = "dunder"
                                        mp, mr = _parse_params_list(
                                            mparams_s, allow_self=(mkind != "staticmethod")
                                        )
                                        ok_mr, r_mr = _is_supported_abi_type(mret_s, for_return=True)
                                        if mr is None and ok_mr and mkind != "classmethod":
                                            methods.append(
                                                _ParsedMethod(mname, mkind, tuple(mp), mret_s, m_is_async)
                                            )
                                        elif mname == "__init__":
                                            cls_reason = mr
                                pending_dec = None
                            elif bl_strip and not bl_strip.startswith("#"):
                                pending_dec = None
                            k += 1
                        if cls_reason is None and ctor is None and not methods:
                            cls_reason = "class has no exportable Host ABI constructor or methods"
                        classes.append(
                            _ParsedClass(
                                module=mod_name,
                                name=cname,
                                is_generic=False,
                                is_exception=False,
                                constructor=ctor,
                                methods=tuple(methods),
                                reason=cls_reason,
                            )
                        )
                i += 1
                continue

            stripped = raw_line.strip()
            if stripped.startswith("def ") or stripped.startswith("async def "):
                sig_buf = stripped
                # Handle multi-line def signatures cleanly
                paren_balance = sig_buf.count("(") - sig_buf.count(")")
                while paren_balance > 0 and i + 1 < len(lines):
                    i += 1
                    sig_buf += " " + lines[i].strip()
                    paren_balance = sig_buf.count("(") - sig_buf.count(")")
                m = _DEF_RE.match(sig_buf)
                if m:
                    fname = m.group(1)
                    if not fname.startswith("_") and (explicit_all is None or fname in explicit_all):
                        is_async = sig_buf.startswith("async ")
                        is_generic = bool(m.group(2))
                        params_str = m.group(3).strip()
                        ret_str = (m.group(4) or "None").strip()
                        parsed_params: list[_ParsedParam] = []
                        bad_reason: str | None = None
                        if is_generic:
                            bad_reason = "generic type parameters require explicit specialization"
                        elif params_str:
                            parsed_params, bad_reason = _parse_params_list(params_str, allow_self=False)

                        if bad_reason is None:
                            ok_r, r_r = _is_supported_abi_type(ret_str, for_return=True)
                            if not ok_r:
                                bad_reason = f"return: {r_r}"

                        funcs.append(
                            _ParsedFunc(
                                module=mod_name,
                                name=fname,
                                params=tuple(parsed_params),
                                return_type=ret_str,
                                is_async=is_async,
                                is_generic=is_generic,
                                reason=bad_reason,
                            )
                        )
        i += 1

    return funcs, unsafe_imports, intra_imports, classes


def _detect_git_remote(pkg_root: Path) -> str | None:
    if not (pkg_root / ".git").exists():
        return None
    res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=pkg_root, capture_output=True, text=True)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return None


def inspect_nox_package(pkg_dir: str | Path, *, modules: list[str] | None = None) -> dict[str, Any]:
    """Inspect an unmodified Nox package (`nox.json`) and emit a binding compatibility report."""
    root = Path(pkg_dir).resolve()
    manifest_p = root / "nox.json"
    if not manifest_p.is_file():
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"Expected nox.json in Nox package directory: {root}",
            location=DiagnosticLocation(file=str(manifest_p)),
        )
    try:
        manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NoxPyError("NPY-E-CONFIG", f"Invalid nox.json in {root}: {exc}") from exc

    pkg_name = str(manifest.get("name") or root.name)
    pkg_version = str(manifest.get("version") or "0.0.0")
    entry_rel = str(manifest.get("entry") or "main.nox")

    mod_files = _discover_module_files(root, entry_rel, modules)
    exportable: list[dict[str, Any]] = []
    exportable_classes: list[dict[str, Any]] = []
    needs_spec: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    unsafe_mods: dict[str, list[str]] = {}
    intra_mods: dict[str, list[str]] = {}
    scanned_names: list[str] = []

    for mod_name, mp in mod_files:
        scanned_names.append(mod_name)
        funcs, u_imps, i_imps, classes = _scan_module(mod_name, mp)
        if u_imps:
            unsafe_mods[mod_name] = u_imps
        if i_imps:
            intra_mods[mod_name] = i_imps
        for cls in classes:
            if cls.is_generic:
                needs_spec.append(
                    {
                        "module": cls.module,
                        "symbol": f"{cls.module}.{cls.name}",
                        "kind": "class",
                        "reason": cls.reason,
                    }
                )
            elif cls.reason is not None:
                unsupported.append(
                    {
                        "module": cls.module,
                        "symbol": f"{cls.module}.{cls.name}",
                        "kind": "class",
                        "reason": cls.reason,
                    }
                )
            else:
                exportable_classes.append(
                    {
                        "module": cls.module,
                        "name": cls.name,
                        "constructor": (
                            {
                                "params": [
                                    {
                                        "name": p.name,
                                        "type": p.type_name,
                                        "kind": p.kind,
                                        "default": p.default_literal,
                                    }
                                    for p in cls.constructor.params
                                ]
                            }
                            if cls.constructor
                            else None
                        ),
                        "methods": [
                            {
                                "name": m.name,
                                "method_kind": m.method_kind,
                                "is_async": m.is_async,
                                "params": [
                                    {
                                        "name": p.name,
                                        "type": p.type_name,
                                        "kind": p.kind,
                                        "default": p.default_literal,
                                    }
                                    for p in m.params
                                ],
                                "return_type": m.return_type,
                            }
                            for m in cls.methods
                        ],
                    }
                )
        for fn in funcs:
            entry_info = {
                "module": fn.module,
                "name": fn.name,
                "is_async": fn.is_async,
                "params": [
                    {"name": p.name, "type": p.type_name, "kind": p.kind, "default": p.default_literal}
                    for p in fn.params
                ],
                "return_type": fn.return_type,
            }
            if fn.reason is None:
                exportable.append(entry_info)
            elif fn.is_generic:
                entry_info["reason"] = fn.reason
                needs_spec.append(entry_info)
            else:
                entry_info["reason"] = fn.reason
                unsupported.append(entry_info)

    return {
        "package": pkg_name,
        "version": pkg_version,
        "package_root": str(root),
        "git_remote": _detect_git_remote(root),
        "git_rev": _resolve_git_ref(root, None),
        "modules_scanned": scanned_names,
        "subinterpreter_safe": len(unsafe_mods) == 0,
        "subinterpreter_warnings": unsafe_mods,
        "intra_package_imports": intra_mods,
        "summary": {
            "exportable_count": len(exportable),
            "exportable_class_count": len(exportable_classes),
            "needs_specialization_count": len(needs_spec),
            "unsupported_count": len(unsupported),
            "intra_package_import_modules": len(intra_mods),
        },
        "exportable": exportable,
        "exportable_classes": exportable_classes,
        "needs_specialization": needs_spec,
        "unsupported": unsupported,
    }


def _resolve_git_ref(pkg_root: Path, override_ref: str | None) -> str:
    if override_ref:
        return override_ref
    if (pkg_root / ".git").exists():
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=pkg_root, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    return "main"


def _emit_param_sig_and_call(params: list[dict[str, Any]]) -> tuple[str, str]:
    sig_parts: list[str] = []
    call_parts: list[str] = []
    for p in params:
        decl = f"{p['name']}: {p['type']}"
        if p["default"] is not None:
            decl += f" = {p['default']}"
        sig_parts.append(decl)
        call_parts.append(p["name"])
    return ", ".join(sig_parts), ", ".join(call_parts)


def generate_package_wrapper(
    pkg_dir: str | Path,
    out_dir: str | Path,
    *,
    module_name: str | None = None,
    modules: list[str] | None = None,
    select: list[str] | None = None,
    git_ref: str | None = None,
    repo_url: str | None = None,
    portable: bool = False,
    multi_module: bool = False,
    include_classes: bool = True,
    fetch: bool = False,
) -> dict[str, Any]:
    """Generate a thin NoxPy wrapper extension project referencing an unmodified Nox package via `nox.json`."""
    root = Path(pkg_dir).resolve()
    report = inspect_nox_package(root, modules=modules)
    pkg_name = str(report["package"])
    pkg_alias = re.sub(r"[^A-Za-z0-9_]", "_", pkg_name).lower()
    if not IDENT_RE.match(pkg_alias) or pkg_alias == "nox":
        pkg_alias = f"pkg_{pkg_alias}"

    base_mod_name = module_name or f"{pkg_alias}_py"
    if not IDENT_RE.match(base_mod_name):
        raise NoxPyError("NPY-E-CONFIG", f"Invalid generated Python module name: {base_mod_name!r}")

    selected_set = {s.strip() for s in select if s.strip()} if select else None
    chosen_fns: list[dict[str, Any]] = []
    seen_py_names: set[str] = set()
    for fn in report["exportable"]:
        fname = str(fn["name"])
        if selected_set is not None and fname not in selected_set and f"{fn['module']}.{fname}" not in selected_set:
            continue
        py_name = fname if fname not in seen_py_names else f"{fn['module'].replace('.', '_')}_{fname}"
        seen_py_names.add(py_name)
        chosen_fns.append({**fn, "py_name": py_name})

    chosen_classes: list[dict[str, Any]] = []
    if include_classes:
        for cls in report["exportable_classes"]:
            cname = str(cls["name"])
            if selected_set is not None and cname not in selected_set and f"{cls['module']}.{cname}" not in selected_set:
                continue
            py_name = cname if cname not in seen_py_names else f"{cls['module'].replace('.', '_')}_{cname}"
            seen_py_names.add(py_name)
            chosen_classes.append({**cls, "py_name": py_name})

    if not chosen_fns and not chosen_classes:
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"No exportable Host ABI functions or classes found in {pkg_name!r} matching filter",
            hint="Inspect supported APIs with `noxpy check --compatibility <pkg_dir>`.",
        )

    detected_remote = report.get("git_remote")
    if repo_url:
        dep_repo = repo_url
        dep_portable = True
    elif portable:
        if not detected_remote:
            raise NoxPyError(
                "NPY-E-CONFIG",
                f"--portable requested for {pkg_name!r}, but no git `origin` remote was found in {root}",
                hint="Pass --repo-url <git-url> explicitly or configure git remote origin.",
            )
        dep_repo = str(detected_remote)
        dep_portable = True
    else:
        dep_repo = str(root)
        dep_portable = False

    out_root = Path(out_dir).resolve()
    (out_root / "src").mkdir(parents=True, exist_ok=True)
    ref = _resolve_git_ref(root, git_ref)

    nox_mod_name = f"{base_mod_name}._native" if multi_module else base_mod_name
    leaf_name = "_native" if multi_module else base_mod_name

    nox_json = {
        "name": normalize_dist_name(f"{leaf_name}_bridge"),
        "version": str(report["version"]),
        "source": "nox",
        "entry": "src/lib.nox",
        "requires": [
            {
                "alias": pkg_alias,
                "repo": dep_repo,
                "ref": ref,
                "require_signed_commit": False,
            }
        ],
    }
    (out_root / "nox.json").write_text(json.dumps(nox_json, indent=2) + "\n", encoding="utf-8")

    dist_name = f"noxpy-{pkg_alias.replace('_', '-')}"
    py_ver = str(report["version"])
    toml_lines = [
        "[build-system]",
        'requires = ["noxpy>=0.1.0a1"]',
        'build-backend = "noxpy.build"',
        "",
        "[project]",
        f'name = "{dist_name}"',
        f'version = "{py_ver}"',
        f'description = "NoxPy native Python bindings for Nox package {pkg_name}"',
        'requires-python = ">=3.15"',
        "",
        "[tool.noxpy]",
        f'module-name = "{nox_mod_name}"',
        'source = "src/lib.nox"',
    ]
    if multi_module:
        toml_lines.append('python-source = "python"')
    toml_lines.extend(
        [
            'backend = "llvm"',
            'abi = "abi3t"',
            "free-threaded = true",
            "generate-stubs = true",
            'export = "public"',
            "",
        ]
    )
    (out_root / "pyproject.toml").write_text("\n".join(toml_lines), encoding="utf-8")

    needed_mods = sorted({str(x["module"]) for x in [*chosen_fns, *chosen_classes]})
    lib_lines = [
        f"# Auto-generated by `noxpy init --from-nox-package` targeting unmodified `{pkg_name}` ({ref}).",
        "",
    ]
    for m in needed_mods:
        lib_lines.append(f"import {pkg_alias}.{m}")
    lib_lines.append("")

    for cls in chosen_classes:
        inner_fq = f"{pkg_alias}.{cls['module']}.{cls['name']}"
        lib_lines.append('@export("instance_locked")')
        lib_lines.append(f"class {cls['py_name']}:")
        lib_lines.append(f"    _inner: {inner_fq}")
        lib_lines.append("")
        ctor = cls.get("constructor")
        if ctor:
            c_sig, c_call = _emit_param_sig_and_call(ctor["params"])
            c_sig_full = f"self, {c_sig}" if c_sig else "self"
            lib_lines.append(f"    def __init__({c_sig_full}) -> None:")
            lib_lines.append(f"        self._inner = {inner_fq}({c_call})")
            lib_lines.append("")
        for m in cls.get("methods", []):
            m_sig, m_call = _emit_param_sig_and_call(m["params"])
            mkind = m["method_kind"]
            ret_t = str(m["return_type"])
            async_kw = "async " if m["is_async"] else ""
            await_kw = "await " if m["is_async"] else ""
            if mkind == "property_get":
                lib_lines.append("    @property")
                lib_lines.append(f"    def {m['name']}(self) -> {ret_t}:")
                lib_lines.append(f"        return self._inner.{m['name']}")
                lib_lines.append("")
            elif mkind == "staticmethod":
                lib_lines.append("    @staticmethod")
                lib_lines.append(f"    {async_kw}def {m['name']}({m_sig}) -> {ret_t}:")
                tcall = f"{await_kw}{inner_fq}.{m['name']}({m_call})"
                lib_lines.append(f"        {tcall}" if ret_t == "None" else f"        return {tcall}")
                lib_lines.append("")
            else:
                m_sig_full = f"self, {m_sig}" if m_sig else "self"
                lib_lines.append(f"    {async_kw}def {m['name']}({m_sig_full}) -> {ret_t}:")
                tcall = f"{await_kw}self._inner.{m['name']}({m_call})"
                lib_lines.append(f"        {tcall}" if ret_t == "None" else f"        return {tcall}")
                lib_lines.append("")

    for fn in chosen_fns:
        sig_str, call_str = _emit_param_sig_and_call(fn["params"])
        ret_t = str(fn["return_type"])
        async_kw = "async " if fn["is_async"] else ""
        await_kw = "await " if fn["is_async"] else ""
        lib_lines.append("@export")
        lib_lines.append(f"{async_kw}def {fn['py_name']}({sig_str}) -> {ret_t}:")
        target_call = f"{await_kw}{pkg_alias}.{fn['module']}.{fn['name']}({call_str})"
        if ret_t == "None":
            lib_lines.append(f"    {target_call}")
        else:
            lib_lines.append(f"    return {target_call}")
        lib_lines.append("")

    (out_root / "src" / "lib.nox").write_text("\n".join(lib_lines), encoding="utf-8")

    generated_py_modules: list[str] = []
    if multi_module:
        py_pkg_dir = out_root / "python" / base_mod_name
        py_pkg_dir.mkdir(parents=True, exist_ok=True)
        by_mod: dict[str, list[tuple[str, str]]] = {}
        for fn in chosen_fns:
            by_mod.setdefault(str(fn["module"]), []).append((str(fn["name"]), str(fn["py_name"])))
        for cls in chosen_classes:
            by_mod.setdefault(str(cls["module"]), []).append((str(cls["name"]), str(cls["py_name"])))

        init_lines = ['"""Auto-generated multi-module NoxPy package bindings."""', "", "from . import _native", ""]
        top_children: set[str] = set()
        for mod_dotted, items in sorted(by_mod.items()):
            parts = mod_dotted.split(".")
            top_children.add(parts[0])
            sub_dir = py_pkg_dir
            for p in parts[:-1]:
                sub_dir = sub_dir / p
                sub_dir.mkdir(parents=True, exist_ok=True)
                sub_init = sub_dir / "__init__.py"
                if not sub_init.exists():
                    sub_init.write_text("", encoding="utf-8")
            rel_dots = "." * len(parts)
            mod_py = sub_dir / f"{parts[-1]}.py"
            m_lines = [f"from {rel_dots} import _native", ""]
            all_names: list[str] = []
            for orig_name, native_name in items:
                m_lines.append(f"{orig_name} = _native.{native_name}")
                all_names.append(orig_name)
            m_lines.append("")
            m_lines.append(f"__all__ = {all_names!r}")
            m_lines.append("")
            mod_py.write_text("\n".join(m_lines), encoding="utf-8")
            generated_py_modules.append(f"{base_mod_name}.{mod_dotted}")

        for child in sorted(top_children):
            init_lines.append(f"from . import {child}")
        init_lines.append("")
        init_lines.append(f"__all__ = {['_native', *sorted(top_children)]!r}")
        init_lines.append("")
        (py_pkg_dir / "__init__.py").write_text("\n".join(init_lines), encoding="utf-8")

    locked_commit: str | None = None
    if fetch:
        fres = subprocess.run(["noxc", "fetch"], cwd=out_root, capture_output=True, text=True)
        if fres.returncode != 0:
            raise NoxPyError(
                "NPY-E-UPSTREAM",
                f"`noxc fetch` failed in generated wrapper {out_root}:\n{fres.stderr.strip() or fres.stdout.strip()}",
            )
        lock_p = out_root / "nox.lock"
        if lock_p.is_file():
            lock_data = json.loads(lock_p.read_text(encoding="utf-8"))
            pkgs = lock_data.get("packages") or []
            if pkgs:
                locked_commit = str(pkgs[0].get("resolved") or "")

    return {
        "status": "ok",
        "source_package": pkg_name,
        "wrapper_dir": str(out_root),
        "module_name": nox_mod_name,
        "distribution_name": dist_name,
        "dependency_repo": dep_repo,
        "dependency_portable": dep_portable,
        "detected_remote": detected_remote,
        "multi_module": multi_module,
        "python_modules": generated_py_modules,
        "exported_functions": [fn["py_name"] for fn in chosen_fns],
        "exported_classes": [cls["py_name"] for cls in chosen_classes],
        "locked_commit": locked_commit,
    }
