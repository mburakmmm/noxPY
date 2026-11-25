#include "noxpy_abi.h"

/*
 * Second independent NoxPy Phase 0 CPython 3.15 abi3t extension via upstream `nox_host.h` v1.
 */

extern NoxHostStatus nox_host_module_init__noxpy_poc_b(NoxHostContext *ctx, NoxHostError **err);
extern void nox_host_module_fini__noxpy_poc_b(NoxHostContext *ctx);

extern NoxHostStatus nox_host_export__noxpy_poc_b_mul(
    NoxHostContext *ctx, int64_t a, int64_t b, int64_t *out, NoxHostError **err);
extern NoxHostStatus nox_host_export__noxpy_poc_b_sub(
    NoxHostContext *ctx, int64_t a, int64_t b, int64_t *out, NoxHostError **err);

static const NoxPyHostOps g_poc_b_ops = {
    .struct_size = (uint32_t)sizeof(NoxPyHostOps),
    .abi_version = NOX_HOST_ABI_VERSION,
    .context_create = nox_host_context_create,
    .context_destroy = nox_host_context_destroy,
    .module_init = nox_host_module_init__noxpy_poc_b,
    .module_fini = nox_host_module_fini__noxpy_poc_b,
    .buffer_release = nox_host_buffer_release,
    .error_release = nox_host_error_release,
    .error_line = nox_host_error_line,
    .error_class_name = nox_host_error_class_name,
    .error_message = nox_host_error_message,
};

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
    NoxHostContext *ctx = NULL;
    if (noxpy_module_ctx_acquire(st, &ctx) < 0) {
        noxpy_module_lease_release(st);
        return NULL;
    }

    int64_t result = 0;
    NoxHostError *err = NULL;
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    rc = nox_host_export__noxpy_poc_b_mul(ctx, a, b, &result, &err);
    Py_END_ALLOW_THREADS

    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(st->host_ops, rc, err);
        noxpy_module_ctx_release(st, ctx);
        noxpy_module_lease_release(st);
        return NULL;
    }
    noxpy_module_ctx_release(st, ctx);
    noxpy_module_lease_release(st);
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
    NoxHostContext *ctx = NULL;
    if (noxpy_module_ctx_acquire(st, &ctx) < 0) {
        noxpy_module_lease_release(st);
        return NULL;
    }

    int64_t result = 0;
    NoxHostError *err = NULL;
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    rc = nox_host_export__noxpy_poc_b_sub(ctx, a, b, &result, &err);
    Py_END_ALLOW_THREADS

    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(st->host_ops, rc, err);
        noxpy_module_ctx_release(st, ctx);
        noxpy_module_lease_release(st);
        return NULL;
    }
    noxpy_module_ctx_release(st, ctx);
    noxpy_module_lease_release(st);
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
    return noxpy_module_state_init(st, &g_poc_b_ops);
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
    PySlot_STATIC_DATA(Py_mod_doc, "Second independent NoxPy Phase 0 abi3t module via upstream nox_host.h v1."),
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
