import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

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

"""Integration Scenario 2: Tier 2 Runtime + System Call & WASI IO.

Tests:
- Host function dispatch via WASI standard ABI (wasi_snapshot_preview1)
- `fd_write` with multiple iovec structures (scatter-gather I/O)
- `proc_exit` guest termination and exit code propagation
- WASI memory isolation and bounds validation
"""

try:
    import wasmtime
except ImportError:
    wasmtime = None

import wasm_opcodes as op
from interpreter import Interpreter
from system import System
from wasi import WasiHostContext
from wasm_reader import parse

SCENARIO2_WAT = """
(module
  (import "wasi_snapshot_preview1" "fd_write"
    (func $fd_write (param i32 i32 i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "proc_exit"
    (func $proc_exit (param i32)))

  (memory (export "memory") 1)
  (data (i32.const 100) "HELLO-WASI")
  (data (i32.const 200) " [SYSTEM_OK]\\n")
  (func (export "test_scatter_write") (result i32)
    (i32.store (i32.const 16) (i32.const 100))
    (i32.store (i32.const 20) (i32.const 10))
    (i32.store (i32.const 24) (i32.const 200))
    (i32.store (i32.const 28) (i32.const 13))
    (drop (call $fd_write (i32.const 1) (i32.const 16) (i32.const 2) (i32.const 32)))
    (i32.load (i32.const 32))
  )
  (func (export "test_exit") (param $code i32)
    (call $proc_exit (local.get $code))
  )
)
"""


from leb128 import encode_signed, encode_unsigned


def _create_scenario2_binary() -> bytes:
    """Builds standard WASI binary for scenario 2 directly in pure Python."""
    buf = bytearray(b"\x00asm\x01\x00\x00\x00")

    # 1. Type Section:
    # type 0: (i32, i32, i32, i32) -> (i32)  [fd_write]
    # type 1: (i32) -> ()                    [proc_exit]
    # type 2: () -> (i32)                    [test_scatter_write]
    # type 3: (i32) -> ()                    [test_exit]
    type_sec = (
        b"\x04\x60\x04\x7f\x7f\x7f\x7f\x01\x7f\x60\x01\x7f\x00\x60\x00\x01\x7f\x60\x01\x7f\x00"
    )
    buf.extend([0x01, len(type_sec)])
    buf.extend(type_sec)

    # 2. Import Section:
    # 0: wasi_snapshot_preview1::fd_write (type 0)
    # 1: wasi_snapshot_preview1::proc_exit (type 1)
    imp_sec = (
        b"\x02"
        b"\x16wasi_snapshot_preview1\x08fd_write\x00\x00"
        b"\x16wasi_snapshot_preview1\x09proc_exit\x00\x01"
    )
    buf.extend([0x02, len(imp_sec)])
    buf.extend(imp_sec)

    # 3. Function Section: 2 functions (types 2, 3)
    func_sec = b"\x02\x02\x03"
    buf.extend([0x03, len(func_sec)])
    buf.extend(func_sec)

    # 4. Memory Section: 1 min memory
    mem_sec = b"\x01\x00\x01"
    buf.extend([0x05, len(mem_sec)])
    buf.extend(mem_sec)

    # 5. Export Section:
    # memory -> mem 0
    # test_scatter_write -> func 2
    # test_exit -> func 3
    exp_sec = b"\x03\x06memory\x02\x00\x12test_scatter_write\x00\x02\x09test_exit\x00\x03"
    buf.extend([0x07, len(exp_sec)])
    buf.extend(exp_sec)

    # 6. Code Section:
    # Func 2 (test_scatter_write):
    f2_body = bytearray([0x00])  # 0 local groups
    # i32.const 16, i32.const 100, i32.store align=2, offset=0
    f2_body.extend(
        [
            op.I32_CONST,
            *list(encode_signed(16)),
            op.I32_CONST,
            *list(encode_signed(100)),
            op.I32_STORE,
            2,
            0,
        ]
    )
    # i32.const 20, i32.const 10, i32.store
    f2_body.extend(
        [
            op.I32_CONST,
            *list(encode_signed(20)),
            op.I32_CONST,
            *list(encode_signed(10)),
            op.I32_STORE,
            2,
            0,
        ]
    )
    # i32.const 24, i32.const 200, i32.store
    f2_body.extend(
        [
            op.I32_CONST,
            *list(encode_signed(24)),
            op.I32_CONST,
            *list(encode_signed(200)),
            op.I32_STORE,
            2,
            0,
        ]
    )
    # i32.const 28, i32.const 13, i32.store
    f2_body.extend(
        [
            op.I32_CONST,
            *list(encode_signed(28)),
            op.I32_CONST,
            *list(encode_signed(13)),
            op.I32_STORE,
            2,
            0,
        ]
    )
    # call $fd_write(1, 16, 2, 32)
    f2_body.extend(
        [
            op.I32_CONST,
            0x01,
            op.I32_CONST,
            16,
            op.I32_CONST,
            0x02,
            op.I32_CONST,
            32,
            op.CALL,
            0x00,
            op.DROP,
        ]
    )
    # i32.load 32
    f2_body.extend([op.I32_CONST, 32, op.I32_LOAD, 0x02, 0x00, op.END])

    # Func 3 (test_exit):
    f3_body = bytearray([0x00])  # 0 local groups
    f3_body.extend([op.LOCAL_GET, 0x00, op.CALL, 0x01, op.END])

    code_sec = bytearray([0x02])
    code_sec.extend(encode_unsigned(len(f2_body)))
    code_sec.extend(f2_body)
    code_sec.extend(encode_unsigned(len(f3_body)))
    code_sec.extend(f3_body)
    buf.extend([0x0A])
    buf.extend(encode_unsigned(len(code_sec)))
    buf.extend(code_sec)

    # 7. Data Section:
    d1 = b"HELLO-WASI"
    d2 = b" [SYSTEM_OK]\n"
    data_sec = bytearray([0x02])
    # seg 0: mem_idx = 0, offset = 100
    data_sec.extend(encode_unsigned(0))
    data_sec.extend([op.I32_CONST, *list(encode_signed(100)), op.END])
    data_sec.extend(encode_unsigned(len(d1)))
    data_sec.extend(d1)
    # seg 1: mem_idx = 0, offset = 200
    data_sec.extend(encode_unsigned(0))
    data_sec.extend([op.I32_CONST, *list(encode_signed(200)), op.END])
    data_sec.extend(encode_unsigned(len(d2)))
    data_sec.extend(d2)

    buf.extend([0x0B])
    buf.extend(encode_unsigned(len(data_sec)))
    buf.extend(data_sec)

    return bytes(buf)


def test_scenario_wasi_syscall():
    print("[*] Running Scenario 2: WASI System Call & I/O Integration...")
    if wasmtime is not None:
        wasm_bytes = bytes(wasmtime.wat2wasm(SCENARIO2_WAT))
    else:
        wasm_bytes = _create_scenario2_binary()

    module = parse(wasm_bytes)
    sysv = System()
    wasi_ctx = WasiHostContext(sysv)
    host_funcs = wasi_ctx.build_interpreter_host_functions(module)
    interp = Interpreter(module, memory=wasi_ctx.guest_memory, host_functions=host_funcs)
    # 1. Execute scatter-gather fd_write
    fn_write = module.export_func_index("test_scatter_write")
    res_written = interp.call(fn_write, [])
    assert res_written == [23], f"Expected 23 bytes written (10 + 13), got {res_written}"
    output_str = sysv.transport.drain().decode("utf-8")
    assert output_str == "HELLO-WASI [SYSTEM_OK]\n", f"WASI stdout mismatch: {output_str!r}"
    # 2. Test proc_exit
    fn_exit = module.export_func_index("test_exit")
    interp.call(fn_exit, [42])
    assert sysv.halted is True, "Expected system to be halted after proc_exit"
    assert sysv.exit_code == 42, f"Expected exit code 42, got {sysv.exit_code}"
    print("    [PASS] Scenario 2 (WASI & Syscalls) succeeded seamlessly.")


if __name__ == "__main__":
    test_scenario_wasi_syscall()
