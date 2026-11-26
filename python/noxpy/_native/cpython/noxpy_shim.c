#include "noxpy_abi.h"

#include <limits.h>
#include <sched.h>
#include <stdlib.h>
#include <string.h>

static int span_eq_cstr(NoxHostSpan span, const char *lit) {
    if (!span.data || !lit) {
        return 0;
    }
    size_t lit_len = strlen(lit);
    return span.len == lit_len && memcmp(span.data, lit, lit_len) == 0;
}

static void pool_lock_acquire(atomic_flag *flag) {
    while (atomic_flag_test_and_set_explicit(flag, memory_order_acquire)) {
        sched_yield();
    }
}

static void pool_lock_release(atomic_flag *flag) {
    atomic_flag_clear_explicit(flag, memory_order_release);
}

static void destroy_host_context(const NoxPyHostOps *ops, NoxHostContext *ctx) {
    if (!ops || !ctx) {
        return;
    }
    if (ops->module_fini) {
        ops->module_fini(ctx);
    }
    if (ops->context_destroy) {
        ops->context_destroy(ctx);
    }
}

static int create_and_init_context(const NoxPyHostOps *ops, NoxHostContext **out_ctx) {
    *out_ctx = NULL;
    if (!ops || !ops->context_create) {
        PyErr_SetString(PyExc_SystemError, "noxpy: host ops missing context_create");
        return -1;
    }
    NoxHostContextConfig cfg = {
        .size = (uint32_t)sizeof(NoxHostContextConfig),
        .abi_version = ops->abi_version,
        .allocator = NULL,
    };
    NoxHostContext *ctx = NULL;
    NoxHostStatus rc = ops->context_create(&cfg, &ctx);
    if (rc != NOX_HOST_OK || !ctx) {
        noxpy_set_exception_from_host(ops, rc == NOX_HOST_OK ? NOX_HOST_ERR_NOMEM : rc, NULL);
        return -1;
    }
    if (ops->module_init) {
        NoxHostError *err = NULL;
        rc = ops->module_init(ctx, &err);
        if (rc != NOX_HOST_OK) {
            /* Error MUST be inspected & released before destroying ctx (nox_host.h). */
            noxpy_set_exception_from_host(ops, rc, err);
            if (ops->context_destroy) {
                ops->context_destroy(ctx);
            }
            return -1;
        }
    }
    *out_ctx = ctx;
    return 0;
}

int noxpy_module_state_init(NoxPyModuleState *st, const NoxPyHostOps *ops) {
    if (!st) {
        PyErr_SetString(PyExc_SystemError, "noxpy: NULL module state");
        return -1;
    }
    if (!ops || ops->struct_size < sizeof(NoxPyHostOps) || ops->abi_version < 1u ||
        ops->abi_version > NOX_HOST_ABI_VERSION) {
        PyErr_SetString(PyExc_RuntimeError, "noxpy: incompatible Nox host ABI ops table");
        return -1;
    }

    st->struct_size = (uint32_t)sizeof(NoxPyModuleState);
    atomic_store_explicit(&st->lifecycle, NOXPY_MOD_INITIALIZING, memory_order_release);
    atomic_store_explicit(&st->active_calls, 0, memory_order_relaxed);
    atomic_store_explicit(&st->current_in_flight, 0, memory_order_relaxed);
    atomic_store_explicit(&st->peak_in_flight, 0, memory_order_relaxed);
    st->host_ops = ops;
    atomic_flag_clear_explicit(&st->pool_lock, memory_order_relaxed);
    st->pool_count = 0;
    for (size_t i = 0; i < NOXPY_CTX_POOL_CAP; i++) {
        st->pool[i] = NULL;
    }

    /* Eagerly initialize one warm context to validate module init on import. */
    NoxHostContext *warm = NULL;
    if (create_and_init_context(ops, &warm) < 0) {
        atomic_store_explicit(&st->lifecycle, NOXPY_MOD_CLOSED, memory_order_release);
        return -1;
    }
    st->pool[0] = warm;
    st->pool_count = 1;

    atomic_store_explicit(&st->lifecycle, NOXPY_MOD_READY, memory_order_release);
    return 0;
}

int noxpy_module_lease_acquire(NoxPyModuleState *st) {
    if (!st) {
        PyErr_SetString(PyExc_SystemError, "noxpy: NULL module state");
        return -1;
    }
    int32_t lc = atomic_load_explicit(&st->lifecycle, memory_order_acquire);
    if (lc != NOXPY_MOD_READY) {
        PyErr_SetString(PyExc_RuntimeError, "noxpy: module is not ready (closing or closed)");
        return -1;
    }

    atomic_fetch_add_explicit(&st->active_calls, 1, memory_order_acq_rel);
    /* Re-check lifecycle after incrementing active_calls to close the check-then-act window. */
    lc = atomic_load_explicit(&st->lifecycle, memory_order_acquire);
    if (lc != NOXPY_MOD_READY) {
        atomic_fetch_sub_explicit(&st->active_calls, 1, memory_order_release);
        PyErr_SetString(PyExc_RuntimeError, "noxpy: module entered shutdown concurrently");
        return -1;
    }
    return 0;
}

void noxpy_module_lease_release(NoxPyModuleState *st) {
    if (!st) {
        return;
    }
    atomic_fetch_sub_explicit(&st->active_calls, 1, memory_order_release);
}

int noxpy_module_ctx_acquire(NoxPyModuleState *st, NoxHostContext **out_ctx) {
    if (!st || !out_ctx || !st->host_ops) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid module context acquire state");
        return -1;
    }
    *out_ctx = NULL;

    NoxHostContext *ctx = NULL;
    pool_lock_acquire(&st->pool_lock);
    if (st->pool_count > 0) {
        st->pool_count--;
        ctx = st->pool[st->pool_count];
        st->pool[st->pool_count] = NULL;
    }
    pool_lock_release(&st->pool_lock);

    if (ctx) {
        *out_ctx = ctx;
        return 0;
    }
    return create_and_init_context(st->host_ops, out_ctx);
}

void noxpy_module_ctx_release(NoxPyModuleState *st, NoxHostContext *ctx) {
    if (!st || !ctx) {
        return;
    }
    int cached = 0;
    if (atomic_load_explicit(&st->lifecycle, memory_order_acquire) == NOXPY_MOD_READY) {
        pool_lock_acquire(&st->pool_lock);
        if (st->pool_count < NOXPY_CTX_POOL_CAP) {
            st->pool[st->pool_count] = ctx;
            st->pool_count++;
            cached = 1;
        }
        pool_lock_release(&st->pool_lock);
    }
    if (!cached) {
        destroy_host_context(st->host_ops, ctx);
    }
}

void noxpy_module_state_close(NoxPyModuleState *st) {
    if (!st || st->struct_size != sizeof(NoxPyModuleState)) {
        return;
    }
    int32_t prev = atomic_exchange_explicit(&st->lifecycle, NOXPY_MOD_CLOSING, memory_order_acq_rel);
    if (prev == NOXPY_MOD_CLOSED || prev == NOXPY_MOD_NEW) {
        atomic_store_explicit(&st->lifecycle, NOXPY_MOD_CLOSED, memory_order_release);
        return;
    }

    while (atomic_load_explicit(&st->active_calls, memory_order_acquire) > 0) {
        sched_yield();
    }

    NoxHostContext *local_ctxs[NOXPY_CTX_POOL_CAP];
    size_t local_count = 0;
    pool_lock_acquire(&st->pool_lock);
    local_count = st->pool_count;
    for (size_t i = 0; i < local_count; i++) {
        local_ctxs[i] = st->pool[i];
        st->pool[i] = NULL;
    }
    st->pool_count = 0;
    pool_lock_release(&st->pool_lock);

    for (size_t i = 0; i < local_count; i++) {
        destroy_host_context(st->host_ops, local_ctxs[i]);
    }

    atomic_store_explicit(&st->lifecycle, NOXPY_MOD_CLOSED, memory_order_release);
}

void noxpy_module_inflight_enter(NoxPyModuleState *st) {
    uint64_t now = atomic_fetch_add_explicit(&st->current_in_flight, 1, memory_order_acq_rel) + 1;
    uint64_t peak = atomic_load_explicit(&st->peak_in_flight, memory_order_relaxed);
    while (now > peak) {
        if (atomic_compare_exchange_weak_explicit(
                &st->peak_in_flight, &peak, now, memory_order_release, memory_order_relaxed)) {
            break;
        }
    }
}

void noxpy_module_inflight_exit(NoxPyModuleState *st) {
    atomic_fetch_sub_explicit(&st->current_in_flight, 1, memory_order_release);
}

uint64_t noxpy_module_peak_in_flight(const NoxPyModuleState *st) {
    return atomic_load_explicit(&((NoxPyModuleState *)st)->peak_in_flight, memory_order_acquire);
}

void noxpy_module_reset_peak_in_flight(NoxPyModuleState *st) {
    atomic_store_explicit(&st->peak_in_flight, 0, memory_order_release);
}

int noxpy_py_to_i64_strict(PyObject *obj, int64_t *out, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!obj || !out) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid null conversion argument");
        return -1;
    }
    if (PyBool_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be int, not bool", label);
        return -1;
    }
    if (!PyLong_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be int", label);
        return -1;
    }
    int64_t v = 0;
    if (PyLong_AsInt64(obj, &v) < 0) {
        if (PyErr_ExceptionMatches(PyExc_OverflowError)) {
            PyErr_Format(PyExc_OverflowError, "argument '%s' overflows 64-bit signed int", label);
        }
        return -1;
    }
    *out = v;
    return 0;
}

PyObject *noxpy_i64_to_py(int64_t val) {
    return PyLong_FromInt64(val);
}

int noxpy_py_to_bool_strict(PyObject *obj, uint8_t *out, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!obj || !out) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid null conversion argument");
        return -1;
    }
    if (!PyBool_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be bool", label);
        return -1;
    }
    *out = Py_IsTrue(obj) ? 1u : 0u;
    return 0;
}

PyObject *noxpy_bool_to_py(uint8_t val) {
    return PyBool_FromLong(val ? 1L : 0L);
}

int noxpy_py_to_f64_strict(PyObject *obj, double *out, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!obj || !out) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid null conversion argument");
        return -1;
    }
    if (!PyFloat_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be float", label);
        return -1;
    }
    double v = PyFloat_AsDouble(obj);
    if (v == -1.0 && PyErr_Occurred()) {
        return -1;
    }
    *out = v;
    return 0;
}

PyObject *noxpy_f64_to_py(double val) {
    return PyFloat_FromDouble(val);
}

int noxpy_py_to_utf8_span_strict(PyObject *obj, NoxHostSpan *out, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!obj || !out) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid null conversion argument");
        return -1;
    }
    if (!PyUnicode_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be str", label);
        return -1;
    }
    Py_ssize_t sz = 0;
    const char *utf8 = PyUnicode_AsUTF8AndSize(obj, &sz);
    if (!utf8 || sz < 0) {
        return -1;
    }
    out->data = (const uint8_t *)utf8;
    out->len = (size_t)sz;
    return 0;
}

PyObject *noxpy_utf8_span_to_py(NoxHostSpan span) {
    if (span.len > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "noxpy: string length exceeds PY_SSIZE_T_MAX");
        return NULL;
    }
    if (span.len > 0 && !span.data) {
        PyErr_SetString(PyExc_SystemError, "noxpy: NULL data pointer with non-zero span length");
        return NULL;
    }
    const char *ptr = span.data ? (const char *)span.data : "";
    return PyUnicode_DecodeUTF8(ptr, (Py_ssize_t)span.len, "strict");
}

PyObject *noxpy_utf8_buffer_to_py(const NoxPyHostOps *ops, NoxHostBuffer *buf) {
    if (!buf) {
        PyErr_SetString(PyExc_SystemError, "noxpy: NULL NoxHostBuffer");
        return NULL;
    }
    NoxHostSpan sp = { .data = buf->data, .len = buf->len };
    PyObject *res = noxpy_utf8_span_to_py(sp);
    if (ops && ops->buffer_release) {
        ops->buffer_release(buf);
    }
    return res;
}

int noxpy_py_to_bytes_span_strict(PyObject *obj, NoxHostSpan *out, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!obj || !out) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid null conversion argument");
        return -1;
    }
    if (!PyBytes_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be bytes", label);
        return -1;
    }
    char *buf = NULL;
    Py_ssize_t sz = 0;
    if (PyBytes_AsStringAndSize(obj, &buf, &sz) < 0) {
        return -1;
    }
    if (sz < 0) {
        PyErr_SetString(PyExc_SystemError, "noxpy: negative bytes length");
        return -1;
    }
    out->data = (const uint8_t *)buf;
    out->len = (size_t)sz;
    return 0;
}

PyObject *noxpy_bytes_span_to_py(NoxHostSpan span) {
    if (span.len > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "noxpy: bytes length exceeds PY_SSIZE_T_MAX");
        return NULL;
    }
    if (span.len > 0 && !span.data) {
        PyErr_SetString(PyExc_SystemError, "noxpy: NULL data pointer with non-zero span length");
        return NULL;
    }
    const char *ptr = span.data ? (const char *)span.data : "";
    return PyBytes_FromStringAndSize(ptr, (Py_ssize_t)span.len);
}

PyObject *noxpy_bytes_buffer_to_py(const NoxPyHostOps *ops, NoxHostBuffer *buf) {
    if (!buf) {
        PyErr_SetString(PyExc_SystemError, "noxpy: NULL NoxHostBuffer");
        return NULL;
    }
    NoxHostSpan sp = { .data = buf->data, .len = buf->len };
    PyObject *res = noxpy_bytes_span_to_py(sp);
    if (ops && ops->buffer_release) {
        ops->buffer_release(buf);
    }
    return res;
}

static PyObject *pick_exc_type(NoxHostStatus status, NoxHostSpan class_name) {
    if (class_name.data && class_name.len > 0) {
        if (span_eq_cstr(class_name, "ValueError")) return PyExc_ValueError;
        if (span_eq_cstr(class_name, "TypeError")) return PyExc_TypeError;
        if (span_eq_cstr(class_name, "OverflowError")) return PyExc_OverflowError;
        if (span_eq_cstr(class_name, "MemoryError")) return PyExc_MemoryError;
        if (span_eq_cstr(class_name, "IndexError")) return PyExc_IndexError;
        if (span_eq_cstr(class_name, "KeyError")) return PyExc_KeyError;
        if (span_eq_cstr(class_name, "ZeroDivisionError")) return PyExc_ZeroDivisionError;
        if (span_eq_cstr(class_name, "NotImplementedError")) return PyExc_NotImplementedError;
        if (span_eq_cstr(class_name, "AssertionError")) return PyExc_AssertionError;
        if (span_eq_cstr(class_name, "AttributeError")) return PyExc_AttributeError;
    }
    switch (status) {
        case NOX_HOST_ERR_NOMEM:
            return PyExc_MemoryError;
        case NOX_HOST_ERR_UTF8:
        case NOX_HOST_ERR_INVAL:
        case NOX_HOST_ERR_INVALID_HANDLE:
            return PyExc_ValueError;
        case NOX_HOST_ERR_VERSION:
        case NOX_HOST_ERR_STATE:
        case NOX_HOST_ERR_EXCEPTION:
        default:
            return PyExc_RuntimeError;
    }
}

void noxpy_set_exception_from_host_ex(
    const NoxPyHostOps *ops,
    NoxHostStatus status,
    NoxHostError *err,
    const NoxPyCustomExc *custom_excs,
    size_t n_custom_excs) {
    NoxHostSpan class_name = {0};
    NoxHostSpan message = {0};
    int64_t line = 0;

    if (err && ops) {
        if (ops->error_class_name) {
            (void)ops->error_class_name(err, &class_name);
        }
        if (ops->error_message) {
            (void)ops->error_message(err, &message);
        }
        if (ops->error_line) {
            line = ops->error_line(err);
        }
    }

    PyObject *exc_type = NULL;
    if (custom_excs && n_custom_excs > 0 && class_name.data && class_name.len > 0) {
        for (size_t i = 0; i < n_custom_excs; i++) {
            if (custom_excs[i].name && custom_excs[i].exc_type &&
                span_eq_cstr(class_name, custom_excs[i].name)) {
                exc_type = custom_excs[i].exc_type;
                break;
            }
        }
    }
    if (!exc_type) {
        exc_type = pick_exc_type(status, class_name);
    }

    if (message.data && message.len > 0) {
        int msg_len = message.len > (size_t)INT_MAX ? INT_MAX : (int)message.len;
        if (class_name.data && class_name.len > 0) {
            int cls_len = class_name.len > (size_t)INT_MAX ? INT_MAX : (int)class_name.len;
            PyErr_Format(
                exc_type,
                "%.*s: %.*s (line %lld)",
                cls_len,
                (const char *)class_name.data,
                msg_len,
                (const char *)message.data,
                (long long)line);
        } else {
            PyErr_Format(exc_type, "%.*s", msg_len, (const char *)message.data);
        }
    } else if (class_name.data && class_name.len > 0) {
        int cls_len = class_name.len > (size_t)INT_MAX ? INT_MAX : (int)class_name.len;
        PyErr_Format(
            exc_type,
            "Nox exception '%.*s' (line %lld)",
            cls_len,
            (const char *)class_name.data,
            (long long)line);
    } else {
        PyErr_Format(exc_type, "Nox host error status=%d", (int)status);
    }

    if (err && ops && ops->error_release) {
        ops->error_release(err);
    }
}

void noxpy_set_exception_from_host(const NoxPyHostOps *ops, NoxHostStatus status, NoxHostError *err) {
    noxpy_set_exception_from_host_ex(ops, status, err, NULL, 0);
}

int noxpy_bind_fastcall_kwargs(
    const char *fn_name,
    const NoxPyParamSpec *specs,
    Py_ssize_t nparams,
    PyObject *const *args,
    Py_ssize_t nargs,
    PyObject *kwnames,
    PyObject **bound_out) {
    if (!fn_name || (!specs && nparams > 0) || (!bound_out && nparams > 0)) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid argument binder state");
        return -1;
    }

    Py_ssize_t max_pos = 0;
    for (Py_ssize_t i = 0; i < nparams; i++) {
        bound_out[i] = NULL;
        if (specs[i].kind != NOXPY_PARAM_KW_ONLY) {
            max_pos++;
        }
    }

    if (nargs > max_pos) {
        PyErr_Format(
            PyExc_TypeError,
            "%s() takes %zd positional argument(s) but %zd were given",
            fn_name,
            max_pos,
            nargs);
        return -1;
    }

    for (Py_ssize_t i = 0; i < nargs; i++) {
        bound_out[i] = args[i];
    }

    Py_ssize_t nkw = 0;
    if (kwnames) {
        nkw = PyTuple_Size(kwnames);
        if (nkw < 0) {
            return -1;
        }
    }

    for (Py_ssize_t k = 0; k < nkw; k++) {
        PyObject *kw = PyTuple_GetItem(kwnames, k);
        if (!kw || !PyUnicode_Check(kw)) {
            PyErr_SetString(PyExc_TypeError, "keywords must be strings");
            return -1;
        }
        Py_ssize_t match = -1;
        for (Py_ssize_t i = 0; i < nparams; i++) {
            int cmp = PyUnicode_CompareWithASCIIString(kw, specs[i].name);
            if (cmp == 0) {
                match = i;
                break;
            }
            if (PyErr_Occurred()) {
                return -1;
            }
        }
        if (match < 0) {
            Py_ssize_t kw_len = 0;
            const char *kw_utf8 = PyUnicode_AsUTF8AndSize(kw, &kw_len);
            if (kw_utf8) {
                int klen = kw_len > (Py_ssize_t)INT_MAX ? INT_MAX : (int)kw_len;
                PyErr_Format(PyExc_TypeError, "%s() got an unexpected keyword argument '%.*s'", fn_name, klen, kw_utf8);
            } else {
                PyErr_Format(PyExc_TypeError, "%s() got an unexpected keyword argument", fn_name);
            }
            return -1;
        }
        if (specs[match].kind == NOXPY_PARAM_POS_ONLY) {
            PyErr_Format(
                PyExc_TypeError,
                "%s() got some positional-only arguments passed as keyword arguments: '%s'",
                fn_name,
                specs[match].name);
            return -1;
        }
        if (bound_out[match] != NULL) {
            PyErr_Format(PyExc_TypeError, "%s() got multiple values for argument '%s'", fn_name, specs[match].name);
            return -1;
        }
        bound_out[match] = args[nargs + k];
    }

    for (Py_ssize_t i = 0; i < nparams; i++) {
        if (bound_out[i] == NULL && !specs[i].has_default) {
            if (specs[i].kind == NOXPY_PARAM_KW_ONLY) {
                PyErr_Format(PyExc_TypeError, "%s() missing required keyword-only argument: '%s'", fn_name, specs[i].name);
            } else {
                PyErr_Format(PyExc_TypeError, "%s() missing required argument: '%s'", fn_name, specs[i].name);
            }
            return -1;
        }
    }
    return 0;
}

int noxpy_bind_tuple_kwargs(
    const char *fn_name,
    const NoxPyParamSpec *specs,
    Py_ssize_t nparams,
    PyObject *args_tuple,
    PyObject *kwds_dict,
    PyObject **bound_out) {
    if (!fn_name || (!specs && nparams > 0) || (!bound_out && nparams > 0) || !args_tuple) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid tuple/kwds argument binder state");
        return -1;
    }

    Py_ssize_t max_pos = 0;
    for (Py_ssize_t i = 0; i < nparams; i++) {
        bound_out[i] = NULL;
        if (specs[i].kind != NOXPY_PARAM_KW_ONLY) {
            max_pos++;
        }
    }

    Py_ssize_t nargs = PyTuple_Size(args_tuple);
    if (nargs < 0) {
        return -1;
    }
    if (nargs > max_pos) {
        PyErr_Format(
            PyExc_TypeError,
            "%s() takes %zd positional argument(s) but %zd were given",
            fn_name,
            max_pos,
            nargs);
        return -1;
    }

    for (Py_ssize_t i = 0; i < nargs; i++) {
        bound_out[i] = PyTuple_GetItem(args_tuple, i);
        if (!bound_out[i]) {
            return -1;
        }
    }

    if (kwds_dict) {
        PyObject *items = PyDict_Items(kwds_dict);
        if (!items) {
            return -1;
        }
        Py_ssize_t nkw = PyList_Size(items);
        if (nkw < 0) {
            Py_DECREF(items);
            return -1;
        }
        for (Py_ssize_t k = 0; k < nkw; k++) {
            PyObject *pair = PySequence_GetItem(items, k);
            if (!pair) {
                Py_DECREF(items);
                return -1;
            }
            PyObject *kw = PyTuple_GetItem(pair, 0);
            PyObject *val = PyTuple_GetItem(pair, 1);
            if (!kw || !val || !PyUnicode_Check(kw)) {
                Py_DECREF(pair);
                Py_DECREF(items);
                PyErr_SetString(PyExc_TypeError, "keywords must be strings");
                return -1;
            }
            Py_ssize_t match = -1;
            for (Py_ssize_t i = 0; i < nparams; i++) {
                int cmp = PyUnicode_CompareWithASCIIString(kw, specs[i].name);
                if (cmp == 0) {
                    match = i;
                    break;
                }
                if (PyErr_Occurred()) {
                    Py_DECREF(pair);
                    Py_DECREF(items);
                    return -1;
                }
            }
            if (match < 0) {
                Py_ssize_t kw_len = 0;
                const char *kw_utf8 = PyUnicode_AsUTF8AndSize(kw, &kw_len);
                if (kw_utf8) {
                    int klen = kw_len > (Py_ssize_t)INT_MAX ? INT_MAX : (int)kw_len;
                    PyErr_Format(PyExc_TypeError, "%s() got an unexpected keyword argument '%.*s'", fn_name, klen, kw_utf8);
                } else {
                    PyErr_Format(PyExc_TypeError, "%s() got an unexpected keyword argument", fn_name);
                }
                Py_DECREF(pair);
                Py_DECREF(items);
                return -1;
            }
            if (specs[match].kind == NOXPY_PARAM_POS_ONLY) {
                PyErr_Format(
                    PyExc_TypeError,
                    "%s() got some positional-only arguments passed as keyword arguments: '%s'",
                    fn_name,
                    specs[match].name);
                Py_DECREF(pair);
                Py_DECREF(items);
                return -1;
            }
            if (bound_out[match] != NULL) {
                PyErr_Format(PyExc_TypeError, "%s() got multiple values for argument '%s'", fn_name, specs[match].name);
                Py_DECREF(pair);
                Py_DECREF(items);
                return -1;
            }
            bound_out[match] = val;
            Py_DECREF(pair);
        }
        Py_DECREF(items);
    }

    for (Py_ssize_t i = 0; i < nparams; i++) {
        if (bound_out[i] == NULL && !specs[i].has_default) {
            if (specs[i].kind == NOXPY_PARAM_KW_ONLY) {
                PyErr_Format(PyExc_TypeError, "%s() missing required keyword-only argument: '%s'", fn_name, specs[i].name);
            } else {
                PyErr_Format(PyExc_TypeError, "%s() missing required argument: '%s'", fn_name, specs[i].name);
            }
            return -1;
        }
    }
    return 0;
}

void noxpy_conv_guard_init(NoxPyConvGuard *g, size_t max_depth, size_t max_items) {
    if (!g) {
        return;
    }
    g->depth = 0;
    g->max_depth = (max_depth == 0 || max_depth > NOXPY_MAX_CONV_DEPTH) ? NOXPY_MAX_CONV_DEPTH : max_depth;
    g->items = 0;
    g->max_items = max_items == 0 ? NOXPY_DEFAULT_MAX_ITEMS : max_items;
    for (size_t i = 0; i < NOXPY_MAX_CONV_DEPTH; i++) {
        g->visited[i] = NULL;
    }
}

int noxpy_conv_guard_enter(NoxPyConvGuard *g, const void *container_ptr, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!g || !container_ptr) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid conversion guard");
        return -1;
    }
    for (size_t i = 0; i < g->depth; i++) {
        if (g->visited[i] == container_ptr) {
            PyErr_Format(PyExc_ValueError, "argument '%s' contains a cyclic reference", label);
            return -1;
        }
    }
    if (g->depth >= g->max_depth) {
        PyErr_Format(PyExc_RecursionError, "argument '%s' exceeds maximum nesting depth (%zu)", label, g->max_depth);
        return -1;
    }
    g->visited[g->depth] = container_ptr;
    g->depth++;
    return 0;
}

void noxpy_conv_guard_leave(NoxPyConvGuard *g) {
    if (g && g->depth > 0) {
        g->depth--;
        g->visited[g->depth] = NULL;
    }
}

int noxpy_conv_guard_account(NoxPyConvGuard *g, size_t add_items, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!g) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid conversion guard");
        return -1;
    }
    if (add_items > g->max_items || g->items > g->max_items - add_items) {
        PyErr_Format(PyExc_ValueError, "argument '%s' exceeds maximum element limit (%zu)", label, g->max_items);
        return -1;
    }
    g->items += add_items;
    return 0;
}

int noxpy_py_to_opt_i64_strict(PyObject *obj, int *has_value, int64_t *out, const char *arg_name) {
    if (!obj || !has_value || !out) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid null optional int argument");
        return -1;
    }
    if (Py_IsNone(obj)) {
        *has_value = 0;
        *out = 0;
        return 0;
    }
    *has_value = 1;
    return noxpy_py_to_i64_strict(obj, out, arg_name);
}

PyObject *noxpy_opt_i64_to_py(int has_value, int64_t val) {
    if (!has_value) {
        Py_RETURN_NONE;
    }
    return noxpy_i64_to_py(val);
}

int noxpy_py_validate_acyclic_tree(PyObject *obj, NoxPyConvGuard *g, const char *arg_name) {
    if (!obj || !g) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid container tree validation argument");
        return -1;
    }
    if (noxpy_conv_guard_account(g, 1, arg_name) < 0) {
        return -1;
    }
    if (PyList_Check(obj)) {
        if (noxpy_conv_guard_enter(g, (const void *)obj, arg_name) < 0) {
            return -1;
        }
        /* Atomic snapshot safe against concurrent 3.15t list mutation. */
        PyObject *snap = PySequence_Tuple(obj);
        if (!snap) {
            noxpy_conv_guard_leave(g);
            return -1;
        }
        Py_ssize_t n = PyTuple_Size(snap);
        int rc = 0;
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *elem = PyTuple_GetItem(snap, i);
            if (!elem || noxpy_py_validate_acyclic_tree(elem, g, arg_name) < 0) {
                rc = -1;
                break;
            }
        }
        Py_DECREF(snap);
        noxpy_conv_guard_leave(g);
        return rc;
    }
    if (PyTuple_Check(obj)) {
        if (noxpy_conv_guard_enter(g, (const void *)obj, arg_name) < 0) {
            return -1;
        }
        Py_ssize_t n = PyTuple_Size(obj);
        int rc = 0;
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *elem = PyTuple_GetItem(obj, i);
            if (!elem || noxpy_py_validate_acyclic_tree(elem, g, arg_name) < 0) {
                rc = -1;
                break;
            }
        }
        noxpy_conv_guard_leave(g);
        return rc;
    }
    if (PyDict_Check(obj)) {
        if (noxpy_conv_guard_enter(g, (const void *)obj, arg_name) < 0) {
            return -1;
        }
        /* Atomic snapshot list of (k, v) pairs safe under 3.15t concurrent mutation. */
        PyObject *items = PyDict_Items(obj);
        if (!items) {
            noxpy_conv_guard_leave(g);
            return -1;
        }
        Py_ssize_t n = PyList_Size(items);
        int rc = 0;
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *pair = PySequence_GetItem(items, i);
            if (!pair) {
                rc = -1;
                break;
            }
            PyObject *k = PyTuple_GetItem(pair, 0);
            PyObject *v = PyTuple_GetItem(pair, 1);
            if (!k || !v ||
                noxpy_py_validate_acyclic_tree(k, g, arg_name) < 0 ||
                noxpy_py_validate_acyclic_tree(v, g, arg_name) < 0) {
                Py_DECREF(pair);
                rc = -1;
                break;
            }
            Py_DECREF(pair);
        }
        Py_DECREF(items);
        noxpy_conv_guard_leave(g);
        return rc;
    }
    return 0;
}

int noxpy_py_list_i64_snapshot_strict(
    PyObject *obj, NoxPyConvGuard *g, int64_t **out_buf, size_t *out_len, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!obj || !g || !out_buf || !out_len) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid list snapshot argument");
        return -1;
    }
    *out_buf = NULL;
    *out_len = 0;
    if (!PyList_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be list[int]", label);
        return -1;
    }
    if (noxpy_conv_guard_enter(g, (const void *)obj, label) < 0) {
        return -1;
    }
    PyObject *snap = PySequence_Tuple(obj);
    if (!snap) {
        noxpy_conv_guard_leave(g);
        return -1;
    }
    Py_ssize_t n = PyTuple_Size(snap);
    if (n < 0 || noxpy_conv_guard_account(g, (size_t)n, label) < 0) {
        Py_DECREF(snap);
        noxpy_conv_guard_leave(g);
        return -1;
    }
    size_t count = (size_t)n;
    if (count == 0) {
        Py_DECREF(snap);
        noxpy_conv_guard_leave(g);
        return 0;
    }
    if (count > SIZE_MAX / sizeof(int64_t)) {
        Py_DECREF(snap);
        noxpy_conv_guard_leave(g);
        PyErr_NoMemory();
        return -1;
    }
    int64_t *buf = (int64_t *)malloc(count * sizeof(int64_t));
    if (!buf) {
        Py_DECREF(snap);
        noxpy_conv_guard_leave(g);
        PyErr_NoMemory();
        return -1;
    }
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *elem = PyTuple_GetItem(snap, i);
        if (!elem || noxpy_py_to_i64_strict(elem, &buf[i], label) < 0) {
            free(buf);
            Py_DECREF(snap);
            noxpy_conv_guard_leave(g);
            return -1;
        }
    }
    Py_DECREF(snap);
    noxpy_conv_guard_leave(g);
    *out_buf = buf;
    *out_len = count;
    return 0;
}

PyObject *noxpy_i64_array_to_py_list(const int64_t *buf, size_t len) {
    if (len > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "noxpy: list length exceeds PY_SSIZE_T_MAX");
        return NULL;
    }
    PyObject *lst = PyList_New((Py_ssize_t)len);
    if (!lst) {
        return NULL;
    }
    for (size_t i = 0; i < len; i++) {
        PyObject *item = noxpy_i64_to_py(buf[i]);
        if (!item) {
            Py_DECREF(lst);
            return NULL;
        }
        PyList_SetItem(lst, (Py_ssize_t)i, item); /* steals item ref */
    }
    return lst;
}

void noxpy_free_buffer(void *p) {
    free(p);
}

PyObject *noxpy_py_dict_str_i64_roundtrip_strict(PyObject *obj, NoxPyConvGuard *g, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!obj || !g) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid dict conversion argument");
        return NULL;
    }
    if (!PyDict_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be dict[str, int]", label);
        return NULL;
    }
    if (noxpy_conv_guard_enter(g, (const void *)obj, label) < 0) {
        return NULL;
    }
    PyObject *items = PyDict_Items(obj);
    if (!items) {
        noxpy_conv_guard_leave(g);
        return NULL;
    }
    Py_ssize_t n = PyList_Size(items);
    if (n < 0 || noxpy_conv_guard_account(g, (size_t)n, label) < 0) {
        Py_DECREF(items);
        noxpy_conv_guard_leave(g);
        return NULL;
    }
    PyObject *out_dict = PyDict_New();
    if (!out_dict) {
        Py_DECREF(items);
        noxpy_conv_guard_leave(g);
        return NULL;
    }
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *pair = PySequence_GetItem(items, i);
        if (!pair) {
            Py_DECREF(out_dict);
            Py_DECREF(items);
            noxpy_conv_guard_leave(g);
            return NULL;
        }
        PyObject *k = PyTuple_GetItem(pair, 0);
        PyObject *v = PyTuple_GetItem(pair, 1);
        NoxHostSpan ksp = {0};
        int64_t ival = 0;
        if (!k || !v ||
            noxpy_py_to_utf8_span_strict(k, &ksp, label) < 0 ||
            noxpy_py_to_i64_strict(v, &ival, label) < 0) {
            Py_DECREF(pair);
            Py_DECREF(out_dict);
            Py_DECREF(items);
            noxpy_conv_guard_leave(g);
            return NULL;
        }
        PyObject *nk = noxpy_utf8_span_to_py(ksp);
        PyObject *nv = noxpy_i64_to_py(ival);
        if (!nk || !nv || PyDict_SetItem(out_dict, nk, nv) < 0) {
            Py_XDECREF(nk);
            Py_XDECREF(nv);
            Py_DECREF(pair);
            Py_DECREF(out_dict);
            Py_DECREF(items);
            noxpy_conv_guard_leave(g);
            return NULL;
        }
        Py_DECREF(nk);
        Py_DECREF(nv);
        Py_DECREF(pair);
    }
    Py_DECREF(items);
    noxpy_conv_guard_leave(g);
    return out_dict;
}

static void trim_type_slice(const char **ptr, size_t *len) {
    while (*len > 0 && ((*ptr)[0] == ' ' || (*ptr)[0] == '\t')) {
        (*ptr)++;
        (*len)--;
    }
    while (*len > 0 && ((*ptr)[*len - 1] == ' ' || (*ptr)[*len - 1] == '\t')) {
        (*len)--;
    }
}

static int slice_eq(const char *ptr, size_t len, const char *lit) {
    size_t lit_len = strlen(lit);
    return len == lit_len && memcmp(ptr, lit, lit_len) == 0;
}

static int slice_starts_ends(const char *ptr, size_t len, const char *prefix, const char *suffix) {
    size_t p_len = strlen(prefix);
    size_t s_len = strlen(suffix);
    if (len < p_len + s_len) {
        return 0;
    }
    return memcmp(ptr, prefix, p_len) == 0 && memcmp(ptr + len - s_len, suffix, s_len) == 0;
}

static int validate_v2_ops(const NoxPyHostOps *ops) {
    if (!ops || !ops->value_none || !ops->value_int || !ops->value_float || !ops->value_bool ||
        !ops->value_str || !ops->value_container || !ops->value_append || !ops->value_dict_set ||
        !ops->value_kind || !ops->value_get_int || !ops->value_get_float || !ops->value_get_span ||
        !ops->value_len || !ops->value_item || !ops->value_dict_entry || !ops->value_release) {
        PyErr_SetString(PyExc_SystemError, "noxpy: host ops missing ABI v2 value-tree callbacks");
        return -1;
    }
    return 0;
}

static int py_to_hv_rec(
    const NoxPyHostOps *ops,
    NoxHostContext *ctx,
    PyObject *obj,
    const char *spec,
    size_t spec_len,
    NoxPyConvGuard *g,
    NoxHostValue *out_val,
    const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    trim_type_slice(&spec, &spec_len);
    if (spec_len == 0 || !obj || !out_val) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid value conversion specification");
        return -1;
    }
    if (noxpy_conv_guard_account(g, 1, label) < 0) {
        return -1;
    }

    /* Check top-level `T | None` union at bracket depth 0. */
    size_t bracket_depth = 0;
    for (size_t i = 0; i < spec_len; i++) {
        char ch = spec[i];
        if (ch == '[') {
            bracket_depth++;
        } else if (ch == ']') {
            if (bracket_depth == 0) {
                PyErr_Format(PyExc_TypeError, "argument '%s': unbalanced type specification", label);
                return -1;
            }
            bracket_depth--;
        } else if (ch == '|' && bracket_depth == 0) {
            const char *lhs = spec;
            size_t lhs_len = i;
            const char *rhs = spec + i + 1;
            size_t rhs_len = spec_len - i - 1;
            trim_type_slice(&lhs, &lhs_len);
            trim_type_slice(&rhs, &rhs_len);
            if (!slice_eq(rhs, rhs_len, "None")) {
                PyErr_Format(PyExc_TypeError, "argument '%s': unsupported union type", label);
                return -1;
            }
            if (Py_IsNone(obj)) {
                NoxHostStatus rc = ops->value_none(ctx, out_val);
                if (rc != NOX_HOST_OK) {
                    noxpy_set_exception_from_host(ops, rc, NULL);
                    return -1;
                }
                return 0;
            }
            return py_to_hv_rec(ops, ctx, obj, lhs, lhs_len, g, out_val, label);
        }
    }
    if (bracket_depth != 0) {
        PyErr_Format(PyExc_TypeError, "argument '%s': unbalanced type specification", label);
        return -1;
    }

    if (slice_eq(spec, spec_len, "int")) {
        int64_t v = 0;
        if (noxpy_py_to_i64_strict(obj, &v, label) < 0) {
            return -1;
        }
        NoxHostStatus rc = ops->value_int(ctx, v, out_val);
        if (rc != NOX_HOST_OK) {
            noxpy_set_exception_from_host(ops, rc, NULL);
            return -1;
        }
        return 0;
    }
    if (slice_eq(spec, spec_len, "float")) {
        double v = 0.0;
        if (noxpy_py_to_f64_strict(obj, &v, label) < 0) {
            return -1;
        }
        NoxHostStatus rc = ops->value_float(ctx, v, out_val);
        if (rc != NOX_HOST_OK) {
            noxpy_set_exception_from_host(ops, rc, NULL);
            return -1;
        }
        return 0;
    }
    if (slice_eq(spec, spec_len, "bool")) {
        uint8_t v = 0;
        if (noxpy_py_to_bool_strict(obj, &v, label) < 0) {
            return -1;
        }
        NoxHostStatus rc = ops->value_bool(ctx, v, out_val);
        if (rc != NOX_HOST_OK) {
            noxpy_set_exception_from_host(ops, rc, NULL);
            return -1;
        }
        return 0;
    }
    if (slice_eq(spec, spec_len, "str")) {
        NoxHostSpan sp = {0};
        if (noxpy_py_to_utf8_span_strict(obj, &sp, label) < 0) {
            return -1;
        }
        NoxHostStatus rc = ops->value_str(ctx, sp.data, sp.len, 0, out_val);
        if (rc != NOX_HOST_OK) {
            noxpy_set_exception_from_host(ops, rc, NULL);
            return -1;
        }
        return 0;
    }
    if (slice_eq(spec, spec_len, "bytes")) {
        NoxHostSpan sp = {0};
        if (noxpy_py_to_bytes_span_strict(obj, &sp, label) < 0) {
            return -1;
        }
        NoxHostStatus rc = ops->value_str(ctx, sp.data, sp.len, 1, out_val);
        if (rc != NOX_HOST_OK) {
            noxpy_set_exception_from_host(ops, rc, NULL);
            return -1;
        }
        return 0;
    }
    if (slice_eq(spec, spec_len, "None")) {
        if (!Py_IsNone(obj)) {
            PyErr_Format(PyExc_TypeError, "argument '%s' must be None", label);
            return -1;
        }
        NoxHostStatus rc = ops->value_none(ctx, out_val);
        if (rc != NOX_HOST_OK) {
            noxpy_set_exception_from_host(ops, rc, NULL);
            return -1;
        }
        return 0;
    }

    if (slice_starts_ends(spec, spec_len, "list[", "]")) {
        if (!PyList_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "argument '%s' must be list", label);
            return -1;
        }
        if (noxpy_conv_guard_enter(g, (const void *)obj, label) < 0) {
            return -1;
        }
        PyObject *snap = PySequence_Tuple(obj);
        if (!snap) {
            noxpy_conv_guard_leave(g);
            return -1;
        }
        NoxHostValue cont = 0;
        NoxHostStatus rc = ops->value_container(ctx, NOX_HOST_VALUE_LIST, &cont);
        if (rc != NOX_HOST_OK) {
            noxpy_set_exception_from_host(ops, rc, NULL);
            Py_DECREF(snap);
            noxpy_conv_guard_leave(g);
            return -1;
        }
        const char *inner = spec + 5;
        size_t inner_len = spec_len - 6;
        Py_ssize_t n = PyTuple_Size(snap);
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *elem = PyTuple_GetItem(snap, i);
            NoxHostValue child = 0;
            if (!elem || py_to_hv_rec(ops, ctx, elem, inner, inner_len, g, &child, label) < 0) {
                (void)ops->value_release(ctx, cont);
                Py_DECREF(snap);
                noxpy_conv_guard_leave(g);
                return -1;
            }
            rc = ops->value_append(ctx, cont, child);
            (void)ops->value_release(ctx, child);
            if (rc != NOX_HOST_OK) {
                noxpy_set_exception_from_host(ops, rc, NULL);
                (void)ops->value_release(ctx, cont);
                Py_DECREF(snap);
                noxpy_conv_guard_leave(g);
                return -1;
            }
        }
        Py_DECREF(snap);
        noxpy_conv_guard_leave(g);
        *out_val = cont;
        return 0;
    }

    if (slice_starts_ends(spec, spec_len, "tuple[", "]")) {
        if (!PyTuple_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "argument '%s' must be tuple", label);
            return -1;
        }
        const char *inner = spec + 6;
        size_t inner_len = spec_len - 7;
        trim_type_slice(&inner, &inner_len);
        if (inner_len == 0) {
            PyErr_Format(PyExc_TypeError, "argument '%s': empty tuple type unsupported", label);
            return -1;
        }
        Py_ssize_t expected_arity = 1;
        size_t td = 0;
        for (size_t i = 0; i < inner_len; i++) {
            if (inner[i] == '[') td++;
            else if (inner[i] == ']') td--;
            else if (inner[i] == ',' && td == 0) expected_arity++;
        }
        Py_ssize_t actual_arity = PyTuple_Size(obj);
        if (actual_arity != expected_arity) {
            PyErr_Format(
                PyExc_TypeError,
                "argument '%s' must be %zd-tuple, got length %zd",
                label,
                expected_arity,
                actual_arity);
            return -1;
        }
        if (noxpy_conv_guard_enter(g, (const void *)obj, label) < 0) {
            return -1;
        }
        NoxHostValue cont = 0;
        NoxHostStatus rc = ops->value_container(ctx, NOX_HOST_VALUE_TUPLE, &cont);
        if (rc != NOX_HOST_OK) {
            noxpy_set_exception_from_host(ops, rc, NULL);
            noxpy_conv_guard_leave(g);
            return -1;
        }
        size_t seg_start = 0;
        td = 0;
        Py_ssize_t elem_idx = 0;
        for (size_t i = 0; i <= inner_len; i++) {
            if (i < inner_len) {
                if (inner[i] == '[') td++;
                else if (inner[i] == ']') td--;
            }
            if (i == inner_len || (inner[i] == ',' && td == 0)) {
                const char *sub = inner + seg_start;
                size_t sub_len = i - seg_start;
                PyObject *elem = PyTuple_GetItem(obj, elem_idx);
                NoxHostValue child = 0;
                if (!elem || py_to_hv_rec(ops, ctx, elem, sub, sub_len, g, &child, label) < 0) {
                    (void)ops->value_release(ctx, cont);
                    noxpy_conv_guard_leave(g);
                    return -1;
                }
                rc = ops->value_append(ctx, cont, child);
                (void)ops->value_release(ctx, child);
                if (rc != NOX_HOST_OK) {
                    noxpy_set_exception_from_host(ops, rc, NULL);
                    (void)ops->value_release(ctx, cont);
                    noxpy_conv_guard_leave(g);
                    return -1;
                }
                elem_idx++;
                seg_start = i + 1;
            }
        }
        noxpy_conv_guard_leave(g);
        *out_val = cont;
        return 0;
    }

    if (slice_starts_ends(spec, spec_len, "dict[", "]")) {
        if (!PyDict_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "argument '%s' must be dict", label);
            return -1;
        }
        const char *inner = spec + 5;
        size_t inner_len = spec_len - 6;
        size_t td = 0;
        size_t comma_pos = SIZE_MAX;
        for (size_t i = 0; i < inner_len; i++) {
            if (inner[i] == '[') td++;
            else if (inner[i] == ']') td--;
            else if (inner[i] == ',' && td == 0) {
                if (comma_pos != SIZE_MAX) {
                    comma_pos = SIZE_MAX;
                    break;
                }
                comma_pos = i;
            }
        }
        if (comma_pos == SIZE_MAX) {
            PyErr_Format(PyExc_TypeError, "argument '%s': invalid dict type specification", label);
            return -1;
        }
        const char *key_spec = inner;
        size_t key_len = comma_pos;
        const char *val_spec = inner + comma_pos + 1;
        size_t val_len = inner_len - comma_pos - 1;

        if (noxpy_conv_guard_enter(g, (const void *)obj, label) < 0) {
            return -1;
        }
        PyObject *items = PyDict_Items(obj);
        if (!items) {
            noxpy_conv_guard_leave(g);
            return -1;
        }
        NoxHostValue dict_hv = 0;
        NoxHostStatus rc = ops->value_container(ctx, NOX_HOST_VALUE_DICT, &dict_hv);
        if (rc != NOX_HOST_OK) {
            noxpy_set_exception_from_host(ops, rc, NULL);
            Py_DECREF(items);
            noxpy_conv_guard_leave(g);
            return -1;
        }
        Py_ssize_t n = PyList_Size(items);
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *pair = PySequence_GetItem(items, i);
            if (!pair) {
                (void)ops->value_release(ctx, dict_hv);
                Py_DECREF(items);
                noxpy_conv_guard_leave(g);
                return -1;
            }
            PyObject *k = PyTuple_GetItem(pair, 0);
            PyObject *v = PyTuple_GetItem(pair, 1);
            NoxHostValue kh = 0;
            NoxHostValue vh = 0;
            if (!k || !v ||
                py_to_hv_rec(ops, ctx, k, key_spec, key_len, g, &kh, label) < 0 ||
                py_to_hv_rec(ops, ctx, v, val_spec, val_len, g, &vh, label) < 0) {
                if (kh) (void)ops->value_release(ctx, kh);
                if (vh) (void)ops->value_release(ctx, vh);
                Py_DECREF(pair);
                (void)ops->value_release(ctx, dict_hv);
                Py_DECREF(items);
                noxpy_conv_guard_leave(g);
                return -1;
            }
            rc = ops->value_dict_set(ctx, dict_hv, kh, vh);
            (void)ops->value_release(ctx, kh);
            (void)ops->value_release(ctx, vh);
            Py_DECREF(pair);
            if (rc != NOX_HOST_OK) {
                noxpy_set_exception_from_host(ops, rc, NULL);
                (void)ops->value_release(ctx, dict_hv);
                Py_DECREF(items);
                noxpy_conv_guard_leave(g);
                return -1;
            }
        }
        Py_DECREF(items);
        noxpy_conv_guard_leave(g);
        *out_val = dict_hv;
        return 0;
    }

    PyErr_Format(PyExc_TypeError, "argument '%s': unsupported type specification", label);
    return -1;
}

int noxpy_py_to_host_value(
    const NoxPyHostOps *ops,
    NoxHostContext *ctx,
    PyObject *obj,
    const char *type_spec,
    NoxHostValue *out_val,
    const char *arg_name) {
    if (!ctx || !type_spec || !out_val) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid null argument to noxpy_py_to_host_value");
        return -1;
    }
    if (validate_v2_ops(ops) < 0) {
        return -1;
    }
    *out_val = 0;
    NoxPyConvGuard g;
    noxpy_conv_guard_init(&g, NOXPY_MAX_CONV_DEPTH, NOXPY_DEFAULT_MAX_ITEMS);
    return py_to_hv_rec(ops, ctx, obj, type_spec, strlen(type_spec), &g, out_val, arg_name);
}

static PyObject *hv_to_py_rec(
    const NoxPyHostOps *ops,
    NoxHostContext *ctx,
    NoxHostValue val,
    size_t depth) {
    if (depth >= NOXPY_MAX_CONV_DEPTH) {
        PyErr_Format(
            PyExc_RecursionError,
            "returned host value exceeds maximum nesting depth (%u)",
            NOXPY_MAX_CONV_DEPTH);
        return NULL;
    }
    int32_t kind = -1;
    NoxHostStatus rc = ops->value_kind(ctx, val, &kind);
    if (rc != NOX_HOST_OK) {
        noxpy_set_exception_from_host(ops, rc, NULL);
        return NULL;
    }
    switch (kind) {
        case NOX_HOST_VALUE_NONE:
            Py_RETURN_NONE;
        case NOX_HOST_VALUE_INT: {
            int64_t v = 0;
            rc = ops->value_get_int(ctx, val, &v);
            if (rc != NOX_HOST_OK) {
                noxpy_set_exception_from_host(ops, rc, NULL);
                return NULL;
            }
            return noxpy_i64_to_py(v);
        }
        case NOX_HOST_VALUE_BOOL: {
            int64_t v = 0;
            rc = ops->value_get_int(ctx, val, &v);
            if (rc != NOX_HOST_OK) {
                noxpy_set_exception_from_host(ops, rc, NULL);
                return NULL;
            }
            return noxpy_bool_to_py((uint8_t)(v != 0));
        }
        case NOX_HOST_VALUE_FLOAT: {
            double v = 0.0;
            rc = ops->value_get_float(ctx, val, &v);
            if (rc != NOX_HOST_OK) {
                noxpy_set_exception_from_host(ops, rc, NULL);
                return NULL;
            }
            return noxpy_f64_to_py(v);
        }
        case NOX_HOST_VALUE_STR: {
            NoxHostSpan sp = {0};
            rc = ops->value_get_span(ctx, val, &sp);
            if (rc != NOX_HOST_OK) {
                noxpy_set_exception_from_host(ops, rc, NULL);
                return NULL;
            }
            return noxpy_utf8_span_to_py(sp);
        }
        case NOX_HOST_VALUE_BYTES: {
            NoxHostSpan sp = {0};
            rc = ops->value_get_span(ctx, val, &sp);
            if (rc != NOX_HOST_OK) {
                noxpy_set_exception_from_host(ops, rc, NULL);
                return NULL;
            }
            return noxpy_bytes_span_to_py(sp);
        }
        case NOX_HOST_VALUE_LIST: {
            size_t len = 0;
            rc = ops->value_len(ctx, val, &len);
            if (rc != NOX_HOST_OK) {
                noxpy_set_exception_from_host(ops, rc, NULL);
                return NULL;
            }
            if (len > (size_t)PY_SSIZE_T_MAX) {
                PyErr_SetString(PyExc_OverflowError, "noxpy: list length exceeds PY_SSIZE_T_MAX");
                return NULL;
            }
            PyObject *lst = PyList_New((Py_ssize_t)len);
            if (!lst) {
                return NULL;
            }
            for (size_t i = 0; i < len; i++) {
                NoxHostValue child = 0;
                rc = ops->value_item(ctx, val, i, &child);
                if (rc != NOX_HOST_OK) {
                    noxpy_set_exception_from_host(ops, rc, NULL);
                    Py_DECREF(lst);
                    return NULL;
                }
                PyObject *py_elem = hv_to_py_rec(ops, ctx, child, depth + 1);
                (void)ops->value_release(ctx, child);
                if (!py_elem) {
                    Py_DECREF(lst);
                    return NULL;
                }
                PyList_SetItem(lst, (Py_ssize_t)i, py_elem); /* steals py_elem */
            }
            return lst;
        }
        case NOX_HOST_VALUE_TUPLE: {
            size_t len = 0;
            rc = ops->value_len(ctx, val, &len);
            if (rc != NOX_HOST_OK) {
                noxpy_set_exception_from_host(ops, rc, NULL);
                return NULL;
            }
            if (len > (size_t)PY_SSIZE_T_MAX) {
                PyErr_SetString(PyExc_OverflowError, "noxpy: tuple length exceeds PY_SSIZE_T_MAX");
                return NULL;
            }
            PyObject *tup = PyTuple_New((Py_ssize_t)len);
            if (!tup) {
                return NULL;
            }
            for (size_t i = 0; i < len; i++) {
                NoxHostValue child = 0;
                rc = ops->value_item(ctx, val, i, &child);
                if (rc != NOX_HOST_OK) {
                    noxpy_set_exception_from_host(ops, rc, NULL);
                    Py_DECREF(tup);
                    return NULL;
                }
                PyObject *py_elem = hv_to_py_rec(ops, ctx, child, depth + 1);
                (void)ops->value_release(ctx, child);
                if (!py_elem) {
                    Py_DECREF(tup);
                    return NULL;
                }
                PyTuple_SetItem(tup, (Py_ssize_t)i, py_elem); /* steals py_elem */
            }
            return tup;
        }
        case NOX_HOST_VALUE_DICT: {
            size_t len = 0;
            rc = ops->value_len(ctx, val, &len);
            if (rc != NOX_HOST_OK) {
                noxpy_set_exception_from_host(ops, rc, NULL);
                return NULL;
            }
            PyObject *dct = PyDict_New();
            if (!dct) {
                return NULL;
            }
            for (size_t i = 0; i < len; i++) {
                NoxHostValue kh = 0;
                NoxHostValue vh = 0;
                rc = ops->value_dict_entry(ctx, val, i, &kh, &vh);
                if (rc != NOX_HOST_OK) {
                    noxpy_set_exception_from_host(ops, rc, NULL);
                    Py_DECREF(dct);
                    return NULL;
                }
                PyObject *py_k = hv_to_py_rec(ops, ctx, kh, depth + 1);
                (void)ops->value_release(ctx, kh);
                PyObject *py_v = py_k ? hv_to_py_rec(ops, ctx, vh, depth + 1) : NULL;
                (void)ops->value_release(ctx, vh);
                if (!py_k || !py_v) {
                    Py_XDECREF(py_k);
                    Py_XDECREF(py_v);
                    Py_DECREF(dct);
                    return NULL;
                }
                int set_rc = PyDict_SetItem(dct, py_k, py_v);
                Py_DECREF(py_k);
                Py_DECREF(py_v);
                if (set_rc < 0) {
                    Py_DECREF(dct);
                    return NULL;
                }
            }
            return dct;
        }
        default:
            PyErr_Format(PyExc_RuntimeError, "noxpy: unknown NoxHostValue kind (%d)", (int)kind);
            return NULL;
    }
}

PyObject *noxpy_host_value_to_py(
    const NoxPyHostOps *ops,
    NoxHostContext *ctx,
    NoxHostValue val) {
    if (!ctx) {
        PyErr_SetString(PyExc_SystemError, "noxpy: NULL context in noxpy_host_value_to_py");
        return NULL;
    }
    if (validate_v2_ops(ops) < 0) {
        return NULL;
    }
    return hv_to_py_rec(ops, ctx, val, 0);
}

static _Atomic uint64_t g_noxpy_instance_gen = 1;

uint64_t noxpy_current_tid(void) {
#if defined(__APPLE__)
    uint64_t tid = 0;
    if (pthread_threadid_np(NULL, &tid) == 0 && tid != 0) {
        return tid;
    }
#endif
    return (uint64_t)(uintptr_t)pthread_self();
}

int noxpy_host_context_create_and_init(const NoxPyHostOps *ops, NoxHostContext **out_ctx) {
    return create_and_init_context(ops, out_ctx);
}

void noxpy_host_context_fini_and_destroy(const NoxPyHostOps *ops, NoxHostContext *ctx) {
    destroy_host_context(ops, ctx);
}

static uint64_t next_instance_gen(void) {
    uint64_t g = atomic_fetch_add_explicit(&g_noxpy_instance_gen, 1, memory_order_relaxed);
    if (g == 0) {
        g = atomic_fetch_add_explicit(&g_noxpy_instance_gen, 1, memory_order_relaxed);
    }
    return g;
}

int noxpy_obj_init(
    NoxPyObjectData *data,
    PyObject *owner_module,
    const NoxPyHostOps *ops,
    NoxHostContext *owner_ctx,
    NoxHostHandle handle,
    uint64_t type_tag,
    uint8_t thread_policy) {
    if (!data || !owner_module || !ops || !owner_ctx || handle == 0) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid arguments to noxpy_obj_init");
        return -1;
    }
    memset(data, 0, sizeof(*data));
    if (pthread_mutex_init(&data->lock, NULL) != 0) {
        PyErr_SetString(PyExc_RuntimeError, "noxpy: failed to initialize instance mutex");
        return -1;
    }
    data->lock_ready = 1u;
    data->owner_module = Py_NewRef(owner_module);
    data->host_ops = ops;
    data->owner_ctx = owner_ctx;
    data->handle = handle;
    data->type_tag = type_tag;
    data->instance_gen = next_instance_gen();
    data->owner_tid = noxpy_current_tid();
    data->thread_policy = thread_policy;
    data->closed = 0u;
    data->initialized = 1u;
    return 0;
}

PyObject *noxpy_obj_new_from_handle(
    PyTypeObject *cls,
    PyObject *owner_module,
    const NoxPyHostOps *ops,
    NoxHostContext *owner_ctx,
    NoxHostHandle handle,
    uint64_t type_tag,
    uint8_t thread_policy) {
    if (!cls || !owner_module || !ops || !owner_ctx || handle == 0) {
        if (handle != 0 && owner_ctx && ops && ops->handle_release) {
            Py_BEGIN_ALLOW_THREADS
            (void)ops->handle_release(owner_ctx, handle);
            Py_END_ALLOW_THREADS
        }
        if (owner_ctx && ops) {
            Py_BEGIN_ALLOW_THREADS
            destroy_host_context(ops, owner_ctx);
            Py_END_ALLOW_THREADS
        }
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid arguments to noxpy_obj_new_from_handle");
        return NULL;
    }
    allocfunc tp_alloc = (allocfunc)PyType_GetSlot(cls, Py_tp_alloc);
    if (!tp_alloc) {
        Py_BEGIN_ALLOW_THREADS
        if (ops->handle_release) {
            (void)ops->handle_release(owner_ctx, handle);
        }
        destroy_host_context(ops, owner_ctx);
        Py_END_ALLOW_THREADS
        PyErr_SetString(PyExc_SystemError, "noxpy: native type missing Py_tp_alloc slot");
        return NULL;
    }
    PyObject *obj = tp_alloc(cls, 0);
    if (!obj) {
        Py_BEGIN_ALLOW_THREADS
        if (ops->handle_release) {
            (void)ops->handle_release(owner_ctx, handle);
        }
        destroy_host_context(ops, owner_ctx);
        Py_END_ALLOW_THREADS
        return NULL;
    }
    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(obj, cls);
    if (!data || noxpy_obj_init(data, owner_module, ops, owner_ctx, handle, type_tag, thread_policy) < 0) {
        if (data && data->initialized) {
            noxpy_obj_close(data);
        } else {
            Py_BEGIN_ALLOW_THREADS
            if (ops->handle_release) {
                (void)ops->handle_release(owner_ctx, handle);
            }
            destroy_host_context(ops, owner_ctx);
            Py_END_ALLOW_THREADS
        }
        Py_DECREF(obj);
        if (!PyErr_Occurred()) {
            PyErr_SetString(PyExc_SystemError, "noxpy: failed to initialize returned handle wrapper");
        }
        return NULL;
    }
    return obj;
}

int noxpy_obj_enter_call(
    NoxPyObjectData *data,
    uint64_t expected_type_tag,
    NoxHostContext **out_ctx,
    NoxHostHandle *out_handle) {
    if (!data || !out_ctx || !out_handle) {
        PyErr_SetString(PyExc_SystemError, "noxpy: null argument to noxpy_obj_enter_call");
        return -1;
    }
    if (!data->initialized) {
        PyErr_SetString(PyExc_RuntimeError, "noxpy: native object is not initialized");
        return -1;
    }
    if (expected_type_tag != 0 && data->type_tag != expected_type_tag) {
        PyErr_SetString(PyExc_TypeError, "noxpy: native object type tag mismatch");
        return -1;
    }
    if (data->thread_policy == NOXPY_POLICY_THREAD_AFFINE && noxpy_current_tid() != data->owner_tid) {
        PyErr_SetString(PyExc_RuntimeError, "noxpy: thread_affine instance accessed from foreign thread");
        return -1;
    }
    if (!data->lock_ready) {
        PyErr_SetString(PyExc_ValueError, "noxpy: operation on closed native object");
        return -1;
    }

    Py_BEGIN_ALLOW_THREADS
    pthread_mutex_lock(&data->lock);
    Py_END_ALLOW_THREADS
    if (data->closed || !data->owner_ctx || data->handle == 0 || !data->owner_module || !data->host_ops) {
        pthread_mutex_unlock(&data->lock);
        PyErr_SetString(PyExc_ValueError, "noxpy: operation on closed native object");
        return -1;
    }
    if (data->host_ops->handle_is_valid &&
        data->host_ops->handle_is_valid(data->owner_ctx, data->handle) == 0) {
        pthread_mutex_unlock(&data->lock);
        PyErr_SetString(PyExc_ValueError, "noxpy: invalid or released native handle");
        return -1;
    }
    NoxPyModuleState *st = (NoxPyModuleState *)PyModule_GetState(data->owner_module);
    if (!st || noxpy_module_lease_acquire(st) < 0) {
        pthread_mutex_unlock(&data->lock);
        return -1;
    }
    *out_ctx = data->owner_ctx;
    *out_handle = data->handle;
    return 0;
}

void noxpy_obj_leave_call(NoxPyObjectData *data) {
    if (!data) {
        return;
    }
    if (data->owner_module) {
        NoxPyModuleState *st = (NoxPyModuleState *)PyModule_GetState(data->owner_module);
        if (st) {
            noxpy_module_lease_release(st);
        }
    }
    if (data->lock_ready) {
        pthread_mutex_unlock(&data->lock);
    }
}

int noxpy_obj_is_closed(NoxPyObjectData *data) {
    if (!data || !data->initialized) {
        return 1;
    }
    if (!data->lock_ready) {
        return 1;
    }
    int closed;
    Py_BEGIN_ALLOW_THREADS
    pthread_mutex_lock(&data->lock);
    closed = (data->closed || !data->owner_ctx || data->handle == 0) ? 1 : 0;
    pthread_mutex_unlock(&data->lock);
    Py_END_ALLOW_THREADS
    return closed;
}

static void obj_close_impl(NoxPyObjectData *data, int release_gil) {
    if (!data) {
        return;
    }
    PyObject *local_mod = NULL;
    const NoxPyHostOps *local_ops = NULL;
    NoxHostContext *local_ctx = NULL;
    NoxHostHandle local_handle = 0;

    if (data->lock_ready) {
        if (release_gil) {
            Py_BEGIN_ALLOW_THREADS
            pthread_mutex_lock(&data->lock);
            Py_END_ALLOW_THREADS
        } else {
            pthread_mutex_lock(&data->lock);
        }
    }
    local_mod = data->owner_module;
    local_ops = data->host_ops;
    local_ctx = data->owner_ctx;
    local_handle = data->handle;
    data->closed = 1u;
    data->owner_module = NULL;
    data->host_ops = NULL;
    data->owner_ctx = NULL;
    data->handle = 0;
    if (data->lock_ready) {
        pthread_mutex_unlock(&data->lock);
    }

    /* Release native handle/context and drop PyObject ref STRICTLY outside locks (AGENTS.md §9/§10.4). */
    if (release_gil) {
        Py_BEGIN_ALLOW_THREADS
        if (local_handle != 0 && local_ctx && local_ops && local_ops->handle_release) {
            (void)local_ops->handle_release(local_ctx, local_handle);
        }
        if (local_ctx && local_ops) {
            destroy_host_context(local_ops, local_ctx);
        }
        Py_END_ALLOW_THREADS
    } else {
        if (local_handle != 0 && local_ctx && local_ops && local_ops->handle_release) {
            (void)local_ops->handle_release(local_ctx, local_handle);
        }
        if (local_ctx && local_ops) {
            destroy_host_context(local_ops, local_ctx);
        }
    }
    Py_XDECREF(local_mod);
}

void noxpy_obj_close(NoxPyObjectData *data) {
    obj_close_impl(data, 0);
}

int noxpy_obj_traverse(PyObject *self, visitproc visit, void *arg) {
    PyTypeObject *cls = Py_TYPE(self);
    Py_VISIT(cls);
    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData_DuringGC(self, cls);
    if (data && data->owner_module) {
        Py_VISIT(data->owner_module);
    }
    return 0;
}

int noxpy_obj_clear(PyObject *self) {
    PyTypeObject *cls = Py_TYPE(self);
    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData_DuringGC(self, cls);
    if (data) {
        noxpy_obj_close(data);
    }
    return 0;
}

void noxpy_obj_dealloc(PyObject *self) {
    PyObject_GC_UnTrack(self);
    PyTypeObject *cls = Py_TYPE(self);
    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, cls);
    if (data) {
        noxpy_obj_close(data);
        if (data->lock_ready) {
            pthread_mutex_destroy(&data->lock);
            data->lock_ready = 0u;
        }
        data->initialized = 0u;
    }
    freefunc tp_free = (freefunc)PyType_GetSlot(cls, Py_tp_free);
    if (tp_free) {
        tp_free(self);
    }
    Py_DECREF(cls);
}

PyObject *noxpy_obj_enter_cm(PyObject *self, PyObject *ignored) {
    (void)ignored;
    PyTypeObject *cls = Py_TYPE(self);
    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, cls);
    if (noxpy_obj_is_closed(data)) {
        PyErr_SetString(PyExc_ValueError, "noxpy: cannot enter context manager for closed native object");
        return NULL;
    }
    return Py_NewRef(self);
}

PyObject *noxpy_obj_exit_cm(PyObject *self, PyObject *const *args, Py_ssize_t nargs) {
    (void)args;
    (void)nargs;
    PyTypeObject *cls = Py_TYPE(self);
    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, cls);
    if (data) {
        obj_close_impl(data, 1);
    }
    Py_RETURN_FALSE;
}

PyObject *noxpy_obj_close_method(PyObject *self, PyObject *ignored) {
    (void)ignored;
    PyTypeObject *cls = Py_TYPE(self);
    NoxPyObjectData *data = (NoxPyObjectData *)PyObject_GetTypeData(self, cls);
    if (data) {
        obj_close_impl(data, 1);
    }
    Py_RETURN_NONE;
}
