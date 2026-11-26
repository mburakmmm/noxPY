"""Phase 3 Slice 3A test: real Nox NoxHostHandle + CPython 3.15 abi3t heap types, GC, & 3.15t concurrency."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.audit import audit_extension_binary  # noqa: E402


def _find_noxc() -> tuple[Path, Path, Path] | None:
    cand = shutil.which("noxc") or str(Path.home() / ".nox-lang" / "bin" / "noxc")
    p = Path(cand)
    if not (p.is_file() and os.access(p, os.X_OK)):
        return None
    p = p.resolve()
    prefix = p.parent.parent
    noxrt = prefix / "lib" / "noxrt.o"
    nox_inc = prefix / "include"
    if not noxrt.is_file() or not (nox_inc / "nox_host.h").is_file():
        return None
    return p, noxrt, nox_inc


def _find_315t() -> str | None:
    candidates = [
        os.environ.get("NOXPY_PYTHON_315T", ""),
        shutil.which("python3.15t") or "",
        str(Path.home() / ".local" / "bin" / "python3.15t"),
        "/Library/Frameworks/PythonT.framework/Versions/3.15/bin/python3.15t",
    ]
    uv = shutil.which("uv")
    if uv:
        probe = subprocess.run([uv, "python", "find", "3.15t"], capture_output=True, text=True)
        if probe.returncode == 0 and probe.stdout.strip():
            candidates.append(probe.stdout.strip())
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


NOX_COUNTER_SRC = """
class Counter:
    value: int

    def __init__(self, start: int) -> None:
        self.value = start


@export
def make_counter(start: int) -> Counter:
    if start < 0:
        raise ValueError("start must be non-negative")
    return Counter(start)


@export
def counter_inc(c: Counter, delta: int) -> int:
    c.value = c.value + delta
    return c.value


@export
def counter_get(c: Counter) -> int:
    return c.value


@export
def counter_work_loop(c: Counter, iters: int) -> int:
    acc: int = 0
    i: int = 0
    while i < iters:
        acc = acc + ((i * 7 + 3) % 13)
        i += 1
    c.value = c.value + acc
    return c.value
"""

C_EXT_SRC = r"""
#include "noxpy_abi.h"

extern NoxHostStatus nox_host_module_init__noxpy_p3_test(NoxHostContext *ctx, NoxHostError **err);
extern void nox_host_module_fini__noxpy_p3_test(NoxHostContext *ctx);
extern NoxHostStatus nox_host_export__noxpy_p3_test_make_counter(
    NoxHostContext *ctx, int64_t start, NoxHostHandle *out, NoxHostError **err);
extern NoxHostStatus nox_host_export__noxpy_p3_test_counter_inc(
    NoxHostContext *ctx, NoxHostHandle c, int64_t delta, int64_t *out, NoxHostError **err);
extern NoxHostStatus nox_host_export__noxpy_p3_test_counter_get(
    NoxHostContext *ctx, NoxHostHandle c, int64_t *out, NoxHostError **err);
extern NoxHostStatus nox_host_export__noxpy_p3_test_counter_work_loop(
    NoxHostContext *ctx, NoxHostHandle c, int64_t iters, int64_t *out, NoxHostError **err);

static const NoxPyHostOps g_p3_ops = {
    .struct_size = (uint32_t)sizeof(NoxPyHostOps),
    .abi_version = NOX_HOST_ABI_VERSION,
    .context_create = nox_host_context_create,
    .context_destroy = nox_host_context_destroy,
    .module_init = nox_host_module_init__noxpy_p3_test,
    .module_fini = nox_host_module_fini__noxpy_p3_test,
    .buffer_release = nox_host_buffer_release,
    .error_release = nox_host_error_release,
    .error_line = nox_host_error_line,
    .error_class_name = nox_host_error_class_name,
    .error_message = nox_host_error_message,
    .handle_retain = nox_host_handle_retain,
    .handle_release = nox_host_handle_release,
    .handle_is_valid = nox_host_handle_is_valid,
};

#define COUNTER_TYPE_TAG 0x434F554E54455231ULL

typedef struct P3ModState {
    NoxPyModuleState base;
    PyObject *counter_type;
    PyObject *counter_error;
} P3ModState;

static int parse_policy_obj(PyObject *pol_obj, uint8_t *out_pol) {
    if (!pol_obj || !PyUnicode_Check(pol_obj)) {
        PyErr_SetString(PyExc_TypeError, "policy must be str");
        return -1;
    }
    if (PyUnicode_CompareWithASCIIString(pol_obj, "instance_locked") == 0) {
        *out_pol = NOXPY_POLICY_INSTANCE_LOCKED;
        return 0;
    }
    if (PyUnicode_CompareWithASCIIString(pol_obj, "parallel") == 0) {
        *out_pol = NOXPY_POLICY_PARALLEL;
        return 0;
    }
    if (PyUnicode_CompareWithASCIIString(pol_obj, "thread_affine") == 0) {
        *out_pol = NOXPY_POLICY_THREAD_AFFINE;
        return 0;
    }
    if (!PyErr_Occurred()) {
        PyErr_SetString(PyExc_ValueError, "unknown thread policy (expected instance_locked|parallel|thread_affine)");
    }
    return -1;
}

static int Counter_init(PyObject *self, PyObject *args, PyObject *kwds) {
    Py_ssize_t nargs = PyTuple_Size(args);
    if (nargs != 1) {
        PyErr_Format(PyExc_TypeError, "Counter() takes 1 positional argument (start), got %zd", nargs);
        return -1;
    }
    int64_t start = 0;
    if (noxpy_py_to_i64_strict(PyTuple_GetItem(args, 0), &start, "start") < 0) {
        return -1;
    }
    uint8_t policy = NOXPY_POLICY_INSTANCE_LOCKED;
    if (kwds) {
        Py_ssize_t kw_sz = PyDict_Size(kwds);
        if (kw_sz < 0) {
            return -1;
        }
        PyObject *pol_obj = NULL;
        int found = PyDict_GetItemStringRef(kwds, "policy", &pol_obj);
        if (found < 0) {
            return -1;
        }
        if (found == 1) {
            int ok = parse_policy_obj(pol_obj, &policy);
            Py_DECREF(pol_obj);
            if (ok < 0 || kw_sz > 1) {
                if (ok == 0) {
                    PyErr_SetString(PyExc_TypeError, "Counter() got an unexpected keyword argument");
                }
                return -1;
            }
        } else if (kw_sz > 0) {
            PyErr_SetString(PyExc_TypeError, "Counter() got an unexpected keyword argument");
            return -1;
        }
    }

    PyTypeObject *cls = Py_TYPE(self);
    PyObject *mod = PyType_GetModule(cls);
    if (!mod) {
        PyErr_SetString(PyExc_SystemError, "noxpy: Counter type missing defining module");
        return -1;
    }
    NoxPyModuleState *mst = (NoxPyModuleState *)PyModule_GetState(mod);
    if (!mst || noxpy_module_lease_acquire(mst) < 0) {
        return -1;
    }

    NoxHostContext *ctx = NULL;
    if (noxpy_host_context_create_and_init(&g_p3_ops, &ctx) < 0) {
        noxpy_module_lease_release(mst);
        return -1;
    }

    NoxHostHandle h = 0;
    NoxHostError *err = NULL;
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    rc = nox_host_export__noxpy_p3_test_make_counter(ctx, start, &h, &err);
    Py_END_ALLOW_THREADS
    if (rc != NOX_HOST_OK || h == 0) {
        noxpy_set_exception_from_host(&g_p3_ops, rc == NOX_HOST_OK ? NOX_HOST_ERR_INVALID_HANDLE : rc, err);
        noxpy_host_context_fini_and_destroy(&g_p3_ops, ctx);
        noxpy_module_lease_release(mst);
        return -1;
    }

    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, cls);
    if (!data) {
        (void)g_p3_ops.handle_release(ctx, h);
        noxpy_host_context_fini_and_destroy(&g_p3_ops, ctx);
        noxpy_module_lease_release(mst);
        return -1;
    }
    /* Re-init guard if __init__ called twice on same instance */
    if (data->initialized) {
        noxpy_obj_close(data);
    }
    int init_rc = noxpy_obj_init(data, mod, &g_p3_ops, ctx, h, COUNTER_TYPE_TAG, policy);
    noxpy_module_lease_release(mst);
    if (init_rc < 0) {
        (void)g_p3_ops.handle_release(ctx, h);
        noxpy_host_context_fini_and_destroy(&g_p3_ops, ctx);
        return -1;
    }
    return 0;
}

static int read_counter_value(PyObject *self, int64_t *out_val) {
    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, Py_TYPE(self));
    NoxHostContext *ctx = NULL;
    NoxHostHandle h = 0;
    if (noxpy_obj_enter_call(data, COUNTER_TYPE_TAG, &ctx, &h) < 0) {
        return -1;
    }
    int64_t v = 0;
    NoxHostError *err = NULL;
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    rc = nox_host_export__noxpy_p3_test_counter_get(ctx, h, &v, &err);
    Py_END_ALLOW_THREADS
    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(&g_p3_ops, rc, err);
        noxpy_obj_leave_call(data);
        return -1;
    }
    noxpy_obj_leave_call(data);
    *out_val = v;
    return 0;
}

static PyObject *Counter_inc(PyObject *self, PyObject *arg) {
    int64_t delta = 0;
    if (noxpy_py_to_i64_strict(arg, &delta, "delta") < 0) {
        return NULL;
    }
    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, Py_TYPE(self));
    NoxHostContext *ctx = NULL;
    NoxHostHandle h = 0;
    if (noxpy_obj_enter_call(data, COUNTER_TYPE_TAG, &ctx, &h) < 0) {
        return NULL;
    }
    int64_t res = 0;
    NoxHostError *err = NULL;
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    rc = nox_host_export__noxpy_p3_test_counter_inc(ctx, h, delta, &res, &err);
    Py_END_ALLOW_THREADS
    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(&g_p3_ops, rc, err);
        noxpy_obj_leave_call(data);
        return NULL;
    }
    noxpy_obj_leave_call(data);
    return noxpy_i64_to_py(res);
}

static PyObject *Counter_get(PyObject *self, PyObject *ignored) {
    (void)ignored;
    int64_t v = 0;
    if (read_counter_value(self, &v) < 0) {
        return NULL;
    }
    return noxpy_i64_to_py(v);
}

static PyObject *Counter_work_loop(PyObject *self, PyObject *arg) {
    int64_t iters = 0;
    if (noxpy_py_to_i64_strict(arg, &iters, "iters") < 0) {
        return NULL;
    }
    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, Py_TYPE(self));
    NoxHostContext *ctx = NULL;
    NoxHostHandle h = 0;
    if (noxpy_obj_enter_call(data, COUNTER_TYPE_TAG, &ctx, &h) < 0) {
        return NULL;
    }
    NoxPyModuleState *mst = (NoxPyModuleState *)PyModule_GetState(data->owner_module);
    int64_t res = 0;
    NoxHostError *err = NULL;
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    noxpy_module_inflight_enter(mst);
    rc = nox_host_export__noxpy_p3_test_counter_work_loop(ctx, h, iters, &res, &err);
    noxpy_module_inflight_exit(mst);
    Py_END_ALLOW_THREADS
    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(&g_p3_ops, rc, err);
        noxpy_obj_leave_call(data);
        return NULL;
    }
    noxpy_obj_leave_call(data);
    return noxpy_i64_to_py(res);
}

static PyObject *Counter_get_value(PyObject *self, void *closure) {
    (void)closure;
    int64_t v = 0;
    if (read_counter_value(self, &v) < 0) {
        return NULL;
    }
    return noxpy_i64_to_py(v);
}

static PyObject *Counter_repr(PyObject *self) {
    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, Py_TYPE(self));
    if (noxpy_obj_is_closed(data)) {
        return PyUnicode_FromFormat("<Counter (closed)>");
    }
    int64_t v = 0;
    if (read_counter_value(self, &v) < 0) {
        return NULL;
    }
    return PyUnicode_FromFormat("Counter(%lld)", (long long)v);
}

static PyObject *Counter_richcompare(PyObject *self, PyObject *other, int op) {
    if (op != Py_EQ && op != Py_NE) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (!Py_IS_TYPE(other, Py_TYPE(self))) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    /* Read both sequentially without holding two instance locks concurrently (`threading.md`). */
    int64_t v1 = 0;
    if (read_counter_value(self, &v1) < 0) {
        return NULL;
    }
    int64_t v2 = 0;
    if (read_counter_value(other, &v2) < 0) {
        return NULL;
    }
    int eq = (v1 == v2);
    return PyBool_FromLong((op == Py_EQ) ? eq : !eq);
}

static PyMethodDef Counter_methods[] = {
    {"inc", Counter_inc, METH_O, NULL},
    {"get", Counter_get, METH_NOARGS, NULL},
    {"work_loop", Counter_work_loop, METH_O, NULL},
    {"close", noxpy_obj_close_method, METH_NOARGS, NULL},
    {"__enter__", noxpy_obj_enter_cm, METH_NOARGS, NULL},
    {"__exit__", _PyCFunction_CAST(noxpy_obj_exit_cm), METH_FASTCALL, NULL},
    {NULL, NULL, 0, NULL}
};

static PyGetSetDef Counter_getset[] = {
    {"value", Counter_get_value, NULL, "Current integer counter value.", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyType_Slot Counter_slots[] = {
    {Py_tp_doc, "Native Counter wrapper over NoxHostHandle."},
    {Py_tp_init, Counter_init},
    {Py_tp_methods, Counter_methods},
    {Py_tp_getset, Counter_getset},
    {Py_tp_repr, Counter_repr},
    {Py_tp_richcompare, Counter_richcompare},
    {Py_tp_hash, PyObject_HashNotImplemented},
    {Py_tp_traverse, noxpy_obj_traverse},
    {Py_tp_clear, noxpy_obj_clear},
    {Py_tp_dealloc, noxpy_obj_dealloc},
    {0, NULL}
};

static PyType_Spec Counter_spec = {
    .name = "_noxpy_p3_test.Counter",
    .basicsize = -(int)sizeof(NoxPyObjectData),
    .itemsize = 0,
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = Counter_slots,
};

static PyObject *mod_peak_in_flight(PyObject *module, PyObject *ignored) {
    (void)ignored;
    P3ModState *st = (P3ModState *)PyModule_GetState(module);
    return noxpy_i64_to_py((int64_t)noxpy_module_peak_in_flight(&st->base));
}

static PyObject *mod_reset_peak_in_flight(PyObject *module, PyObject *ignored) {
    (void)ignored;
    P3ModState *st = (P3ModState *)PyModule_GetState(module);
    noxpy_module_reset_peak_in_flight(&st->base);
    Py_RETURN_NONE;
}

static PyMethodDef p3_methods[] = {
    {"peak_in_flight", mod_peak_in_flight, METH_NOARGS, NULL},
    {"reset_peak_in_flight", mod_reset_peak_in_flight, METH_NOARGS, NULL},
    {NULL, NULL, 0, NULL}
};

static int p3_exec(PyObject *module) {
    P3ModState *st = (P3ModState *)PyModule_GetState(module);
    if (!st) {
        PyErr_SetString(PyExc_RuntimeError, "noxpy: missing P3ModState");
        return -1;
    }
    st->counter_type = NULL;
    st->counter_error = NULL;
    if (noxpy_module_state_init(&st->base, &g_p3_ops) < 0) {
        return -1;
    }
    st->counter_error = PyErr_NewExceptionWithDoc("_noxpy_p3_test.CounterError", "Counter error.", NULL, NULL);
    if (!st->counter_error || PyModule_AddObjectRef(module, "CounterError", st->counter_error) < 0) {
        return -1;
    }
    st->counter_type = PyType_FromModuleAndSpec(module, &Counter_spec, NULL);
    if (!st->counter_type || PyModule_AddObjectRef(module, "Counter", st->counter_type) < 0) {
        return -1;
    }
    return 0;
}

static int p3_traverse(PyObject *module, visitproc visit, void *arg) {
    P3ModState *st = (P3ModState *)PyModule_GetState(module);
    if (st) {
        Py_VISIT(st->counter_type);
        Py_VISIT(st->counter_error);
    }
    return 0;
}

static int p3_clear(PyObject *module) {
    P3ModState *st = (P3ModState *)PyModule_GetState(module);
    if (st) {
        Py_CLEAR(st->counter_type);
        Py_CLEAR(st->counter_error);
    }
    return 0;
}

static void p3_free(void *module) {
    P3ModState *st = (P3ModState *)PyModule_GetState((PyObject *)module);
    if (st) {
        Py_CLEAR(st->counter_type);
        Py_CLEAR(st->counter_error);
        noxpy_module_state_close(&st->base);
    }
}

PyABIInfo_VAR(p3_abi);

static PySlot p3_slots[] = {
    PySlot_STATIC_DATA(Py_mod_abi, &p3_abi),
    PySlot_STATIC_DATA(Py_mod_name, "_noxpy_p3_test"),
    PySlot_SIZE(Py_mod_state_size, sizeof(P3ModState)),
    PySlot_STATIC_DATA(Py_mod_methods, p3_methods),
    PySlot_FUNC(Py_mod_exec, p3_exec),
    PySlot_FUNC(Py_mod_state_traverse, p3_traverse),
    PySlot_FUNC(Py_mod_state_clear, p3_clear),
    PySlot_FUNC(Py_mod_state_free, p3_free),
    PySlot_DATA(Py_mod_gil, Py_MOD_GIL_NOT_USED),
    PySlot_DATA(Py_mod_multiple_interpreters, Py_MOD_MULTIPLE_INTERPRETERS_NOT_SUPPORTED),
    PySlot_END
};

PyMODEXPORT_FUNC
PyModExport__noxpy_p3_test(void) {
    return p3_slots;
}
"""


class TestPhase3HeapTypes(unittest.TestCase):
    def test_heap_type_handles_gc_and_315t_concurrency(self) -> None:
        if sys.platform not in ("darwin",) and not sys.platform.startswith("linux"):
            self.skipTest(f"Native Phase 3 build test targets macOS/Linux; got {sys.platform}")

        tc = _find_noxc()
        py315t = _find_315t()
        if not tc:
            self.skipTest("noxc >= 2.2.87 toolchain not installed")

        noxc_p, noxrt_p, nox_inc = tc
        cc = shutil.which("clang") or shutil.which("cc")
        py_inc = sysconfig.get_path("include")
        assert cc and py_inc

        with tempfile.TemporaryDirectory(prefix="noxpy-p3-") as td:
            work = Path(td)
            nox_src = work / "counter_mod.nox"
            nox_obj = work / "counter.o"
            c_src = work / "p3_ext.c"
            so_path = work / "_noxpy_p3_test.abi3t.so"

            nox_src.write_text(NOX_COUNTER_SRC, encoding="utf-8")
            c_src.write_text(C_EXT_SRC, encoding="utf-8")

            res_nox = subprocess.run(
                [
                    str(noxc_p),
                    "build",
                    "--backend",
                    "llvm",
                    "--host-abi",
                    "--module-name",
                    "_noxpy_p3_test",
                    "--emit-obj",
                    "-o",
                    str(nox_obj),
                    str(nox_src),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_nox.returncode, 0, res_nox.stderr)

            cflags = [
                cc,
                "-std=c11",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-fPIC",
                "-fvisibility=hidden",
                f"-I{ROOT / 'native' / 'include'}",
                f"-I{nox_inc}",
                f"-I{py_inc}",
            ]
            if sys.platform == "darwin":
                exp = work / "exp.txt"
                exp.write_text("_PyModExport__noxpy_p3_test\n", encoding="utf-8")
                cflags.extend(
                    [
                        "-mmacosx-version-min=14.0",
                        "-bundle",
                        "-undefined",
                        "dynamic_lookup",
                        f"-Wl,-exported_symbols_list,{exp}",
                    ]
                )
            else:
                ver = work / "exp.ver"
                ver.write_text("{\n  global: PyModExport__noxpy_p3_test;\n  local: *;\n};\n", encoding="utf-8")
                cflags.extend(["-shared", f"-Wl,--version-script,{ver}", "-pthread"])

            res_cc = subprocess.run(
                [
                    *cflags,
                    "-o",
                    str(so_path),
                    str(c_src),
                    str(ROOT / "native" / "cpython" / "noxpy_shim.c"),
                    str(nox_obj),
                    str(noxrt_p),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_cc.returncode, 0, f"clang failed:\n{res_cc.stderr}")

            audit = audit_extension_binary(so_path, "PyModExport__noxpy_p3_test")
            self.assertTrue(audit.exports)

            verify_script = """
import concurrent.futures, gc, sys, threading
import _noxpy_p3_test as m

# Custom exception + construction + methods + properties + repr + eq + immutability + unhashable
assert issubclass(m.CounterError, Exception)
c = m.Counter(10)
assert c.value == 10
assert c.get() == 10
assert c.inc(5) == 15
assert repr(c) == "Counter(15)"
try:
    c.value = 99
    raise AssertionError("expected AttributeError")
except AttributeError:
    pass

try:
    hash(c)
    raise AssertionError("expected TypeError (unhashable)")
except TypeError:
    pass

try:
    class Sub(m.Counter): pass
    raise AssertionError("expected TypeError on Py_TPFLAGS_IMMUTABLETYPE")
except TypeError:
    pass

c2 = m.Counter(15)
assert c == c2
assert not (c != c2)
assert (c == "other") is False
assert (c != "other") is True
try:
    _ = (c < c2)
    raise AssertionError("expected TypeError on unsupported ordering")
except TypeError:
    pass
c2.close()

try:
    m.Counter(-1)
    raise AssertionError("expected ValueError from Nox constructor")
except ValueError as e:
    assert "non-negative" in str(e)

# Idempotent close + use-after-close rejection
c.close()
c.close()
assert repr(c) == "<Counter (closed)>"
for op in (lambda: c.get(), lambda: c.inc(1), lambda: c.value, lambda: c == m.Counter(0)):
    try:
        op()
        raise AssertionError("expected ValueError on closed object")
    except ValueError:
        pass

# Context manager cleanup on normal return & exception
with m.Counter(5) as cm:
    assert cm.inc(2) == 7
try:
    cm.get()
    raise AssertionError("expected ValueError after with exit")
except ValueError:
    pass

try:
    with m.Counter(3) as cm2:
        assert cm2.value == 3
        raise RuntimeError("abort_with")
except RuntimeError:
    pass
try:
    cm2.get()
    raise AssertionError("expected ValueError after exception in with")
except ValueError:
    pass

# GC cycle cleanup
c_cyc = m.Counter(7)
lst = [c_cyc]
lst.append(lst)
del c_cyc, lst
gc.collect()

# Same-instance 8-thread contention on instance_locked Counter
shared = m.Counter(0, policy="instance_locked")
b_same = threading.Barrier(8)
def same_worker():
    b_same.wait(timeout=10.0)
    for _ in range(250):
        shared.inc(1)

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    for f in [pool.submit(same_worker) for _ in range(8)]:
        f.result()
assert shared.value == 2000, f"lost update: {shared.value}"
shared.close()

# Distinct-instance 8-thread parallel execution (peak_in_flight == 8 on 3.15t)
m.reset_peak_in_flight()
b_par = threading.Barrier(8)
def par_worker(idx: int) -> int:
    inst = m.Counter(idx, policy="parallel")
    b_par.wait(timeout=10.0)
    res = inst.work_loop(1500000)
    inst.close()
    return res

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    outs = [f.result() for f in [pool.submit(par_worker, i) for i in range(8)]]
assert len(outs) == 8
if not sys._is_gil_enabled():
    peak = m.peak_in_flight()
    assert peak == 8, f"expected peak_in_flight == 8 on free-threaded 3.15t, got {peak}"

# thread_affine enforcement
aff = m.Counter(3, policy="thread_affine")
assert aff.get() == 3
def foreign_reader():
    aff.get()
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    try:
        pool.submit(foreign_reader).result()
        raise AssertionError("expected RuntimeError on foreign thread")
    except RuntimeError as e:
        assert "thread_affine" in str(e)
assert aff.inc(4) == 7
aff.close()

print("PHASE3_OK", sys.version.split()[0], "gil_enabled=", sys._is_gil_enabled())
"""
            env = {"PATH": "/usr/bin:/bin", "HOME": str(work), "PYTHONPATH": str(work)}
            targets = [("3.15", sys.executable, True)]
            if py315t:
                targets.append(("3.15t", py315t, False))

            for label, py_exe, expect_gil in targets:
                prog = (
                    "import sys\n"
                    f"assert sys._is_gil_enabled() is {expect_gil}\n"
                    + verify_script
                    + f"assert sys._is_gil_enabled() is {expect_gil}\n"
                )
                run_r = subprocess.run([py_exe, "-c", prog], cwd=work, env=env, capture_output=True, text=True)
                self.assertEqual(run_r.returncode, 0, f"{label} failed:\n{run_r.stdout}\n{run_r.stderr}")
                self.assertIn("PHASE3_OK", run_r.stdout)


if __name__ == "__main__":
    unittest.main()
