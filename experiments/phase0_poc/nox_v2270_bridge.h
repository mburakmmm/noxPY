#ifndef NOX_V2270_BRIDGE_H
#define NOX_V2270_BRIDGE_H

/*
 * PHASE 0 EXPERIMENTAL BRIDGE ONLY.
 *
 * This file adapts the undocumented `noxc 2.2.70 --selfhost-obj` output and
 * private `nox_runtime_init`/`nox_runtime_deinit` symbols behind the target
 * `NoxHostRuntimeOps` boundary (`native/include/nox_host_abi.h`).
 *
 * It MUST NOT be used in Phase 1+ production code; see UPSTREAM_REQUIREMENTS.md
 * (UR-001 through UR-006).
 */

#ifndef NOXPY_PHASE0_EXPERIMENTAL_SELFHOST_BRIDGE
#  error "nox_v2270_bridge.h is restricted to the gated Phase 0 PoC spike."
#endif

#include <stdint.h>
#include "nox_host_abi.h"

#ifdef __cplusplus
extern "C" {
#endif

const NoxHostRuntimeOps *nox_v2270_runtime_ops(void);

/* Generic signature adapters for per-call isolated execution of Nox functions. */
typedef int64_t (*NoxRawFn1I64)(void *rt, int64_t a);
typedef int64_t (*NoxRawFn2I64)(void *rt, int64_t a, int64_t b);

NoxHostStatus nox_v2270_call_1_i64(NoxRawFn1I64 fn, int64_t a, int64_t *out, NoxHostError *err_out);
NoxHostStatus nox_v2270_call_2_i64(NoxRawFn2I64 fn, int64_t a, int64_t b, int64_t *out, NoxHostError *err_out);

#ifdef __cplusplus
}
#endif

#endif /* NOX_V2270_BRIDGE_H */
