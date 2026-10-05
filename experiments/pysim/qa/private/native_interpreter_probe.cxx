// Diagnostic dispatcher instantiations belong only to the QA library.
#include "../../native/tier2_runtime/interpreter/native_interpreter.cxx"

struct qa_native_result : fb_native_result {
  std::uint32_t trace_count;
  std::uint32_t body_count;
  std::uint32_t dispatcher_trace_transitions;
  std::uint32_t control_handler_count;
  std::uint32_t eligible_block_visits;
  std::uint32_t interpreted_block_count;
};
static_assert(sizeof(qa_native_result) == 44);

extern "C" FB_PYSIM_INTERPRETER_EXPORT int fb_qa_run_dispatch(
    const fb_native_dispatch_call* call, fb_native_result* result) {
  return run_native_dispatch_abi<false, false, void, qa_native_result>(
      call, static_cast<qa_native_result*>(result));
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int fb_qa_run_dispatch_extension(
    const fb_native_dispatch_call* call, fb_native_result* result) {
  return run_native_dispatch_abi<false, true, void, qa_native_result>(
      call, static_cast<qa_native_result*>(result));
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int fb_qa_run_dispatch_stats(
    const fb_native_dispatch_call* call, fb_native_result* result) {
  return run_native_dispatch_abi<true, false, void, qa_native_result>(
      call, static_cast<qa_native_result*>(result));
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int fb_qa_run_dispatch_stats_extension(
    const fb_native_dispatch_call* call, fb_native_result* result) {
  return run_native_dispatch_abi<true, true, void, qa_native_result>(
      call, static_cast<qa_native_result*>(result));
}
