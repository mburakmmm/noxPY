#ifndef NOX_HOST_H
#define NOX_HOST_H

/*
 * Synchronized copy of upstream `nox-lang/include/nox_host.h` (`v1`),
 * with C11 static layout/size invariants verified for NoxPy (`AGENTS.md` §6).
 */

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define NOX_HOST_ABI_VERSION 1u

typedef int32_t NoxHostStatus;
#define NOX_HOST_OK 0
#define NOX_HOST_ERR_EXCEPTION 1
#define NOX_HOST_ERR_NOMEM 2
#define NOX_HOST_ERR_INVAL 3
#define NOX_HOST_ERR_VERSION 4
#define NOX_HOST_ERR_UTF8 5
#define NOX_HOST_ERR_STATE 6
#define NOX_HOST_ERR_INVALID_HANDLE 7

typedef uint64_t NoxHostHandle;

typedef struct NoxHostContext NoxHostContext;
typedef struct NoxHostError NoxHostError;

typedef struct NoxHostSpan {
    const uint8_t *data;
    size_t len;
} NoxHostSpan;

typedef struct NoxHostBuffer {
    uint8_t *data;
    size_t len;
} NoxHostBuffer;

typedef struct NoxHostContextConfig {
    uint32_t size;
    uint32_t abi_version;
    const void *allocator;
} NoxHostContextConfig;

uint32_t nox_host_abi_version(void);
NoxHostStatus nox_host_context_create(const NoxHostContextConfig *config, NoxHostContext **out);
void nox_host_context_destroy(NoxHostContext *ctx);

void nox_host_buffer_release(NoxHostBuffer *buf);

NoxHostStatus nox_host_handle_release(NoxHostContext *ctx, NoxHostHandle h);
NoxHostStatus nox_host_handle_retain(NoxHostContext *ctx, NoxHostHandle h, NoxHostHandle *out);
int32_t nox_host_handle_is_valid(NoxHostContext *ctx, NoxHostHandle h);

void nox_host_error_release(NoxHostError *err);
int64_t nox_host_error_line(const NoxHostError *err);
NoxHostStatus nox_host_error_class_name(const NoxHostError *err, NoxHostSpan *out);
NoxHostStatus nox_host_error_message(const NoxHostError *err, NoxHostSpan *out);

#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
_Static_assert(sizeof(int32_t) == 4, "int32_t must be 4 bytes");
_Static_assert(sizeof(int64_t) == 8, "int64_t must be 8 bytes");
_Static_assert(sizeof(NoxHostHandle) == 8, "NoxHostHandle must be 8 bytes");
_Static_assert(sizeof(NoxHostSpan) == 2 * sizeof(void *), "NoxHostSpan layout check");
_Static_assert(sizeof(NoxHostBuffer) == 2 * sizeof(void *), "NoxHostBuffer layout check");
_Static_assert(sizeof(NoxHostContextConfig) >= 16, "NoxHostContextConfig size check");
#endif

#ifdef __cplusplus
}
#endif

#endif /* NOX_HOST_H */
