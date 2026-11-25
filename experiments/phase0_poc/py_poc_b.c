#ifndef NOXPY_PHASE0_EXPERIMENTAL_SELFHOST_BRIDGE
#  error "py_poc_b.c requires NOXPY_PHASE0_EXPERIMENTAL_SELFHOST_BRIDGE"
#endif

#include "noxpy_abi.h"
#include "nox_v2270_bridge.h"

extern int64_t nox_mul(void *rt, int64_t a, int64_t b);
extern int64_t nox_sub(void *rt, int64_t a, int64_t b);

static NoxPyModuleState *get_mod_state(PyObject *module) {
    NoxPyModuleState *st = (NoxPyModuleState *)PyModule_GetState(module);
    if (!st) {
        PyErr_SetString(PyExc_RuntimeError, "noxpy: module state unavailable");
        return NULL;
    }
    return st;
}

static PyObject *py_nox_mul(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    if (nargs != 2) {
        PyErr_Format(PyExc_TypeError, "mul() takes exactly 2 arguments (%zd given)", nargs);
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
    rc = nox_v2270_call_2_i64(nox_mul, a, b, &result, &err);
    Py_END_ALLOW_THREADS

    noxpy_module_lease_release(st);
    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(rc, &err);
        return NULL;
    }
    return noxpy_i64_to_py(result);
}

static PyObject *py_nox_sub(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    if (nargs != 2) {
        PyErr_Format(PyExc_TypeError, "sub() takes exactly 2 arguments (%zd given)", nargs);
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
    rc = nox_v2270_call_2_i64(nox_sub, a, b, &result, &err);
    Py_END_ALLOW_THREADS

    noxpy_module_lease_release(st);
    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(rc, &err);
        return NULL;
    }
    return noxpy_i64_to_py(result);
}

static PyMethodDef poc_b_methods[] = {
    {"mul", _PyCFunction_CAST(py_nox_mul), METH_FASTCALL, "Multiply two int64 values via native Nox."},
    {"sub", _PyCFunction_CAST(py_nox_sub), METH_FASTCALL, "Subtract two int64 values via native Nox."},
    {NULL, NULL, 0, NULL}
};

static int poc_b_exec(PyObject *module) {
    NoxPyModuleState *st = get_mod_state(module);
    if (!st) {
        return -1;
    }
    return noxpy_module_state_init(st, nox_v2270_runtime_ops());
}

static void poc_b_free(void *module) {
    NoxPyModuleState *st = (NoxPyModuleState *)PyModule_GetState((PyObject *)module);
    if (st) {
        noxpy_module_state_close(st);
    }
}

PyABIInfo_VAR(noxpy_poc_b_abi_info);

static PySlot poc_b_slots[] = {
    PySlot_STATIC_DATA(Py_mod_abi, &noxpy_poc_b_abi_info),
    PySlot_STATIC_DATA(Py_mod_name, "_noxpy_poc_b"),
    PySlot_STATIC_DATA(Py_mod_doc, "Second independent NoxPy Phase 0 abi3t module."),
    PySlot_SIZE(Py_mod_state_size, sizeof(NoxPyModuleState)),
    PySlot_STATIC_DATA(Py_mod_methods, poc_b_methods),
    PySlot_FUNC(Py_mod_exec, poc_b_exec),
    PySlot_FUNC(Py_mod_state_free, poc_b_free),
    PySlot_DATA(Py_mod_gil, Py_MOD_GIL_NOT_USED),
    PySlot_DATA(Py_mod_multiple_interpreters, Py_MOD_MULTIPLE_INTERPRETERS_NOT_SUPPORTED),
    PySlot_END
};

PyMODEXPORT_FUNC
PyModExport__noxpy_poc_b(void) {
    return poc_b_slots;
}
