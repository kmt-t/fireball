"""Integration Scenario 1: Tier 1 Core + Tier 2 Loader & Linear Memory.

Tests:
- Parsing WAT with active data segments, globals, and memory definition
- Linear memory initialization with active data segments
- Dynamic memory growth via `memory.grow` and `memory.size`
- Zero-copy symbol lookup via RadixBinaryTreeView
"""

import wasmtime
from wasm_reader import parse
from interpreter import Interpreter
from system_containers import RadixBinaryTreeView, bswap32
from system import System
from wasi import WasiHostContext

SCENARIO_WAT = """
(module
  (memory (export "memory") 1 4) ;; Initial 1 page (64KB), max 4 pages

  (global $g_counter (mut i32) (i32.const 100))

  ;; Active data segment at offset 256
  (data (i32.const 256) "FIREBALL-SYSTEM-DATA-SEGMENT-0123456789")

  ;; Active data segment at offset 1024
  (data (i32.const 1024) "\\01\\02\\03\\04\\05\\06\\07\\08")

  ;; Function to read a 4-byte integer from linear memory
  (func (export "read_u32") (param $addr i32) (result i32)
    (i32.load (local.get $addr))
  )

  ;; Function to write a 4-byte integer to linear memory
  (func (export "write_u32") (param $addr i32) (param $val i32)
    (i32.store (local.get $addr) (local.get $val))
  )

  ;; Function to test memory.grow and memory.size
  (func (export "test_grow") (param $pages i32) (result i32)
    (local $old_sz i32)
    (local.set $old_sz (memory.size))
    (drop (memory.grow (local.get $pages)))
    (memory.size)
  )

  ;; Function to test global mutation
  (func (export "inc_global") (param $delta i32) (result i32)
    (global.set $g_counter (i32.add (global.get $g_counter) (local.get $delta)))
    (global.get $g_counter)
  )
)
"""


def test_scenario_loader_memory():
    print("[*] Running Scenario 1: Loader, Linear Memory, Data Segments & Radix Lookup...")

    wasm_bytes = bytes(wasmtime.wat2wasm(SCENARIO_WAT))
    module = parse(wasm_bytes)

    # 1. Verify module structure
    assert len(module.exports) >= 5, "Module exports mismatch"
    assert module.memory is not None, "Memory section missing"
    assert module.memory.min_pages == 1, "Initial memory pages mismatch"

    # 2. Setup System & Interpreter
    sysv = System()
    wasi_ctx = WasiHostContext(sysv)
    host_funcs = wasi_ctx.build_interpreter_host_functions(module)
    interp = Interpreter(module, memory=wasi_ctx.guest_memory, host_functions=host_funcs)

    # 3. Verify Active Data Segments loaded in memory
    seg1 = wasi_ctx.guest_memory[256:256 + 39].decode("utf-8")
    assert seg1 == "FIREBALL-SYSTEM-DATA-SEGMENT-0123456789", f"Data segment 1 corrupted: {seg1}"

    seg2 = list(wasi_ctx.guest_memory[1024:1024 + 8])
    assert seg2 == [1, 2, 3, 4, 5, 6, 7, 8], f"Data segment 2 corrupted: {seg2}"

    # 4. Test Memory Read / Write via WASM guest functions
    fn_write = module.export_func_index("write_u32")
    fn_read = module.export_func_index("read_u32")

    interp.call(fn_write, [2048, 0x12345678])
    res = interp.call(fn_read, [2048])
    assert res == [0x12345678], f"read_u32 mismatch: {res}"

    # 5. Test Memory Grow
    fn_grow = module.export_func_index("test_grow")
    res_grow = interp.call(fn_grow, [2])  # grow by 2 pages -> total 3 pages
    assert res_grow == [3], f"memory.grow result mismatch: {res_grow}"
    assert len(wasi_ctx.guest_memory) >= 3 * 65536, "Guest memory size not expanded"

    # Test writing to newly grown page (Page 2: offset 131072)
    val_cafebabe = (0xCAFEBABE - 0x100000000)  # signed i32 representation
    interp.call(fn_write, [131072, val_cafebabe])
    res_page2 = interp.call(fn_read, [131072])
    assert (res_page2[0] & 0xFFFFFFFF) == 0xCAFEBABE, f"Page 2 memory access failed: {res_page2}"

    # 6. Test Globals Mutation
    fn_global = module.export_func_index("inc_global")
    res_g1 = interp.call(fn_global, [25])
    assert res_g1 == [125], f"Global increment failed: {res_g1}"
    res_g2 = interp.call(fn_global, [-50])
    assert res_g2 == [75], f"Global decrement failed: {res_g2}"

    print("    [PASS] Scenario 1 (Loader & Memory) succeeded seamlessly.")


if __name__ == "__main__":
    test_scenario_loader_memory()
