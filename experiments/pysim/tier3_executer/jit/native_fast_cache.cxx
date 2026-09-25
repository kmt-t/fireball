#define Py_SSIZE_T_CLEAN
#include <Python.h>

#include <bit>
#include <cstddef>
#include <cstdint>

#ifndef FB_CONF_JIT_ENABLED
#error "FB_CONF_JIT_ENABLED must be defined by the build configuration"
#elif FB_CONF_JIT_ENABLED != 1
#error "native_fast_cache is only part of a JIT-enabled build"
#endif

#ifndef FB_CONF_JIT_CACHE_FAST_SLOT_COUNT
#error "FB_CONF_JIT_CACHE_FAST_SLOT_COUNT must be defined by the build configuration"
#endif

namespace {

constexpr std::size_t kFastSlotCount = FB_CONF_JIT_CACHE_FAST_SLOT_COUNT;
static_assert(kFastSlotCount > 0);
static_assert(std::has_single_bit(kFastSlotCount));

struct fast_cache_object {
  PyObject_HEAD
  std::uint32_t keys[kFastSlotCount];
  PyObject* values[kFastSlotCount];
  std::uint8_t occupied[kFastSlotCount];
};

#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wmissing-field-initializers"
PyTypeObject fast_cache_type = {PyVarObject_HEAD_INIT(nullptr, 0)};
#pragma clang diagnostic pop

std::size_t hash_slot(std::uint32_t pc) noexcept {
  auto folded = pc ^ (pc >> 16);
  folded ^= folded >> 8;
  folded ^= folded >> 4;
  return static_cast<std::size_t>(folded) & (kFastSlotCount - 1);
}

void clear_slot(fast_cache_object* self, std::size_t slot) noexcept {
  Py_CLEAR(self->values[slot]);
  self->keys[slot] = 0;
  self->occupied[slot] = 0;
}

void fast_cache_dealloc(PyObject* object) {
  auto* self = reinterpret_cast<fast_cache_object*>(object);
  for (std::size_t slot = 0; slot < kFastSlotCount; ++slot) {
    clear_slot(self, slot);
  }
  Py_TYPE(object)->tp_free(object);
}

PyObject* fast_cache_new(PyTypeObject* type, PyObject*, PyObject*) {
  auto* self = reinterpret_cast<fast_cache_object*>(type->tp_alloc(type, 0));
  if (self == nullptr) return nullptr;
  for (std::size_t slot = 0; slot < kFastSlotCount; ++slot) {
    self->keys[slot] = 0;
    self->values[slot] = nullptr;
    self->occupied[slot] = 0;
  }
  return reinterpret_cast<PyObject*>(self);
}

PyObject* fast_cache_lookup(fast_cache_object* self, PyObject* args) {
  unsigned int pc = 0;
  if (!PyArg_ParseTuple(args, "I", &pc)) return nullptr;
  const auto slot = hash_slot(static_cast<std::uint32_t>(pc));
  if (self->occupied[slot] == 0 || self->keys[slot] != pc) Py_RETURN_NONE;
  return Py_NewRef(self->values[slot]);
}

PyObject* fast_cache_store(fast_cache_object* self, PyObject* args) {
  unsigned int pc = 0;
  PyObject* value = nullptr;
  if (!PyArg_ParseTuple(args, "IO", &pc, &value)) return nullptr;
  if (value == Py_None) {
    PyErr_SetString(PyExc_ValueError, "JIT fast-cache entries must hold a trace");
    return nullptr;
  }

  const auto slot = hash_slot(static_cast<std::uint32_t>(pc));
  if (self->occupied[slot] != 0 && self->keys[slot] == pc && self->values[slot] == value) {
    Py_RETURN_NONE;
  }
  Py_INCREF(value);
  Py_XDECREF(self->values[slot]);
  self->keys[slot] = static_cast<std::uint32_t>(pc);
  self->values[slot] = value;
  self->occupied[slot] = 1;
  Py_RETURN_NONE;
}

PyObject* fast_cache_clear(fast_cache_object* self, PyObject*) {
  for (std::size_t slot = 0; slot < kFastSlotCount; ++slot) {
    clear_slot(self, slot);
  }
  Py_RETURN_NONE;
}

PyMethodDef fast_cache_methods[] = {
    {"lookup", reinterpret_cast<PyCFunction>(fast_cache_lookup), METH_VARARGS,
     "Return a trace when its PC occupies the matching folded-XOR slot."},
    {"store", reinterpret_cast<PyCFunction>(fast_cache_store), METH_VARARGS,
     "Store one trace in the fixed-capacity folded-XOR slot."},
    {"clear", reinterpret_cast<PyCFunction>(fast_cache_clear), METH_NOARGS,
     "Release all trace references held by the fixed-capacity cache."},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    "_jit_cache_native",
    "Native fixed-capacity folded-XOR cache slots for the Tier 3 JIT cache.",
    -1,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

}  // namespace

PyMODINIT_FUNC PyInit__jit_cache_native() {
  fast_cache_type.tp_name = "_jit_cache_native.FastCache";
  fast_cache_type.tp_basicsize = sizeof(fast_cache_object);
  fast_cache_type.tp_itemsize = 0;
  fast_cache_type.tp_dealloc = fast_cache_dealloc;
  fast_cache_type.tp_flags = Py_TPFLAGS_DEFAULT;
  fast_cache_type.tp_doc = "Fixed-capacity folded-XOR JIT fast cache.";
  fast_cache_type.tp_methods = fast_cache_methods;
  fast_cache_type.tp_new = fast_cache_new;
  if (PyType_Ready(&fast_cache_type) < 0) return nullptr;

  PyObject* module = PyModule_Create(&module_definition);
  if (module == nullptr) return nullptr;
  if (PyModule_AddIntConstant(module, "FAST_SLOT_COUNT",
                              static_cast<long>(kFastSlotCount)) < 0) {
    Py_DECREF(module);
    return nullptr;
  }
  Py_INCREF(&fast_cache_type);
  if (PyModule_AddObject(module, "FastCache", reinterpret_cast<PyObject*>(&fast_cache_type)) < 0) {
    Py_DECREF(&fast_cache_type);
    Py_DECREF(module);
    return nullptr;
  }
  return module;
}
