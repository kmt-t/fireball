#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include "../../../tier2_runtime/abi/native_abi.hxx"

#include <algorithm>
#include <array>
#include <bit>
#include <cassert>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <functional>
#include <initializer_list>
#include <limits>
#include <type_traits>

#define FIREBALL_CPS_CALL FIREBALL_NATIVE_CPS_CALL

// Keep this order and translation unit: dispatch_next musttail-calls the static
// handler table.
// clang-format off
#include "native_interpreter_utilities.hxx"
#include "native_interpreter_runtime_apis.hxx"
#include "native_interpreter_handlers.hxx"
#include "native_interpreter_runtime.hxx"
// clang-format on

extern "C" int fb_native_run_step(const fb_native_step_call *call,
                                  fb_native_result *result) {
  return run_native_step_abi(call, result, false);
}

extern "C" int fb_native_run_control_step(const fb_native_step_call *call,
                                          fb_native_result *result) {
  return run_native_step_abi(call, result, true);
}

extern "C" int fb_native_run_dispatch(const fb_native_dispatch_call *call,
                                      fb_native_result *result) {
  return run_native_dispatch_abi<false, false, false>(call, result);
}

extern "C" int
fb_native_run_dispatch_jit(const fb_native_dispatch_call *call,
                           fb_native_result *result) {
  return run_native_dispatch_abi<false, true, false>(call, result);
}

extern "C" int
fb_native_run_dispatch_block(const fb_native_dispatch_call *call,
                             fb_native_result *result) {
  return run_native_dispatch_abi<false, false, false, true>(call, result);
}

extern "C" int fb_native_run_debug_dispatch(const fb_native_dispatch_call *call,
                                            fb_native_result *result) {
  return run_native_dispatch_abi<false, false, false, false, debugger_aspect>(
      call, result);
}
