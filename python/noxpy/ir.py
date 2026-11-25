"""Deterministic Binding Intermediate Representation (IR) (`AGENTS.md` §7, `UR-007`)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import DiagnosticLocation, NoxPyError

IR_SCHEMA_VERSION = 1

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
C_SYM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PHASE1_PARAM_TYPES = frozenset({"int", "float", "bool", "str", "bytes"})
PHASE1_RETURN_TYPES = frozenset({"int", "float", "bool", "str", "bytes", "None"})
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
    kind: str = "positional_or_keyword"
    has_default: bool = False


@dataclass(frozen=True)
class ExportedFunctionIR:
    export_id: str
    name: str
    c_symbol: str
    params: tuple[ParamIR, ...]
    return_type: str
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

        ret_ty = entry.get("return_type")
        if not isinstance(ret_ty, str) or ret_ty not in PHASE1_RETURN_TYPES:
            raise NoxPyError(
                "NPY-E-TYPE",
                f"Unsupported return type {ret_ty!r} on {name!r}",
                location=loc,
            )

        raw_params = entry.get("params")
        if not isinstance(raw_params, list):
            raise NoxPyError("NPY-E-CODEGEN", f"Export {name!r} missing 'params' list", location=loc)
        params: list[ParamIR] = []
        seen_pnames: set[str] = set()
        seen_kw_only = False
        for p in raw_params:
            if not isinstance(p, dict):
                raise NoxPyError("NPY-E-CODEGEN", f"Invalid param entry on {name!r}", location=loc)
            pname = _validate_ident(p.get("name"), "parameter", loc)
            if pname in seen_pnames:
                raise NoxPyError("NPY-E-TYPE", f"Duplicate parameter {pname!r} on {name!r}", location=loc)
            seen_pnames.add(pname)
            ptype = p.get("type")
            if not isinstance(ptype, str) or ptype not in PHASE1_PARAM_TYPES:
                raise NoxPyError(
                    "NPY-E-TYPE",
                    f"Unsupported parameter type {ptype!r} for {name}.{pname}",
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
            if has_default:
                raise NoxPyError(
                    "NPY-E-TYPE",
                    f"Default values are not yet supported in Phase 1 IR for {name}.{pname}",
                    location=loc,
                )
            params.append(ParamIR(name=pname, type_name=ptype, kind=pkind, has_default=has_default))

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
