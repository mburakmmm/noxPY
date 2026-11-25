#ifndef NOXPY_PHASE0_EXPERIMENTAL_SELFHOST_BRIDGE
#  error "lib_poc_a_entry.c requires NOXPY_PHASE0_EXPERIMENTAL_SELFHOST_BRIDGE"
#endif

#include <stdint.h>
#include "nox_host_abi.h"
#include "nox_v2270_bridge.h"

extern int64_t nox_add(void *rt, int64_t a, int64_t b);
extern int64_t nox_fib(void *rt, int64_t n);
extern int64_t nox_work_loop(void *rt, int64_t iters, int64_t seed);
extern int64_t nox_checked_div(void *rt, int64_t a, int64_t b);
extern int64_t nox_alloc_checksum(void *rt, int64_t n);

__attribute__((visibility("default")))
int32_t poc_a_add(int64_t a, int64_t b, int64_t *out) {
    return (int32_t)nox_v2270_call_2_i64(nox_add, a, b, out, NULL);
}

__attribute__((visibility("default")))
int32_t poc_a_fib(int64_t n, int64_t *out) {
    return (int32_t)nox_v2270_call_1_i64(nox_fib, n, out, NULL);
}

__attribute__((visibility("default")))
int32_t poc_a_work_loop(int64_t iters, int64_t seed, int64_t *out) {
    return (int32_t)nox_v2270_call_2_i64(nox_work_loop, iters, seed, out, NULL);
}

__attribute__((visibility("default")))
int32_t poc_a_checked_div(int64_t a, int64_t b, int64_t *out) {
    return (int32_t)nox_v2270_call_2_i64(nox_checked_div, a, b, out, NULL);
}

__attribute__((visibility("default")))
int32_t poc_a_alloc_checksum(int64_t n, int64_t *out) {
    return (int32_t)nox_v2270_call_1_i64(nox_alloc_checksum, n, out, NULL);
}
