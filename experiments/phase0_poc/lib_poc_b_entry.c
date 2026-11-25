#ifndef NOXPY_PHASE0_EXPERIMENTAL_SELFHOST_BRIDGE
#  error "lib_poc_b_entry.c requires NOXPY_PHASE0_EXPERIMENTAL_SELFHOST_BRIDGE"
#endif

#include <stdint.h>
#include "nox_host_abi.h"
#include "nox_v2270_bridge.h"

extern int64_t nox_mul(void *rt, int64_t a, int64_t b);
extern int64_t nox_sub(void *rt, int64_t a, int64_t b);

__attribute__((visibility("default")))
int32_t poc_b_mul(int64_t a, int64_t b, int64_t *out) {
    return (int32_t)nox_v2270_call_2_i64(nox_mul, a, b, out, NULL);
}

__attribute__((visibility("default")))
int32_t poc_b_sub(int64_t a, int64_t b, int64_t *out) {
    return (int32_t)nox_v2270_call_2_i64(nox_sub, a, b, out, NULL);
}
