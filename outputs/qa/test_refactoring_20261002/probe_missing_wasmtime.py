import importlib.abc
import runpy
import sys


class MissingWasmtime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "wasmtime":
            raise ModuleNotFoundError("controlled missing wasmtime", name=fullname)
        return None


sys.meta_path.insert(0, MissingWasmtime())
try:
    runpy.run_path("experiments/pysim/qa/scenarios/scenario10_vmmio_virtual_devices.py")
except ModuleNotFoundError as exc:
    assert exc.name == "wasmtime"
    print("PASS: missing required dependency fails before any Scenario 10 success output")
else:
    raise AssertionError("missing wasmtime must not produce a success result")
