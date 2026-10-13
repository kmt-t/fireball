// Diagnostic dispatcher instantiations belong only to the QA library.
#include "../../tier2_runtime/abi/native_abi.hxx"

#include "../../native/tier2_runtime/interpreter/native_interpreter_runtime.hxx"
#include "interpreter_measurements.hxx"
using namespace fireball::interpreter;

template <bool WithJit, bool WithExtension>
int run_stats_dispatch(const dispatch_input_t<WithExtension> *call, fb_native_result *result) {
  // One scheduling boundary can cross multiple Python memory fallbacks.
  auto &total = *static_cast<qa_native_result *>(result);
  qa_native_result current{};
  const auto status =
      run_native_dispatch_abi<true, WithJit, WithExtension, false, void, qa_native_result>(
          call, &current);
  total.status = current.status;
  total.error_code = current.error_code;
  total.trace_count += current.trace_count;
  total.body_count += current.body_count;
  total.dispatcher_trace_transitions += current.dispatcher_trace_transitions;
  total.control_handler_count += current.control_handler_count;
  total.eligible_block_visits += current.eligible_block_visits;
  total.interpreted_block_count += current.interpreted_block_count;
  total.block_search_cache_hits += current.block_search_cache_hits;
  total.block_search_cache_misses += current.block_search_cache_misses;
  return status;
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int fb_qa_run_dispatch(const fb_native_dispatch_call *call,
                                                              fb_native_result *result) {
  return run_native_dispatch_abi<false, false, false, false, void, qa_native_result>(
      call, static_cast<qa_native_result *>(result));
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int
fb_qa_run_dispatch_extension(const fb_native_dispatch_call *call, fb_native_result *result) {
  return run_native_dispatch_abi<false, false, true, false, void, qa_native_result>(
      reinterpret_cast<const fb_native_dispatch_extension_call *>(call),
      static_cast<qa_native_result *>(result));
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int
fb_qa_run_dispatch_stats(const fb_native_dispatch_call *call, fb_native_result *result) {
  return run_stats_dispatch<false, false>(call, result);
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int
fb_qa_run_dispatch_stats_extension(const fb_native_dispatch_call *call, fb_native_result *result) {
  return run_stats_dispatch<false, true>(
      reinterpret_cast<const fb_native_dispatch_extension_call *>(call), result);
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int
fb_qa_run_dispatch_jit(const fb_native_dispatch_call *call, fb_native_result *result) {
  return run_native_dispatch_abi<false, true, false>(call, result);
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int
fb_qa_run_dispatch_jit_extension(const fb_native_dispatch_extension_call *call,
                                 fb_native_result *result) {
  return run_native_dispatch_abi<false, true, true>(call, result);
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int
fb_qa_run_dispatch_jit_stats(const fb_native_dispatch_call *call, fb_native_result *result) {
  return run_stats_dispatch<true, false>(call, result);
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int
fb_qa_run_dispatch_jit_stats_extension(const fb_native_dispatch_extension_call *call,
                                       fb_native_result *result) {
  return run_stats_dispatch<true, true>(call, result);
}
