#include "noxpy_abi.h"

#include <limits.h>
#include <sched.h>
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
        .abi_version = NOX_HOST_ABI_VERSION,
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
    if (!ops || ops->struct_size < sizeof(NoxPyHostOps) || ops->abi_version != NOX_HOST_ABI_VERSION) {
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

void noxpy_set_exception_from_host(const NoxPyHostOps *ops, NoxHostStatus status, NoxHostError *err) {
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

    PyObject *exc_type = pick_exc_type(status, class_name);
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
        if (bound_out[i] == NULL) {
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
