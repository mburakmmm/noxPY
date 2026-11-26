"""Deterministic `.pyi` type stub and `abi3t` C adapter emitters from ModuleIR (`AGENTS.md` §7)."""

from __future__ import annotations

import re

from .ir import (
    ExportedClassIR,
    ExportedExceptionIR,
    ExportedFunctionIR,
    MethodIR,
    ModuleIR,
    ParamIR,
)


def _c_escape(text: str) -> str:
    """Escape arbitrary UTF-8 text into a safe C string literal."""
    out: list[str] = ['"']
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\0":
            out.append("\\000")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _py_escape_str(text: str) -> str:
    out: list[str] = ['"']
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\0":
            out.append("\\x00")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _py_escape_bytes_hex(hex_str: str) -> str:
    out = ['b"']
    for i in range(0, len(hex_str) - 1, 2):
        out.append("\\x" + hex_str[i : i + 2])
    out.append('"')
    return "".join(out)


def _format_float_lit(val: float) -> str:
    s = str(val)
    if "." not in s and "e" not in s and "E" not in s:
        return s + ".0"
    return s


def _format_default_py(p: ParamIR) -> str:
    if p.default_type == "None":
        return "None"
    if p.default_type == "bool":
        return "True" if p.default_bool else "False"
    if p.default_type == "int":
        return str(p.default_int)
    if p.default_type == "float":
        return _format_float_lit(p.default_float)
    if p.default_type == "str":
        return _py_escape_str(p.default_str)
    if p.default_type == "bytes":
        return _py_escape_bytes_hex(p.default_str)
    return "None"


_BUILTIN_BUFFER_MAP = {
    "nox_host_F64Buffer": ("F64Buffer", "NOXPY_BUF_F64", "float64", 8),
    "nox_host_I64Buffer": ("I64Buffer", "NOXPY_BUF_I64", "int64", 8),
    "nox_host_I32Buffer": ("I32Buffer", "NOXPY_BUF_I32", "int32", 4),
    "nox_host_U8Buffer": ("U8Buffer", "NOXPY_BUF_U8", "uint8", 1),
}


def _ir_used_buffer_types(ir: ModuleIR) -> list[str]:
    found: set[str] = set()
    for fn in ir.functions:
        if fn.return_type in _BUILTIN_BUFFER_MAP:
            found.add(fn.return_type)
        for p in fn.params:
            if p.type_name in _BUILTIN_BUFFER_MAP:
                found.add(p.type_name)
    for cls in ir.classes:
        for p in cls.constructor.params:
            if p.type_name in _BUILTIN_BUFFER_MAP:
                found.add(p.type_name)
        for m in cls.methods:
            if m.return_type in _BUILTIN_BUFFER_MAP:
                found.add(m.return_type)
            for p in m.params:
                if p.type_name in _BUILTIN_BUFFER_MAP:
                    found.add(p.type_name)
    return sorted(found)


def _pyi_type(t: str) -> str:
    res = re.sub(r"\bHostObject\b", "object", t)
    for raw_t, (py_t, _, _, _) in _BUILTIN_BUFFER_MAP.items():
        res = re.sub(rf"\b{re.escape(raw_t)}\b", py_t, res)
    return res


def _format_pyi_params_list(
    params: tuple[ParamIR, ...],
    *,
    first_arg: str | None = None,
) -> str:
    pos_only = [p for p in params if p.kind == "positional_only"]
    pos_or_kw = [p for p in params if p.kind == "positional_or_keyword"]
    kw_only = [p for p in params if p.kind == "keyword_only"]

    parts: list[str] = []
    if first_arg is not None:
        parts.append(first_arg)
    for p in pos_only:
        s = f"{p.name}: {_pyi_type(p.type_name)}"
        if p.has_default:
            s += f" = {_format_default_py(p)}"
        parts.append(s)
    if pos_only:
        parts.append("/")
    for p in pos_or_kw:
        s = f"{p.name}: {_pyi_type(p.type_name)}"
        if p.has_default:
            s += f" = {_format_default_py(p)}"
        parts.append(s)
    if kw_only:
        parts.append("*")
        for p in kw_only:
            s = f"{p.name}: {_pyi_type(p.type_name)}"
            if p.has_default:
                s += f" = {_format_default_py(p)}"
            parts.append(s)
    return ", ".join(parts)


def _format_pyi_params(fn: ExportedFunctionIR) -> str:
    return _format_pyi_params_list(fn.params)


def _append_pyi_doc(lines: list[str], indent: str, sig: str, doc: str | None) -> None:
    if doc:
        escaped_doc = doc.replace('"""', '\\"\\"\\"')
        lines.append(f"{indent}{sig}")
        lines.append(f'{indent}    """{escaped_doc}"""')
        lines.append(f"{indent}    ...")
    else:
        lines.append(f"{indent}{sig} ...")


def generate_pyi(ir: ModuleIR) -> str:
    """Emit deterministic PEP 561 `.pyi` stub from validated ModuleIR."""
    lines = [
        f"# Generated by noxpy from {ir.source} (noxc {ir.compiler_version}). Do not hand-edit.",
        "",
    ]
    for raw_buf in _ir_used_buffer_types(ir):
        py_buf, _, dt_str, isz = _BUILTIN_BUFFER_MAP[raw_buf]
        lines.extend(
            [
                f"class {py_buf}:",
                f'    """Zero-copy PEP 3118 pinned native {dt_str} buffer ({isz}-byte items)."""',
                f"    @property",
                f"    def dtype(self) -> str: ...",
                f"    @property",
                f"    def itemsize(self) -> int: ...",
                f"    @property",
                f"    def nbytes(self) -> int: ...",
                f"    @property",
                f"    def is_pinned(self) -> bool: ...",
                f"    def __len__(self) -> int: ...",
                f"    def writable(self) -> object: ...",
                f"    def close(self) -> None: ...",
                f"    def __enter__(self) -> {py_buf}: ...",
                f"    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool: ...",
                f"    def __buffer__(self, flags: int) -> memoryview: ...",
            ]
        )
    for exc in ir.exceptions:
        sig = f"class {exc.name}({exc.base}):"
        if exc.doc:
            escaped_doc = exc.doc.replace('"""', '\\"\\"\\"')
            lines.append(sig)
            lines.append(f'    """{escaped_doc}"""')
            lines.append("    ...")
        else:
            lines.append(f"{sig} ...")
    for cls in ir.classes:
        lines.append(f"class {cls.name}:")
        if cls.doc:
            escaped_doc = cls.doc.replace('"""', '\\"\\"\\"')
            lines.append(f'    """{escaped_doc}"""')
        ctor_sig = f"def __init__({_format_pyi_params_list(cls.constructor.params, first_arg='self')}) -> None:"
        _append_pyi_doc(lines, "    ", ctor_sig, cls.constructor.doc)

        prop_gets = {m.name: m for m in cls.methods if m.method_kind == "property_get"}
        prop_sets = {m.name: m for m in cls.methods if m.method_kind == "property_set"}
        for pname in sorted(prop_gets):
            pg = prop_gets[pname]
            lines.append("    @property")
            _append_pyi_doc(lines, "    ", f"def {pname}(self) -> {_pyi_type(pg.return_type)}:", pg.doc)
            if pname in prop_sets:
                ps = prop_sets[pname]
                val_p = ps.params[0]
                lines.append(f"    @{pname}.setter")
                _append_pyi_doc(
                    lines, "    ", f"def {pname}(self, {val_p.name}: {_pyi_type(val_p.type_name)}) -> None:", ps.doc
                )

        defined_names = {m.name for m in cls.methods}
        for m in cls.methods:
            if m.method_kind in ("property_get", "property_set"):
                continue
            def_kw = "async def" if m.is_async else "def"
            mret = _pyi_type(m.return_type)
            if m.method_kind == "staticmethod":
                lines.append("    @staticmethod")
                msig = f"{def_kw} {m.name}({_format_pyi_params_list(m.params)}) -> {mret}:"
            elif m.method_kind == "classmethod":
                lines.append("    @classmethod")
                msig = f"{def_kw} {m.name}({_format_pyi_params_list(m.params, first_arg='cls')}) -> {mret}:"
            else:
                msig = f"{def_kw} {m.name}({_format_pyi_params_list(m.params, first_arg='self')}) -> {mret}:"
            _append_pyi_doc(lines, "    ", msig, m.doc)

        if "close" not in defined_names:
            lines.append("    def close(self) -> None: ...")
        if "__enter__" not in defined_names:
            lines.append(f"    def __enter__(self) -> {cls.name}: ...")
        if "__exit__" not in defined_names:
            lines.append("    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool: ...")

    for fn in ir.functions:
        def_kw = "async def" if fn.is_async else "def"
        sig = f"{def_kw} {fn.name}({_format_pyi_params(fn)}) -> {_pyi_type(fn.return_type)}:"
        _append_pyi_doc(lines, "", sig, fn.doc)
    lines.append("")
    return "\n".join(lines)


_C_IN_VAR_MAP = {
    "int": "int64_t",
    "float": "double",
    "bool": "uint8_t",
    "str": "NoxHostSpan",
    "bytes": "NoxHostSpan",
}

_C_RET_VAR_MAP = {
    "int": "int64_t",
    "float": "double",
    "bool": "uint8_t",
    "str": "NoxHostBuffer",
    "bytes": "NoxHostBuffer",
    "None": "void",
}


_PARAM_KIND_MACRO = {
    "positional_only": "NOXPY_PARAM_POS_ONLY",
    "positional_or_keyword": "NOXPY_PARAM_POS_OR_KW",
    "keyword_only": "NOXPY_PARAM_KW_ONLY",
}

_IN_CONV_MAP = {
    "int": "noxpy_py_to_i64_strict",
    "float": "noxpy_py_to_f64_strict",
    "bool": "noxpy_py_to_bool_strict",
    "str": "noxpy_py_to_utf8_span_strict",
    "bytes": "noxpy_py_to_bytes_span_strict",
}


_POLICY_MACRO = {
    "parallel": "NOXPY_POLICY_PARALLEL",
    "instance_locked": "NOXPY_POLICY_INSTANCE_LOCKED",
    "thread_affine": "NOXPY_POLICY_THREAD_AFFINE",
}

_STD_PYEXC_MAP = {
    "Exception": "PyExc_Exception",
    "ValueError": "PyExc_ValueError",
    "TypeError": "PyExc_TypeError",
    "RuntimeError": "PyExc_RuntimeError",
    "KeyError": "PyExc_KeyError",
    "IndexError": "PyExc_IndexError",
    "OverflowError": "PyExc_OverflowError",
    "MemoryError": "PyExc_MemoryError",
    "ZeroDivisionError": "PyExc_ZeroDivisionError",
    "NotImplementedError": "PyExc_NotImplementedError",
    "AssertionError": "PyExc_AssertionError",
}


def _fnv1a_u64(text: str) -> str:
    h = 0xCBF29CE484222325
    for b in text.encode("utf-8"):
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    if h == 0:
        h = 1
    return f"0x{h:016X}ULL"


def _topo_sort_exceptions(exceptions: tuple[ExportedExceptionIR, ...]) -> list[ExportedExceptionIR]:
    by_name = {e.name: e for e in exceptions}
    ordered: list[ExportedExceptionIR] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(exc: ExportedExceptionIR) -> None:
        if exc.name in visited:
            return
        if exc.name in visiting:
            raise ValueError(f"Cyclic exception hierarchy involving {exc.name!r}")
        visiting.add(exc.name)
        if exc.base in by_name:
            visit(by_name[exc.base])
        visiting.remove(exc.name)
        visited.add(exc.name)
        ordered.append(exc)

    for exc in exceptions:
        visit(exc)
    return ordered


def _c_in_var_type(p: ParamIR) -> str:
    if p.marshal == "value":
        return "NoxHostValue"
    if p.marshal == "span":
        return "NoxHostSpan"
    if p.marshal == "handle":
        return "NoxHostHandle"
    if p.marshal == "external_ref":
        return "NoxHostExternalRef"
    return {"int": "int64_t", "float": "double", "bool": "uint8_t"}[p.type_name]


def _c_ret_var_type(return_type: str, return_marshal: str) -> str:
    if return_marshal == "value":
        return "NoxHostValue"
    if return_marshal == "span":
        return "NoxHostBuffer"
    if return_marshal == "handle":
        return "NoxHostHandle"
    if return_marshal == "external_ref":
        return "NoxHostExternalRef"
    return {"int": "int64_t", "float": "double", "bool": "uint8_t"}.get(return_type, "void")


def _c_async_start_symbol(c_symbol: str) -> str:
    if c_symbol.startswith("nox_host_export_"):
        return "nox_host_async_start_" + c_symbol[len("nox_host_export_") :]
    return c_symbol


def _type_uses_host_object(t: str) -> bool:
    return bool(re.search(r"\bHostObject\b", t))


def _ir_uses_host_object(ir: ModuleIR) -> bool:
    for fn in ir.functions:
        if _type_uses_host_object(fn.return_type) or any(_type_uses_host_object(p.type_name) for p in fn.params):
            return True
    for cls in ir.classes:
        if any(_type_uses_host_object(p.type_name) for p in cls.constructor.params):
            return True
        for m in cls.methods:
            if _type_uses_host_object(m.return_type) or any(_type_uses_host_object(p.type_name) for p in m.params):
                return True
    return False


def _c_int64_literal(v: int) -> str:
    if v < -9223372036854775807:
        return "(-9223372036854775807LL - 1LL)"
    return f"{v}LL"


def _c_hex_bytes_literal(hex_str: str) -> str:
    out = ['"']
    for i in range(0, len(hex_str) - 1, 2):
        out.append("\\x" + hex_str[i : i + 2])
    out.append('"')
    return "".join(out)


def _c_scalar_or_span_default_init(p: ParamIR) -> str:
    if not p.has_default:
        return "{0}" if p.marshal == "span" else "0"
    if p.default_type == "int":
        return _c_int64_literal(p.default_int)
    if p.default_type == "float":
        return _format_float_lit(p.default_float)
    if p.default_type == "bool":
        return "1u" if p.default_bool else "0u"
    if p.default_type == "str":
        blen = len(p.default_str.encode("utf-8"))
        return f"{{ (const uint8_t *){_c_escape(p.default_str)}, (size_t){blen}u }}"
    if p.default_type == "bytes":
        blen = len(p.default_str) // 2
        return f"{{ (const uint8_t *){_c_hex_bytes_literal(p.default_str)}, (size_t){blen}u }}"
    return "0"


def _c_default_host_value_call(p: ParamIR, target_var: str) -> str:
    if p.default_type == "None":
        return f"st->host_ops->value_none(ctx, &{target_var})"
    if p.default_type == "int":
        return f"st->host_ops->value_int(ctx, {_c_int64_literal(p.default_int)}, &{target_var})"
    if p.default_type == "float":
        return f"st->host_ops->value_float(ctx, {_format_float_lit(p.default_float)}, &{target_var})"
    if p.default_type == "bool":
        bval = "1u" if p.default_bool else "0u"
        return f"st->host_ops->value_bool(ctx, {bval}, &{target_var})"
    if p.default_type == "str":
        blen = len(p.default_str.encode("utf-8"))
        return f"st->host_ops->value_str(ctx, (const uint8_t *){_c_escape(p.default_str)}, (size_t){blen}u, 0, &{target_var})"
    blen = len(p.default_str) // 2
    return f"st->host_ops->value_str(ctx, (const uint8_t *){_c_hex_bytes_literal(p.default_str)}, (size_t){blen}u, 1, &{target_var})"


def _emit_ml_doc_parts(name: str, params: tuple[ParamIR, ...], doc: str | None, *, first_target: str | None) -> str:
    pos_only = [p for p in params if p.kind == "positional_only"]
    pos_or_kw = [p for p in params if p.kind == "positional_or_keyword"]
    kw_only = [p for p in params if p.kind == "keyword_only"]

    sig_parts: list[str] = [f"{p.name}={_format_default_py(p)}" if p.has_default else p.name for p in pos_only]
    if pos_only:
        sig_parts.append("/")
    sig_parts.extend(f"{p.name}={_format_default_py(p)}" if p.has_default else p.name for p in pos_or_kw)
    if kw_only:
        sig_parts.append("*")
        sig_parts.extend(f"{p.name}={_format_default_py(p)}" if p.has_default else p.name for p in kw_only)

    prefix = f"{name}({first_target}" if first_target else f"{name}("
    if sig_parts:
        sig_header = prefix + (", " if first_target else "") + ", ".join(sig_parts) + ")"
    else:
        sig_header = prefix + ")"
    body = doc if doc else ""
    return f"{sig_header}\n--\n\n{body}"


def _emit_extref_arg_setup(
    lines: list[str],
    params: tuple[ParamIR, ...],
    extref_indices: list[int],
    *,
    indent: str,
    mod_expr: str,
    release_ctx_line: str | None,
    release_lease_line: str | None,
    err_return: str,
) -> None:
    for epos, ei in enumerate(extref_indices):
        pe = params[ei]
        if pe.has_default:
            lines.append(f"{indent}if (bound[{ei}] != NULL) {{")
            lines.append(f"{indent}    c_arg_{ei} = noxpy_extref_intern({mod_expr}, bound[{ei}]);")
            lines.append(f"{indent}    if (c_arg_{ei} == 0) {{")
            for k in range(epos):
                lines.append(
                    f"{indent}        if (c_arg_{extref_indices[k]}) {{ noxpy_extref_release_token({mod_expr}, c_arg_{extref_indices[k]}); }}"
                )
            if release_ctx_line:
                lines.append(f"{indent}        {release_ctx_line}")
            if release_lease_line:
                lines.append(f"{indent}        {release_lease_line}")
            lines.append(f"{indent}        return {err_return};")
            lines.append(f"{indent}    }}")
            lines.append(f"{indent}}}")
        else:
            lines.append(f"{indent}c_arg_{ei} = noxpy_extref_intern({mod_expr}, bound[{ei}]);")
            lines.append(f"{indent}if (c_arg_{ei} == 0) {{")
            for k in range(epos):
                lines.append(
                    f"{indent}    if (c_arg_{extref_indices[k]}) {{ noxpy_extref_release_token({mod_expr}, c_arg_{extref_indices[k]}); }}"
                )
            if release_ctx_line:
                lines.append(f"{indent}    {release_ctx_line}")
            if release_lease_line:
                lines.append(f"{indent}    {release_lease_line}")
            lines.append(f"{indent}    return {err_return};")
            lines.append(f"{indent}}}")


def _emit_value_arg_setup(
    lines: list[str],
    params: tuple[ParamIR, ...],
    value_indices: list[int],
    *,
    indent: str,
    release_ctx_line: str | None,
    release_lease_line: str | None,
    err_return: str,
    use_ex: bool = False,
    mod_expr: str = "NULL",
    extref_indices: list[int] | tuple[int, ...] = (),
) -> None:
    def _emit_err_cleanup(pfx: str, vpos: int) -> None:
        for k in range(vpos):
            lines.append(
                f"{pfx}if (c_arg_{value_indices[k]}) {{ (void)st->host_ops->value_release(ctx, c_arg_{value_indices[k]}); }}"
            )
        for ei in extref_indices:
            lines.append(f"{pfx}if (c_arg_{ei}) {{ noxpy_extref_release_token({mod_expr}, c_arg_{ei}); }}")
        if release_ctx_line:
            lines.append(f"{pfx}{release_ctx_line}")
        if release_lease_line:
            lines.append(f"{pfx}{release_lease_line}")
        lines.append(f"{pfx}return {err_return};")

    for vpos, vi in enumerate(value_indices):
        pv = params[vi]
        if use_ex:
            conv_call = f"noxpy_py_to_host_value_ex({mod_expr}, st->host_ops, ctx, bound[{vi}], {_c_escape(pv.type_name)}, &c_arg_{vi}, {_c_escape(pv.name)})"
        else:
            conv_call = f"noxpy_py_to_host_value(st->host_ops, ctx, bound[{vi}], {_c_escape(pv.type_name)}, &c_arg_{vi}, {_c_escape(pv.name)})"
        if pv.has_default:
            lines.append(f"{indent}if (bound[{vi}] != NULL) {{")
            lines.append(f"{indent}    if ({conv_call} < 0) {{")
            _emit_err_cleanup(indent + "        ", vpos)
            lines.append(f"{indent}    }}")
            lines.append(f"{indent}}} else {{")
            lines.append(f"{indent}    NoxHostStatus def_rc_{vi} = {_c_default_host_value_call(pv, f'c_arg_{vi}')};")
            lines.append(f"{indent}    if (def_rc_{vi} != NOX_HOST_OK) {{")
            lines.append(f"{indent}        noxpy_set_exception_from_host(st->host_ops, def_rc_{vi}, NULL);")
            _emit_err_cleanup(indent + "        ", vpos)
            lines.append(f"{indent}    }}")
            lines.append(f"{indent}}}")
        else:
            lines.append(f"{indent}if ({conv_call} < 0) {{")
            _emit_err_cleanup(indent + "    ", vpos)
            lines.append(f"{indent}}}")


def _emit_module_or_class_callable(
    *,
    c_func_name: str,
    py_name: str,
    c_symbol: str,
    params: tuple[ParamIR, ...],
    return_type: str,
    return_marshal: str,
    leaf: str,
    first_arg_is_cls: bool,
    has_phase3_state: bool,
    class_policy_map: dict[str, str],
    use_ex: bool = False,
) -> list[str]:
    lines: list[str] = []
    c_params = ["NoxHostContext *ctx"]
    for i, p in enumerate(params):
        if p.marshal == "span":
            c_params.append(f"const uint8_t *arg_{i}_data")
            c_params.append(f"size_t arg_{i}_len")
        else:
            c_params.append(f"{_c_in_var_type(p)} arg_{i}")
    if return_type != "None":
        c_params.append(f"{_c_ret_var_type(return_type, return_marshal)} *out_val")
    c_params.append("NoxHostError **out_err")

    lines.append(f"extern NoxHostStatus {c_symbol}({', '.join(c_params)});")
    lines.append("")
    first_c_param = "PyObject *cls" if first_arg_is_cls else "PyObject *module"
    lines.append(
        f"static PyObject *{c_func_name}({first_c_param}, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames) {{"
    )
    argc = len(params)
    if argc == 0:
        lines.append("    (void)args;")
        lines.append("    if (nargs != 0 || (kwnames && PyTuple_Size(kwnames) != 0)) {")
        lines.append(
            f'        PyErr_Format(PyExc_TypeError, "{py_name}() takes 0 argument(s) (%zd positional given)", nargs);'
        )
        lines.append("        return NULL;")
        lines.append("    }")
    else:
        lines.append("    static const NoxPyParamSpec params[] = {")
        for p in params:
            hd_u = "1u" if p.has_default else "0u"
            lines.append(f"        {{ {_c_escape(p.name)}, {_PARAM_KIND_MACRO[p.kind]}, {hd_u} }},")
        lines.append("    };")
        lines.append(f"    PyObject *bound[{argc}] = {{ NULL }};")
        lines.append(
            f"    if (noxpy_bind_fastcall_kwargs({_c_escape(py_name)}, params, {argc}, args, nargs, kwnames, bound) < 0) {{"
        )
        lines.append("        return NULL;")
        lines.append("    }")

    value_indices: list[int] = []
    extref_indices: list[int] = []
    buf_indices: list[int] = []
    for i, p in enumerate(params):
        if p.marshal == "value":
            value_indices.append(i)
            continue
        if p.marshal == "external_ref":
            extref_indices.append(i)
            continue
        if p.marshal == "handle" and p.type_name in _BUILTIN_BUFFER_MAP:
            buf_indices.append(i)
            continue
        ctype = _c_in_var_type(p)
        init_val = _c_scalar_or_span_default_init(p)
        lines.append(f"    {ctype} c_arg_{i} = {init_val};")
        conv = _IN_CONV_MAP[p.type_name]
        if p.has_default:
            lines.append(f"    if (bound[{i}] != NULL && {conv}(bound[{i}], &c_arg_{i}, {_c_escape(p.name)}) < 0) {{")
        else:
            lines.append(f"    if ({conv}(bound[{i}], &c_arg_{i}, {_c_escape(p.name)}) < 0) {{")
        lines.append("        return NULL;")
        lines.append("    }")

    if first_arg_is_cls:
        lines.append("    PyObject *module = PyType_GetModule((PyTypeObject *)cls);")
        lines.append("    if (!module) {")
        lines.append('        PyErr_SetString(PyExc_SystemError, "noxpy: class missing defining module");')
        lines.append("        return NULL;")
        lines.append("    }")

    if has_phase3_state:
        lines.append(f"    noxpy_mod_state_{leaf} *mst = (noxpy_mod_state_{leaf} *)PyModule_GetState(module);")
        lines.append("    NoxPyModuleState *st = mst ? &mst->base : NULL;")
    else:
        lines.append("    NoxPyModuleState *st = (NoxPyModuleState *)PyModule_GetState(module);")

    if buf_indices:
        p0 = buf_indices[0]
        _, p0_macro, _, _ = _BUILTIN_BUFFER_MAP[params[p0].type_name]
        lines.append("    if (!st) {")
        lines.append('        PyErr_SetString(PyExc_RuntimeError, "noxpy: missing module state");')
        lines.append("        return NULL;")
        lines.append("    }")
        for bi in buf_indices:
            _, bmacro, _, _ = _BUILTIN_BUFFER_MAP[params[bi].type_name]
            lines.append(
                f"    NoxPyObjectData *hdata_{bi} = noxpy_buf_extract_data(module, bound[{bi}], {bmacro}, {_c_escape(params[bi].name)});"
            )
            lines.append(f"    if (!hdata_{bi}) {{ return NULL; }}")
        lines.append("    NoxHostContext *ctx = NULL;")
        for bi in buf_indices:
            lines.append(f"    NoxHostHandle c_arg_{bi} = 0;")
        lines.append(f"    if (noxpy_obj_enter_call(hdata_{p0}, 0ULL, &ctx, &c_arg_{p0}) < 0) {{")
        lines.append("        return NULL;")
        lines.append("    }")
        for bi in buf_indices[1:]:
            lines.append(f"    if (hdata_{bi}->owner_ctx != ctx || noxpy_obj_is_closed(hdata_{bi})) {{")
            lines.append(f"        noxpy_obj_leave_call(hdata_{p0});")
            lines.append('        PyErr_SetString(PyExc_ValueError, "noxpy: buffer arguments must share a live Nox context");')
            lines.append("        return NULL;")
            lines.append("    }")
            lines.append(f"    c_arg_{bi} = hdata_{bi}->handle;")
        rel_ctx = "NULL"
        rel_lease = f"noxpy_obj_leave_call(hdata_{p0});"
    else:
        lines.append("    if (!st || noxpy_module_lease_acquire(st) < 0) {")
        lines.append("        return NULL;")
        lines.append("    }")
        lines.append("    NoxHostContext *ctx = NULL;")
        if return_marshal == "handle":
            if use_ex:
                lines.append(f"    if (noxpy_host_context_create_for_module(module, &noxpy_host_ops_{leaf}, &ctx) < 0) {{")
            else:
                lines.append(f"    if (noxpy_host_context_create_and_init(&noxpy_host_ops_{leaf}, &ctx) < 0) {{")
            lines.append("        noxpy_module_lease_release(st);")
            lines.append("        return NULL;")
            lines.append("    }")
            rel_ctx = f"noxpy_host_context_fini_and_destroy(&noxpy_host_ops_{leaf}, ctx);"
        else:
            lines.append("    if (noxpy_module_ctx_acquire(st, &ctx) < 0) {")
            lines.append("        noxpy_module_lease_release(st);")
            lines.append("        return NULL;")
            lines.append("    }")
            rel_ctx = "noxpy_module_ctx_release(st, ctx);"
        rel_lease = "noxpy_module_lease_release(st);"

    for ei in extref_indices:
        lines.append(f"    NoxHostExternalRef c_arg_{ei} = 0;")
    if extref_indices:
        _emit_extref_arg_setup(
            lines,
            params,
            extref_indices,
            indent="    ",
            mod_expr="module",
            release_ctx_line=None if buf_indices else rel_ctx,
            release_lease_line=rel_lease,
            err_return="NULL",
        )

    for vi in value_indices:
        lines.append(f"    NoxHostValue c_arg_{vi} = 0;")
    _emit_value_arg_setup(
        lines,
        params,
        value_indices,
        indent="    ",
        release_ctx_line=None if buf_indices else rel_ctx,
        release_lease_line=rel_lease,
        err_return="NULL",
        use_ex=use_ex,
        mod_expr="module",
        extref_indices=extref_indices,
    )

    if return_type != "None":
        ret_ctype = _c_ret_var_type(return_type, return_marshal)
        ret_init = "{0}" if ret_ctype == "NoxHostBuffer" else "0"
        lines.append(f"    {ret_ctype} c_ret = {ret_init};")

    call_args = ["ctx"]
    for i, p in enumerate(params):
        if p.marshal == "span":
            call_args.append(f"c_arg_{i}.data")
            call_args.append(f"c_arg_{i}.len")
        else:
            call_args.append(f"c_arg_{i}")
    if return_type != "None":
        call_args.append("&c_ret")
    call_args.append("&err")

    lines.append("    NoxHostError *err = NULL;")
    lines.append("    NoxHostStatus rc;")
    lines.append("    Py_BEGIN_ALLOW_THREADS")
    lines.append(f"    rc = {c_symbol}({', '.join(call_args)});")
    lines.append("    Py_END_ALLOW_THREADS")
    for vi in value_indices:
        lines.append(f"    if (c_arg_{vi}) {{ (void)st->host_ops->value_release(ctx, c_arg_{vi}); }}")
    for ei in extref_indices:
        lines.append(f"    if (c_arg_{ei}) {{ noxpy_extref_release_token(module, c_arg_{ei}); }}")

    if has_phase3_state or use_ex:
        raise_fn = f"noxpy_raise_host_{leaf}(module, "
    else:
        raise_fn = "noxpy_set_exception_from_host(st->host_ops, "
    if return_marshal == "handle":
        lines.append("    if (rc != NOX_HOST_OK || c_ret == 0) {")
        lines.append(f"        {raise_fn}(rc == NOX_HOST_OK ? NOX_HOST_ERR_INVALID_HANDLE : rc), err);")
        if buf_indices:
            lines.append(f"        {rel_lease}")
        else:
            lines.append(f"        noxpy_host_context_fini_and_destroy(&noxpy_host_ops_{leaf}, ctx);")
            lines.append(f"        {rel_lease}")
        lines.append("        return NULL;")
        lines.append("    }")
        if return_type in _BUILTIN_BUFFER_MAP:
            _, rbuf_macro, _, _ = _BUILTIN_BUFFER_MAP[return_type]
            if buf_indices:
                lines.append(
                    f"    PyObject *py_ret = noxpy_buf_new_from_shared_handle(module, hdata_{buf_indices[0]}->shared_ctx, c_ret, {rbuf_macro});"
                )
            else:
                lines.append(
                    f"    PyObject *py_ret = noxpy_buf_new_from_handle(module, &noxpy_host_ops_{leaf}, ctx, c_ret, {rbuf_macro});"
                )
        else:
            ret_pol = _POLICY_MACRO[class_policy_map[return_type]]
            if buf_indices:
                lines.append(
                    f"    PyObject *py_ret = noxpy_obj_new_from_shared_handle((PyTypeObject *)mst->cls_{return_type}, module, "
                    f"hdata_{buf_indices[0]}->shared_ctx, c_ret, NOXPY_TAG_{return_type}, {ret_pol});"
                )
            else:
                lines.append(
                    f"    PyObject *py_ret = noxpy_obj_new_from_handle((PyTypeObject *)mst->cls_{return_type}, module, "
                    f"&noxpy_host_ops_{leaf}, ctx, c_ret, NOXPY_TAG_{return_type}, {ret_pol});"
                )
        lines.append(f"    {rel_lease}")
        lines.append("    return py_ret;")
    else:
        lines.append("    if (rc != NOX_HOST_OK) {")
        lines.append(f"        {raise_fn}rc, err);")
        if not buf_indices:
            lines.append("        noxpy_module_ctx_release(st, ctx);")
        lines.append(f"        {rel_lease}")
        lines.append("        return NULL;")
        lines.append("    }")

        if return_marshal == "value":
            if use_ex:
                lines.append("    PyObject *py_ret = noxpy_host_value_to_py_ex(module, st->host_ops, ctx, c_ret);")
            else:
                lines.append("    PyObject *py_ret = noxpy_host_value_to_py(st->host_ops, ctx, c_ret);")
            lines.append("    if (c_ret) { (void)st->host_ops->value_release(ctx, c_ret); }")
            if not buf_indices:
                lines.append("    noxpy_module_ctx_release(st, ctx);")
            lines.append(f"    {rel_lease}")
            lines.append("    return py_ret;")
        elif return_marshal == "external_ref":
            lines.append("    PyObject *py_ret = noxpy_extref_take_returned(module, c_ret);")
            if not buf_indices:
                lines.append("    noxpy_module_ctx_release(st, ctx);")
            lines.append(f"    {rel_lease}")
            lines.append("    return py_ret;")
        else:
            if not buf_indices:
                lines.append("    noxpy_module_ctx_release(st, ctx);")
            lines.append(f"    {rel_lease}")
            if return_type == "None":
                lines.append("    Py_RETURN_NONE;")
            elif return_type == "int":
                lines.append("    return noxpy_i64_to_py(c_ret);")
            elif return_type == "float":
                lines.append("    return noxpy_f64_to_py(c_ret);")
            elif return_type == "bool":
                lines.append("    return noxpy_bool_to_py(c_ret);")
            elif return_type == "str":
                lines.append("    return noxpy_utf8_buffer_to_py(st->host_ops, &c_ret);")
            else:
                lines.append("    return noxpy_bytes_buffer_to_py(st->host_ops, &c_ret);")
    lines.append("}")
    lines.append("")
    return lines


def _emit_async_callable(
    *,
    c_func_name: str,
    py_name: str,
    c_symbol: str,
    params: tuple[ParamIR, ...],
    return_type: str,
    return_marshal: str,
    leaf: str,
    first_arg_is_cls: bool,
    is_instance_method: bool,
    owner_cls_name: str | None,
    exceptions: tuple[ExportedExceptionIR, ...],
    class_policy_map: dict[str, str],
) -> list[str]:
    start_sym = _c_async_start_symbol(c_symbol)
    lines: list[str] = []
    c_params = ["NoxHostContext *ctx"]
    if is_instance_method:
        c_params.append("NoxHostHandle self_h")
    for i, p in enumerate(params):
        if p.marshal == "span":
            c_params.append(f"const uint8_t *arg_{i}_data")
            c_params.append(f"size_t arg_{i}_len")
        else:
            c_params.append(f"{_c_in_var_type(p)} arg_{i}")
    c_params.extend(["NoxHostTaskCallback on_done", "void *user_data", "NoxHostTask *out_task", "NoxHostError **err"])
    lines.append(f"extern NoxHostStatus {start_sym}({', '.join(c_params)});")
    lines.append("")

    if is_instance_method:
        first_c_param = "PyObject *self"
    elif first_arg_is_cls:
        first_c_param = "PyObject *cls"
    else:
        first_c_param = "PyObject *module"

    lines.append(
        f"static PyObject *{c_func_name}({first_c_param}, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames) {{"
    )
    argc = len(params)
    if argc == 0:
        lines.append("    (void)args;")
        lines.append("    if (nargs != 0 || (kwnames && PyTuple_Size(kwnames) != 0)) {")
        lines.append(
            f'        PyErr_Format(PyExc_TypeError, "{py_name}() takes 0 argument(s) (%zd positional given)", nargs);'
        )
        lines.append("        return NULL;")
        lines.append("    }")
    else:
        lines.append("    static const NoxPyParamSpec params[] = {")
        for p in params:
            hd_u = "1u" if p.has_default else "0u"
            lines.append(f"        {{ {_c_escape(p.name)}, {_PARAM_KIND_MACRO[p.kind]}, {hd_u} }},")
        lines.append("    };")
        lines.append(f"    PyObject *bound[{argc}] = {{ NULL }};")
        lines.append(
            f"    if (noxpy_bind_fastcall_kwargs({_c_escape(py_name)}, params, {argc}, args, nargs, kwnames, bound) < 0) {{"
        )
        lines.append("        return NULL;")
        lines.append("    }")

    value_indices: list[int] = []
    extref_indices: list[int] = []
    for i, p in enumerate(params):
        if p.marshal == "value":
            value_indices.append(i)
            continue
        if p.marshal == "external_ref":
            extref_indices.append(i)
            continue
        ctype = _c_in_var_type(p)
        init_val = _c_scalar_or_span_default_init(p)
        lines.append(f"    {ctype} c_arg_{i} = {init_val};")
        conv = _IN_CONV_MAP[p.type_name]
        if p.has_default:
            lines.append(f"    if (bound[{i}] != NULL && {conv}(bound[{i}], &c_arg_{i}, {_c_escape(p.name)}) < 0) {{")
        else:
            lines.append(f"    if ({conv}(bound[{i}], &c_arg_{i}, {_c_escape(p.name)}) < 0) {{")
        lines.append("        return NULL;")
        lines.append("    }")

    if is_instance_method:
        assert owner_cls_name is not None
        lines.append("    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, Py_TYPE(self));")
        lines.append("    NoxHostContext *ctx = NULL;")
        lines.append("    NoxHostHandle h = 0;")
        lines.append(f"    if (noxpy_obj_enter_call(data, NOXPY_TAG_{owner_cls_name}, &ctx, &h) < 0) {{")
        lines.append("        return NULL;")
        lines.append("    }")
        lines.append("    PyObject *module = data->owner_module;")
        lines.append(f"    noxpy_mod_state_{leaf} *mst = (noxpy_mod_state_{leaf} *)PyModule_GetState(module);")
        lines.append("    NoxPyModuleState *st = mst ? &mst->base : NULL;")
        rel_ctx: str | None = None
        rel_lease = "noxpy_obj_leave_call(data);"
        owns_ctx_expr = "0"
        data_ptr_expr = "data"
        self_ptr_expr = "self"
    else:
        if first_arg_is_cls:
            lines.append("    PyObject *module = PyType_GetModule((PyTypeObject *)cls);")
            lines.append("    if (!module) {")
            lines.append('        PyErr_SetString(PyExc_SystemError, "noxpy: class missing defining module");')
            lines.append("        return NULL;")
            lines.append("    }")
        lines.append(f"    noxpy_mod_state_{leaf} *mst = (noxpy_mod_state_{leaf} *)PyModule_GetState(module);")
        lines.append("    NoxPyModuleState *st = mst ? &mst->base : NULL;")
        lines.append("    if (!st || noxpy_module_lease_acquire(st) < 0) {")
        lines.append("        return NULL;")
        lines.append("    }")
        lines.append("    NoxHostContext *ctx = NULL;")
        lines.append(f"    if (noxpy_host_context_create_for_module(module, &noxpy_host_ops_{leaf}, &ctx) < 0) {{")
        lines.append("        noxpy_module_lease_release(st);")
        lines.append("        return NULL;")
        lines.append("    }")
        rel_ctx = f"noxpy_host_context_fini_and_destroy(&noxpy_host_ops_{leaf}, ctx);"
        rel_lease = "noxpy_module_lease_release(st);"
        owns_ctx_expr = "1"
        data_ptr_expr = "NULL"
        self_ptr_expr = "NULL"

    lines.append("    (void)st;")
    for ei in extref_indices:
        lines.append(f"    NoxHostExternalRef c_arg_{ei} = 0;")
    if extref_indices:
        _emit_extref_arg_setup(
            lines,
            params,
            extref_indices,
            indent="    ",
            mod_expr="module",
            release_ctx_line=rel_ctx,
            release_lease_line=rel_lease,
            err_return="NULL",
        )
    for vi in value_indices:
        lines.append(f"    NoxHostValue c_arg_{vi} = 0;")
    if value_indices:
        _emit_value_arg_setup(
            lines,
            params,
            value_indices,
            indent="    ",
            release_ctx_line=rel_ctx,
            release_lease_line=rel_lease,
            err_return="NULL",
            use_ex=True,
            mod_expr="module",
            extref_indices=extref_indices,
        )

    if exceptions:
        lines.append("    const NoxPyCustomExc custom_excs[] = {")
        for exc in exceptions:
            lines.append(f"        {{ {_c_escape(exc.name)}, mst->exc_{exc.name} }},")
        lines.append("    };")
        cex_ptr = "custom_excs"
        cex_cnt = "sizeof(custom_excs) / sizeof(custom_excs[0])"
    else:
        cex_ptr = "NULL"
        cex_cnt = "0"

    if extref_indices:
        lines.append(
            "    const NoxHostExternalRef kept_extrefs[] = { "
            + ", ".join(f"c_arg_{ei}" for ei in extref_indices)
            + " };"
        )
        kept_ptr = "kept_extrefs"
        kept_cnt = str(len(extref_indices))
    else:
        kept_ptr = "NULL"
        kept_cnt = "0"

    if return_marshal == "handle":
        ret_cls_expr = f"mst->cls_{return_type}"
        ret_tag_expr = f"NOXPY_TAG_{return_type}"
        ret_pol_expr = _POLICY_MACRO[class_policy_map[return_type]]
    else:
        ret_cls_expr = "NULL"
        ret_tag_expr = "0"
        ret_pol_expr = "0"

    lines.append("    NoxPyAsyncOp *op = NULL;")
    lines.append("    PyObject *fut_unused = NULL;")
    lines.append(
        f"    if (noxpy_async_op_prepare(module, &noxpy_host_ops_{leaf}, ctx, {owns_ctx_expr}, {data_ptr_expr}, "
        f"{self_ptr_expr}, {_c_escape(return_type)}, {_c_escape(return_marshal)}, {ret_cls_expr}, {ret_tag_expr}, "
        f"{ret_pol_expr}, {cex_ptr}, {cex_cnt}, {kept_ptr}, {kept_cnt}, &op, &fut_unused) < 0) {{"
    )
    lines.append("        (void)fut_unused;")
    for vi in value_indices:
        lines.append(f"        if (c_arg_{vi}) {{ (void)st->host_ops->value_release(ctx, c_arg_{vi}); }}")
    for ei in extref_indices:
        lines.append(f"        if (c_arg_{ei}) {{ noxpy_extref_release_token(module, c_arg_{ei}); }}")
    if rel_ctx:
        lines.append(f"        {rel_ctx}")
    lines.append(f"        {rel_lease}")
    lines.append("        return NULL;")
    lines.append("    }")
    lines.append("    (void)fut_unused;")

    call_args = ["ctx"]
    if is_instance_method:
        call_args.append("h")
    for i, p in enumerate(params):
        if p.marshal == "span":
            call_args.append(f"c_arg_{i}.data")
            call_args.append(f"c_arg_{i}.len")
        else:
            call_args.append(f"c_arg_{i}")
    call_args.extend(["noxpy_async_on_done", "op", "&task", "&err"])

    lines.append("    NoxHostTask task = 0;")
    lines.append("    NoxHostError *err = NULL;")
    lines.append("    NoxHostStatus rc;")
    lines.append("    Py_BEGIN_ALLOW_THREADS")
    lines.append(f"    rc = {start_sym}({', '.join(call_args)});")
    lines.append("    Py_END_ALLOW_THREADS")
    for vi in value_indices:
        lines.append(f"    if (c_arg_{vi}) {{ (void)st->host_ops->value_release(ctx, c_arg_{vi}); }}")
    lines.append("    PyObject *ret_fut = noxpy_async_op_finish_start(op, rc, task, err);")
    lines.append(f"    {rel_lease}")
    lines.append("    return ret_fut;")
    lines.append("}")
    lines.append("")
    return lines


def _emit_class_c_wrappers(
    cls: ExportedClassIR,
    *,
    leaf: str,
    class_policy_map: dict[str, str],
    exceptions: tuple[ExportedExceptionIR, ...] = (),
) -> list[str]:
    lines: list[str] = []
    cname = cls.name
    tag_macro = f"NOXPY_TAG_{cname}"
    policy_macro = _POLICY_MACRO[cls.thread_policy]

    # Prototypes for constructor & all exported synchronous instance/dunder/property methods
    ctor_c_params = ["NoxHostContext *ctx"]
    for i, p in enumerate(cls.constructor.params):
        if p.marshal == "span":
            ctor_c_params.append(f"const uint8_t *arg_{i}_data")
            ctor_c_params.append(f"size_t arg_{i}_len")
        else:
            ctor_c_params.append(f"{_c_in_var_type(p)} arg_{i}")
    ctor_c_params.append("NoxHostHandle *out_handle")
    ctor_c_params.append("NoxHostError **out_err")
    lines.append(f"extern NoxHostStatus {cls.constructor.c_symbol}({', '.join(ctor_c_params)});")

    for m in cls.methods:
        if m.method_kind in ("staticmethod", "classmethod") or m.is_async:
            continue  # Emitted alongside callable helper below
        mc_params = ["NoxHostContext *ctx", "NoxHostHandle self_h"]
        for i, p in enumerate(m.params):
            if p.marshal == "span":
                mc_params.append(f"const uint8_t *arg_{i}_data")
                mc_params.append(f"size_t arg_{i}_len")
            else:
                mc_params.append(f"{_c_in_var_type(p)} arg_{i}")
        if m.return_type != "None":
            mc_params.append(f"{_c_ret_var_type(m.return_type, m.return_marshal)} *out_val")
        mc_params.append("NoxHostError **out_err")
        lines.append(f"extern NoxHostStatus {m.c_symbol}({', '.join(mc_params)});")
    lines.append("")

    # Constructor Py_tp_init
    ctor_params = cls.constructor.params
    ctor_argc = len(ctor_params)
    lines.append(f"static int noxpy_cls_{cname}_init(PyObject *self, PyObject *args, PyObject *kwds) {{")
    if ctor_argc == 0:
        lines.append("    if ((args && PyTuple_Size(args) != 0) || (kwds && PyDict_Size(kwds) != 0)) {")
        lines.append(f'        PyErr_Format(PyExc_TypeError, "{cname}() takes no arguments");')
        lines.append("        return -1;")
        lines.append("    }")
    else:
        lines.append("    static const NoxPyParamSpec params[] = {")
        for p in ctor_params:
            hd_u = "1u" if p.has_default else "0u"
            lines.append(f"        {{ {_c_escape(p.name)}, {_PARAM_KIND_MACRO[p.kind]}, {hd_u} }},")
        lines.append("    };")
        lines.append(f"    PyObject *bound[{ctor_argc}] = {{ NULL }};")
        lines.append(
            f"    if (noxpy_bind_tuple_kwargs({_c_escape(cname)}, params, {ctor_argc}, args, kwds, bound) < 0) {{"
        )
        lines.append("        return -1;")
        lines.append("    }")

    ctor_value_indices: list[int] = []
    ctor_extref_indices: list[int] = []
    for i, p in enumerate(ctor_params):
        if p.marshal == "value":
            ctor_value_indices.append(i)
            continue
        if p.marshal == "external_ref":
            ctor_extref_indices.append(i)
            continue
        ctype = _c_in_var_type(p)
        init_val = _c_scalar_or_span_default_init(p)
        lines.append(f"    {ctype} c_arg_{i} = {init_val};")
        conv = _IN_CONV_MAP[p.type_name]
        if p.has_default:
            lines.append(f"    if (bound[{i}] != NULL && {conv}(bound[{i}], &c_arg_{i}, {_c_escape(p.name)}) < 0) {{")
        else:
            lines.append(f"    if ({conv}(bound[{i}], &c_arg_{i}, {_c_escape(p.name)}) < 0) {{")
        lines.append("        return -1;")
        lines.append("    }")

    lines.append("    PyTypeObject *py_cls = Py_TYPE(self);")
    lines.append("    PyObject *mod = PyType_GetModule(py_cls);")
    lines.append("    if (!mod) {")
    lines.append(f'        PyErr_SetString(PyExc_SystemError, "noxpy: {cname} missing defining module");')
    lines.append("        return -1;")
    lines.append("    }")
    lines.append(f"    noxpy_mod_state_{leaf} *mst = (noxpy_mod_state_{leaf} *)PyModule_GetState(mod);")
    lines.append("    NoxPyModuleState *st = mst ? &mst->base : NULL;")
    lines.append("    if (!st || noxpy_module_lease_acquire(st) < 0) {")
    lines.append("        return -1;")
    lines.append("    }")
    lines.append("    NoxHostContext *ctx = NULL;")
    lines.append(f"    if (noxpy_host_context_create_for_module(mod, &noxpy_host_ops_{leaf}, &ctx) < 0) {{")
    lines.append("        noxpy_module_lease_release(st);")
    lines.append("        return -1;")
    lines.append("    }")
    for ei in ctor_extref_indices:
        lines.append(f"    NoxHostExternalRef c_arg_{ei} = 0;")
    if ctor_extref_indices:
        _emit_extref_arg_setup(
            lines,
            ctor_params,
            ctor_extref_indices,
            indent="    ",
            mod_expr="mod",
            release_ctx_line=f"noxpy_host_context_fini_and_destroy(&noxpy_host_ops_{leaf}, ctx);",
            release_lease_line="noxpy_module_lease_release(st);",
            err_return="-1",
        )
    for vi in ctor_value_indices:
        lines.append(f"    NoxHostValue c_arg_{vi} = 0;")
    _emit_value_arg_setup(
        lines,
        ctor_params,
        ctor_value_indices,
        indent="    ",
        release_ctx_line=f"noxpy_host_context_fini_and_destroy(&noxpy_host_ops_{leaf}, ctx);",
        release_lease_line="noxpy_module_lease_release(st);",
        err_return="-1",
        use_ex=True,
        mod_expr="mod",
        extref_indices=ctor_extref_indices,
    )

    ctor_call_args = ["ctx"]
    for i, p in enumerate(ctor_params):
        if p.marshal == "span":
            ctor_call_args.append(f"c_arg_{i}.data")
            ctor_call_args.append(f"c_arg_{i}.len")
        else:
            ctor_call_args.append(f"c_arg_{i}")
    ctor_call_args.extend(["&h", "&err"])

    lines.append("    NoxHostHandle h = 0;")
    lines.append("    NoxHostError *err = NULL;")
    lines.append("    NoxHostStatus rc;")
    lines.append("    Py_BEGIN_ALLOW_THREADS")
    lines.append(f"    rc = {cls.constructor.c_symbol}({', '.join(ctor_call_args)});")
    lines.append("    Py_END_ALLOW_THREADS")
    for vi in ctor_value_indices:
        lines.append(f"    if (c_arg_{vi}) {{ (void)st->host_ops->value_release(ctx, c_arg_{vi}); }}")
    for ei in ctor_extref_indices:
        lines.append(f"    if (c_arg_{ei}) {{ noxpy_extref_release_token(mod, c_arg_{ei}); }}")
    lines.append("    if (rc != NOX_HOST_OK || h == 0) {")
    lines.append(f"        noxpy_raise_host_{leaf}(mod, rc == NOX_HOST_OK ? NOX_HOST_ERR_INVALID_HANDLE : rc, err);")
    lines.append(f"        noxpy_host_context_fini_and_destroy(&noxpy_host_ops_{leaf}, ctx);")
    lines.append("        noxpy_module_lease_release(st);")
    lines.append("        return -1;")
    lines.append("    }")
    lines.append("    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, py_cls);")
    lines.append("    if (!data) {")
    lines.append("        (void)st->host_ops->handle_release(ctx, h);")
    lines.append(f"        noxpy_host_context_fini_and_destroy(&noxpy_host_ops_{leaf}, ctx);")
    lines.append("        noxpy_module_lease_release(st);")
    lines.append("        return -1;")
    lines.append("    }")
    lines.append("    if (data->initialized) {")
    lines.append("        noxpy_obj_close(data);")
    lines.append("    }")
    lines.append(
        f"    int init_rc = noxpy_obj_init(data, mod, &noxpy_host_ops_{leaf}, ctx, h, {tag_macro}, {policy_macro});"
    )
    lines.append("    noxpy_module_lease_release(st);")
    lines.append("    if (init_rc < 0) {")
    lines.append("        (void)noxpy_host_ops_{leaf}.handle_release(ctx, h);".replace("{leaf}", leaf))
    lines.append(f"        noxpy_host_context_fini_and_destroy(&noxpy_host_ops_{leaf}, ctx);")
    lines.append("        return -1;")
    lines.append("    }")
    lines.append("    return 0;")
    lines.append("}")
    lines.append("")

    # Instance methods, staticmethods, classmethods, properties, dunders
    prop_gets: dict[str, MethodIR] = {}
    prop_sets: dict[str, MethodIR] = {}
    repr_m: MethodIR | None = None
    str_m: MethodIR | None = None
    len_m: MethodIR | None = None
    iter_m: MethodIR | None = None
    next_m: MethodIR | None = None
    regular_methods: list[MethodIR] = []

    for m in cls.methods:
        if m.method_kind == "property_get":
            prop_gets[m.name] = m
        elif m.method_kind == "property_set":
            prop_sets[m.name] = m
        elif m.method_kind == "dunder" and m.name == "__repr__":
            repr_m = m
        elif m.method_kind == "dunder" and m.name == "__str__":
            str_m = m
        elif m.method_kind == "dunder" and m.name == "__len__":
            len_m = m
        elif m.method_kind == "dunder" and m.name == "__iter__":
            iter_m = m
        elif m.method_kind == "dunder" and m.name == "__next__":
            next_m = m
        elif m.method_kind in ("staticmethod", "classmethod"):
            if m.is_async:
                lines.extend(
                    _emit_async_callable(
                        c_func_name=f"noxpy_cls_{cname}_m_{m.name}",
                        py_name=f"{cname}.{m.name}",
                        c_symbol=m.c_symbol,
                        params=m.params,
                        return_type=m.return_type,
                        return_marshal=m.return_marshal,
                        leaf=leaf,
                        first_arg_is_cls=True,
                        is_instance_method=False,
                        owner_cls_name=None,
                        exceptions=exceptions,
                        class_policy_map=class_policy_map,
                    )
                )
            else:
                lines.extend(
                    _emit_module_or_class_callable(
                        c_func_name=f"noxpy_cls_{cname}_m_{m.name}",
                        py_name=f"{cname}.{m.name}",
                        c_symbol=m.c_symbol,
                        params=m.params,
                        return_type=m.return_type,
                        return_marshal=m.return_marshal,
                        leaf=leaf,
                        first_arg_is_cls=True,
                        has_phase3_state=True,
                        class_policy_map=class_policy_map,
                        use_ex=True,
                    )
                )
            regular_methods.append(m)
        elif m.is_async:
            regular_methods.append(m)
            lines.extend(
                _emit_async_callable(
                    c_func_name=f"noxpy_cls_{cname}_m_{m.name}",
                    py_name=f"{cname}.{m.name}",
                    c_symbol=m.c_symbol,
                    params=m.params,
                    return_type=m.return_type,
                    return_marshal=m.return_marshal,
                    leaf=leaf,
                    first_arg_is_cls=False,
                    is_instance_method=True,
                    owner_cls_name=cname,
                    exceptions=exceptions,
                    class_policy_map=class_policy_map,
                )
            )
        else:
            # synchronous instance method (or custom dunder callable)
            regular_methods.append(m)
            argc = len(m.params)
            lines.append(
                f"static PyObject *noxpy_cls_{cname}_m_{m.name}(PyObject *self, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames) {{"
            )
            if argc == 0:
                lines.append("    (void)args;")
                lines.append("    if (nargs != 0 || (kwnames && PyTuple_Size(kwnames) != 0)) {")
                lines.append(
                    f'        PyErr_Format(PyExc_TypeError, "{cname}.{m.name}() takes 0 argument(s) (%zd positional given)", nargs);'
                )
                lines.append("        return NULL;")
                lines.append("    }")
            else:
                lines.append("    static const NoxPyParamSpec params[] = {")
                for p in m.params:
                    hd_u = "1u" if p.has_default else "0u"
                    lines.append(f"        {{ {_c_escape(p.name)}, {_PARAM_KIND_MACRO[p.kind]}, {hd_u} }},")
                lines.append("    };")
                lines.append(f"    PyObject *bound[{argc}] = {{ NULL }};")
                lines.append(
                    f"    if (noxpy_bind_fastcall_kwargs({_c_escape(f'{cname}.{m.name}')}, params, {argc}, args, nargs, kwnames, bound) < 0) {{"
                )
                lines.append("        return NULL;")
                lines.append("    }")

            m_val_indices: list[int] = []
            m_ext_indices: list[int] = []
            for i, p in enumerate(m.params):
                if p.marshal == "value":
                    m_val_indices.append(i)
                    continue
                if p.marshal == "external_ref":
                    m_ext_indices.append(i)
                    continue
                ctype = _c_in_var_type(p)
                init_val = _c_scalar_or_span_default_init(p)
                lines.append(f"    {ctype} c_arg_{i} = {init_val};")
                conv = _IN_CONV_MAP[p.type_name]
                if p.has_default:
                    lines.append(f"    if (bound[{i}] != NULL && {conv}(bound[{i}], &c_arg_{i}, {_c_escape(p.name)}) < 0) {{")
                else:
                    lines.append(f"    if ({conv}(bound[{i}], &c_arg_{i}, {_c_escape(p.name)}) < 0) {{")
                lines.append("        return NULL;")
                lines.append("    }")

            lines.append("    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, Py_TYPE(self));")
            lines.append("    NoxHostContext *ctx = NULL;")
            lines.append("    NoxHostHandle h = 0;")
            lines.append(f"    if (noxpy_obj_enter_call(data, {tag_macro}, &ctx, &h) < 0) {{")
            lines.append("        return NULL;")
            lines.append("    }")
            lines.append("    const NoxPyHostOps *ops = data->host_ops;")
            lines.append("    (void)ops;")
            lines.append("    PyObject *owner_mod = data->owner_module;")
            lines.append("    NoxPyModuleState *st = (NoxPyModuleState *)PyModule_GetState(owner_mod);")
            lines.append("    (void)st;")
            for ei in m_ext_indices:
                lines.append(f"    NoxHostExternalRef c_arg_{ei} = 0;")
            if m_ext_indices:
                _emit_extref_arg_setup(
                    lines,
                    m.params,
                    m_ext_indices,
                    indent="    ",
                    mod_expr="owner_mod",
                    release_ctx_line=None,
                    release_lease_line="noxpy_obj_leave_call(data);",
                    err_return="NULL",
                )
            if m_val_indices:
                for vi in m_val_indices:
                    lines.append(f"    NoxHostValue c_arg_{vi} = 0;")
                _emit_value_arg_setup(
                    lines,
                    m.params,
                    m_val_indices,
                    indent="    ",
                    release_ctx_line=None,
                    release_lease_line="noxpy_obj_leave_call(data);",
                    err_return="NULL",
                    use_ex=True,
                    mod_expr="owner_mod",
                    extref_indices=m_ext_indices,
                )

            if m.return_type != "None":
                ret_ctype = _c_ret_var_type(m.return_type, m.return_marshal)
                ret_init = "{0}" if ret_ctype == "NoxHostBuffer" else "0"
                lines.append(f"    {ret_ctype} c_ret = {ret_init};")

            m_call_args = ["ctx", "h"]
            for i, p in enumerate(m.params):
                if p.marshal == "span":
                    m_call_args.append(f"c_arg_{i}.data")
                    m_call_args.append(f"c_arg_{i}.len")
                else:
                    m_call_args.append(f"c_arg_{i}")
            if m.return_type != "None":
                m_call_args.append("&c_ret")
            m_call_args.append("&err")

            lines.append("    NoxHostError *err = NULL;")
            lines.append("    NoxHostStatus rc;")
            lines.append("    Py_BEGIN_ALLOW_THREADS")
            lines.append(f"    rc = {m.c_symbol}({', '.join(m_call_args)});")
            lines.append("    Py_END_ALLOW_THREADS")
            for vi in m_val_indices:
                lines.append(f"    if (c_arg_{vi}) {{ (void)ops->value_release(ctx, c_arg_{vi}); }}")
            for ei in m_ext_indices:
                lines.append(f"    if (c_arg_{ei}) {{ noxpy_extref_release_token(owner_mod, c_arg_{ei}); }}")
            lines.append("    if (rc != NOX_HOST_OK) {")
            lines.append(f"        noxpy_raise_host_{leaf}(owner_mod, rc, err);")
            lines.append("        noxpy_obj_leave_call(data);")
            lines.append("        return NULL;")
            lines.append("    }")
            if m.return_marshal == "value":
                lines.append("    PyObject *py_ret = noxpy_host_value_to_py_ex(owner_mod, ops, ctx, c_ret);")
                lines.append("    if (c_ret) { (void)ops->value_release(ctx, c_ret); }")
                lines.append("    noxpy_obj_leave_call(data);")
                lines.append("    return py_ret;")
            elif m.return_marshal == "external_ref":
                lines.append("    PyObject *py_ret = noxpy_extref_take_returned(owner_mod, c_ret);")
                lines.append("    noxpy_obj_leave_call(data);")
                lines.append("    return py_ret;")
            else:
                lines.append("    noxpy_obj_leave_call(data);")
                if m.return_type == "None":
                    lines.append("    Py_RETURN_NONE;")
                elif m.return_type == "int":
                    lines.append("    return noxpy_i64_to_py(c_ret);")
                elif m.return_type == "float":
                    lines.append("    return noxpy_f64_to_py(c_ret);")
                elif m.return_type == "bool":
                    lines.append("    return noxpy_bool_to_py(c_ret);")
                elif m.return_type == "str":
                    lines.append("    return noxpy_utf8_buffer_to_py(ops, &c_ret);")
                else:
                    lines.append("    return noxpy_bytes_buffer_to_py(ops, &c_ret);")
            lines.append("}")
            lines.append("")

    # Property getters & setters
    for pname in sorted(prop_gets):
        pg = prop_gets[pname]
        lines.append(f"static PyObject *noxpy_cls_{cname}_get_{pname}(PyObject *self, void *closure) {{")
        lines.append("    (void)closure;")
        lines.append("    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, Py_TYPE(self));")
        lines.append("    NoxHostContext *ctx = NULL;")
        lines.append("    NoxHostHandle h = 0;")
        lines.append(f"    if (noxpy_obj_enter_call(data, {tag_macro}, &ctx, &h) < 0) {{")
        lines.append("        return NULL;")
        lines.append("    }")
        lines.append("    const NoxPyHostOps *ops = data->host_ops;")
        lines.append("    (void)ops;")
        ret_ctype = _c_ret_var_type(pg.return_type, pg.return_marshal)
        ret_init = "{0}" if ret_ctype == "NoxHostBuffer" else "0"
        lines.append(f"    {ret_ctype} c_ret = {ret_init};")
        lines.append("    NoxHostError *err = NULL;")
        lines.append("    NoxHostStatus rc;")
        lines.append("    Py_BEGIN_ALLOW_THREADS")
        lines.append(f"    rc = {pg.c_symbol}(ctx, h, &c_ret, &err);")
        lines.append("    Py_END_ALLOW_THREADS")
        lines.append("    if (rc != NOX_HOST_OK) {")
        lines.append(f"        noxpy_raise_host_{leaf}(data->owner_module, rc, err);")
        lines.append("        noxpy_obj_leave_call(data);")
        lines.append("        return NULL;")
        lines.append("    }")
        if pg.return_marshal == "value":
            lines.append("    PyObject *py_ret = noxpy_host_value_to_py_ex(data->owner_module, ops, ctx, c_ret);")
            lines.append("    if (c_ret) { (void)ops->value_release(ctx, c_ret); }")
            lines.append("    noxpy_obj_leave_call(data);")
            lines.append("    return py_ret;")
        elif pg.return_marshal == "external_ref":
            lines.append("    PyObject *py_ret = noxpy_extref_take_returned(data->owner_module, c_ret);")
            lines.append("    noxpy_obj_leave_call(data);")
            lines.append("    return py_ret;")
        else:
            lines.append("    noxpy_obj_leave_call(data);")
            if pg.return_type == "int":
                lines.append("    return noxpy_i64_to_py(c_ret);")
            elif pg.return_type == "float":
                lines.append("    return noxpy_f64_to_py(c_ret);")
            elif pg.return_type == "bool":
                lines.append("    return noxpy_bool_to_py(c_ret);")
            elif pg.return_type == "str":
                lines.append("    return noxpy_utf8_buffer_to_py(ops, &c_ret);")
            else:
                lines.append("    return noxpy_bytes_buffer_to_py(ops, &c_ret);")
        lines.append("}")
        lines.append("")

        if pname in prop_sets:
            ps = prop_sets[pname]
            vp = ps.params[0]
            lines.append(
                f"static int noxpy_cls_{cname}_set_{pname}(PyObject *self, PyObject *val, void *closure) {{"
            )
            lines.append("    (void)closure;")
            lines.append("    if (!val) {")
            lines.append(f'        PyErr_SetString(PyExc_AttributeError, "cannot delete attribute \'{pname}\'");')
            lines.append("        return -1;")
            lines.append("    }")
            if vp.marshal != "value":
                ctype = _c_in_var_type(vp)
                init_v = "{0}" if vp.marshal == "span" else "0"
                conv = _IN_CONV_MAP[vp.type_name]
                lines.append(f"    {ctype} c_val = {init_v};")
                lines.append(f"    if ({conv}(val, &c_val, {_c_escape(vp.name)}) < 0) {{")
                lines.append("        return -1;")
                lines.append("    }")
            lines.append("    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, Py_TYPE(self));")
            lines.append("    NoxHostContext *ctx = NULL;")
            lines.append("    NoxHostHandle h = 0;")
            lines.append(f"    if (noxpy_obj_enter_call(data, {tag_macro}, &ctx, &h) < 0) {{")
            lines.append("        return -1;")
            lines.append("    }")
            lines.append("    const NoxPyHostOps *ops = data->host_ops;")
            if vp.marshal == "external_ref":
                lines.append("    PyObject *owner_mod = data->owner_module;")
                lines.append("    NoxHostExternalRef c_val = noxpy_extref_intern(owner_mod, val);")
                lines.append("    if (c_val == 0) {")
                lines.append("        noxpy_obj_leave_call(data);")
                lines.append("        return -1;")
                lines.append("    }")
            elif vp.marshal == "value":
                lines.append("    PyObject *owner_mod = data->owner_module;")
                lines.append("    NoxHostValue c_val = 0;")
                lines.append(
                    f"    if (noxpy_py_to_host_value_ex(owner_mod, ops, ctx, val, {_c_escape(vp.type_name)}, &c_val, {_c_escape(vp.name)}) < 0) {{"
                )
                lines.append("        noxpy_obj_leave_call(data);")
                lines.append("        return -1;")
                lines.append("    }")
            lines.append("    NoxHostError *err = NULL;")
            lines.append("    NoxHostStatus rc;")
            lines.append("    Py_BEGIN_ALLOW_THREADS")
            if vp.marshal == "span":
                lines.append(f"    rc = {ps.c_symbol}(ctx, h, c_val.data, c_val.len, &err);")
            else:
                lines.append(f"    rc = {ps.c_symbol}(ctx, h, c_val, &err);")
            lines.append("    Py_END_ALLOW_THREADS")
            if vp.marshal == "value":
                lines.append("    if (c_val) { (void)ops->value_release(ctx, c_val); }")
            elif vp.marshal == "external_ref":
                lines.append("    if (c_val) { noxpy_extref_release_token(owner_mod, c_val); }")
            lines.append("    if (rc != NOX_HOST_OK) {")
            lines.append(f"        noxpy_raise_host_{leaf}(data->owner_module, rc, err);")
            lines.append("        noxpy_obj_leave_call(data);")
            lines.append("        return -1;")
            lines.append("    }")
            lines.append("    (void)ops;")
            lines.append("    noxpy_obj_leave_call(data);")
            lines.append("    return 0;")
            lines.append("}")
            lines.append("")

    # Dunders: __repr__, optional __str__, optional __len__
    lines.append(f"static PyObject *noxpy_cls_{cname}_repr(PyObject *self) {{")
    lines.append("    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, Py_TYPE(self));")
    lines.append("    if (noxpy_obj_is_closed(data)) {")
    lines.append(f'        return PyUnicode_FromFormat("<{cname} (closed)>");')
    lines.append("    }")
    if repr_m is not None:
        lines.append("    NoxHostContext *ctx = NULL;")
        lines.append("    NoxHostHandle h = 0;")
        lines.append(f"    if (noxpy_obj_enter_call(data, {tag_macro}, &ctx, &h) < 0) {{")
        lines.append("        return NULL;")
        lines.append("    }")
        lines.append("    const NoxPyHostOps *ops = data->host_ops;")
        lines.append("    NoxHostBuffer c_ret = {0};")
        lines.append("    NoxHostError *err = NULL;")
        lines.append("    NoxHostStatus rc;")
        lines.append("    Py_BEGIN_ALLOW_THREADS")
        lines.append(f"    rc = {repr_m.c_symbol}(ctx, h, &c_ret, &err);")
        lines.append("    Py_END_ALLOW_THREADS")
        lines.append("    if (rc != NOX_HOST_OK) {")
        lines.append(f"        noxpy_raise_host_{leaf}(data->owner_module, rc, err);")
        lines.append("        noxpy_obj_leave_call(data);")
        lines.append("        return NULL;")
        lines.append("    }")
        lines.append("    noxpy_obj_leave_call(data);")
        lines.append("    return noxpy_utf8_buffer_to_py(ops, &c_ret);")
    else:
        lines.append(f'    return PyUnicode_FromFormat("<{cname} object>");')
    lines.append("}")
    lines.append("")

    if str_m is not None:
        lines.append(f"static PyObject *noxpy_cls_{cname}_str(PyObject *self) {{")
        lines.append("    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, Py_TYPE(self));")
        lines.append("    if (noxpy_obj_is_closed(data)) {")
        lines.append(f'        return PyUnicode_FromFormat("<{cname} (closed)>");')
        lines.append("    }")
        lines.append("    NoxHostContext *ctx = NULL;")
        lines.append("    NoxHostHandle h = 0;")
        lines.append(f"    if (noxpy_obj_enter_call(data, {tag_macro}, &ctx, &h) < 0) {{")
        lines.append("        return NULL;")
        lines.append("    }")
        lines.append("    const NoxPyHostOps *ops = data->host_ops;")
        lines.append("    NoxHostBuffer c_ret = {0};")
        lines.append("    NoxHostError *err = NULL;")
        lines.append("    NoxHostStatus rc;")
        lines.append("    Py_BEGIN_ALLOW_THREADS")
        lines.append(f"    rc = {str_m.c_symbol}(ctx, h, &c_ret, &err);")
        lines.append("    Py_END_ALLOW_THREADS")
        lines.append("    if (rc != NOX_HOST_OK) {")
        lines.append(f"        noxpy_raise_host_{leaf}(data->owner_module, rc, err);")
        lines.append("        noxpy_obj_leave_call(data);")
        lines.append("        return NULL;")
        lines.append("    }")
        lines.append("    noxpy_obj_leave_call(data);")
        lines.append("    return noxpy_utf8_buffer_to_py(ops, &c_ret);")
        lines.append("}")
        lines.append("")

    if len_m is not None:
        lines.append(f"static Py_ssize_t noxpy_cls_{cname}_len(PyObject *self) {{")
        lines.append("    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, Py_TYPE(self));")
        lines.append("    NoxHostContext *ctx = NULL;")
        lines.append("    NoxHostHandle h = 0;")
        lines.append(f"    if (noxpy_obj_enter_call(data, {tag_macro}, &ctx, &h) < 0) {{")
        lines.append("        return -1;")
        lines.append("    }")
        lines.append("    int64_t c_ret = 0;")
        lines.append("    NoxHostError *err = NULL;")
        lines.append("    NoxHostStatus rc;")
        lines.append("    Py_BEGIN_ALLOW_THREADS")
        lines.append(f"    rc = {len_m.c_symbol}(ctx, h, &c_ret, &err);")
        lines.append("    Py_END_ALLOW_THREADS")
        lines.append("    if (rc != NOX_HOST_OK) {")
        lines.append(f"        noxpy_raise_host_{leaf}(data->owner_module, rc, err);")
        lines.append("        noxpy_obj_leave_call(data);")
        lines.append("        return -1;")
        lines.append("    }")
        lines.append("    noxpy_obj_leave_call(data);")
        lines.append("    if (c_ret < 0) {")
        lines.append('        PyErr_SetString(PyExc_ValueError, "__len__() should return >= 0");')
        lines.append("        return -1;")
        lines.append("    }")
        lines.append("    if ((uint64_t)c_ret > (uint64_t)PY_SSIZE_T_MAX) {")
        lines.append('        PyErr_SetString(PyExc_OverflowError, "__len__() overflows Py_ssize_t");')
        lines.append("        return -1;")
        lines.append("    }")
        lines.append("    return (Py_ssize_t)c_ret;")
        lines.append("}")
        lines.append("")

    if iter_m is not None:
        lines.append(f"static PyObject *noxpy_cls_{cname}_iter(PyObject *self) {{")
        lines.append("    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, Py_TYPE(self));")
        lines.append("    NoxHostContext *ctx = NULL;")
        lines.append("    NoxHostHandle h = 0;")
        lines.append(f"    if (noxpy_obj_enter_call(data, {tag_macro}, &ctx, &h) < 0) {{")
        lines.append("        return NULL;")
        lines.append("    }")
        lines.append("    const NoxPyHostOps *ops = data->host_ops;")
        lines.append("    (void)ops;")
        lines.append("    PyObject *owner_mod = data->owner_module;")
        ret_ctype = _c_ret_var_type(iter_m.return_type, iter_m.return_marshal)
        lines.append(f"    {ret_ctype} c_ret = 0;")
        lines.append("    NoxHostError *err = NULL;")
        lines.append("    NoxHostStatus rc;")
        lines.append("    Py_BEGIN_ALLOW_THREADS")
        lines.append(f"    rc = {iter_m.c_symbol}(ctx, h, &c_ret, &err);")
        lines.append("    Py_END_ALLOW_THREADS")
        if iter_m.return_marshal == "handle":
            lines.append("    if (rc != NOX_HOST_OK || c_ret == 0) {")
            lines.append(
                f"        noxpy_raise_host_{leaf}(owner_mod, rc == NOX_HOST_OK ? NOX_HOST_ERR_INVALID_HANDLE : rc, err);"
            )
            lines.append("        noxpy_obj_leave_call(data);")
            lines.append("        return NULL;")
            lines.append("    }")
            lines.append(f"    noxpy_mod_state_{leaf} *mst = (noxpy_mod_state_{leaf} *)PyModule_GetState(owner_mod);")
            ret_pol = _POLICY_MACRO[class_policy_map[iter_m.return_type]]
            lines.append(
                f"    PyObject *py_ret = noxpy_obj_new_from_shared_handle((PyTypeObject *)mst->cls_{iter_m.return_type}, "
                f"owner_mod, data->shared_ctx, c_ret, NOXPY_TAG_{iter_m.return_type}, {ret_pol});"
            )
            lines.append("    noxpy_obj_leave_call(data);")
            lines.append("    return py_ret;")
        else:
            lines.append("    if (rc != NOX_HOST_OK) {")
            lines.append(f"        noxpy_raise_host_{leaf}(owner_mod, rc, err);")
            lines.append("        noxpy_obj_leave_call(data);")
            lines.append("        return NULL;")
            lines.append("    }")
            lines.append("    PyObject *py_ret = noxpy_host_value_to_py_ex(owner_mod, ops, ctx, c_ret);")
            lines.append("    if (c_ret) { (void)ops->value_release(ctx, c_ret); }")
            lines.append("    noxpy_obj_leave_call(data);")
            lines.append("    return py_ret;")
        lines.append("}")
        lines.append("")

    if next_m is not None:
        lines.append(f"static PyObject *noxpy_cls_{cname}_iternext(PyObject *self) {{")
        lines.append("    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, Py_TYPE(self));")
        lines.append("    NoxHostContext *ctx = NULL;")
        lines.append("    NoxHostHandle h = 0;")
        lines.append(f"    if (noxpy_obj_enter_call(data, {tag_macro}, &ctx, &h) < 0) {{")
        lines.append("        return NULL;")
        lines.append("    }")
        lines.append("    const NoxPyHostOps *ops = data->host_ops;")
        lines.append("    (void)ops;")
        lines.append("    PyObject *owner_mod = data->owner_module;")
        ret_ctype = _c_ret_var_type(next_m.return_type, next_m.return_marshal)
        ret_init = "{0}" if ret_ctype == "NoxHostBuffer" else "0"
        lines.append(f"    {ret_ctype} c_ret = {ret_init};")
        lines.append("    NoxHostError *err = NULL;")
        lines.append("    NoxHostStatus rc;")
        lines.append("    Py_BEGIN_ALLOW_THREADS")
        lines.append(f"    rc = {next_m.c_symbol}(ctx, h, &c_ret, &err);")
        lines.append("    Py_END_ALLOW_THREADS")
        lines.append("    if (rc != NOX_HOST_OK) {")
        lines.append("        if (noxpy_error_is_stop_iteration(ops, err)) {")
        lines.append("            if (err && ops->error_release) { ops->error_release(err); }")
        lines.append("            noxpy_obj_leave_call(data);")
        lines.append("            return NULL;")
        lines.append("        }")
        lines.append(f"        noxpy_raise_host_{leaf}(owner_mod, rc, err);")
        lines.append("        noxpy_obj_leave_call(data);")
        lines.append("        return NULL;")
        lines.append("    }")
        if next_m.return_marshal == "handle":
            lines.append("    if (c_ret == 0) {")
            lines.append(f"        noxpy_raise_host_{leaf}(owner_mod, NOX_HOST_ERR_INVALID_HANDLE, NULL);")
            lines.append("        noxpy_obj_leave_call(data);")
            lines.append("        return NULL;")
            lines.append("    }")
            lines.append(f"    noxpy_mod_state_{leaf} *mst = (noxpy_mod_state_{leaf} *)PyModule_GetState(owner_mod);")
            ret_pol = _POLICY_MACRO[class_policy_map[next_m.return_type]]
            lines.append(
                f"    PyObject *py_ret = noxpy_obj_new_from_shared_handle((PyTypeObject *)mst->cls_{next_m.return_type}, "
                f"owner_mod, data->shared_ctx, c_ret, NOXPY_TAG_{next_m.return_type}, {ret_pol});"
            )
            lines.append("    noxpy_obj_leave_call(data);")
            lines.append("    return py_ret;")
        elif next_m.return_marshal == "value":
            lines.append("    PyObject *py_ret = noxpy_host_value_to_py_ex(owner_mod, ops, ctx, c_ret);")
            lines.append("    if (c_ret) { (void)ops->value_release(ctx, c_ret); }")
            lines.append("    noxpy_obj_leave_call(data);")
            lines.append("    return py_ret;")
        elif next_m.return_marshal == "external_ref":
            lines.append("    PyObject *py_ret = noxpy_extref_take_returned(owner_mod, c_ret);")
            lines.append("    noxpy_obj_leave_call(data);")
            lines.append("    return py_ret;")
        else:
            lines.append("    noxpy_obj_leave_call(data);")
            if next_m.return_type == "int":
                lines.append("    return noxpy_i64_to_py(c_ret);")
            elif next_m.return_type == "float":
                lines.append("    return noxpy_f64_to_py(c_ret);")
            elif next_m.return_type == "bool":
                lines.append("    return noxpy_bool_to_py(c_ret);")
            elif next_m.return_type == "str":
                lines.append("    return noxpy_utf8_buffer_to_py(ops, &c_ret);")
            else:
                lines.append("    return noxpy_bytes_buffer_to_py(ops, &c_ret);")
        lines.append("}")
        lines.append("")

    # PyMethodDef for class
    lines.append(f"static PyMethodDef noxpy_cls_{cname}_methods[] = {{")
    for m in regular_methods:
        if m.method_kind in ("staticmethod", "classmethod"):
            flags = "METH_CLASS | METH_FASTCALL | METH_KEYWORDS"
            first_t = "$type"
        else:
            flags = "METH_FASTCALL | METH_KEYWORDS"
            first_t = "$self"
        mdoc_c = _c_escape(_emit_ml_doc_parts(m.name, m.params, m.doc, first_target=first_t))
        lines.append(
            f"    {{ {_c_escape(m.name)}, _PyCFunction_CAST(noxpy_cls_{cname}_m_{m.name}), {flags}, {mdoc_c} }},"
        )
    lines.append('    { "close", noxpy_obj_close_method, METH_NOARGS, NULL },')
    lines.append('    { "__enter__", noxpy_obj_enter_cm, METH_NOARGS, NULL },')
    lines.append('    { "__exit__", _PyCFunction_CAST(noxpy_obj_exit_cm), METH_FASTCALL, NULL },')
    lines.append("    { NULL, NULL, 0, NULL }")
    lines.append("};")
    lines.append("")

    if prop_gets:
        lines.append(f"static PyGetSetDef noxpy_cls_{cname}_getset[] = {{")
        for pname in sorted(prop_gets):
            pg = prop_gets[pname]
            setter_fn = f"noxpy_cls_{cname}_set_{pname}" if pname in prop_sets else "NULL"
            pdoc = _c_escape(pg.doc) if pg.doc else "NULL"
            lines.append(
                f"    {{ {_c_escape(pname)}, noxpy_cls_{cname}_get_{pname}, {setter_fn}, {pdoc}, NULL }},"
            )
        lines.append("    { NULL, NULL, NULL, NULL, NULL }")
        lines.append("};")
        lines.append("")

    lines.append(f"static PyType_Slot noxpy_cls_{cname}_slots[] = {{")
    if cls.doc:
        lines.append(f"    {{ Py_tp_doc, {_c_escape(cls.doc)} }},")
    lines.append(f"    {{ Py_tp_init, noxpy_cls_{cname}_init }},")
    lines.append(f"    {{ Py_tp_methods, noxpy_cls_{cname}_methods }},")
    if prop_gets:
        lines.append(f"    {{ Py_tp_getset, noxpy_cls_{cname}_getset }},")
    lines.append(f"    {{ Py_tp_repr, noxpy_cls_{cname}_repr }},")
    if str_m is not None:
        lines.append(f"    {{ Py_tp_str, noxpy_cls_{cname}_str }},")
    if len_m is not None:
        lines.append(f"    {{ Py_sq_length, noxpy_cls_{cname}_len }},")
    if iter_m is not None:
        lines.append(f"    {{ Py_tp_iter, noxpy_cls_{cname}_iter }},")
    if next_m is not None:
        lines.append(f"    {{ Py_tp_iternext, noxpy_cls_{cname}_iternext }},")
    lines.append("    { Py_tp_hash, PyObject_HashNotImplemented },")
    lines.append("    { Py_tp_traverse, noxpy_obj_traverse },")
    lines.append("    { Py_tp_clear, noxpy_obj_clear },")
    lines.append("    { Py_tp_dealloc, noxpy_obj_dealloc },")
    lines.append("    { 0, NULL }")
    lines.append("};")
    lines.append("")
    lines.append(f"static PyType_Spec noxpy_cls_{cname}_spec = {{")
    lines.append(f"    .name = {_c_escape(f'{leaf}.{cname}')},")
    lines.append("    .basicsize = -(int)sizeof(NoxPyObjectData),")
    lines.append("    .itemsize = 0,")
    lines.append("    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE,")
    lines.append(f"    .slots = noxpy_cls_{cname}_slots,")
    lines.append("};")
    lines.append("")
    return lines


def _emit_c_function_adapter(fn: ExportedFunctionIR, *, use_ex: bool = False) -> list[str]:
    return _emit_module_or_class_callable(
        c_func_name=f"noxpy_wrap_{fn.name}",
        py_name=fn.name,
        c_symbol=fn.c_symbol,
        params=fn.params,
        return_type=fn.return_type,
        return_marshal=fn.return_marshal,
        leaf="unused",
        first_arg_is_cls=False,
        has_phase3_state=False,
        class_policy_map={},
        use_ex=use_ex,
    )


def _format_ml_doc(fn: ExportedFunctionIR) -> str:
    return _emit_ml_doc_parts(fn.name, fn.params, fn.doc, first_target="$module")


def generate_c_adapter(ir: ModuleIR) -> str:
    """Emit deterministic CPython 3.15 `abi3t` C adapter targeting `nox_host.h`."""
    leaf = ir.leaf_name
    has_async = any(fn.is_async for fn in ir.functions) or any(
        m.is_async for c in ir.classes for m in c.methods
    )
    used_bufs = _ir_used_buffer_types(ir)
    has_rich_state = bool(
        ir.classes or ir.exceptions or any(fn.is_async for fn in ir.functions) or used_bufs
    )
    uses_host_obj = _ir_uses_host_object(ir)
    class_policy_map = {cls.name: cls.thread_policy for cls in ir.classes}

    lines: list[str] = [
        f"/* Generated by noxpy from {ir.source} (noxc {ir.compiler_version}). Do not hand-edit. */",
        '#include "noxpy_abi.h"',
        "",
        f"extern NoxHostStatus nox_host_module_init_{leaf}(NoxHostContext *ctx, NoxHostError **err);",
        f"extern void nox_host_module_fini_{leaf}(NoxHostContext *ctx);",
        "",
        f"static const NoxPyHostOps noxpy_host_ops_{leaf} = {{",
        "    .struct_size = (uint32_t)sizeof(NoxPyHostOps),",
        "    .abi_version = NOX_HOST_ABI_VERSION,",
        "    .context_create = nox_host_context_create,",
        "    .context_destroy = nox_host_context_destroy,",
        f"    .module_init = nox_host_module_init_{leaf},",
        f"    .module_fini = nox_host_module_fini_{leaf},",
        "    .buffer_release = nox_host_buffer_release,",
        "    .error_release = nox_host_error_release,",
        "    .error_line = nox_host_error_line,",
        "    .error_class_name = nox_host_error_class_name,",
        "    .error_message = nox_host_error_message,",
        "    .value_none = nox_host_value_none,",
        "    .value_int = nox_host_value_int,",
        "    .value_float = nox_host_value_float,",
        "    .value_bool = nox_host_value_bool,",
        "    .value_str = nox_host_value_str,",
        "    .value_container = nox_host_value_container,",
        "    .value_append = nox_host_value_append,",
        "    .value_dict_set = nox_host_value_dict_set,",
        "    .value_kind = nox_host_value_kind,",
        "    .value_get_int = nox_host_value_get_int,",
        "    .value_get_float = nox_host_value_get_float,",
        "    .value_get_span = nox_host_value_get_span,",
        "    .value_len = nox_host_value_len,",
        "    .value_item = nox_host_value_item,",
        "    .value_dict_entry = nox_host_value_dict_entry,",
        "    .value_release = nox_host_value_release,",
        "    .handle_retain = nox_host_handle_retain,",
        "    .handle_release = nox_host_handle_release,",
        "    .handle_is_valid = nox_host_handle_is_valid,",
        "    .handle_type_name = nox_host_handle_type_name,",
        "    .value_get_handle = nox_host_value_get_handle,",
        "    .context_set_callbacks = nox_host_context_set_callbacks,",
        "    .error_host_token = nox_host_error_host_token,",
        "    .value_external_ref = nox_host_value_external_ref,",
        "    .value_get_external_ref = nox_host_value_get_external_ref,",
        "    .task_cancel = nox_host_task_cancel,",
        "    .task_take_result = nox_host_task_take_result,",
        "    .task_release = nox_host_task_release,",
        "    .handle_pin_buffer = nox_host_handle_pin_buffer,",
        "    .buffer_unpin = nox_host_buffer_unpin,",
        "    .value_typed_array = nox_host_value_typed_array,",
        "    .value_get_typed_array = nox_host_value_get_typed_array,",
        "    .capabilities = nox_host_capabilities,",
        "};",
        "",
    ]

    if not has_rich_state:
        if uses_host_obj:
            lines.append(f"static void noxpy_raise_host_{leaf}(PyObject *module, NoxHostStatus rc, NoxHostError *err) {{")
            lines.append(
                f"    noxpy_set_exception_from_host_mod(module, &noxpy_host_ops_{leaf}, rc, err, NULL, 0);"
            )
            lines.append("}")
            lines.append("")
        for fn in ir.functions:
            lines.extend(_emit_c_function_adapter(fn, use_ex=uses_host_obj))

        lines.append(f"static PyMethodDef noxpy_methods_{leaf}[] = {{")
        for fn in ir.functions:
            doc_c = _c_escape(_format_ml_doc(fn))
            lines.append(
                f"    {{ {_c_escape(fn.name)}, _PyCFunction_CAST(noxpy_wrap_{fn.name}), METH_FASTCALL | METH_KEYWORDS, {doc_c} }},"
            )
        lines.append("    { NULL, NULL, 0, NULL }")
        lines.append("};")
        lines.append("")
        lines.extend(
            [
                f"static int noxpy_exec_{leaf}(PyObject *module) {{",
                "    NoxPyModuleState *st = (NoxPyModuleState *)PyModule_GetState(module);",
                "    if (!st) {",
                '        PyErr_SetString(PyExc_RuntimeError, "noxpy: missing module state");',
                "        return -1;",
                "    }",
                f"    return noxpy_module_state_init(st, &noxpy_host_ops_{leaf});",
                "}",
                "",
                f"static void noxpy_free_{leaf}(void *module) {{",
                "    NoxPyModuleState *st = (NoxPyModuleState *)PyModule_GetState((PyObject *)module);",
                "    if (st) {",
                "        noxpy_module_state_close(st);",
                "    }",
                "}",
                "",
                f"PyABIInfo_VAR(noxpy_abi_info_{leaf});",
                "",
                f"static PySlot noxpy_slots_{leaf}[] = {{",
                f"    PySlot_STATIC_DATA(Py_mod_abi, &noxpy_abi_info_{leaf}),",
                f"    PySlot_STATIC_DATA(Py_mod_name, {_c_escape(leaf)}),",
                "    PySlot_SIZE(Py_mod_state_size, sizeof(NoxPyModuleState)),",
                f"    PySlot_STATIC_DATA(Py_mod_methods, noxpy_methods_{leaf}),",
                f"    PySlot_FUNC(Py_mod_exec, noxpy_exec_{leaf}),",
                f"    PySlot_FUNC(Py_mod_state_free, noxpy_free_{leaf}),",
                "    PySlot_DATA(Py_mod_gil, Py_MOD_GIL_NOT_USED),",
                "    PySlot_DATA(Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED),",
                "    PySlot_END",
                "};",
                "",
                "PyMODEXPORT_FUNC",
                f"PyModExport_{leaf}(void) {{",
                f"    return noxpy_slots_{leaf};",
                "}",
                "",
            ]
        )
        return "\n".join(lines)

    # Phase 3 rich module state + custom exception mapper + heap types
    for cls in ir.classes:
        lines.append(f"#define NOXPY_TAG_{cls.name} {_fnv1a_u64(cls.export_id)}")
    if ir.classes:
        lines.append("")

    lines.append(f"typedef struct noxpy_mod_state_{leaf} {{")
    lines.append("    NoxPyModuleState base;")
    for cls in ir.classes:
        lines.append(f"    PyObject *cls_{cls.name};")
    for exc in ir.exceptions:
        lines.append(f"    PyObject *exc_{exc.name};")
    lines.append(f"}} noxpy_mod_state_{leaf};")
    lines.append("")

    lines.append(f"static void noxpy_raise_host_{leaf}(PyObject *module, NoxHostStatus rc, NoxHostError *err) {{")
    if ir.exceptions:
        lines.append(f"    noxpy_mod_state_{leaf} *mst = module ? (noxpy_mod_state_{leaf} *)PyModule_GetState(module) : NULL;")
        lines.append("    if (!mst) {")
        lines.append(f"        noxpy_set_exception_from_host(&noxpy_host_ops_{leaf}, rc, err);")
        lines.append("        return;")
        lines.append("    }")
        lines.append("    const NoxPyCustomExc custom_excs[] = {")
        for exc in ir.exceptions:
            lines.append(f"        {{ {_c_escape(exc.name)}, mst->exc_{exc.name} }},")
        lines.append("    };")
        lines.append(
            f"    noxpy_set_exception_from_host_mod(module, &noxpy_host_ops_{leaf}, rc, err, custom_excs, sizeof(custom_excs) / sizeof(custom_excs[0]));"
        )
    else:
        lines.append(f"    noxpy_set_exception_from_host_mod(module, &noxpy_host_ops_{leaf}, rc, err, NULL, 0);")
    lines.append("}")
    lines.append("")

    for fn in ir.functions:
        if fn.is_async:
            lines.extend(
                _emit_async_callable(
                    c_func_name=f"noxpy_wrap_{fn.name}",
                    py_name=fn.name,
                    c_symbol=fn.c_symbol,
                    params=fn.params,
                    return_type=fn.return_type,
                    return_marshal=fn.return_marshal,
                    leaf=leaf,
                    first_arg_is_cls=False,
                    is_instance_method=False,
                    owner_cls_name=None,
                    exceptions=ir.exceptions,
                    class_policy_map=class_policy_map,
                )
            )
        else:
            lines.extend(
                _emit_module_or_class_callable(
                    c_func_name=f"noxpy_wrap_{fn.name}",
                    py_name=fn.name,
                    c_symbol=fn.c_symbol,
                    params=fn.params,
                    return_type=fn.return_type,
                    return_marshal=fn.return_marshal,
                    leaf=leaf,
                    first_arg_is_cls=False,
                    has_phase3_state=True,
                    class_policy_map=class_policy_map,
                    use_ex=True,
                )
            )

    for cls in ir.classes:
        lines.extend(
            _emit_class_c_wrappers(
                cls,
                leaf=leaf,
                class_policy_map=class_policy_map,
                exceptions=ir.exceptions,
            )
        )

    lines.append(f"static PyMethodDef noxpy_methods_{leaf}[] = {{")
    for fn in ir.functions:
        doc_c = _c_escape(_format_ml_doc(fn))
        lines.append(
            f"    {{ {_c_escape(fn.name)}, _PyCFunction_CAST(noxpy_wrap_{fn.name}), METH_FASTCALL | METH_KEYWORDS, {doc_c} }},"
        )
    lines.append("    { NULL, NULL, 0, NULL }")
    lines.append("};")
    lines.append("")

    topo_excs = _topo_sort_exceptions(ir.exceptions)
    lines.extend(
        [
            f"static int noxpy_exec_{leaf}(PyObject *module) {{",
            f"    noxpy_mod_state_{leaf} *mst = (noxpy_mod_state_{leaf} *)PyModule_GetState(module);",
            "    if (!mst) {",
            '        PyErr_SetString(PyExc_RuntimeError, "noxpy: missing module state");',
            "        return -1;",
            "    }",
        ]
    )
    for cls in ir.classes:
        lines.append(f"    mst->cls_{cls.name} = NULL;")
    for exc in ir.exceptions:
        lines.append(f"    mst->exc_{exc.name} = NULL;")
    if has_async:
        lines.append(f"    if (noxpy_module_state_init_mod(module, &mst->base, &noxpy_host_ops_{leaf}) < 0) {{")
    else:
        lines.append(f"    if (noxpy_module_state_init(&mst->base, &noxpy_host_ops_{leaf}) < 0) {{")
    lines.append("        return -1;")
    lines.append("    }")
    if used_bufs:
        lines.append("    if (noxpy_buffers_init_module(module, &mst->base) < 0) {")
        lines.append("        return -1;")
        lines.append("    }")
        for raw_buf in used_bufs:
            py_buf, bmacro, _, _ = _BUILTIN_BUFFER_MAP[raw_buf]
            lines.append(
                f"    if (PyModule_AddObjectRef(module, {_c_escape(py_buf)}, noxpy_buf_get_type(module, {bmacro})) < 0) {{"
            )
            lines.append("        return -1;")
            lines.append("    }")
    for exc in topo_excs:
        base_expr = f"mst->exc_{exc.base}" if exc.base not in _STD_PYEXC_MAP else _STD_PYEXC_MAP[exc.base]
        doc_expr = _c_escape(exc.doc) if exc.doc else "NULL"
        lines.append(
            f"    mst->exc_{exc.name} = PyErr_NewExceptionWithDoc({_c_escape(f'{leaf}.{exc.name}')}, {doc_expr}, {base_expr}, NULL);"
        )
        lines.append(
            f"    if (!mst->exc_{exc.name} || PyModule_AddObjectRef(module, {_c_escape(exc.name)}, mst->exc_{exc.name}) < 0) {{"
        )
        lines.append("        return -1;")
        lines.append("    }")
    for cls in ir.classes:
        lines.append(
            f"    mst->cls_{cls.name} = PyType_FromModuleAndSpec(module, &noxpy_cls_{cls.name}_spec, NULL);"
        )
        lines.append(
            f"    if (!mst->cls_{cls.name} || PyModule_AddObjectRef(module, {_c_escape(cls.name)}, mst->cls_{cls.name}) < 0) {{"
        )
        lines.append("        return -1;")
        lines.append("    }")
    lines.extend(
        [
            "    return 0;",
            "}",
            "",
            f"static int noxpy_traverse_{leaf}(PyObject *module, visitproc visit, void *arg) {{",
            f"    noxpy_mod_state_{leaf} *mst = (noxpy_mod_state_{leaf} *)PyModule_GetState(module);",
            "    if (mst) {",
        ]
    )
    for cls in ir.classes:
        lines.append(f"        Py_VISIT(mst->cls_{cls.name});")
    for exc in ir.exceptions:
        lines.append(f"        Py_VISIT(mst->exc_{exc.name});")
    lines.extend(
        [
            "        if (noxpy_module_state_traverse(&mst->base, visit, arg) < 0) { return -1; }",
            "    }",
            "    return 0;",
            "}",
            "",
            f"static int noxpy_clear_{leaf}(PyObject *module) {{",
            f"    noxpy_mod_state_{leaf} *mst = (noxpy_mod_state_{leaf} *)PyModule_GetState(module);",
            "    if (mst) {",
        ]
    )
    for cls in ir.classes:
        lines.append(f"        Py_CLEAR(mst->cls_{cls.name});")
    for exc in ir.exceptions:
        lines.append(f"        Py_CLEAR(mst->exc_{exc.name});")
    lines.extend(
        [
            "        noxpy_module_state_clear(&mst->base);",
            "    }",
            "    return 0;",
            "}",
            "",
            f"static void noxpy_free_{leaf}(void *module) {{",
            f"    noxpy_mod_state_{leaf} *mst = (noxpy_mod_state_{leaf} *)PyModule_GetState((PyObject *)module);",
            "    if (mst) {",
        ]
    )
    for cls in ir.classes:
        lines.append(f"        Py_CLEAR(mst->cls_{cls.name});")
    for exc in ir.exceptions:
        lines.append(f"        Py_CLEAR(mst->exc_{exc.name});")
    lines.extend(
        [
            "        noxpy_module_state_close(&mst->base);",
            "    }",
            "}",
            "",
            f"PyABIInfo_VAR(noxpy_abi_info_{leaf});",
            "",
            f"static PySlot noxpy_slots_{leaf}[] = {{",
            f"    PySlot_STATIC_DATA(Py_mod_abi, &noxpy_abi_info_{leaf}),",
            f"    PySlot_STATIC_DATA(Py_mod_name, {_c_escape(leaf)}),",
            f"    PySlot_SIZE(Py_mod_state_size, sizeof(noxpy_mod_state_{leaf})),",
            f"    PySlot_STATIC_DATA(Py_mod_methods, noxpy_methods_{leaf}),",
            f"    PySlot_FUNC(Py_mod_exec, noxpy_exec_{leaf}),",
            f"    PySlot_FUNC(Py_mod_state_traverse, noxpy_traverse_{leaf}),",
            f"    PySlot_FUNC(Py_mod_state_clear, noxpy_clear_{leaf}),",
            f"    PySlot_FUNC(Py_mod_state_free, noxpy_free_{leaf}),",
            "    PySlot_DATA(Py_mod_gil, Py_MOD_GIL_NOT_USED),",
            "    PySlot_DATA(Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED),",
            "    PySlot_END",
            "};",
            "",
            "PyMODEXPORT_FUNC",
            f"PyModExport_{leaf}(void) {{",
            f"    return noxpy_slots_{leaf};",
            "}",
            "",
        ]
    )
    return "\n".join(lines)
