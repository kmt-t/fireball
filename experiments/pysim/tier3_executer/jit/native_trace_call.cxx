#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <array>
#include <cstddef>
#include <cstdint>

namespace {

using trace_fn_t = void (*)(void*, void*, void*, std::uint32_t);

constexpr std::size_t kTraceHeaderBytes = 52;
constexpr std::size_t kMaxBodyBytes = 8192;
constexpr std::size_t kMaxStackLocations = 8192;
constexpr std::int32_t kTos = -1;
constexpr std::int32_t kNos = -2;

constexpr int kDrop = 0x1A;
constexpr int kLocalGet = 0x20;
constexpr int kLocalSet = 0x21;
constexpr int kLocalTee = 0x22;
constexpr int kI32Const = 0x41;
constexpr int kI64Const = 0x42;
constexpr int kF32Const = 0x43;
constexpr int kF64Const = 0x44;
constexpr int kI32Eqz = 0x45;
constexpr int kI32Eq = 0x46;
constexpr int kI32Ne = 0x47;
constexpr int kI32LtS = 0x48;
constexpr int kI32LtU = 0x49;
constexpr int kI32GtS = 0x4A;
constexpr int kI32GtU = 0x4B;
constexpr int kI32LeS = 0x4C;
constexpr int kI32LeU = 0x4D;
constexpr int kI32GeS = 0x4E;
constexpr int kI32GeU = 0x4F;
constexpr int kI32Add = 0x6A;
constexpr int kI32Sub = 0x6B;
constexpr int kI32Mul = 0x6C;
constexpr int kI32DivS = 0x6D;
constexpr int kI32DivU = 0x6E;
constexpr int kI32RemS = 0x6F;
constexpr int kI32RemU = 0x70;
constexpr int kI32And = 0x71;
constexpr int kI32Or = 0x72;
constexpr int kI32Xor = 0x73;
constexpr int kI32Shl = 0x74;
constexpr int kI32ShrS = 0x75;
constexpr int kI32ShrU = 0x76;
constexpr int kI64Add = 0x7C;
constexpr int kI64Sub = 0x7D;
constexpr int kI64Mul = 0x7E;
constexpr int kF32Add = 0x92;
constexpr int kF32Sub = 0x93;
constexpr int kF32Mul = 0x94;
constexpr int kF32Div = 0x95;
constexpr int kF64Add = 0xA0;
constexpr int kF64Sub = 0xA1;
constexpr int kF64Mul = 0xA2;
constexpr int kF64Div = 0xA3;

struct buffer_guard {
  Py_buffer view{};
  bool active = false;
  ~buffer_guard() {
    if (active) PyBuffer_Release(&view);
  }
};

struct trace_builder {
  std::array<std::uint8_t, kMaxBodyBytes> body{};
  std::size_t body_size = 0;
  std::array<std::int32_t, kMaxStackLocations> locations{};
  std::size_t location_count = 0;
  int spilled_words = 0;
  int max_spilled_words = 0;
  int helper_words = 0;
  int helper_index = -1;
  bool saw_op = false;
  bool declined = false;

  bool byte(std::uint8_t value) {
    if (body_size >= body.size()) return false;
    body[body_size++] = value;
    return true;
  }
  bool u32(std::uint32_t value) {
    for (int shift = 0; shift < 32; shift += 8)
      if (!byte(static_cast<std::uint8_t>(value >> shift))) return false;
    return true;
  }
};

template <std::size_t N>
bool append_stencil(trace_builder& builder, const std::array<std::uint8_t, N>& stencil) {
  if (builder.body_size + N > builder.body.size()) return false;
  for (std::size_t index = 0; index < N; ++index) {
    builder.body[builder.body_size + index] = stencil[index];
  }
  builder.body_size += N;
  return true;
}

template <std::size_t N>
bool append_u32_patch(trace_builder& builder, const std::array<std::uint8_t, N>& stencil,
                     std::uint32_t value) {
  return append_stencil(builder, stencil) && builder.u32(value);
}

constexpr std::array<std::uint8_t, 2> kLoadImmStencil = {0x41, 0xB9};
constexpr std::array<std::uint8_t, 3> kLoadLocalStencil = {0x45, 0x8B, 0x8A};
constexpr std::array<std::uint8_t, 3> kStoreLocalStencil = {0x45, 0x89, 0x8A};
constexpr std::array<std::uint8_t, 4> kStoreTosStencil = {0x45, 0x89, 0x8C, 0x24};
constexpr std::array<std::uint8_t, 4> kStoreNosStencil = {0x45, 0x89, 0x9C, 0x24};
constexpr std::array<std::uint8_t, 4> kLoadTosStencil = {0x45, 0x8B, 0x8C, 0x24};
constexpr std::array<std::uint8_t, 4> kLoadNosStencil = {0x45, 0x8B, 0x9C, 0x24};
constexpr std::array<std::uint8_t, 3> kMoveNosFromTosStencil = {0x45, 0x89, 0xCB};
constexpr std::array<std::uint8_t, 3> kI32AddStencil = {0x45, 0x01, 0xD9};
constexpr std::array<std::uint8_t, 6> kI32SubStencil = {
    0x45, 0x29, 0xCB, 0x45, 0x89, 0xD9};
constexpr std::array<std::uint8_t, 4> kI32MulStencil = {0x45, 0x0F, 0xAF, 0xCB};
constexpr std::array<std::uint8_t, 3> kI32AndStencil = {0x45, 0x21, 0xD9};
constexpr std::array<std::uint8_t, 3> kI32OrStencil = {0x45, 0x09, 0xD9};
constexpr std::array<std::uint8_t, 3> kI32XorStencil = {0x45, 0x31, 0xD9};
constexpr std::array<std::uint8_t, 10> kI32EqzStencil = {
    0x45, 0x85, 0xC9, 0x0F, 0x94, 0xC0, 0x44, 0x0F, 0xB6, 0xC8};
constexpr std::array<std::uint8_t, 6> kShiftPrefixStencil = {
    0x44, 0x89, 0xC9, 0x45, 0x89, 0xD9};
constexpr std::array<std::uint8_t, 3> kI32ShlStencil = {0x41, 0xD3, 0xE1};
constexpr std::array<std::uint8_t, 3> kI32ShrSStencil = {0x41, 0xD3, 0xF9};
constexpr std::array<std::uint8_t, 3> kI32ShrUStencil = {0x41, 0xD3, 0xE9};
constexpr std::array<std::uint8_t, 15> kEntryStencil = {
    0x48, 0xB8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0xE9, 0x00, 0x00, 0x00, 0x00};
constexpr std::array<std::uint8_t, 12> kHelperTailStencil = {
    0x48, 0x8D, 0x05, 0x00, 0x00, 0x00, 0x00, 0xE9, 0x00, 0x00, 0x00, 0x00};
constexpr std::array<std::uint8_t, 4> kPublishNextPcStencil = {0x41, 0xC7, 0x45, 0x00};
constexpr std::array<std::uint8_t, 7> kChainHeaderStencil = {
    0x48, 0x8D, 0x05, 0x00, 0x00, 0x00, 0x00};
constexpr std::array<std::uint8_t, 7> kChainLoadTestStencil = {
    0x48, 0x8B, 0x50, 0x10, 0x48, 0x85, 0xD2};
constexpr std::array<std::uint8_t, 6> kChainFallbackStencil = {
    0x0F, 0x84, 0x00, 0x00, 0x00, 0x00};
constexpr std::array<std::uint8_t, 2> kChainJumpStencil = {0xFF, 0xE2};
constexpr std::array<std::uint8_t, 5> kExitJumpStencil = {0xE9, 0x00, 0x00, 0x00, 0x00};

constexpr std::array<std::uint8_t, 10> make_compare_stencil(std::uint8_t condition) {
  return {0x45, 0x39, 0xCB, 0x0F, condition, 0xC0, 0x44, 0x0F, 0xB6, 0xC8};
}

constexpr auto kI32EqStencil = make_compare_stencil(0x94);
constexpr auto kI32NeStencil = make_compare_stencil(0x95);
constexpr auto kI32LtSStencil = make_compare_stencil(0x9C);
constexpr auto kI32LtUStencil = make_compare_stencil(0x92);
constexpr auto kI32GtSStencil = make_compare_stencil(0x9F);
constexpr auto kI32GtUStencil = make_compare_stencil(0x97);
constexpr auto kI32LeSStencil = make_compare_stencil(0x9E);
constexpr auto kI32LeUStencil = make_compare_stencil(0x96);
constexpr auto kI32GeSStencil = make_compare_stencil(0x9D);
constexpr auto kI32GeUStencil = make_compare_stencil(0x93);

bool is_binary(int op) {
  return op == kI32Add || op == kI32Sub || op == kI32Mul || op == kI32And ||
         op == kI32Or || op == kI32Xor || op == kI32Eq || op == kI32Ne ||
         op == kI32LtS || op == kI32LtU || op == kI32GtS || op == kI32GtU ||
         op == kI32LeS || op == kI32LeU || op == kI32GeS || op == kI32GeU ||
         (op >= kI64Add && op <= kI64Mul) ||
         (op >= kF32Add && op <= kF32Div) || (op >= kF64Add && op <= kF64Div);
}

bool stack_effect(int op, int& pops, int& pushes) {
  if (op == kI32Const || op == kI64Const || op == kF32Const || op == kF64Const ||
      op == kLocalGet) { pops = 0; pushes = 1; return true; }
  if (op == kLocalSet || op == kDrop) { pops = 1; pushes = 0; return true; }
  if (op == kLocalTee || op == kI32Eqz) { pops = 1; pushes = 1; return true; }
  if (is_binary(op) || op == kI32Shl || op == kI32ShrS || op == kI32ShrU) {
    pops = 2; pushes = 1; return true;
  }
  return false;
}

int helper_index(int op) {
  if (op >= kI32DivS && op <= kI32RemU) return op - kI32DivS + 11;
  if (op >= kI64Add && op <= kI64Mul) return op - kI64Add;
  if (op >= kF32Add && op <= kF32Div) return op - kF32Add + 3;
  if (op >= kF64Add && op <= kF64Div) return op - kF64Add + 7;
  return -1;
}

int local_words(const Py_buffer& map, unsigned int count, int index) {
  if (index < 0 || static_cast<unsigned int>(index) >= count) return 0;
  const auto byte_index = static_cast<std::size_t>(index) >> 2;
  if (byte_index >= static_cast<std::size_t>(map.len)) return 0;
  const auto byte = static_cast<const std::uint8_t*>(map.buf)[byte_index];
  return 1 << ((byte >> ((index & 3) * 2)) & 3u);
}

bool load_imm(trace_builder& b, std::uint32_t value) {
  return append_u32_patch(b, kLoadImmStencil, value);
}
bool load_local(trace_builder& b, std::uint32_t offset) {
  return append_u32_patch(b, kLoadLocalStencil, offset);
}
bool store_local(trace_builder& b, std::uint32_t offset) {
  return append_u32_patch(b, kStoreLocalStencil, offset);
}
bool store_sp(trace_builder& b, std::int32_t location, int slot) {
  if (slot < 0 || (location != kTos && location != kNos)) return false;
  return append_u32_patch(b, location == kTos ? kStoreTosStencil : kStoreNosStencil,
                          static_cast<std::uint32_t>(slot * 4));
}
bool load_sp(trace_builder& b, std::int32_t location, int slot) {
  if (slot < 0 || (location != kTos && location != kNos)) return false;
  return append_u32_patch(b, location == kTos ? kLoadTosStencil : kLoadNosStencil,
                          static_cast<std::uint32_t>(slot * 4));
}

bool emit_push(trace_builder& b, int op, std::uint64_t arg, std::uint32_t slot_bytes) {
  if (b.location_count >= 2 && b.locations[b.location_count - 2] == kNos) {
    if (!store_sp(b, kNos, b.spilled_words)) return false;
    b.locations[b.location_count - 2] = b.spilled_words++;
  } else if (b.location_count >= 2 && b.locations[b.location_count - 2] < 0) return false;
  if (b.location_count > 0) {
    if (b.locations[b.location_count - 1] != kTos ||
        !append_stencil(b, kMoveNosFromTosStencil)) return false;
    b.locations[b.location_count - 1] = kNos;
  }
  if (op == kI32Const) {
    if (!load_imm(b, static_cast<std::uint32_t>(arg))) return false;
  } else if (op == kLocalGet) {
    if (!load_local(b, static_cast<std::uint32_t>(arg * slot_bytes))) return false;
  } else return false;
  if (b.location_count >= b.locations.size()) return false;
  b.locations[b.location_count++] = kTos;
  return true;
}

bool emit_binary(trace_builder& b, int op) {
  if (op == kI32Add) return append_stencil(b, kI32AddStencil);
  if (op == kI32Sub) return append_stencil(b, kI32SubStencil);
  if (op == kI32Mul) return append_stencil(b, kI32MulStencil);
  if (op == kI32And) return append_stencil(b, kI32AndStencil);
  if (op == kI32Or) return append_stencil(b, kI32OrStencil);
  if (op == kI32Xor) return append_stencil(b, kI32XorStencil);
  if (op == kI32Eq) return append_stencil(b, kI32EqStencil);
  if (op == kI32Ne) return append_stencil(b, kI32NeStencil);
  if (op == kI32LtS) return append_stencil(b, kI32LtSStencil);
  if (op == kI32LtU) return append_stencil(b, kI32LtUStencil);
  if (op == kI32GtS) return append_stencil(b, kI32GtSStencil);
  if (op == kI32GtU) return append_stencil(b, kI32GtUStencil);
  if (op == kI32LeS) return append_stencil(b, kI32LeSStencil);
  if (op == kI32LeU) return append_stencil(b, kI32LeUStencil);
  if (op == kI32GeS) return append_stencil(b, kI32GeSStencil);
  if (op == kI32GeU) return append_stencil(b, kI32GeUStencil);
  return false;
}

bool emit_pop(trace_builder& b) {
  if (b.location_count == 0 || b.locations[b.location_count - 1] != kTos) return false;
  --b.location_count;
  if (b.location_count == 0) return true;
  if (b.locations[b.location_count - 1] == kNos) {
    b.locations[b.location_count - 1] = kTos;
    return true;
  }
  if (b.locations[b.location_count - 1] != b.spilled_words - 1 || b.spilled_words <= 0) return false;
  const auto slot = b.locations[b.location_count - 1];
  if (!load_sp(b, kTos, slot)) return false;
  --b.location_count; --b.spilled_words; b.locations[b.location_count++] = kTos;
  return true;
}

bool emit_binary_spill(trace_builder& b, int op) {
  if (b.location_count < 2 || b.locations[b.location_count - 1] != kTos) return false;
  auto& nos = b.locations[b.location_count - 2];
  if (nos >= 0) {
    if (nos != b.spilled_words - 1 || b.spilled_words <= 0 || !load_sp(b, kNos, nos)) return false;
    nos = kNos; --b.spilled_words;
  }
  if (nos != kNos || !emit_binary(b, op)) return false;
  b.location_count -= 2; b.locations[b.location_count++] = kTos;
  return true;
}

bool emit_shift(trace_builder& b, int op) {
  if (!append_stencil(b, kShiftPrefixStencil)) return false;
  if (op == kI32Shl) return append_stencil(b, kI32ShlStencil);
  if (op == kI32ShrS) return append_stencil(b, kI32ShrSStencil);
  if (op == kI32ShrU) return append_stencil(b, kI32ShrUStencil);
  return false;
}

bool operand(const PyObject* object, std::uint64_t& value) {
  if (object == Py_None) return false;
  const auto signed_value = PyLong_AsLongLong(const_cast<PyObject*>(object));
  if (signed_value == -1 && PyErr_Occurred()) return false;
  value = static_cast<std::uint64_t>(signed_value);
  return true;
}

bool compile_operations(trace_builder& b, PyObject* instructions, const Py_buffer& map,
                        unsigned int local_count, std::uint32_t slot_bytes) {
  PyObject* iterator = PyObject_GetIter(instructions);
  if (iterator == nullptr) return false;
  while (PyObject* item = PyIter_Next(iterator)) {
    PyObject* pair = PySequence_Fast(item, "JIT instruction must be a pair");
    Py_DECREF(item);
    if (pair == nullptr) { Py_DECREF(iterator); return false; }
    if (PySequence_Fast_GET_SIZE(pair) < 2) {
      Py_DECREF(pair); Py_DECREF(iterator);
      PyErr_SetString(PyExc_ValueError, "JIT instruction pair is incomplete");
      return false;
    }
    const int op = static_cast<int>(PyLong_AsLong(PySequence_Fast_GET_ITEM(pair, 0)));
    std::uint64_t arg = 0;
    const auto* arg_object = PySequence_Fast_GET_ITEM(pair, 1);
    const bool has_arg = arg_object != Py_None;
    if ((op == -1 && PyErr_Occurred()) || (has_arg && !operand(arg_object, arg))) {
      Py_DECREF(pair); Py_DECREF(iterator); return false;
    }
    Py_DECREF(pair);
    if (b.spilled_words > b.max_spilled_words) b.max_spilled_words = b.spilled_words;
    b.saw_op = true;
    b.helper_index = helper_index(op);
    if (b.helper_index >= 0) {
      if (b.helper_index >= 11) {
        if (b.location_count != 2 || b.spilled_words != 0 || b.helper_words != 0 ||
            b.locations[0] != kNos || b.locations[1] != kTos) {
          b.declined = true; Py_DECREF(iterator); return true;
        }
        b.location_count = 0;
      } else {
          const int expected =
              b.helper_index >= 3 && b.helper_index <= 6 ? 2 : 4;
        if (b.location_count != 0 || (b.helper_words != 4 && b.helper_words != 2) ||
            b.helper_words != expected) {
          b.declined = true; Py_DECREF(iterator); return true;
        }
      }
      break;
    }
    int pops = 0, pushes = 0;
    if (!stack_effect(op, pops, pushes) || pops > static_cast<int>(b.location_count)) {
      b.declined = true; Py_DECREF(iterator); return true;
    }
    bool ok = true;
    if (op == kI32Const || op == kLocalGet) {
      if (op == kLocalGet) {
        const int index = static_cast<int>(arg);
        if (local_words(map, local_count, index) != 1) ok = false;
        arg = static_cast<std::uint64_t>(index);
      }
      if (ok) ok = emit_push(b, op, arg, slot_bytes);
    } else if (op == kI64Const || op == kF32Const || op == kF64Const) {
      if (b.location_count != 0) ok = false;
      const int width = op == kF32Const ? 1 : 2;
      for (int word = 0; ok && word < width; ++word) {
        ok = load_imm(b, static_cast<std::uint32_t>(arg >> (word * 32))) &&
             store_sp(b, kTos, b.helper_words++);
      }
    } else if (op == kLocalSet || op == kDrop) {
      if (op == kLocalSet) {
        const int index = static_cast<int>(arg);
        ok = local_words(map, local_count, index) == 1 &&
             store_local(b, static_cast<std::uint32_t>(index * slot_bytes));
      }
      if (ok) ok = emit_pop(b);
    } else if (op == kLocalTee) {
      const int index = static_cast<int>(arg);
      ok = local_words(map, local_count, index) == 1 && b.location_count != 0 &&
           store_local(b, static_cast<std::uint32_t>(index * slot_bytes));
    } else if (op == kI32Eqz) {
      ok = b.location_count != 0 && b.locations[b.location_count - 1] == kTos &&
           append_stencil(b, kI32EqzStencil);
    } else if (is_binary(op)) {
      ok = emit_binary_spill(b, op);
    } else if (op == kI32Shl || op == kI32ShrS || op == kI32ShrU) {
      if (b.location_count < 2 || b.locations[b.location_count - 1] != kTos) ok = false;
      auto& nos = b.locations[b.location_count - 2];
      if (ok && nos >= 0) {
        ok = nos == b.spilled_words - 1 && b.spilled_words > 0 && load_sp(b, kNos, nos);
        if (ok) { nos = kNos; --b.spilled_words; }
      }
      if (ok && nos == kNos && emit_shift(b, op)) {
        b.location_count -= 2; b.locations[b.location_count++] = kTos;
      } else ok = false;
    } else ok = false;
    if (!ok) { b.declined = true; Py_DECREF(iterator); return true; }
  }
  const bool success = !PyErr_Occurred();
  Py_DECREF(iterator);
  return success;
}

PyObject* compile_trace(PyObject*, PyObject* args) {
  PyObject* instructions = nullptr;
  PyObject* next_object = nullptr;
  PyObject* loops_object = nullptr;
  unsigned int byte_span = 0;
  PyObject* width_object = nullptr;
  unsigned int local_count = 0;
  unsigned int slot_words = 0;
  int tail = 0;
  unsigned long long helper_target = 0;
  if (!PyArg_ParseTuple(args, "OOOIOIIpK", &instructions, &next_object, &loops_object,
                        &byte_span, &width_object, &local_count, &slot_words, &tail,
                        &helper_target)) {
    return nullptr;
  }
  if (byte_span == 0 || local_count > kMaxStackLocations ||
      (slot_words != 1 && slot_words != 2 && slot_words != 4)) {
    PyErr_SetString(PyExc_ValueError, "invalid native JIT compiler bounds");
    return nullptr;
  }
  const bool has_next = next_object != Py_None;
  const bool has_loops = loops_object != Py_None;
  const auto next_pc = has_next ? PyLong_AsUnsignedLong(next_object) : 0u;
  const auto loops_to = has_loops ? PyLong_AsUnsignedLong(loops_object) : 0u;
  if ((has_next || has_loops) && PyErr_Occurred()) return nullptr;
  buffer_guard width_buffer;
  if (PyObject_GetBuffer(width_object, &width_buffer.view, PyBUF_SIMPLE) != 0) return nullptr;
  width_buffer.active = true;
  trace_builder b;
  if (!append_stencil(b, kEntryStencil)) {
    PyErr_SetString(PyExc_MemoryError, "native JIT body buffer exhausted");
    return nullptr;
  }
  const auto slot_bytes = slot_words * 4;
  if (!compile_operations(b, instructions, width_buffer.view, local_count, slot_bytes)) return nullptr;
  if (b.declined || !b.saw_op || b.location_count > 1 || b.spilled_words != 0 ||
      (b.helper_index < 0 && b.helper_words != 0)) Py_RETURN_NONE;

  int helper_header = -1, helper_exit = -1, exit_patch = -1;
  int chain_header = -1, chain_fallback = -1;
  if (tail) {
    if (b.helper_index >= 0 || b.location_count != 0 || helper_target == 0) Py_RETURN_NONE;
    const auto base = b.body_size;
    if (!append_stencil(b, kHelperTailStencil)) Py_RETURN_NONE;
    helper_header = static_cast<int>(kTraceHeaderBytes + base + 3);
    helper_exit = static_cast<int>(kTraceHeaderBytes + base + 8);
  } else if (b.helper_index >= 0) {
    if (helper_target == 0 || b.location_count != 0) Py_RETURN_NONE;
    const auto base = b.body_size;
    if (!append_stencil(b, kHelperTailStencil)) Py_RETURN_NONE;
    helper_header = static_cast<int>(kTraceHeaderBytes + base + 3);
    helper_exit = static_cast<int>(kTraceHeaderBytes + base + 8);
  } else {
    if (b.location_count != 0 && !store_sp(b, kTos, 0)) Py_RETURN_NONE;
    if (has_next && !has_loops) {
      if (!append_u32_patch(b, kPublishNextPcStencil, static_cast<std::uint32_t>(next_pc))) {
        Py_RETURN_NONE;
      }
      chain_header = static_cast<int>(kTraceHeaderBytes + b.body_size + 3);
      if (!append_stencil(b, kChainHeaderStencil) ||
          !append_stencil(b, kChainLoadTestStencil)) Py_RETURN_NONE;
      chain_fallback = static_cast<int>(kTraceHeaderBytes + b.body_size + 2);
      if (!append_stencil(b, kChainFallbackStencil) ||
          !append_stencil(b, kChainJumpStencil)) Py_RETURN_NONE;
    } else {
      exit_patch = static_cast<int>(kTraceHeaderBytes + b.body_size + 1);
      if (!append_stencil(b, kExitJumpStencil)) Py_RETURN_NONE;
    }
  }
  if (tail) b.helper_index = -1;
  if (kTraceHeaderBytes + b.body_size > 0xFFFF) Py_RETURN_NONE;
  PyObject* result = PyTuple_New(10);
  if (result == nullptr) return nullptr;
  PyTuple_SET_ITEM(result, 0, PyBytes_FromStringAndSize(reinterpret_cast<const char*>(b.body.data()),
                                                        static_cast<Py_ssize_t>(b.body_size)));
  PyTuple_SET_ITEM(result, 1, PyLong_FromLong(b.helper_index));
  PyTuple_SET_ITEM(result, 2, PyLong_FromLong(b.helper_words));
  PyTuple_SET_ITEM(result, 3, PyLong_FromLong(b.max_spilled_words));
  PyTuple_SET_ITEM(result, 4, PyLong_FromLong(static_cast<long>(b.location_count)));
  PyTuple_SET_ITEM(result, 5, PyLong_FromLong(helper_header));
  PyTuple_SET_ITEM(result, 6, PyLong_FromLong(helper_exit));
  PyTuple_SET_ITEM(result, 7, PyLong_FromLong(exit_patch));
  PyTuple_SET_ITEM(result, 8, PyLong_FromLong(chain_header));
  PyTuple_SET_ITEM(result, 9, PyLong_FromLong(chain_fallback));
  return result;
}

PyObject* invoke_trace(PyObject*, PyObject* args) {
  unsigned long long fn_addr = 0, ctx_addr = 0, sp_addr = 0, local_base_addr = 0;
  unsigned int tos = 0;
  if (!PyArg_ParseTuple(args, "KKKKI", &fn_addr, &ctx_addr, &sp_addr, &local_base_addr, &tos)) return nullptr;
  if (fn_addr == 0) { PyErr_SetString(PyExc_ValueError, "trace function address must be non-zero"); return nullptr; }
  const auto fn = reinterpret_cast<trace_fn_t>(static_cast<std::uintptr_t>(fn_addr));
  Py_BEGIN_ALLOW_THREADS
  fn(reinterpret_cast<void*>(static_cast<std::uintptr_t>(ctx_addr)),
     reinterpret_cast<void*>(static_cast<std::uintptr_t>(sp_addr)),
     reinterpret_cast<void*>(static_cast<std::uintptr_t>(local_base_addr)), tos);
  Py_END_ALLOW_THREADS
  Py_RETURN_NONE;
}

PyMethodDef module_methods[] = {
    {"compile_trace", compile_trace, METH_VARARGS, "Compile one x64 trace in Native C++."},
    {"invoke_trace", invoke_trace, METH_VARARGS, "Call a JIT trace using the native ABI."},
    {nullptr, nullptr, 0, nullptr},
};
PyModuleDef module_definition = {PyModuleDef_HEAD_INIT, "native_trace_call",
                                 "Native x64 Copy-and-Patch compiler and ABI bridge.", -1,
                                 module_methods};
}  // namespace

PyMODINIT_FUNC PyInit_native_trace_call() { return PyModule_Create(&module_definition); }
