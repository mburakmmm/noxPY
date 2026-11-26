"""Deterministic Binding Intermediate Representation (IR) (`AGENTS.md` §7, `UR-007`)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .errors import DiagnosticLocation, NoxPyError

IR_SCHEMA_VERSION = 1
MAX_TYPE_NEST_DEPTH = 16

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
C_SYM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
HEX_RE = re.compile(r"^[0-9a-fA-F]*$")

PHASE1_PARAM_TYPES = frozenset({"int", "float", "bool", "str", "bytes"})
PHASE1_RETURN_TYPES = frozenset({"int", "float", "bool", "str", "bytes", "None"})
DICT_KEY_TYPES = frozenset({"int", "float", "bool", "str", "bytes"})
OPTIONAL_SCALAR_ONLY_CONTAINERS = frozenset({"int", "float", "bool"})
PARAM_KINDS = frozenset({"positional_only", "positional_or_keyword", "keyword_only"})
THREAD_POLICIES = frozenset({"parallel", "instance_locked", "thread_affine"})

RESERVED_EXPORT_NAMES = frozenset(
    {
        "__init__",
        "__new__",
        "__del__",
        "__dict__",
        "__name__",
        "__doc__",
        "__package__",
        "__loader__",
        "__spec__",
    }
)

C_KEYWORDS = frozenset(
    {
        "auto", "break", "case", "char", "const", "continue", "default", "do",
        "double", "else", "enum", "extern", "float", "for", "goto", "if",
        "inline", "int", "long", "register", "restrict", "return", "short",
        "signed", "sizeof", "static", "struct", "switch", "typedef", "union",
        "unsigned", "void", "volatile", "while", "_Atomic", "_Bool", "_Complex",
    }
)


@dataclass(frozen=True)
class SourceSpan:
    file: str
    line: int
    column: int = 1


@dataclass(frozen=True)
class ParamIR:
    name: str
    type_name: str
    marshal: str = "scalar"
    kind: str = "positional_or_keyword"
    has_default: bool = False
    default_type: str = ""
    default_int: int = 0
    default_float: float = 0.0
    default_bool: bool = False
    default_str: str = ""


@dataclass(frozen=True)
class ExportedFunctionIR:
    export_id: str
    name: str
    c_symbol: str
    params: tuple[ParamIR, ...]
    return_type: str
    return_marshal: str
    doc: str | None
    thread_policy: str
    span: SourceSpan


@dataclass(frozen=True)
class ModuleIR:
    schema_version: int
    compiler_name: str
    compiler_version: str
    compiler_commit: str
    module_name: str
    leaf_name: str
    source: str
    abi: str
    free_threaded: bool
    functions: tuple[ExportedFunctionIR, ...]


def _split_top_level_commas(inner: str, context: str, loc: DiagnosticLocation) -> list[str]:
    parts: list[str] = []
    depth = 0
    seg_start = 0
    for i, ch in enumerate(inner):
        if ch == "[":
            depth += 1
        elif ch == "]":
            if depth == 0:
                raise NoxPyError("NPY-E-TYPE", f"Unbalanced ']' in {context}", location=loc)
            depth -= 1
        elif ch == "," and depth == 0:
            item = inner[seg_start:i].strip(" \t")
            if not item:
                raise NoxPyError("NPY-E-TYPE", f"Empty type element in {context}", location=loc)
            parts.append(item)
            seg_start = i + 1
    if depth != 0:
        raise NoxPyError("NPY-E-TYPE", f"Unbalanced '[' in {context}", location=loc)
    last_item = inner[seg_start:].strip(" \t")
    if not last_item:
        raise NoxPyError("NPY-E-TYPE", f"Empty type element in {context}", location=loc)
    parts.append(last_item)
    return parts


def _classify_type_impl(
    raw_t: str,
    *,
    depth: int,
    allow_none: bool,
    in_container: bool,
    loc: DiagnosticLocation,
) -> str:
    if depth > MAX_TYPE_NEST_DEPTH:
        raise NoxPyError("NPY-E-TYPE", f"Type nesting depth exceeds limit (16): {raw_t!r}", location=loc)
    t = raw_t.strip(" \t")
    if not t:
        raise NoxPyError("NPY-E-TYPE", "Empty type name is not supported", location=loc)
    if t == "None":
        if not allow_none:
            raise NoxPyError("NPY-E-TYPE", "'None' is only supported as return type or 'T | None'", location=loc)
        return "none"

    bdepth = 0
    pipe_idx = -1
    for i, ch in enumerate(t):
        if ch == "[":
            bdepth += 1
        elif ch == "]":
            if bdepth == 0:
                raise NoxPyError("NPY-E-TYPE", f"Unbalanced ']' in type {t!r}", location=loc)
            bdepth -= 1
        elif ch == "|" and bdepth == 0:
            if pipe_idx >= 0:
                raise NoxPyError("NPY-E-TYPE", f"Unsupported multi-union type {t!r}", location=loc)
            pipe_idx = i
    if bdepth != 0:
        raise NoxPyError("NPY-E-TYPE", f"Unbalanced '[' in type {t!r}", location=loc)

    if pipe_idx >= 0:
        lhs = t[:pipe_idx].strip(" \t")
        rhs = t[pipe_idx + 1 :].strip(" \t")
        if rhs != "None" or not lhs or lhs == "None":
            raise NoxPyError("NPY-E-TYPE", f"Only 'T | None' optional unions are supported, got {t!r}", location=loc)
        if in_container and lhs in OPTIONAL_SCALAR_ONLY_CONTAINERS:
            raise NoxPyError(
                "NPY-E-TYPE",
                f"Optional scalar {t!r} inside containers is not supported by upstream Host ABI",
                location=loc,
            )
        _classify_type_impl(lhs, depth=depth + 1, allow_none=False, in_container=in_container, loc=loc)
        return "value"

    if t in ("int", "float", "bool"):
        return "scalar"
    if t in ("str", "bytes"):
        return "span"

    if t.startswith("list[") and t.endswith("]"):
        inner = t[5:-1].strip(" \t")
        if not inner:
            raise NoxPyError("NPY-E-TYPE", f"Empty list element type in {t!r}", location=loc)
        _classify_type_impl(inner, depth=depth + 1, allow_none=False, in_container=True, loc=loc)
        return "value"

    if t.startswith("dict[") and t.endswith("]"):
        inner = t[5:-1].strip(" \t")
        parts = _split_top_level_commas(inner, t, loc)
        if len(parts) != 2:
            raise NoxPyError("NPY-E-TYPE", f"dict must have exactly 2 type arguments: {t!r}", location=loc)
        if parts[0] not in DICT_KEY_TYPES:
            raise NoxPyError(
                "NPY-E-TYPE",
                f"dict key type {parts[0]!r} must be int, float, bool, str, or bytes in {t!r}",
                location=loc,
            )
        _classify_type_impl(parts[1], depth=depth + 1, allow_none=False, in_container=True, loc=loc)
        return "value"

    if t.startswith("tuple[") and t.endswith("]"):
        inner = t[6:-1].strip(" \t")
        if not inner:
            raise NoxPyError("NPY-E-TYPE", "Empty tuple[] is not supported", location=loc)
        parts = _split_top_level_commas(inner, t, loc)
        for ep in parts:
            if ep == "...":
                raise NoxPyError("NPY-E-TYPE", f"Variadic tuple[...] is not supported: {t!r}", location=loc)
            _classify_type_impl(ep, depth=depth + 1, allow_none=False, in_container=True, loc=loc)
        return "value"

    raise NoxPyError("NPY-E-TYPE", f"Unsupported type {t!r}", location=loc)


def marshal_for_type(t: str, *, is_return: bool = False) -> str:
    """Return Host ABI marshal kind ('scalar'|'span'|'value'|'none') or raise NPY-E-TYPE."""
    return _classify_type_impl(t, depth=0, allow_none=is_return, in_container=False, loc=DiagnosticLocation())


def is_supported_param_type(t: str) -> bool:
    try:
        marshal_for_type(t, is_return=False)
        return True
    except NoxPyError:
        return False


def is_supported_return_type(t: str) -> bool:
    try:
        marshal_for_type(t, is_return=True)
        return True
    except NoxPyError:
        return False


def _is_optional_default_compatible(ptype: str, def_ty: str) -> bool:
    if ptype == def_ty:
        return True
    if ptype.endswith(" | None"):
        if def_ty == "None":
            return True
        return ptype[:-7] == def_ty
    return False


def _validate_ident(name: object, what: str, loc: DiagnosticLocation) -> str:
    if not isinstance(name, str) or not IDENT_RE.match(name):
        raise NoxPyError("NPY-E-TYPE", f"Invalid {what} identifier: {name!r}", location=loc)
    if name in RESERVED_EXPORT_NAMES or name.startswith("PyModExport_") or name.startswith("PyInit_"):
        raise NoxPyError("NPY-E-TYPE", f"Reserved {what} name rejected: {name!r}", location=loc)
    if name in C_KEYWORDS:
        raise NoxPyError("NPY-E-TYPE", f"C reserved keyword cannot be exported as {what}: {name!r}", location=loc)
    return name


def parse_upstream_metadata_json(
    data: dict[str, object],
    *,
    expected_module_name: str | None = None,
    abi: str = "abi3t",
    free_threaded: bool = True,
) -> ModuleIR:
    """Validate and lower compiler-owned UR-007 JSON metadata into a deterministic ModuleIR."""
    if not isinstance(data, dict):
        raise NoxPyError("NPY-E-CODEGEN", "Metadata root must be a JSON object")

    schema_ver = data.get("schema_version")
    if schema_ver != IR_SCHEMA_VERSION:
        raise NoxPyError(
            "NPY-E-CODEGEN",
            f"Unsupported IR metadata schema_version: {schema_ver!r} (expected {IR_SCHEMA_VERSION})",
        )

    compiler = data.get("compiler")
    if not isinstance(compiler, dict):
        raise NoxPyError("NPY-E-CODEGEN", "Missing 'compiler' block in metadata")
    comp_name = str(compiler.get("name") or "")
    comp_ver = str(compiler.get("version") or "")
    comp_commit = str(compiler.get("commit") or "")
    if comp_name != "noxc" or not comp_ver:
        raise NoxPyError("NPY-E-CODEGEN", f"Invalid compiler identity in metadata: {compiler!r}")

    mod = data.get("module")
    if not isinstance(mod, dict):
        raise NoxPyError("NPY-E-CODEGEN", "Missing 'module' block in metadata")
    mod_name = str(mod.get("name") or "")
    mod_src = str(mod.get("source") or "")
    if not mod_name or any(not IDENT_RE.match(p) for p in mod_name.split(".")):
        raise NoxPyError("NPY-E-CONFIG", f"Invalid module name in metadata: {mod_name!r}")
    if expected_module_name and mod_name != expected_module_name:
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"Metadata module {mod_name!r} does not match pyproject module-name {expected_module_name!r}",
        )
    leaf_name = mod_name.split(".")[-1]

    raw_exports = data.get("exports")
    if not isinstance(raw_exports, list):
        raise NoxPyError("NPY-E-CODEGEN", "Missing 'exports' list in metadata")

    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    seen_syms: set[str] = set()
    functions: list[ExportedFunctionIR] = []

    for idx, entry in enumerate(raw_exports):
        if not isinstance(entry, dict):
            raise NoxPyError("NPY-E-CODEGEN", f"Export entry #{idx} must be an object")
        span_raw = entry.get("span")
        if not isinstance(span_raw, dict):
            raise NoxPyError("NPY-E-CODEGEN", f"Export entry #{idx} missing 'span'")
        span = SourceSpan(
            file=str(span_raw.get("file") or mod_src or "<unknown>"),
            line=int(span_raw.get("line") or 0),
            column=int(span_raw.get("column") or 1),
        )
        loc = DiagnosticLocation(file=span.file, line=span.line, column=span.column)

        kind = entry.get("kind")
        if kind != "function":
            raise NoxPyError(
                "NPY-E-TYPE",
                f"Unsupported export kind {kind!r} in Phase 1 IR (only 'function' supported)",
                location=loc,
            )

        name = _validate_ident(entry.get("name"), "function", loc)
        if name in seen_names:
            raise NoxPyError("NPY-E-TYPE", f"Duplicate exported function name: {name!r}", location=loc)
        seen_names.add(name)

        export_id = entry.get("id")
        if not isinstance(export_id, str) or not export_id:
            raise NoxPyError("NPY-E-CODEGEN", f"Export {name!r} missing unique stable 'id'", location=loc)
        if export_id in seen_ids:
            raise NoxPyError("NPY-E-CODEGEN", f"Duplicate export id: {export_id!r}", location=loc)
        seen_ids.add(export_id)

        c_symbol = entry.get("c_symbol")
        if not isinstance(c_symbol, str) or not C_SYM_RE.match(c_symbol):
            raise NoxPyError("NPY-E-CODEGEN", f"Invalid C export symbol for {name!r}: {c_symbol!r}", location=loc)
        if c_symbol in seen_syms:
            raise NoxPyError("NPY-E-CODEGEN", f"Duplicate C symbol: {c_symbol!r}", location=loc)
        seen_syms.add(c_symbol)

        thread_policy = entry.get("thread_policy", "parallel")
        if not isinstance(thread_policy, str) or thread_policy not in THREAD_POLICIES:
            raise NoxPyError(
                "NPY-E-TYPE",
                f"Unsupported thread_policy {thread_policy!r} on {name!r}",
                location=loc,
            )

        ret_ty_raw = entry.get("return_type")
        if not isinstance(ret_ty_raw, str):
            raise NoxPyError(
                "NPY-E-TYPE",
                f"Unsupported return type {ret_ty_raw!r} on {name!r}",
                location=loc,
            )
        ret_ty = ret_ty_raw.strip(" \t")
        ret_marshal = _classify_type_impl(ret_ty, depth=0, allow_none=True, in_container=False, loc=loc)

        rm_raw = entry.get("return_marshal")
        if rm_raw is not None:
            if not isinstance(rm_raw, str) or rm_raw != ret_marshal:
                raise NoxPyError(
                    "NPY-E-TYPE",
                    f"Return marshal mismatch for {name!r}: got {rm_raw!r}, expected {ret_marshal!r}",
                    location=loc,
                )

        raw_params = entry.get("params")
        if not isinstance(raw_params, list):
            raise NoxPyError("NPY-E-CODEGEN", f"Export {name!r} missing 'params' list", location=loc)
        params: list[ParamIR] = []
        seen_pnames: set[str] = set()
        seen_kw_only = False
        seen_pos_default = False
        for p in raw_params:
            if not isinstance(p, dict):
                raise NoxPyError("NPY-E-CODEGEN", f"Invalid param entry on {name!r}", location=loc)
            pname = _validate_ident(p.get("name"), "parameter", loc)
            if pname in seen_pnames:
                raise NoxPyError("NPY-E-TYPE", f"Duplicate parameter {pname!r} on {name!r}", location=loc)
            seen_pnames.add(pname)
            ptype_raw = p.get("type")
            if not isinstance(ptype_raw, str):
                raise NoxPyError(
                    "NPY-E-TYPE",
                    f"Unsupported parameter type {ptype_raw!r} for {name}.{pname}",
                    location=loc,
                )
            ptype = ptype_raw.strip(" \t")
            pmarshal = _classify_type_impl(ptype, depth=0, allow_none=False, in_container=False, loc=loc)
            pm_raw = p.get("marshal")
            if pm_raw is not None:
                if not isinstance(pm_raw, str) or pm_raw != pmarshal:
                    raise NoxPyError(
                        "NPY-E-TYPE",
                        f"Parameter marshal mismatch for {name}.{pname}: got {pm_raw!r}, expected {pmarshal!r}",
                        location=loc,
                    )

            pkind = p.get("kind", "positional_or_keyword")
            if not isinstance(pkind, str) or pkind not in PARAM_KINDS:
                raise NoxPyError(
                    "NPY-E-TYPE",
                    f"Unsupported parameter kind {pkind!r} for {name}.{pname}",
                    location=loc,
                )
            if pkind == "positional_only" and seen_kw_only:
                raise NoxPyError("NPY-E-TYPE", f"Invalid parameter order in {name!r}", location=loc)
            if pkind == "keyword_only":
                seen_kw_only = True

            has_default = bool(p.get("has_default", False))
            if pkind != "keyword_only":
                if has_default:
                    seen_pos_default = True
                elif seen_pos_default:
                    raise NoxPyError(
                        "NPY-E-TYPE",
                        f"Non-default parameter {pname!r} cannot follow parameter with default value in {name!r}",
                        location=loc,
                    )

            def_ty = ""
            def_int = 0
            def_float = 0.0
            def_bool = False
            def_str = ""
            if has_default:
                def_obj = p.get("default")
                if not isinstance(def_obj, dict):
                    raise NoxPyError(
                        "NPY-E-TYPE",
                        f"Missing or non-constant default value metadata for {name}.{pname}",
                        location=loc,
                    )
                raw_def_ty = def_obj.get("type")
                if not isinstance(raw_def_ty, str):
                    raise NoxPyError(
                        "NPY-E-TYPE",
                        f"Missing default value type for {name}.{pname}",
                        location=loc,
                    )
                def_ty = raw_def_ty
                if not _is_optional_default_compatible(ptype, def_ty):
                    raise NoxPyError(
                        "NPY-E-TYPE",
                        f"Default value type {def_ty!r} incompatible with parameter type {ptype!r} on {name}.{pname}",
                        location=loc,
                    )
                if def_ty == "None":
                    if "value" in def_obj and def_obj["value"] is not None:
                        raise NoxPyError("NPY-E-TYPE", f"None default value must be null on {name}.{pname}", location=loc)
                elif def_ty == "bool":
                    dv = def_obj.get("value")
                    if type(dv) is not bool:
                        raise NoxPyError("NPY-E-TYPE", f"bool default value must be JSON bool on {name}.{pname}", location=loc)
                    def_bool = dv
                elif def_ty == "int":
                    dv = def_obj.get("value")
                    if type(dv) is not int or dv < -(1 << 63) or dv > ((1 << 63) - 1):
                        raise NoxPyError("NPY-E-TYPE", f"int default out of int64 range on {name}.{pname}", location=loc)
                    def_int = dv
                elif def_ty == "float":
                    dv = def_obj.get("value")
                    if type(dv) not in (int, float):
                        raise NoxPyError("NPY-E-TYPE", f"float default value must be JSON number on {name}.{pname}", location=loc)
                    def_float = float(dv)
                    if not math.isfinite(def_float):
                        raise NoxPyError("NPY-E-TYPE", f"Non-finite float default not allowed on {name}.{pname}", location=loc)
                elif def_ty == "str":
                    dv = def_obj.get("value")
                    if not isinstance(dv, str):
                        raise NoxPyError("NPY-E-TYPE", f"str default value must be JSON string on {name}.{pname}", location=loc)
                    def_str = dv
                elif def_ty == "bytes":
                    hx = def_obj.get("hex")
                    if not isinstance(hx, str) or len(hx) % 2 != 0 or not HEX_RE.match(hx):
                        raise NoxPyError("NPY-E-TYPE", f"Invalid hex string for bytes default on {name}.{pname}", location=loc)
                    def_str = hx.lower()
                else:
                    raise NoxPyError("NPY-E-TYPE", f"Unsupported default value type {def_ty!r} on {name}.{pname}", location=loc)

            params.append(
                ParamIR(
                    name=pname,
                    type_name=ptype,
                    marshal=pmarshal,
                    kind=pkind,
                    has_default=has_default,
                    default_type=def_ty,
                    default_int=def_int,
                    default_float=def_float,
                    default_bool=def_bool,
                    default_str=def_str,
                )
            )

        doc = entry.get("doc")
        if doc is not None and not isinstance(doc, str):
            raise NoxPyError("NPY-E-CODEGEN", f"Docstring for {name!r} must be string or null", location=loc)

        functions.append(
            ExportedFunctionIR(
                export_id=export_id,
                name=name,
                c_symbol=c_symbol,
                params=tuple(params),
                return_type=ret_ty,
                return_marshal=ret_marshal,
                doc=doc,
                thread_policy=thread_policy,
                span=span,
            )
        )

    # Deterministic ordering (`AGENTS.md` §7)
    functions.sort(key=lambda fn: (fn.name, fn.export_id))
    return ModuleIR(
        schema_version=IR_SCHEMA_VERSION,
        compiler_name=comp_name,
        compiler_version=comp_ver,
        compiler_commit=comp_commit,
        module_name=mod_name,
        leaf_name=leaf_name,
        source=mod_src,
        abi=abi,
        free_threaded=free_threaded,
        functions=tuple(functions),
    )
