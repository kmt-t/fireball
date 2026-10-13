#pragma once
/** @file @brief QA専用のInterpreter計測結果と拡張呼び出しの宣言。 */
#include "../../tier2_runtime/abi/native_abi.hxx"
#define FB_INTERPRETER_MEASURED_RESULT qa_native_result

struct fb_native_dispatch_extension_call {
  fb_native_dispatch_call call;
  const fb_native_execution_extension *extension;
};

struct qa_native_result : fb_native_result {
  std::uint32_t trace_count;
  std::uint32_t body_count;
  std::uint32_t dispatcher_trace_transitions;
  std::uint32_t control_handler_count;
  std::uint32_t eligible_block_visits;
  std::uint32_t interpreted_block_count;
  std::uint32_t block_search_cache_hits;
  std::uint32_t block_search_cache_misses;
};
static_assert(sizeof(qa_native_result) == 40);
