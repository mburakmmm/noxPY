#include "noxpy_abi.h"

/*
 * Phase 0 CPython 3.15 abi3t proof-of-concept binding against upstream `nox_host.h` v1.
 * Compiled via `noxc build --backend llvm --host-abi --module-name _noxpy_poc --emit-obj`.
 */

extern NoxHostStatus nox_host_module_init__noxpy_poc(NoxHostContext *ctx, NoxHostError **err);
extern void nox_host_module_fini__noxpy_poc(NoxHostContext *ctx);

extern NoxHostStatus nox_host_export__noxpy_poc_add(
    NoxHostContext *ctx, int64_t a, int64_t b, int64_t *out, NoxHostError **err);
extern NoxHostStatus nox_host_export__noxpy_poc_fib(
    NoxHostContext *ctx, int64_t n, int64_t *out, NoxHostError **err);
extern NoxHostStatus nox_host_export__noxpy_poc_work_loop(
    NoxHostContext *ctx, int64_t iters, int64_t seed, int64_t *out, NoxHostError **err);
extern NoxHostStatus nox_host_export__noxpy_poc_checked_div(
    NoxHostContext *ctx, int64_t a, int64_t b, int64_t *out, NoxHostError **err);
extern NoxHostStatus nox_host_export__noxpy_poc_alloc_checksum(
    NoxHostContext *ctx, int64_t n, int64_t *out, NoxHostError **err);
extern NoxHostStatus nox_host_export__noxpy_poc_echo_str(
    NoxHostContext *ctx, const uint8_t *data, size_t len, NoxHostBuffer *out, NoxHostError **err);
extern NoxHostStatus nox_host_export__noxpy_poc_echo_bytes(
    NoxHostContext *ctx, const uint8_t *data, size_t len, NoxHostBuffer *out, NoxHostError **err);

static const NoxPyHostOps g_poc_a_ops = {
    .struct_size = (uint32_t)sizeof(NoxPyHostOps),
    .abi_version = NOX_HOST_ABI_VERSION,
    .context_create = nox_host_context_create,
    .context_destroy = nox_host_context_destroy,
    .module_init = nox_host_module_init__noxpy_poc,
    .module_fini = nox_host_module_fini__noxpy_poc,
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
    NoxHostContext *ctx = NULL;
    if (noxpy_module_ctx_acquire(st, &ctx) < 0) {
        noxpy_module_lease_release(st);
        return NULL;
    }

    int64_t result = 0;
    NoxHostError *err = NULL;
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    noxpy_module_inflight_enter(st);
    rc = nox_host_export__noxpy_poc_add(ctx, a, b, &result, &err);
    noxpy_module_inflight_exit(st);
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
    NoxHostContext *ctx = NULL;
    if (noxpy_module_ctx_acquire(st, &ctx) < 0) {
        noxpy_module_lease_release(st);
        return NULL;
    }

    int64_t result = 0;
    NoxHostError *err = NULL;
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    noxpy_module_inflight_enter(st);
    rc = nox_host_export__noxpy_poc_fib(ctx, n, &result, &err);
    noxpy_module_inflight_exit(st);
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
    NoxHostContext *ctx = NULL;
    if (noxpy_module_ctx_acquire(st, &ctx) < 0) {
        noxpy_module_lease_release(st);
        return NULL;
    }

    int64_t result = 0;
    NoxHostError *err = NULL;
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    noxpy_module_inflight_enter(st);
    rc = nox_host_export__noxpy_poc_work_loop(ctx, iters, seed, &result, &err);
    noxpy_module_inflight_exit(st);
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
    NoxHostContext *ctx = NULL;
    if (noxpy_module_ctx_acquire(st, &ctx) < 0) {
        noxpy_module_lease_release(st);
        return NULL;
    }

    int64_t result = 0;
    NoxHostError *err = NULL;
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    noxpy_module_inflight_enter(st);
    rc = nox_host_export__noxpy_poc_checked_div(ctx, a, b, &result, &err);
    noxpy_module_inflight_exit(st);
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
    NoxHostContext *ctx = NULL;
    if (noxpy_module_ctx_acquire(st, &ctx) < 0) {
        noxpy_module_lease_release(st);
        return NULL;
    }

    int64_t result = 0;
    NoxHostError *err = NULL;
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    noxpy_module_inflight_enter(st);
    rc = nox_host_export__noxpy_poc_alloc_checksum(ctx, n, &result, &err);
    noxpy_module_inflight_exit(st);
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

static PyObject *py_nox_echo_str(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    if (nargs != 1) {
        PyErr_Format(PyExc_TypeError, "echo_str() takes exactly 1 argument (%zd given)", nargs);
        return NULL;
    }
    NoxHostSpan sp = {0};
    if (noxpy_py_to_utf8_span_strict(args[0], &sp, "s") < 0) {
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

    NoxHostBuffer out = {0};
    NoxHostError *err = NULL;
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    noxpy_module_inflight_enter(st);
    rc = nox_host_export__noxpy_poc_echo_str(ctx, sp.data, sp.len, &out, &err);
    noxpy_module_inflight_exit(st);
    Py_END_ALLOW_THREADS

    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(st->host_ops, rc, err);
        noxpy_module_ctx_release(st, ctx);
        noxpy_module_lease_release(st);
        return NULL;
    }
    noxpy_module_ctx_release(st, ctx);
    noxpy_module_lease_release(st);
    return noxpy_utf8_buffer_to_py(st->host_ops, &out);
}

static PyObject *py_nox_echo_bytes(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    if (nargs != 1) {
        PyErr_Format(PyExc_TypeError, "echo_bytes() takes exactly 1 argument (%zd given)", nargs);
        return NULL;
    }
    NoxHostSpan sp = {0};
    if (noxpy_py_to_bytes_span_strict(args[0], &sp, "raw") < 0) {
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

    NoxHostBuffer out = {0};
    NoxHostError *err = NULL;
    NoxHostStatus rc;
    Py_BEGIN_ALLOW_THREADS
    noxpy_module_inflight_enter(st);
    rc = nox_host_export__noxpy_poc_echo_bytes(ctx, sp.data, sp.len, &out, &err);
    noxpy_module_inflight_exit(st);
    Py_END_ALLOW_THREADS

    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(st->host_ops, rc, err);
        noxpy_module_ctx_release(st, ctx);
        noxpy_module_lease_release(st);
        return NULL;
    }
    noxpy_module_ctx_release(st, ctx);
    noxpy_module_lease_release(st);
    return noxpy_bytes_buffer_to_py(st->host_ops, &out);
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
    {"echo_str", _PyCFunction_CAST(py_nox_echo_str), METH_FASTCALL, "Round-trip binary-safe UTF-8 str via NoxHostBuffer."},
    {"echo_bytes", _PyCFunction_CAST(py_nox_echo_bytes), METH_FASTCALL, "Round-trip raw bytes via NoxHostBuffer."},
    {"peak_in_flight", py_peak_in_flight, METH_NOARGS, "Return peak concurrent in-flight native calls."},
    {"reset_peak_in_flight", py_reset_peak_in_flight, METH_NOARGS, "Reset peak concurrent in-flight counter."},
    {NULL, NULL, 0, NULL}
};

static int poc_a_exec(PyObject *module) {
    NoxPyModuleState *st = get_mod_state(module);
    if (!st) {
        return -1;
    }
    return noxpy_module_state_init(st, &g_poc_a_ops);
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
    PySlot_STATIC_DATA(Py_mod_doc, "NoxPy Phase 0 abi3t proof-of-concept module via upstream nox_host.h v1."),
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
