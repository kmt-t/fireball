/**
 * Fireball WASM interpreter/JIT C ABI Native structures used by pysim.
 *
 * This header is the C++ layout mirror for the reference simulator's
 * ``interop_abi.py``. The records contain no ownership, constructors,
 * virtual functions, or C++ library types. The ABI is intentionally x86-64
 * only, matching the current JIT ABI.
 */
#pragma once

#include <stdint.h>

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
  uint32_t sp_base;
  uint32_t sp_limit;
  uint32_t sp_offset;
  uint32_t local_base_addr;
  uint32_t local_limit_addr;
  uint32_t local_offset;
  uint32_t cf_base_addr;
  uint32_t cf_limit_addr;
  uint32_t cf_offset;
  uint32_t mem_base;
  uint32_t mem_size;
  uint32_t globals_base;
  uint32_t globals_limit;
  uint32_t handler_table;
  uint32_t runtime_flags;
  const uint8_t *code;
  uint32_t code_size;
  struct fireball_control_stack_native *control_stack;
  uint32_t control_base;
  uint32_t stack_checkpoint;
  struct fireball_call_stack_native *call_stack;
  uint32_t call_base;
  uint32_t call_offset;
  uint32_t sp_capacity;
  uint32_t loop_jump_count;
  uint32_t loop_jump_threshold;
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

typedef struct fireball_call_frame_native {
  uint32_t func_index;
  const uint8_t *code;
  uint32_t code_size;
  const fireball_control_map_entry_native *control_map;
  uint32_t local_base;
  uint32_t local_count;
  uint32_t local_slot_count;
  uint32_t slot_words;
  const uint8_t *local_width_map;
  uint32_t local_width_count;
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

#ifdef __cplusplus
} // extern "C"

#include <stddef.h>

namespace fireball::runtime {

using execution_context_native = ::fireball_execution_context_native;
using const_buffer_view_native = ::fireball_const_buffer_view_native;
using wasm_function_view_native = ::fireball_wasm_function_view_native;
using wasm_module_view_native = ::fireball_wasm_module_view_native;
using wasm_run_request_native = ::fireball_wasm_run_request_native;
using wasm_run_result_native = ::fireball_wasm_run_result_native;
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
static_assert(__is_standard_layout(value_stack_native));
static_assert(__is_trivially_copyable(value_stack_native));
static_assert(alignof(value_stack_native) == FIREBALL_NATIVE_STACK_ALIGNMENT_BYTES);
static_assert(__is_standard_layout(control_frame_native));
static_assert(__is_trivially_copyable(control_frame_native));
static_assert(__is_standard_layout(control_stack_native));
static_assert(__is_trivially_copyable(control_stack_native));
static_assert(__is_standard_layout(call_frame_native));
static_assert(__is_trivially_copyable(call_frame_native));
static_assert(__is_standard_layout(call_stack_native));
static_assert(__is_trivially_copyable(call_stack_native));

static_assert(sizeof(execution_context_native) == 128);
static_assert(offsetof(execution_context_native, ip) == 0x00);
static_assert(offsetof(execution_context_native, mem_base) == 0x28);
static_assert(offsetof(execution_context_native, handler_table) == 0x38);
static_assert(offsetof(execution_context_native, runtime_flags) == 0x3c);
static_assert(offsetof(execution_context_native, code) == 0x40);
static_assert(offsetof(execution_context_native, code_size) == 0x48);
static_assert(offsetof(execution_context_native, control_stack) == 0x50);
static_assert(offsetof(execution_context_native, control_base) == 0x58);
static_assert(offsetof(execution_context_native, stack_checkpoint) == 0x5c);
static_assert(offsetof(execution_context_native, call_stack) == 0x60);
static_assert(offsetof(execution_context_native, call_base) == 0x68);
static_assert(offsetof(execution_context_native, call_offset) == 0x6c);
static_assert(offsetof(execution_context_native, sp_capacity) == 0x70);
static_assert(offsetof(execution_context_native, loop_jump_count) == 0x74);
static_assert(offsetof(execution_context_native, loop_jump_threshold) == 0x78);
static_assert(sizeof(const_buffer_view_native) == 16);
static_assert(sizeof(control_map_entry_native) == 32);
static_assert(sizeof(wasm_function_view_native) == 24);
static_assert(sizeof(wasm_module_view_native) == 24);
static_assert(sizeof(wasm_run_request_native) == 48);
static_assert(sizeof(wasm_run_result_native) == 24);
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
static_assert(offsetof(call_frame_native, control_map) == 24);
static_assert(offsetof(call_frame_native, local_width_map) == 48);
static_assert(sizeof(call_stack_native) == 3080);
static_assert(offsetof(call_stack_native, frames) == 0);
static_assert(offsetof(call_stack_native, size) == 3072);

} // namespace fireball::runtime
#endif
