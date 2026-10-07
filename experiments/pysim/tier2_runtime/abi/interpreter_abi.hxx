/**
 * Fireball Tier 2 interpreter C ABI structures used by pysim.
 *
 * This header is the C++ layout mirror for the reference simulator's
 * ``interpreter_abi.py``. The records contain no ownership, constructors,
 * virtual functions, or C++ library types. The ABI is intentionally x86-64
 * only, matching the current JIT ABI.
 */
#pragma once

#include <stdint.h>

#ifndef FB_CONF_JIT_BLOCK_COUNTERS
#define FB_CONF_JIT_BLOCK_COUNTERS 0
#endif

#ifdef __cplusplus
extern "C" {
#endif

struct fireball_control_stack_native;
struct fireball_call_stack_native;

typedef struct fireball_control_map_entry_native {
  uint32_t match_end;
  uint32_t else_offset;
  uint32_t next_pc;
  uint32_t result_arity;
  uint32_t operand_width;
  uint32_t br_table_target_count;
  const uint32_t *br_table_targets;
} fireball_control_map_entry_native;

typedef struct fireball_execution_context_native {
  uint32_t ip;
  uint32_t sp_offset;
  uint32_t local_offset;
  uint32_t local_capacity;
  uint32_t runtime_flags;
  uint32_t code_size;
  const uint8_t *code;
  struct fireball_control_stack_native *control_stack;
  uint32_t control_base;
  uint32_t stack_checkpoint;
  struct fireball_call_stack_native *call_stack;
  uint32_t call_base;
  uint32_t sp_capacity;
  uint32_t loop_jump_count;
  uint32_t loop_jump_threshold;
  uint8_t *linear_memory_host_base;
  uint64_t linear_memory_size;
  uint32_t trap_code;
} fireball_execution_context_native;

typedef struct fireball_const_buffer_view_native {
  const uint8_t *data;
  uint32_t size;
  uint32_t reserved0;
} fireball_const_buffer_view_native;

typedef struct fireball_wasm_function_view_native {
  fireball_const_buffer_view_native code;
  uint32_t type_index;
  uint32_t locals_count;
} fireball_wasm_function_view_native;

typedef struct fireball_wasm_module_view_native {
  const fireball_wasm_function_view_native *function_table;
  uint32_t function_count;
  uint32_t imported_function_count;
  uint32_t start_function;
  uint32_t flags;
} fireball_wasm_module_view_native;

typedef struct fireball_wasm_run_request_native {
  const fireball_wasm_module_view_native *module_view;
  fireball_execution_context_native *execution_context;
  const uint64_t *arguments;
  uint64_t *results;
  uint32_t function_index;
  uint32_t argument_count;
  uint32_t result_capacity;
  uint32_t max_blocks;
} fireball_wasm_run_request_native;

typedef struct fireball_wasm_run_result_native {
  uint32_t status;
  uint32_t fault_code;
  uint32_t value_count;
  uint32_t reserved0;
  uint64_t *results;
} fireball_wasm_run_result_native;

struct fireball_wasm_module_execution_view_native;

typedef struct fireball_wasm_function_execution_view_native {
  const uint8_t *code;
  uint32_t code_size;
  uint32_t code_pc_offset;
  const fireball_control_map_entry_native *control_map;
  const uint16_t *local_offsets;
  const uint8_t *local_sizes;
  uint32_t local_count;
  uint32_t local_slot_count;
  uint32_t param_count;
  uint32_t param_packed_slot_count;
  uint32_t result_arity;
  uint32_t type_index;
  uint32_t is_import;
  const struct fireball_wasm_module_execution_view_native *module_view;
} fireball_wasm_function_execution_view_native;

typedef struct fireball_wasm_function_type_execution_view_native {
  uint32_t param_offset;
  uint32_t param_count;
  uint32_t result_offset;
  uint32_t result_count;
} fireball_wasm_function_type_execution_view_native;

typedef struct fireball_wasm_table_execution_view_native {
  const uint32_t *function_indices;
  uint32_t size;
} fireball_wasm_table_execution_view_native;

typedef struct fireball_wasm_block_execution_view_native {
  uint32_t head_pc, function_index, next_pc, loops_to, frame_depth, byte_span;
  void *extension_data;
} fireball_wasm_block_execution_view_native;

typedef struct fireball_wasm_module_execution_view_native {
  const fireball_wasm_function_execution_view_native *functions;
  uint32_t function_count;
  uint32_t imported_function_count;
  const fireball_wasm_function_type_execution_view_native *types;
  uint32_t type_count;
  const uint8_t *signature_bytes;
  const fireball_wasm_table_execution_view_native *tables;
  uint32_t table_count;
  uint64_t *globals;
  const uint8_t *global_widths;
  uint32_t global_count;
  const fireball_wasm_block_execution_view_native *blocks;
  uint32_t block_count;
} fireball_wasm_module_execution_view_native;

typedef struct fireball_call_frame_native {
  uint32_t func_index;
  const uint8_t *code;
  uint32_t code_size;
  const fireball_wasm_function_execution_view_native *function_view;
  uint32_t local_base;
  uint32_t local_count;
  uint32_t local_slot_count;
  const uint16_t *local_offsets;
  const uint8_t *local_sizes;
  uint32_t param_count;
  uint32_t param_packed_slot_count;
  uint32_t result_arity;
  uint32_t control_base;
  uint32_t return_ip;
  uint32_t return_func_index;
  uint32_t boundary_next_pc;
  uint32_t boundary_loops_to;
} fireball_call_frame_native;

enum {
  FIREBALL_NATIVE_CALL_STACK_CAPACITY = 32,
};

typedef struct fireball_call_stack_native {
  fireball_call_frame_native frames[FIREBALL_NATIVE_CALL_STACK_CAPACITY];
  uint32_t size;
  uint32_t reserved0;
} fireball_call_stack_native;

enum {
  FIREBALL_NATIVE_VALUE_STACK_CAPACITY = 128,
  FIREBALL_NATIVE_CONTROL_STACK_CAPACITY = 32,
  FIREBALL_NATIVE_STACK_ALIGNMENT_BYTES = sizeof(uint64_t),
};

typedef struct fireball_value_stack_native {
#ifdef __cplusplus
  alignas(FIREBALL_NATIVE_STACK_ALIGNMENT_BYTES)
      uint32_t values[FIREBALL_NATIVE_VALUE_STACK_CAPACITY];
#else
  _Alignas(FIREBALL_NATIVE_STACK_ALIGNMENT_BYTES)
      uint32_t values[FIREBALL_NATIVE_VALUE_STACK_CAPACITY];
#endif
  uint32_t size;
  uint32_t reserved0;
} fireball_value_stack_native;

typedef struct fireball_control_frame_native {
  uint32_t kind;
  uint32_t start;
  uint32_t match_end;
  uint32_t stack_height;
  uint16_t result_arity;
} fireball_control_frame_native;

typedef struct fireball_control_stack_native {
  fireball_control_frame_native frames[FIREBALL_NATIVE_CONTROL_STACK_CAPACITY];
  uint32_t size;
  uint32_t reserved0;
} fireball_control_stack_native;

typedef struct fireball_op_result_native {
  uint32_t kind;
} fireball_op_result_native;

enum { FIREBALL_NATIVE_OP_CONTINUE = 8 };

#ifdef __cplusplus
} // extern "C"

#if defined(_WIN32)
#define FIREBALL_NATIVE_CPS_CALL __fastcall
#else
#define FIREBALL_NATIVE_CPS_CALL
#endif

using fireball_native_cps_handler = fireball_op_result_native(
    FIREBALL_NATIVE_CPS_CALL *)(fireball_execution_context_native *, uint32_t *,
                                uint32_t *, uint32_t);

static_assert(sizeof(fireball_op_result_native) == sizeof(uint32_t));

static inline void fb_native_record_block_execution(
    const fireball_wasm_module_execution_view_native *module,
    uint32_t head_pc) {
#if FB_CONF_JIT_BLOCK_COUNTERS
  if (module == nullptr || module->blocks == nullptr)
    return;
  uint32_t first = 0, last = module->block_count;
  while (first < last) {
    const uint32_t middle = first + (last - first) / 2;
    if (module->blocks[middle].head_pc < head_pc)
      first = middle + 1;
    else
      last = middle;
  }
  if (first == module->block_count ||
      module->blocks[first].head_pc != head_pc ||
      module->blocks[first].extension_data == nullptr)
    return;
  uint32_t *count =
      static_cast<uint32_t *>(module->blocks[first].extension_data);
  if (*count != UINT32_MAX)
    ++*count;
#else
  (void)module;
  (void)head_pc;
#endif
}

#include <stddef.h>

namespace fireball::runtime {

using execution_context_native = ::fireball_execution_context_native;
using const_buffer_view_native = ::fireball_const_buffer_view_native;
using wasm_function_view_native = ::fireball_wasm_function_view_native;
using wasm_module_view_native = ::fireball_wasm_module_view_native;
using wasm_run_request_native = ::fireball_wasm_run_request_native;
using wasm_run_result_native = ::fireball_wasm_run_result_native;
using wasm_function_execution_view_native =
    ::fireball_wasm_function_execution_view_native;
using wasm_function_type_execution_view_native =
    ::fireball_wasm_function_type_execution_view_native;
using wasm_table_execution_view_native =
    ::fireball_wasm_table_execution_view_native;
using wasm_module_execution_view_native =
    ::fireball_wasm_module_execution_view_native;
using call_frame_native = ::fireball_call_frame_native;
using call_stack_native = ::fireball_call_stack_native;
using value_stack_native = ::fireball_value_stack_native;
using control_frame_native = ::fireball_control_frame_native;
using control_stack_native = ::fireball_control_stack_native;
using control_map_entry_native = ::fireball_control_map_entry_native;

static_assert(sizeof(void *) == sizeof(uint64_t));

static_assert(__is_standard_layout(execution_context_native));
static_assert(__is_trivially_copyable(execution_context_native));
static_assert(__is_standard_layout(control_map_entry_native));
static_assert(__is_trivially_copyable(control_map_entry_native));
static_assert(__is_standard_layout(const_buffer_view_native));
static_assert(__is_trivially_copyable(const_buffer_view_native));
static_assert(__is_standard_layout(wasm_function_view_native));
static_assert(__is_trivially_copyable(wasm_function_view_native));
static_assert(__is_standard_layout(wasm_module_view_native));
static_assert(__is_trivially_copyable(wasm_module_view_native));
static_assert(__is_standard_layout(wasm_run_request_native));
static_assert(__is_trivially_copyable(wasm_run_request_native));
static_assert(__is_standard_layout(wasm_run_result_native));
static_assert(__is_trivially_copyable(wasm_run_result_native));
static_assert(__is_standard_layout(wasm_function_execution_view_native));
static_assert(__is_trivially_copyable(wasm_function_execution_view_native));
static_assert(offsetof(wasm_function_execution_view_native, code_pc_offset) ==
              12);
static_assert(__is_standard_layout(wasm_function_type_execution_view_native));
static_assert(
    __is_trivially_copyable(wasm_function_type_execution_view_native));
static_assert(__is_standard_layout(wasm_table_execution_view_native));
static_assert(__is_trivially_copyable(wasm_table_execution_view_native));
static_assert(__is_standard_layout(wasm_module_execution_view_native));
static_assert(__is_trivially_copyable(wasm_module_execution_view_native));
static_assert(__is_standard_layout(value_stack_native));
static_assert(__is_trivially_copyable(value_stack_native));
static_assert(alignof(value_stack_native) ==
              FIREBALL_NATIVE_STACK_ALIGNMENT_BYTES);
static_assert(__is_standard_layout(control_frame_native));
static_assert(__is_trivially_copyable(control_frame_native));
static_assert(__is_standard_layout(control_stack_native));
static_assert(__is_trivially_copyable(control_stack_native));
static_assert(__is_standard_layout(call_frame_native));
static_assert(__is_trivially_copyable(call_frame_native));
static_assert(__is_standard_layout(call_stack_native));
static_assert(__is_trivially_copyable(call_stack_native));

static_assert(sizeof(execution_context_native) == 96);
static_assert(offsetof(execution_context_native, ip) == 0x00);
static_assert(offsetof(execution_context_native, sp_offset) == 0x04);
static_assert(offsetof(execution_context_native, local_offset) == 0x08);
static_assert(offsetof(execution_context_native, local_capacity) == 0x0c);
static_assert(offsetof(execution_context_native, runtime_flags) == 0x10);
static_assert(offsetof(execution_context_native, code_size) == 0x14);
static_assert(offsetof(execution_context_native, code) == 0x18);
static_assert(offsetof(execution_context_native, control_stack) == 0x20);
static_assert(offsetof(execution_context_native, control_base) == 0x28);
static_assert(offsetof(execution_context_native, stack_checkpoint) == 0x2c);
static_assert(offsetof(execution_context_native, call_stack) == 0x30);
static_assert(offsetof(execution_context_native, call_base) == 0x38);
static_assert(offsetof(execution_context_native, sp_capacity) == 0x3c);
static_assert(offsetof(execution_context_native, loop_jump_count) == 0x40);
static_assert(offsetof(execution_context_native, loop_jump_threshold) == 0x44);
static_assert(offsetof(execution_context_native, linear_memory_host_base) ==
              0x48);
static_assert(offsetof(execution_context_native, linear_memory_size) == 0x50);
static_assert(offsetof(execution_context_native, trap_code) == 0x58);
static_assert(sizeof(const_buffer_view_native) == 16);
static_assert(sizeof(control_map_entry_native) == 32);
static_assert(sizeof(wasm_function_view_native) == 24);
static_assert(sizeof(wasm_module_view_native) == 24);
static_assert(sizeof(wasm_run_request_native) == 48);
static_assert(sizeof(wasm_run_result_native) == 24);
static_assert(sizeof(wasm_function_type_execution_view_native) == 16);
static_assert(sizeof(wasm_function_execution_view_native) == 80);
static_assert(offsetof(wasm_function_execution_view_native, local_offsets) == 24);
static_assert(offsetof(wasm_function_execution_view_native, local_sizes) == 32);
static_assert(sizeof(wasm_table_execution_view_native) == 16);
static_assert(sizeof(wasm_module_execution_view_native) == 96);
static_assert(offsetof(wasm_module_execution_view_native, globals) == 56);
static_assert(offsetof(wasm_module_execution_view_native, global_widths) == 64);
static_assert(offsetof(wasm_module_execution_view_native, global_count) == 72);
static_assert(sizeof(value_stack_native) == 520);
static_assert(offsetof(value_stack_native, values) == 0);
static_assert(offsetof(value_stack_native, size) == 512);
static_assert(sizeof(control_frame_native) == 20);
static_assert(sizeof(control_stack_native) == 648);
static_assert(offsetof(control_stack_native, frames) == 0);
static_assert(offsetof(control_stack_native, size) == 640);
static_assert(sizeof(call_frame_native) == 96);
static_assert(offsetof(call_frame_native, func_index) == 0);
static_assert(offsetof(call_frame_native, code) == 8);
static_assert(offsetof(call_frame_native, function_view) == 24);
static_assert(offsetof(call_frame_native, local_offsets) == 48);
static_assert(offsetof(call_frame_native, local_sizes) == 56);
static_assert(sizeof(call_stack_native) == 3080);
static_assert(offsetof(call_stack_native, frames) == 0);
static_assert(offsetof(call_stack_native, size) == 3072);

} // namespace fireball::runtime
#endif
