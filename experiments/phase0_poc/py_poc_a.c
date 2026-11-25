#ifndef NOXPY_PHASE0_EXPERIMENTAL_SELFHOST_BRIDGE
#  error "py_poc_a.c requires NOXPY_PHASE0_EXPERIMENTAL_SELFHOST_BRIDGE"
#endif

#include "noxpy_abi.h"
#include "nox_v2270_bridge.h"

extern int64_t nox_add(void *rt, int64_t a, int64_t b);
extern int64_t nox_fib(void *rt, int64_t n);
extern int64_t nox_work_loop(void *rt, int64_t iters, int64_t seed);
extern int64_t nox_checked_div(void *rt, int64_t a, int64_t b);
extern int64_t nox_alloc_checksum(void *rt, int64_t n);

static NoxPyModuleState *get_mod_state(PyObject *module) {
    NoxPyModuleState *st = (NoxPyModuleState *)PyModule_GetState(module);
    if (!st) {
        PyErr_SetString(PyExc_RuntimeError, "noxpy: module state unavailable");
        return NULL;
    }
    return st;
}

static PyObject *py_nox_add(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    if (nargs != 2) {
        PyErr_Format(PyExc_TypeError, "add() takes exactly 2 arguments (%zd given)", nargs);
        return NULL;
    }
    int64_t a = 0, b = 0;
    if (noxpy_py_to_i64_strict(args[0], &a, "a") < 0 ||
        noxpy_py_to_i64_strict(args[1], &b, "b") < 0) {
        return NULL;
    }
    NoxPyModuleState *st = get_mod_state(module);
    if (!st || noxpy_module_lease_acquire(st) < 0) {
        return NULL;
    }

    int64_t result = 0;
    NoxHostError err = {0};
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    noxpy_module_inflight_enter(st);
    rc = nox_v2270_call_2_i64(nox_add, a, b, &result, &err);
    noxpy_module_inflight_exit(st);
    Py_END_ALLOW_THREADS

    noxpy_module_lease_release(st);
    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(rc, &err);
        if (st->runtime_ops && st->runtime_ops->error_clear) {
            st->runtime_ops->error_clear(&err);
        }
        return NULL;
    }
    return noxpy_i64_to_py(result);
}

static PyObject *py_nox_fib(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    if (nargs != 1) {
        PyErr_Format(PyExc_TypeError, "fib() takes exactly 1 argument (%zd given)", nargs);
        return NULL;
    }
    int64_t n = 0;
    if (noxpy_py_to_i64_strict(args[0], &n, "n") < 0) {
        return NULL;
    }
    if (n > 90) {
        PyErr_SetString(PyExc_OverflowError, "fib(n) limited to n <= 90 to stay within int64");
        return NULL;
    }
    NoxPyModuleState *st = get_mod_state(module);
    if (!st || noxpy_module_lease_acquire(st) < 0) {
        return NULL;
    }

    int64_t result = 0;
    NoxHostError err = {0};
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    noxpy_module_inflight_enter(st);
    rc = nox_v2270_call_1_i64(nox_fib, n, &result, &err);
    noxpy_module_inflight_exit(st);
    Py_END_ALLOW_THREADS

    noxpy_module_lease_release(st);
    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(rc, &err);
        if (st->runtime_ops && st->runtime_ops->error_clear) {
            st->runtime_ops->error_clear(&err);
        }
        return NULL;
    }
    return noxpy_i64_to_py(result);
}

static PyObject *py_nox_work_loop(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    if (nargs != 2) {
        PyErr_Format(PyExc_TypeError, "work_loop() takes exactly 2 arguments (%zd given)", nargs);
        return NULL;
    }
    int64_t iters = 0, seed = 0;
    if (noxpy_py_to_i64_strict(args[0], &iters, "iters") < 0 ||
        noxpy_py_to_i64_strict(args[1], &seed, "seed") < 0) {
        return NULL;
    }
    NoxPyModuleState *st = get_mod_state(module);
    if (!st || noxpy_module_lease_acquire(st) < 0) {
        return NULL;
    }

    int64_t result = 0;
    NoxHostError err = {0};
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    noxpy_module_inflight_enter(st);
    rc = nox_v2270_call_2_i64(nox_work_loop, iters, seed, &result, &err);
    noxpy_module_inflight_exit(st);
    Py_END_ALLOW_THREADS

    noxpy_module_lease_release(st);
    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(rc, &err);
        if (st->runtime_ops && st->runtime_ops->error_clear) {
            st->runtime_ops->error_clear(&err);
        }
        return NULL;
    }
    return noxpy_i64_to_py(result);
}

static PyObject *py_nox_checked_div(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    if (nargs != 2) {
        PyErr_Format(PyExc_TypeError, "checked_div() takes exactly 2 arguments (%zd given)", nargs);
        return NULL;
    }
    int64_t a = 0, b = 0;
    if (noxpy_py_to_i64_strict(args[0], &a, "a") < 0 ||
        noxpy_py_to_i64_strict(args[1], &b, "b") < 0) {
        return NULL;
    }
    NoxPyModuleState *st = get_mod_state(module);
    if (!st || noxpy_module_lease_acquire(st) < 0) {
        return NULL;
    }

    int64_t result = 0;
    NoxHostError err = {0};
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    noxpy_module_inflight_enter(st);
    rc = nox_v2270_call_2_i64(nox_checked_div, a, b, &result, &err);
    noxpy_module_inflight_exit(st);
    Py_END_ALLOW_THREADS

    noxpy_module_lease_release(st);
    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(rc, &err);
        if (st->runtime_ops && st->runtime_ops->error_clear) {
            st->runtime_ops->error_clear(&err);
        }
        return NULL;
    }
    return noxpy_i64_to_py(result);
}

static PyObject *py_nox_alloc_checksum(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    if (nargs != 1) {
        PyErr_Format(PyExc_TypeError, "alloc_checksum() takes exactly 1 argument (%zd given)", nargs);
        return NULL;
    }
    int64_t n = 0;
    if (noxpy_py_to_i64_strict(args[0], &n, "n") < 0) {
        return NULL;
    }
    NoxPyModuleState *st = get_mod_state(module);
    if (!st || noxpy_module_lease_acquire(st) < 0) {
        return NULL;
    }

    int64_t result = 0;
    NoxHostError err = {0};
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    noxpy_module_inflight_enter(st);
    rc = nox_v2270_call_1_i64(nox_alloc_checksum, n, &result, &err);
    noxpy_module_inflight_exit(st);
    Py_END_ALLOW_THREADS

    noxpy_module_lease_release(st);
    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(rc, &err);
        if (st->runtime_ops && st->runtime_ops->error_clear) {
            st->runtime_ops->error_clear(&err);
        }
        return NULL;
    }
    return noxpy_i64_to_py(result);
}

static PyObject *py_peak_in_flight(PyObject *module, PyObject *Py_UNUSED(ignored)) {
    NoxPyModuleState *st = get_mod_state(module);
    if (!st) {
        return NULL;
    }
    return noxpy_i64_to_py((int64_t)noxpy_module_peak_in_flight(st));
}

static PyObject *py_reset_peak_in_flight(PyObject *module, PyObject *Py_UNUSED(ignored)) {
    NoxPyModuleState *st = get_mod_state(module);
    if (!st) {
        return NULL;
    }
    noxpy_module_reset_peak_in_flight(st);
    Py_RETURN_NONE;
}

static PyMethodDef poc_a_methods[] = {
    {"add", _PyCFunction_CAST(py_nox_add), METH_FASTCALL, "Add two int64 values via native Nox."},
    {"fib", _PyCFunction_CAST(py_nox_fib), METH_FASTCALL, "Fibonacci via native Nox."},
    {"work_loop", _PyCFunction_CAST(py_nox_work_loop), METH_FASTCALL, "CPU loop via native Nox."},
    {"checked_div", _PyCFunction_CAST(py_nox_checked_div), METH_FASTCALL, "Division with Nox exception containment."},
    {"alloc_checksum", _PyCFunction_CAST(py_nox_alloc_checksum), METH_FASTCALL, "Nox list allocation checksum."},
    {"peak_in_flight", py_peak_in_flight, METH_NOARGS, "Return peak concurrent in-flight native calls."},
    {"reset_peak_in_flight", py_reset_peak_in_flight, METH_NOARGS, "Reset peak concurrent in-flight counter."},
    {NULL, NULL, 0, NULL}
};

static int poc_a_exec(PyObject *module) {
    NoxPyModuleState *st = get_mod_state(module);
    if (!st) {
        return -1;
    }
    return noxpy_module_state_init(st, nox_v2270_runtime_ops());
}

static void poc_a_free(void *module) {
    NoxPyModuleState *st = (NoxPyModuleState *)PyModule_GetState((PyObject *)module);
    if (st) {
        noxpy_module_state_close(st);
    }
}

PyABIInfo_VAR(noxpy_poc_a_abi_info);

static PySlot poc_a_slots[] = {
    PySlot_STATIC_DATA(Py_mod_abi, &noxpy_poc_a_abi_info),
    PySlot_STATIC_DATA(Py_mod_name, "_noxpy_poc"),
    PySlot_STATIC_DATA(Py_mod_doc, "NoxPy Phase 0 experimental abi3t proof-of-concept module."),
    PySlot_SIZE(Py_mod_state_size, sizeof(NoxPyModuleState)),
    PySlot_STATIC_DATA(Py_mod_methods, poc_a_methods),
    PySlot_FUNC(Py_mod_exec, poc_a_exec),
    PySlot_FUNC(Py_mod_state_free, poc_a_free),
    PySlot_DATA(Py_mod_gil, Py_MOD_GIL_NOT_USED),
    PySlot_DATA(Py_mod_multiple_interpreters, Py_MOD_MULTIPLE_INTERPRETERS_NOT_SUPPORTED),
    PySlot_END
};

PyMODEXPORT_FUNC
PyModExport__noxpy_poc(void) {
    return poc_a_slots;
}
