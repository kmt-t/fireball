#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <cstdint>

using trace_fn_t = void (*)(void*, void*, void*, std::uint32_t);

static PyObject* invoke_trace(PyObject*, PyObject* args) {
  unsigned long long fn_addr = 0;
  unsigned long long ctx_addr = 0;
  unsigned long long sp_addr = 0;
  unsigned long long local_base_addr = 0;
  unsigned int tos = 0;

  if (!PyArg_ParseTuple(args, "KKKKI", &fn_addr, &ctx_addr, &sp_addr,
                        &local_base_addr, &tos)) {
    return nullptr;
  }
  if (fn_addr == 0) {
    PyErr_SetString(PyExc_ValueError, "trace function address must be non-zero");
    return nullptr;
  }

  const auto fn = reinterpret_cast<trace_fn_t>(
      static_cast<std::uintptr_t>(fn_addr));
  auto* ctx = reinterpret_cast<void*>(static_cast<std::uintptr_t>(ctx_addr));
  auto* sp = reinterpret_cast<void*>(static_cast<std::uintptr_t>(sp_addr));
  auto* local_base = reinterpret_cast<void*>(
      static_cast<std::uintptr_t>(local_base_addr));

  Py_BEGIN_ALLOW_THREADS
  fn(ctx, sp, local_base, static_cast<std::uint32_t>(tos));
  Py_END_ALLOW_THREADS
  Py_RETURN_NONE;
}

static PyMethodDef module_methods[] = {
    {"invoke_trace", invoke_trace, METH_VARARGS,
     "Call a JIT trace using the native four-argument ABI."},
    {nullptr, nullptr, 0, nullptr},
};

static PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    "native_trace_call",
    "Minimal native bridge for the JIT trace ABI.",
    -1,
    module_methods,
};

PyMODINIT_FUNC PyInit_native_trace_call() {
  return PyModule_Create(&module_definition);
}
