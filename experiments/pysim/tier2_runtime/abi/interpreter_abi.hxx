/**
 * Fireball Tier 2 interpreter C ABI structures used by pysim.
 *
 * This header is the C++ layout mirror for the reference simulator's
 * ``interpreter_abi.py``. The records contain no ownership, constructors,
 * virtual functions, or C++ library types. The ABI is intentionally x86-64
 * only, matching the current JIT ABI.
 */
#pragma once

#include "integer_types.hxx"
#include <assert.h>
#include <stdint.h>

#ifndef FB_CONF_JIT_BLOCK_COUNTERS
#define FB_CONF_JIT_BLOCK_COUNTERS 0
#endif

#ifdef __cplusplus
extern "C" {
#endif

struct fireball_call_stack_native;
struct fireball_call_frame_native;
struct fb_native_jit_trace_exit;

typedef struct fireball_execution_context_native {
  code_ip_t ip;
  uint32_t *stack_base;
  uint32_t *stack_pointer;
  uint32_t *stack_limit;
  uint32_t *local_stack_base;
  uint32_t *local_stack_pointer;
  uint32_t *local_stack_limit;
  uint32_t runtime_flags;
  code_offset_t code_size;
  const uint8_t *code;
  struct fireball_call_stack_native *call_stack;
  struct fireball_call_frame_native *call_frame_base;
  uint32_t loop_jump_count;
  uint32_t loop_jump_threshold;
  uint8_t *linear_memory_host_base;
  uint64_t linear_memory_size;
  uint32_t trap_code;
  uint32_t *local_base;
  const uint8_t *rel;
  block_index_t current_block_index;
  uint16_t reserved0;
} fireball_execution_context_native;

struct fireball_wasm_module_execution_view_native;

typedef struct fireball_wasm_function_execution_view_native {
  const uint8_t *code;
  code_offset_t code_size;
  wasm_pc_t code_pc_offset;
  const frame_word_offset_t *local_offsets;
  const uint8_t *local_types;
  const struct fireball_wasm_module_execution_view_native *module_view;
  const uint8_t *interpreter_annotation_pool;
  annotation_offset_t interpreter_annotation_byte_count;
  local_count_t local_count;
  local_count_t local_slot_count;
  function_index_t function_index;
  type_index_t type_index;
  block_index_t entry_block_index;
  operand_words_t param_count;
  operand_words_t param_packed_slot_count;
  operand_words_t result_arity;
  operand_words_t max_operand_stack_words;
  uint8_t is_import;
  uint8_t reserved0[5];
} fireball_wasm_function_execution_view_native;

typedef struct fireball_wasm_function_type_execution_view_native {
  uint32_t param_offset;
  operand_words_t param_count;
  uint8_t reserved0[3];
  uint32_t result_offset;
  operand_words_t result_count;
  uint8_t reserved1[3];
} fireball_wasm_function_type_execution_view_native;

typedef struct fireball_wasm_table_execution_view_native {
  /* Loader table storage uses 32-bit slots, including the 0xFFFF sentinel. */
  const uint32_t *function_indices;
  local_count_t size;
  uint16_t reserved0;
} fireball_wasm_table_execution_view_native;

typedef struct fireball_wasm_block_execution_view_native {
  wasm_pc_t head_pc;
  function_index_t function_index;
  uint16_t reserved0;
  wasm_pc_t next_pc, loops_to;
  frame_depth_t frame_depth;
  uint8_t jit_cache_reference;
  uint16_t reserved1;
  code_offset_t byte_span;
  annotation_offset_t interpreter_annotation_byte_offset;
  block_index_t next_block_index;
  block_index_t loops_to_block_index;
} fireball_wasm_block_execution_view_native;

typedef struct fireball_wasm_module_execution_view_native {
  const fireball_wasm_function_execution_view_native *functions;
  function_index_t function_count;
  function_index_t imported_function_count;
  const fireball_wasm_function_type_execution_view_native *types;
  type_index_t type_count;
  uint16_t reserved0;
  const uint8_t *signature_bytes;
  const fireball_wasm_table_execution_view_native *tables;
  uint8_t table_count;
  uint8_t reserved1[7];
  uint64_t *globals;
  const uint8_t *global_widths;
  uint8_t global_count;
  uint8_t reserved2[7];
  fireball_wasm_block_execution_view_native *blocks;
  block_index_t block_count;
  uint16_t reserved3;
  uint32_t *block_execution_counts;
} fireball_wasm_module_execution_view_native;

typedef struct fireball_call_frame_native {
  const fireball_wasm_function_execution_view_native *function_view;
  uint32_t *local_base;
  uint32_t *operand_stack_base;
  code_ip_t return_ip;
  wasm_pc_t boundary_next_pc;
  wasm_pc_t boundary_loops_to;
  block_index_t return_block_index;
  uint16_t reserved0;
} fireball_call_frame_native;

enum {
  FIREBALL_NATIVE_CALL_STACK_CAPACITY = 32,
};

typedef struct fireball_call_stack_native {
  fireball_call_frame_native frames[FIREBALL_NATIVE_CALL_STACK_CAPACITY];
  frame_depth_t size;
  uint8_t reserved0[7];
} fireball_call_stack_native;

enum {
  FIREBALL_NATIVE_VALUE_STACK_CAPACITY = 128,
  FIREBALL_NATIVE_STACK_ALIGNMENT_BYTES = sizeof(uint64_t),
};

typedef struct fireball_value_stack_native {
#ifdef __cplusplus
  alignas(
      FIREBALL_NATIVE_STACK_ALIGNMENT_BYTES) uint32_t values[FIREBALL_NATIVE_VALUE_STACK_CAPACITY];
#else
  _Alignas(
      FIREBALL_NATIVE_STACK_ALIGNMENT_BYTES) uint32_t values[FIREBALL_NATIVE_VALUE_STACK_CAPACITY];
#endif
  operand_words_t size;
  uint8_t reserved0[7];
} fireball_value_stack_native;

#ifdef __cplusplus
} // extern "C"

#if defined(_WIN32)
#define FIREBALL_NATIVE_CPS_CALL __fastcall
#else
#define FIREBALL_NATIVE_CPS_CALL
#endif

using fireball_native_cps_handler = void(FIREBALL_NATIVE_CPS_CALL *)(
    fireball_execution_context_native *, const uint8_t *, uint32_t *, const uint8_t *);

// JIT trace entries receive the Interpreter execution state and a trace-local
// constant-pool pointer named rel as their fourth argument.
using fireball_native_jit_trace_entry = bool(FIREBALL_NATIVE_CPS_CALL *)(
    fireball_execution_context_native *, const uint8_t *, uint32_t *, const uint8_t *) noexcept;

static inline void
fb_native_record_block_execution(const fireball_wasm_module_execution_view_native *module,
                                 block_index_t block_index) {
#if FB_CONF_JIT_BLOCK_COUNTERS
  if (module == 0 || module->block_execution_counts == 0) {
    return;
  }
  assert(block_index < module->block_count);
  uint32_t *count = module->block_execution_counts + block_index;
  if (*count != UINT32_MAX) {
    ++*count;
  }
#else
  (void)module;
  (void)block_index;
#endif
}

#include <stddef.h>

namespace fireball::runtime {

using execution_context_native = ::fireball_execution_context_native;
using wasm_function_execution_view_native = ::fireball_wasm_function_execution_view_native;
using wasm_function_type_execution_view_native =
    ::fireball_wasm_function_type_execution_view_native;
using wasm_table_execution_view_native = ::fireball_wasm_table_execution_view_native;
using wasm_block_execution_view_native = ::fireball_wasm_block_execution_view_native;
using wasm_module_execution_view_native = ::fireball_wasm_module_execution_view_native;
using call_frame_native = ::fireball_call_frame_native;
using call_stack_native = ::fireball_call_stack_native;
using value_stack_native = ::fireball_value_stack_native;

static_assert(sizeof(void *) == sizeof(uint64_t));

static_assert(__is_standard_layout(execution_context_native));
static_assert(__is_trivially_copyable(execution_context_native));
static_assert(__is_standard_layout(wasm_function_execution_view_native));
static_assert(__is_trivially_copyable(wasm_function_execution_view_native));
static_assert(offsetof(wasm_function_execution_view_native, code_pc_offset) == 12);
static_assert(__is_standard_layout(wasm_function_type_execution_view_native));
static_assert(__is_trivially_copyable(wasm_function_type_execution_view_native));
static_assert(__is_standard_layout(wasm_table_execution_view_native));
static_assert(__is_trivially_copyable(wasm_table_execution_view_native));
static_assert(__is_standard_layout(wasm_module_execution_view_native));
static_assert(__is_trivially_copyable(wasm_module_execution_view_native));
static_assert(__is_standard_layout(value_stack_native));
static_assert(__is_trivially_copyable(value_stack_native));
static_assert(alignof(value_stack_native) == FIREBALL_NATIVE_STACK_ALIGNMENT_BYTES);
static_assert(__is_standard_layout(call_frame_native));
static_assert(__is_trivially_copyable(call_frame_native));
static_assert(__is_standard_layout(call_stack_native));
static_assert(__is_trivially_copyable(call_stack_native));

static_assert(sizeof(block_index_t) == 2);
static_assert(sizeof(frame_word_offset_t) == 2);
static_assert(sizeof(annotation_offset_t) == 4);
static_assert(sizeof(wasm_pc_t) == 4);
static_assert(sizeof(code_ip_t) == 4);
static_assert(sizeof(code_offset_t) == 4);
static_assert(sizeof(function_index_t) == 2);
static_assert(sizeof(type_index_t) == 2);
static_assert(sizeof(global_index_t) == 2);
static_assert(sizeof(local_count_t) == 2);
static_assert(sizeof(operand_words_t) == 1);
static_assert(sizeof(frame_depth_t) == 1);
static_assert(sizeof(instruction_width_t) == 1);
static_assert(sizeof(jit_stack_words_t) == 1);
static_assert(sizeof(jit_body_count_t) == 1);
static_assert(sizeof(card_index_t) == 4);

static_assert(sizeof(execution_context_native) == 144);
static_assert(offsetof(execution_context_native, ip) == 0x00);
static_assert(offsetof(execution_context_native, stack_base) == 0x08);
static_assert(offsetof(execution_context_native, stack_pointer) == 0x10);
static_assert(offsetof(execution_context_native, stack_limit) == 0x18);
static_assert(offsetof(execution_context_native, local_stack_base) == 0x20);
static_assert(offsetof(execution_context_native, local_stack_pointer) == 0x28);
static_assert(offsetof(execution_context_native, local_stack_limit) == 0x30);
static_assert(offsetof(execution_context_native, runtime_flags) == 0x38);
static_assert(offsetof(execution_context_native, code_size) == 0x3c);
static_assert(offsetof(execution_context_native, code) == 0x40);
static_assert(offsetof(execution_context_native, call_stack) == 0x48);
static_assert(offsetof(execution_context_native, call_frame_base) == 0x50);
static_assert(offsetof(execution_context_native, loop_jump_count) == 0x58);
static_assert(offsetof(execution_context_native, loop_jump_threshold) == 0x5C);
static_assert(offsetof(execution_context_native, linear_memory_host_base) == 0x60);
static_assert(offsetof(execution_context_native, linear_memory_size) == 0x68);
static_assert(offsetof(execution_context_native, trap_code) == 0x70);
static_assert(offsetof(execution_context_native, local_base) == 0x78);
static_assert(offsetof(execution_context_native, rel) == 0x80);
static_assert(offsetof(execution_context_native, current_block_index) == 0x88);
static_assert(sizeof(execution_context_native) == 0x90);
static_assert(sizeof(wasm_function_type_execution_view_native) == 16);
static_assert(sizeof(wasm_function_execution_view_native) == 72);
static_assert(offsetof(wasm_function_execution_view_native, local_offsets) == 16);
static_assert(offsetof(wasm_function_execution_view_native, local_types) == 24);
static_assert(offsetof(wasm_function_execution_view_native, module_view) == 32);
static_assert(offsetof(wasm_function_execution_view_native, interpreter_annotation_pool) == 40);
static_assert(offsetof(wasm_function_execution_view_native, interpreter_annotation_byte_count) == 48);
static_assert(offsetof(wasm_function_execution_view_native, function_index) == 56);
static_assert(offsetof(wasm_function_execution_view_native, entry_block_index) == 60);
static_assert(offsetof(wasm_function_execution_view_native, max_operand_stack_words) == 65);
static_assert(sizeof(wasm_table_execution_view_native) == 16);
static_assert(sizeof(wasm_block_execution_view_native) == 32);
static_assert(offsetof(wasm_block_execution_view_native, frame_depth) == 16);
static_assert(offsetof(wasm_block_execution_view_native, jit_cache_reference) == 17);
static_assert(offsetof(wasm_block_execution_view_native, byte_span) == 20);
static_assert(offsetof(wasm_block_execution_view_native, interpreter_annotation_byte_offset) == 24);
static_assert(offsetof(wasm_block_execution_view_native, next_block_index) == 28);
static_assert(offsetof(wasm_block_execution_view_native, loops_to_block_index) == 30);
static_assert(sizeof(wasm_module_execution_view_native) == 104);
static_assert(offsetof(wasm_module_execution_view_native, globals) == 56);
static_assert(offsetof(wasm_module_execution_view_native, global_widths) == 64);
static_assert(offsetof(wasm_module_execution_view_native, global_count) == 72);
static_assert(offsetof(wasm_module_execution_view_native, block_execution_counts) == 96);
static_assert(sizeof(value_stack_native) == 520);
static_assert(offsetof(value_stack_native, values) == 0);
static_assert(offsetof(value_stack_native, size) == 512);
static_assert(sizeof(call_frame_native) == 40);
static_assert(offsetof(call_frame_native, function_view) == 0);
static_assert(offsetof(call_frame_native, local_base) == 8);
static_assert(offsetof(call_frame_native, operand_stack_base) == 16);
static_assert(offsetof(call_frame_native, return_ip) == 24);
static_assert(offsetof(call_frame_native, boundary_next_pc) == 28);
static_assert(offsetof(call_frame_native, boundary_loops_to) == 32);
static_assert(offsetof(call_frame_native, return_block_index) == 36);
static_assert(sizeof(call_stack_native) == 1288);
static_assert(offsetof(call_stack_native, frames) == 0);
static_assert(offsetof(call_stack_native, size) == 1280);

} // namespace fireball::runtime
#endif
