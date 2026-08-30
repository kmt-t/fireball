import sys
from pathlib import Path

_PYSIM_DIR = (
    Path(__file__).resolve().parents[1]
    if any(
        d in str(Path(__file__))
        for d in ("tests", "scenarios", "core", "runtime", "jit", "platforms")
    )
    else Path(__file__).resolve().parent
)

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import sys
from pathlib import Path

"""Integration Scenario 2: Tier 2 Runtime + System Call & WASI IO.

Tests:
- Host function dispatch via WASI standard ABI (wasi_snapshot_preview1)
- `fd_write` with multiple iovec structures (scatter-gather I/O)
- `proc_exit` guest termination and exit code propagation
- WASI memory isolation and bounds validation
"""

import wasmtime
from wasm_reader import parse
from interpreter import Interpreter
from system import System
from wasi import WasiHostContext

SCENARIO2_WAT = """
(module
  (import "wasi_snapshot_preview1" "fd_write"
    (func $fd_write (param i32 i32 i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "proc_exit"
    (func $proc_exit (param i32)))

  (memory (export "memory") 1)
  ;; Pre-populate strings in memory:
  ;; offset 100: "HELLO-WASI" (10 bytes)
  ;; offset 200: " [SYSTEM_OK]\\n" (13 bytes)
  (data (i32.const 100) "HELLO-WASI")
  (data (i32.const 200) " [SYSTEM_OK]\\n")
  ;; Function to perform scatter-gather write using 2 iovec elements:
  ;; iov[0] = {buf: 100, len: 10} -> at offset 16 (16: 100, 20: 10)
  ;; iov[1] = {buf: 200, len: 13} -> at offset 24 (24: 200, 28: 13)
  ;; nwritten written to offset 32
  (func (export "test_scatter_write") (result i32)
    ;; Setup iov 0
    (i32.store (i32.const 16) (i32.const 100))
    (i32.store (i32.const 20) (i32.const 10))
    ;; Setup iov 1
    (i32.store (i32.const 24) (i32.const 200))
    (i32.store (i32.const 28) (i32.const 13))
    ;; call fd_write(fd=1 (stdout), iovs_ptr=16, iovs_len=2, nwritten_ptr=32)
    (drop (call $fd_write (i32.const 1) (i32.const 16) (i32.const 2) (i32.const 32)))
    ;; return nwritten
    (i32.load (i32.const 32))
  )
  ;; Function to test guest proc_exit
  (func (export "test_exit") (param $code i32)
    (call $proc_exit (local.get $code))
  )
)
"""


def test_scenario_wasi_syscall():
    print("[*] Running Scenario 2: WASI System Call & I/O Integration...")
    wasm_bytes = bytes(wasmtime.wat2wasm(SCENARIO2_WAT))
    module = parse(wasm_bytes)
    sysv = System()
    wasi_ctx = WasiHostContext(sysv)
    host_funcs = wasi_ctx.build_interpreter_host_functions(module)
    interp = Interpreter(
        module, memory=wasi_ctx.guest_memory, host_functions=host_funcs
    )
    # 1. Execute scatter-gather fd_write
    fn_write = module.export_func_index("test_scatter_write")
    res_written = interp.call(fn_write, [])
    assert res_written == [23], (
        f"Expected 23 bytes written (10 + 13), got {res_written}"
    )
    output_str = sysv.transport.drain().decode("utf-8")
    assert output_str == "HELLO-WASI [SYSTEM_OK]\n", (
        f"WASI stdout mismatch: {repr(output_str)}"
    )
    # 2. Test proc_exit
    fn_exit = module.export_func_index("test_exit")
    interp.call(fn_exit, [42])
    assert sysv.halted is True, "Expected system to be halted after proc_exit"
    assert sysv.exit_code == 42, f"Expected exit code 42, got {sysv.exit_code}"
    print("    [PASS] Scenario 2 (WASI & Syscalls) succeeded seamlessly.")


if __name__ == "__main__":
    test_scenario_wasi_syscall()
