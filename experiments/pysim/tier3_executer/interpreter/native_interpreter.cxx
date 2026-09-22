#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "../../tier2_runtime/wasm_interop.hxx"

#include <cstddef>
#include <cstdint>
#include <initializer_list>

namespace {

#if defined(_WIN32)
#define FIREBALL_CPS_CALL __fastcall
#elif defined(__arm__) && defined(__clang__)
#define FIREBALL_CPS_CALL __attribute__((pcs("aapcs")))
#else
#define FIREBALL_CPS_CALL
#endif

constexpr std::uint32_t kFallback = 0;
constexpr std::uint32_t kComplete = 1;
constexpr std::uint32_t kTrap = 2;
constexpr std::uint32_t kTrapUnreachable = 9;
constexpr std::uint32_t kTrapDivideByZero = 15;
constexpr std::uint32_t kTrapIntegerOverflow = 16;
constexpr std::uint32_t kSentinel = 0xFFFF'FFFFu;

using execution_context = fireball_execution_context_native;

struct step_result {
  std::uint32_t kind;
  std::uint32_t next_ip;
  std::uint32_t trap_code;
};

using handler_fn = FIREBALL_CPS_CALL step_result (*)(
    execution_context*, std::uint32_t*, std::uint32_t*, std::uint32_t);

handler_fn handler_table[256] = {};

constexpr step_result fallback(std::uint32_t ip) {
  return {kFallback, ip, 0};
}

constexpr step_result complete() { return {kComplete, kSentinel, 0}; }

constexpr step_result trap(std::uint32_t code) { return {kTrap, kSentinel, code}; }

FIREBALL_CPS_CALL step_result dispatch(execution_context* context, std::uint32_t* sp,
                                       std::uint32_t* local_base, std::uint32_t tos);

std::uint32_t top_value(const execution_context& current, const std::uint32_t* sp) {
  return current.sp_offset == 0 ? 0 : sp[current.sp_offset - 1];
}

bool read_s32(const execution_context& current, std::uint32_t& ip, std::int32_t& value) {
  std::uint32_t raw = 0;
  std::uint32_t shift = 0;
  std::uint8_t byte = 0;
  while (ip < current.code_size && shift < 35) {
    byte = current.code[ip++];
    raw |= static_cast<std::uint32_t>(byte & 0x7Fu) << shift;
    shift += 7;
    if ((byte & 0x80u) == 0) {
      if ((byte & 0x40u) != 0 && shift < 32) {
        raw |= ~0u << shift;
      }
      value = static_cast<std::int32_t>(raw);
      return true;
    }
  }
  return false;
}

bool pop(execution_context& current, std::uint32_t* sp, std::uint32_t& value) {
  if (current.sp_offset == 0) {
    return false;
  }
  value = sp[--current.sp_offset];
  return true;
}

bool push(execution_context& current, std::uint32_t* sp, std::uint32_t value) {
  if (current.sp_offset >= 128) {
    return false;
  }
  sp[current.sp_offset++] = value;
  return true;
}

FIREBALL_CPS_CALL step_result unary_i32_eqz(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  std::uint32_t value = 0;
  if (!pop(current, sp, value) || !push(current, sp, value == 0 ? 1u : 0u)) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result unary_i32_clz(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  std::uint32_t value = 0;
  if (!pop(current, sp, value)) {
    return fallback(current.ip);
  }
  const std::uint32_t result = value == 0 ? 32u : static_cast<std::uint32_t>(__builtin_clz(value));
  if (!push(current, sp, result)) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result unary_i32_ctz(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  std::uint32_t value = 0;
  if (!pop(current, sp, value)) {
    return fallback(current.ip);
  }
  const std::uint32_t result = value == 0 ? 32u : static_cast<std::uint32_t>(__builtin_ctz(value));
  if (!push(current, sp, result)) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result binary_i32(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  const std::uint8_t opcode = current.code[current.ip];
  std::uint32_t rhs = 0;
  std::uint32_t lhs = 0;
  if (!pop(current, sp, rhs) || !pop(current, sp, lhs)) {
    return fallback(current.ip);
  }
  std::uint32_t result = 0;
  switch (opcode) {
    case 0x46: result = lhs == rhs; break;
    case 0x47: result = lhs != rhs; break;
    case 0x48: result = static_cast<std::int32_t>(lhs) < static_cast<std::int32_t>(rhs); break;
    case 0x49: result = lhs < rhs; break;
    case 0x4A: result = static_cast<std::int32_t>(lhs) > static_cast<std::int32_t>(rhs); break;
    case 0x4B: result = lhs > rhs; break;
    case 0x4C: result = static_cast<std::int32_t>(lhs) <= static_cast<std::int32_t>(rhs); break;
    case 0x4D: result = lhs <= rhs; break;
    case 0x4E: result = static_cast<std::int32_t>(lhs) >= static_cast<std::int32_t>(rhs); break;
    case 0x4F: result = lhs >= rhs; break;
    case 0x6A: result = lhs + rhs; break;
    case 0x6B: result = lhs - rhs; break;
    case 0x6C: result = lhs * rhs; break;
    case 0x6D:
      if (rhs == 0) return trap(kTrapDivideByZero);
      if (lhs == 0x8000'0000u && rhs == 0xFFFF'FFFFu) return trap(kTrapIntegerOverflow);
      result = static_cast<std::uint32_t>(static_cast<std::int32_t>(lhs) / static_cast<std::int32_t>(rhs));
      break;
    case 0x6E:
      if (rhs == 0) return trap(kTrapDivideByZero);
      result = lhs / rhs;
      break;
    case 0x6F:
      if (rhs == 0) return trap(kTrapDivideByZero);
      if (lhs == 0x8000'0000u && rhs == 0xFFFF'FFFFu) {
        result = 0;
      } else {
        result = static_cast<std::uint32_t>(static_cast<std::int32_t>(lhs) % static_cast<std::int32_t>(rhs));
      }
      break;
    case 0x70:
      if (rhs == 0) return trap(kTrapDivideByZero);
      result = lhs % rhs;
      break;
    case 0x71: result = lhs & rhs; break;
    case 0x72: result = lhs | rhs; break;
    case 0x73: result = lhs ^ rhs; break;
    case 0x74: result = lhs << (rhs & 31u); break;
    case 0x75: result = static_cast<std::uint32_t>(static_cast<std::int32_t>(lhs) >> (rhs & 31u)); break;
    case 0x76: result = lhs >> (rhs & 31u); break;
    case 0x77: result = (lhs << (rhs & 31u)) | (lhs >> ((32u - rhs) & 31u)); break;
    case 0x78: result = (lhs >> (rhs & 31u)) | (lhs << ((32u - rhs) & 31u)); break;
    default: return fallback(current.ip);
  }
  if (!push(current, sp, result)) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_nop(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_else(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  if (current.control_stack->size <= current.control_base) {
    return fallback(current.ip);
  }
  const auto frame = current.control_stack->frames[current.control_stack->size - 1];
  current.control_stack->size -= 1;
  current.ip = frame.match_end + 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_end(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  if (current.control_stack->size > current.control_base) {
    current.control_stack->size -= 1;
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_unreachable(
    execution_context*, std::uint32_t*, std::uint32_t*, std::uint32_t) {
  return trap(kTrapUnreachable);
}

FIREBALL_CPS_CALL step_result h_i32_const(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  auto operand_ip = current.ip + 1;
  std::int32_t value = 0;
  if (!read_s32(current, operand_ip, value) ||
      !push(current, sp, static_cast<std::uint32_t>(value))) {
    return fallback(current.ip);
  }
  current.ip = operand_ip;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_binary(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t tos) {
  [[clang::musttail]] return binary_i32(context, sp, local_base, tos);
}

FIREBALL_CPS_CALL step_result h_unary_eqz(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t tos) {
  [[clang::musttail]] return unary_i32_eqz(context, sp, local_base, tos);
}

FIREBALL_CPS_CALL step_result h_unary_clz(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t tos) {
  [[clang::musttail]] return unary_i32_clz(context, sp, local_base, tos);
}

FIREBALL_CPS_CALL step_result h_unary_ctz(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t tos) {
  [[clang::musttail]] return unary_i32_ctz(context, sp, local_base, tos);
}

FIREBALL_CPS_CALL step_result dispatch(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
  std::uint32_t tos) {
  auto& current = *context;
  current.cf_offset = current.control_stack->size;
  if (current.ip >= current.code_size) {
    return complete();
  }
  current.stack_checkpoint = current.sp_offset;
  const auto handler = handler_table[current.code[current.ip]];
  if (handler == nullptr) {
    return fallback(current.ip);
  }
  [[clang::musttail]] return handler(context, sp, local_base, tos);
}

void initialize_handler_table() {
  static bool initialized = false;
  if (initialized) return;
  initialized = true;
  handler_table[0x00] = h_unreachable;
  handler_table[0x01] = h_nop;
  handler_table[0x05] = h_else;
  handler_table[0x0B] = h_end;
  handler_table[0x41] = h_i32_const;
  handler_table[0x45] = h_unary_eqz;
  handler_table[0x67] = h_unary_clz;
  handler_table[0x68] = h_unary_ctz;
  for (const auto opcode : {0x46, 0x47, 0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F,
                            0x6A, 0x6B, 0x6C, 0x6D, 0x6E, 0x6F, 0x70, 0x71, 0x72, 0x73,
                            0x74, 0x75, 0x76, 0x77, 0x78}) {
    handler_table[opcode] = h_binary;
  }
}

struct buffer_guard {
  Py_buffer view{};
  bool active = false;

  ~buffer_guard() {
    if (active) {
      PyBuffer_Release(&view);
    }
  }
};

PyObject* run_step(PyObject*, PyObject* args) {
  PyObject* code_object = nullptr;
  PyObject* context_object = nullptr;
  PyObject* stack_object = nullptr;
  PyObject* locals_object = nullptr;
  PyObject* control_object = nullptr;
  unsigned int stack_size = 0;
  unsigned int ip = 0;
  unsigned int local_slots = 0;
  unsigned int control_base = 0;
  if (!PyArg_ParseTuple(args, "OOOOOIIII", &code_object, &context_object, &stack_object,
                        &locals_object, &control_object, &stack_size, &ip, &local_slots,
                        &control_base)) {
    return nullptr;
  }
  buffer_guard code_buffer;
  if (PyObject_GetBuffer(code_object, &code_buffer.view, PyBUF_SIMPLE) != 0) {
    return nullptr;
  }
  code_buffer.active = true;
  const auto* raw_code = static_cast<const std::uint8_t*>(code_buffer.view.buf);
  const auto code_size = code_buffer.view.len;

  buffer_guard context_buffer;
  if (PyObject_GetBuffer(context_object, &context_buffer.view, PyBUF_SIMPLE) != 0) {
    return nullptr;
  }
  context_buffer.active = true;

  buffer_guard stack_buffer;
  if (PyObject_GetBuffer(stack_object, &stack_buffer.view, PyBUF_SIMPLE) != 0) {
    return nullptr;
  }
  stack_buffer.active = true;
  buffer_guard locals_buffer;
  if (PyObject_GetBuffer(locals_object, &locals_buffer.view, PyBUF_SIMPLE) != 0) {
    return nullptr;
  }
  locals_buffer.active = true;
  buffer_guard control_buffer;
  if (PyObject_GetBuffer(control_object, &control_buffer.view, PyBUF_SIMPLE) != 0) {
    return nullptr;
  }
  control_buffer.active = true;
  const auto stack_bytes = static_cast<Py_ssize_t>(128 * sizeof(std::uint32_t));
  const auto context_bytes = static_cast<Py_ssize_t>(sizeof(fireball_execution_context_native));
  const auto control_bytes = static_cast<Py_ssize_t>(sizeof(fireball_control_stack_native));
  const auto local_bytes = static_cast<Py_ssize_t>(local_slots * sizeof(std::uint32_t));
  if (stack_size > 128 || context_buffer.view.len < context_bytes ||
      stack_buffer.view.len < stack_bytes || control_buffer.view.len < control_bytes ||
      locals_buffer.view.len < local_bytes ||
      ip > static_cast<unsigned int>(code_size)) {
    PyErr_SetString(PyExc_ValueError, "invalid native interpreter buffer");
    return nullptr;
  }

  initialize_handler_table();
  auto* execution_context =
      static_cast<fireball_execution_context_native*>(context_buffer.view.buf);
  auto* control_stack = static_cast<fireball_control_stack_native*>(control_buffer.view.buf);
  if (control_base > control_stack->size || control_stack->size > 32) {
    PyErr_SetString(PyExc_ValueError, "invalid native control stack window");
    return nullptr;
  }
  if (execution_context->call_stack != nullptr) {
    if (execution_context->call_stack->size > FIREBALL_NATIVE_CALL_STACK_CAPACITY ||
        execution_context->call_base > execution_context->call_stack->size) {
      PyErr_SetString(PyExc_ValueError, "invalid native call stack window");
      return nullptr;
    }
    execution_context->call_offset = execution_context->call_stack->size;
  }
  auto* stack = static_cast<std::uint32_t*>(stack_buffer.view.buf);
  auto* locals = static_cast<std::uint32_t*>(locals_buffer.view.buf);
  execution_context->ip = ip;
  execution_context->code = raw_code;
  execution_context->code_size = static_cast<std::uint32_t>(code_size);
  execution_context->control_stack = control_stack;
  execution_context->control_base = control_base;
  execution_context->sp_offset = stack_size;
  execution_context->cf_offset = control_stack->size;
  execution_context->stack_checkpoint = stack_size;
  const step_result result =
      dispatch(execution_context, stack, locals, top_value(*execution_context, stack));
  if (result.kind == kFallback) {
    execution_context->sp_offset = execution_context->stack_checkpoint;
    execution_context->ip = result.next_ip;
    return Py_BuildValue("IIII", kFallback, result.next_ip, execution_context->sp_offset, 0);
  }
  if (result.kind == kTrap) {
    return Py_BuildValue("IIII", kTrap, kSentinel, execution_context->sp_offset,
                         result.trap_code);
  }
  return Py_BuildValue("IIII", kComplete, kSentinel, execution_context->sp_offset, 0);
}

PyMethodDef module_methods[] = {
    {"run_step", run_step, METH_VARARGS, "Run the native CPS handler table until a boundary."},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    "_interpreter_native",
    "Native CPS handlers for the Tier 3 interpreter.",
    -1,
    module_methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

}  // namespace

PyMODINIT_FUNC PyInit__interpreter_native() { return PyModule_Create(&module_definition); }
