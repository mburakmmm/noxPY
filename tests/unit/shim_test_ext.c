#include "noxpy_abi.h"

/* Pure CPython 3.15 abi3t self-test extension exercising noxpy_shim.c primitive converters. */

static NoxHostStatus dummy_ctx_create(const NoxHostContextConfig *cfg, NoxHostContext **out_ctx) {
    (void)cfg;
    *out_ctx = (NoxHostContext *)(uintptr_t)1;
    return NOX_HOST_OK;
}

static void dummy_ctx_destroy(NoxHostContext *ctx) {
    (void)ctx;
}

static NoxHostStatus dummy_mod_init(NoxHostContext *ctx, NoxHostError **err) {
    (void)ctx;
    (void)err;
    return NOX_HOST_OK;
}

static void dummy_mod_fini(NoxHostContext *ctx) {
    (void)ctx;
}

static const NoxPyHostOps g_dummy_ops = {
    .struct_size = (uint32_t)sizeof(NoxPyHostOps),
    .abi_version = NOX_HOST_ABI_VERSION,
    .context_create = dummy_ctx_create,
    .context_destroy = dummy_ctx_destroy,
    .module_init = dummy_mod_init,
    .module_fini = dummy_mod_fini,
    .buffer_release = NULL,
    .error_release = NULL,
    .error_line = NULL,
    .error_class_name = NULL,
    .error_message = NULL,
};

static PyObject *test_i64(PyObject *self, PyObject *arg) {
    (void)self;
    int64_t v = 0;
    if (noxpy_py_to_i64_strict(arg, &v, "x") < 0) return NULL;
    return noxpy_i64_to_py(v);
}

static PyObject *test_bool(PyObject *self, PyObject *arg) {
    (void)self;
    uint8_t v = 0;
    if (noxpy_py_to_bool_strict(arg, &v, "b") < 0) return NULL;
    return noxpy_bool_to_py(v);
}

static PyObject *test_f64(PyObject *self, PyObject *arg) {
    (void)self;
    double v = 0.0;
    if (noxpy_py_to_f64_strict(arg, &v, "f") < 0) return NULL;
    return noxpy_f64_to_py(v);
}

static PyObject *test_str(PyObject *self, PyObject *arg) {
    (void)self;
    NoxHostSpan sp = {0};
    if (noxpy_py_to_utf8_span_strict(arg, &sp, "s") < 0) return NULL;
    return noxpy_utf8_span_to_py(sp);
}

static PyObject *test_bytes(PyObject *self, PyObject *arg) {
    (void)self;
    NoxHostSpan sp = {0};
    if (noxpy_py_to_bytes_span_strict(arg, &sp, "raw") < 0) return NULL;
    return noxpy_bytes_span_to_py(sp);
}

static PyObject *test_opt_i64(PyObject *self, PyObject *arg) {
    (void)self;
    int has_val = 0;
    int64_t val = 0;
    if (noxpy_py_to_opt_i64_strict(arg, &has_val, &val, "opt") < 0) return NULL;
    return noxpy_opt_i64_to_py(has_val, val);
}

static PyObject *test_list_i64(PyObject *self, PyObject *arg) {
    (void)self;
    NoxPyConvGuard g;
    noxpy_conv_guard_init(&g, NOXPY_MAX_CONV_DEPTH, 10000);
    int64_t *buf = NULL;
    size_t len = 0;
    if (noxpy_py_list_i64_snapshot_strict(arg, &g, &buf, &len, "xs") < 0) return NULL;
    PyObject *out = noxpy_i64_array_to_py_list(buf, len);
    noxpy_free_buffer(buf);
    return out;
}

static PyObject *test_dict_str_i64(PyObject *self, PyObject *arg) {
    (void)self;
    NoxPyConvGuard g;
    noxpy_conv_guard_init(&g, NOXPY_MAX_CONV_DEPTH, 10000);
    return noxpy_py_dict_str_i64_roundtrip_strict(arg, &g, "d");
}

static PyObject *test_validate_tree(PyObject *self, PyObject *arg) {
    (void)self;
    NoxPyConvGuard g;
    noxpy_conv_guard_init(&g, NOXPY_MAX_CONV_DEPTH, 5000);
    if (noxpy_py_validate_acyclic_tree(arg, &g, "tree") < 0) return NULL;
    return noxpy_i64_to_py((int64_t)g.items);
}

static PyMethodDef shim_test_methods[] = {
    {"rt_i64", test_i64, METH_O, NULL},
    {"rt_bool", test_bool, METH_O, NULL},
    {"rt_f64", test_f64, METH_O, NULL},
    {"rt_str", test_str, METH_O, NULL},
    {"rt_bytes", test_bytes, METH_O, NULL},
    {"rt_opt_i64", test_opt_i64, METH_O, NULL},
    {"rt_list_i64", test_list_i64, METH_O, NULL},
    {"rt_dict_str_i64", test_dict_str_i64, METH_O, NULL},
    {"validate_tree", test_validate_tree, METH_O, NULL},
    {NULL, NULL, 0, NULL}
};

static int shim_test_exec(PyObject *module) {
    NoxPyModuleState *st = (NoxPyModuleState *)PyModule_GetState(module);
    return noxpy_module_state_init(st, &g_dummy_ops);
}

static void shim_test_free(void *module) {
    NoxPyModuleState *st = (NoxPyModuleState *)PyModule_GetState((PyObject *)module);
    noxpy_module_state_close(st);
}

PyABIInfo_VAR(shim_test_abi);

static PySlot shim_test_slots[] = {
    PySlot_STATIC_DATA(Py_mod_abi, &shim_test_abi),
    PySlot_STATIC_DATA(Py_mod_name, "_noxpy_shim_test"),
    PySlot_SIZE(Py_mod_state_size, sizeof(NoxPyModuleState)),
    PySlot_STATIC_DATA(Py_mod_methods, shim_test_methods),
    PySlot_FUNC(Py_mod_exec, shim_test_exec),
    PySlot_FUNC(Py_mod_state_free, shim_test_free),
    PySlot_DATA(Py_mod_gil, Py_MOD_GIL_NOT_USED),
    PySlot_DATA(Py_mod_multiple_interpreters, Py_MOD_MULTIPLE_INTERPRETERS_NOT_SUPPORTED),
    PySlot_END
};

PyMODEXPORT_FUNC
PyModExport__noxpy_shim_test(void) {
    return shim_test_slots;
}
