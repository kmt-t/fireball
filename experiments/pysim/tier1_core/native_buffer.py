"""Shared CPython buffer borrowing for native components, independent of runtimes."""

from __future__ import annotations

import ctypes


class PythonBuffer(ctypes.Structure):
    _fields_ = (
        ("buf", ctypes.c_void_p),
        ("obj", ctypes.c_void_p),
        ("length", ctypes.c_ssize_t),
        ("itemsize", ctypes.c_ssize_t),
        ("readonly", ctypes.c_int),
        ("ndim", ctypes.c_int),
        ("format", ctypes.c_void_p),
        ("shape", ctypes.POINTER(ctypes.c_ssize_t)),
        ("strides", ctypes.POINTER(ctypes.c_ssize_t)),
        ("suboffsets", ctypes.POINTER(ctypes.c_ssize_t)),
        ("internal", ctypes.c_void_p),
    )


_PYTHON_C_API = ctypes.PyDLL(None)
_PYOBJECT_GET_BUFFER = _PYTHON_C_API.PyObject_GetBuffer
_PYOBJECT_GET_BUFFER.argtypes = (
    ctypes.py_object,
    ctypes.POINTER(PythonBuffer),
    ctypes.c_int,
)
_PYOBJECT_GET_BUFFER.restype = ctypes.c_int
_PYBUFFER_RELEASE = _PYTHON_C_API.PyBuffer_Release
_PYBUFFER_RELEASE.argtypes = (ctypes.POINTER(PythonBuffer),)
_PYBUFFER_RELEASE.restype = None


class BufferLease:
    """Hold one CPython buffer export while C++ reads or writes its bytes."""

    __slots__ = ("_active", "_owner", "_view")

    def __init__(self, owner: memoryview, flags: int = 0) -> None:
        # Py_buffer.obj is opaque to GC. Keep storage reachable until all
        # finalizers have run, including native borrowers' teardown.
        self._owner = owner
        self._view = PythonBuffer()
        self._active = False
        assert _PYOBJECT_GET_BUFFER(owner, ctypes.byref(self._view), flags) == 0
        self._active = True

    @property
    def address(self) -> int:
        assert self._active
        return 0 if self._view.buf is None else int(self._view.buf)

    @property
    def size(self) -> int:
        assert self._active
        return self._view.length

    @property
    def stride(self) -> int:
        assert self._active
        return self._view.strides[0] if self._view.strides else self._view.itemsize

    def release(self) -> None:
        if self._active:
            _PYBUFFER_RELEASE(ctypes.byref(self._view))
            self._active = False

    def __del__(self) -> None:
        self.release()
