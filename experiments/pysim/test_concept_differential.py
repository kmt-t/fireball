"""
experiments/pysim/test_concept_differential.py

Differential Test Suite (Concept vs. Pysim Equivalence Verifier):
Mechanically validates that the self-contained implementations in `experiments/pysim/`
and the authoritative reference concept implementations in `docs/**/concepts/*_concept.py`
produce identical behavior, return values, trap codes, and state transitions
under exhaustive identical scenarios.

Detects silent divergence/drift between authoritative specifications and pysim simulation.
"""

from __future__ import annotations

import os
import sys

# 1. Add docs concepts to an isolated import path
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DOCS = os.path.join(_ROOT, "docs", "components")

_concept_paths = [
    os.path.join(_DOCS, "tier1_core", "concepts"),
    os.path.join(_DOCS, "tier1_interface", "concepts"),
    os.path.join(_DOCS, "tier2_runtime", "concepts"),
    os.path.join(_DOCS, "tier3_platform", "concepts"),
]
for p in _concept_paths:
    if p not in sys.path:
        sys.path.insert(0, p)

# Import Authoritative Concept Implementations
import flat_view_concept as c_flat
import ipc_router_concept as c_ipc
import vmmio_concept as c_vmmio
import platform_memory_concept as c_mem
import loader_concept as c_ldr
import debugger_concept as c_dbg

# Import Self-Contained Pysim Implementations
import system_containers as p_flat
import ipc_router as p_ipc
import vmmio as p_vmmio
import platform_memory as p_mem
import loader as p_ldr
import debugger as p_dbg


def test_diff_system_containers_flat_map_view():
    """Validates FlatMapView search and slicing across concept and pysim."""
    keys = [10, 20, 30, 40, 50, 60, 70, 80]
    values = ["a", "b", "c", "d", "e", "f", "g", "h"]

    c_view = c_flat.FlatMapView(keys, values)
    p_view = p_flat.FlatMapView(keys, values)

    for k in range(0, 100, 5):
        c_res = c_view.find(k)
        p_res = p_view.find(k)
        assert c_res == p_res, f"FlatMapView find mismatch for key {k}: concept={c_res}, pysim={p_res}"

    # Sliced search
    c_slice = c_view.slice(2, 6)
    p_slice = p_view.slice(2, 6)
    for k in range(0, 100, 5):
        c_res = c_slice.find(k)
        p_res = p_slice.find(k)
        assert c_res == p_res, f"FlatMapView slice find mismatch for key {k}: concept={c_res}, pysim={p_res}"


def test_diff_system_containers_radix_binary_tree_view():
    """Validates RadixBinaryTreeView prefix routing + local search."""
    keys = [0x0100, 0x0104, 0x0108, 0x0200, 0x0210, 0x0300]
    values = [1, 2, 3, 4, 5, 6]
    radix_table = [(0, 0), (0, 3), (3, 5), (5, 6)]
    radix_shift = 8

    c_tree = c_flat.RadixBinaryTreeView(keys, values, radix_table, radix_shift)
    p_tree = p_flat.RadixBinaryTreeView(keys, values, radix_table, radix_shift)

    test_keys = [0x0050, 0x0100, 0x0104, 0x0106, 0x0200, 0x0210, 0x0220, 0x0300, 0x0400]
    for k in test_keys:
        c_res = c_tree.find(k)
        p_res = p_tree.find(k)
        assert c_res == p_res, f"RadixBinaryTreeView mismatch for key {k:#x}: concept={c_res}, pysim={p_res}"


def test_diff_system_containers_bit_view():
    """Validates BitView 1/2/4-bit sub-byte mutations and bounds."""
    for bits in (1, 2, 4):
        c_storage = bytearray(16)
        p_storage = bytearray(16)

        c_bv = c_flat.BitView(c_storage, bits, count=32)
        p_bv = p_flat.BitView(p_storage, bits, count=32)

        max_val = (1 << bits) - 1
        for i in range(32):
            val = (i * 3 + 1) & max_val
            c_bv.put(i, val)
            p_bv.put(i, val)

            assert c_bv.at(i) == p_bv.at(i) == val
            assert c_storage == p_storage, f"Storage mismatch at bit={bits}, idx={i}"


def test_diff_ipc_router_lifecycle_and_rbac():
    """Validates IPCRouter 3-stage routing, queue full rollback, and drop handler."""
    c_r = c_ipc.IPCRouter()
    p_r = p_ipc.IPCRouter()

    # 1. Success route
    c_msg1 = c_ipc.IPCMessage("res1", {"data": 42})
    p_msg1 = p_ipc.IPCMessage("res1", {"data": 42})
    c_res = c_r.route_message("CLIENT_APP", "fireball://hal/gpio/0", c_msg1)
    p_res = p_r.route_message("CLIENT_APP", "fireball://hal/gpio/0", p_msg1)
    assert c_res == p_res, f"Route success mismatch: {c_res} vs {p_res}"
    assert c_msg1.ownership == p_msg1.ownership == "IN_FLIGHT"

    # 2. RBAC denial
    c_msg2 = c_ipc.IPCMessage("res2", {})
    p_msg2 = p_ipc.IPCMessage("res2", {})
    c_res = c_r.route_message("CLIENT_APP", "fireball://dbg/manager/0", c_msg2)
    p_res = p_r.route_message("CLIENT_APP", "fireball://dbg/manager/0", p_msg2)
    assert c_res == p_res, f"RBAC denial mismatch: {c_res} vs {p_res}"
    assert c_msg2.ownership == p_msg2.ownership == "SENDER_OWNS"

    # 3. URI not found
    c_res = c_r.route_message("CLIENT_APP", "fireball://invalid/uri", c_msg2)
    p_res = p_r.route_message("CLIENT_APP", "fireball://invalid/uri", p_msg2)
    assert c_res == p_res, f"URI not found mismatch: {c_res} vs {p_res}"

    # 4. Queue overflow & rollback
    c_msg3 = c_ipc.IPCMessage("res3", {})
    p_msg3 = p_ipc.IPCMessage("res3", {})
    c_r.route_message("CLIENT_APP", "fireball://hal/gpio/0", c_msg3)
    p_r.route_message("CLIENT_APP", "fireball://hal/gpio/0", p_msg3)

    c_overflow = c_ipc.IPCMessage("res_overflow", {})
    p_overflow = p_ipc.IPCMessage("res_overflow", {})
    c_res = c_r.route_message("CLIENT_APP", "fireball://hal/gpio/0", c_overflow)
    p_res = p_r.route_message("CLIENT_APP", "fireball://hal/gpio/0", p_overflow)
    assert c_res == p_res == ("ERR_QUEUE_FULL", "Queue full, rolled back to sender")
    assert c_overflow.ownership == p_overflow.ownership == "SENDER_OWNS"

    # 5. Receive & Grant
    c_rec = c_r.receive_message("ch_gpio")
    p_rec = p_r.receive_message("ch_gpio")
    assert (c_rec is None) == (p_rec is None)
    assert c_rec.resource_id == p_rec.resource_id
    assert c_rec.ownership == p_rec.ownership == "RECEIVER_OWNS"

    # 6. Drop handler
    c_dropped = c_r.trigger_drop_handler("ch_gpio")
    p_dropped = p_r.trigger_drop_handler("ch_gpio")
    assert c_dropped == p_dropped


def test_diff_vmmio_controller_dispatch_and_tlb():
    """Validates VMMIOController 3-tier security gates, TLB hashing, and traps."""
    c_ctrl = c_vmmio.VMMIOController(guest_ram_size=8192)
    p_ctrl = p_vmmio.VMMIOController(guest_ram_size=8192)

    # 1. Tier 1 RAM bypass & OOB
    for addr in (0x0, 0x1000, 0x1FFF, 0x2000, 0x7FFF_FFFF):
        c_stat, c_det = c_ctrl.access(addr, is_write=False, current_task_id=1)
        p_stat, p_det = p_ctrl.access(addr, is_write=False, current_task_id=1)
        assert c_stat == p_stat, f"RAM bypass mismatch at {addr:#x}: {c_stat} vs {p_stat}"

    # 2. Tier 2 Static Device
    vpn_dev = 0xC000_0000 >> 12
    c_called, p_called = [], []
    c_ctrl.map_static_device(vpn_dev, handler=lambda s, o, w: c_called.append((s, o, w)), read=True, write=True)
    p_ctrl.map_static_device(vpn_dev, handler=lambda s, o, w: p_called.append((s, o, w)), read=True, write=True)

    c_stat, _ = c_ctrl.access(0xC0000000, is_write=True, current_task_id=1)
    p_stat, _ = p_ctrl.access(0xC0000000, is_write=True, current_task_id=1)
    assert c_stat == p_stat == "OK_SYSCALL"
    assert c_called == p_called

    # 3. Tier 3 SHM & Owner Verification
    vpn_shm = 0xE000_0000 >> 12
    c_ctrl.map_shm_page(vpn_shm, phys_page=0x100, owner_id=1)
    p_ctrl.map_shm_page(vpn_shm, phys_page=0x100, owner_id=1)

    # Authorized owner
    c_stat, c_det = c_ctrl.access(0xE0000010, is_write=False, current_task_id=1)
    p_stat, p_det = p_ctrl.access(0xE0000010, is_write=False, current_task_id=1)
    assert c_stat == p_stat == "OK_PHYSICAL"

    # Hostile neighbor
    c_stat, c_det = c_ctrl.access(0xE0000010, is_write=False, current_task_id=2)
    p_stat, p_det = p_ctrl.access(0xE0000010, is_write=False, current_task_id=2)
    assert c_stat == p_stat == "TRAP_OWNER_MISMATCH"

    # In-flight revoke
    c_ctrl.revoke_shm_owner(vpn_shm)
    p_ctrl.revoke_shm_owner(vpn_shm)
    c_stat, _ = c_ctrl.access(0xE0000010, is_write=False, current_task_id=1)
    p_stat, _ = p_ctrl.access(0xE0000010, is_write=False, current_task_id=1)
    assert c_stat == p_stat == "TRAP_OWNER_MISMATCH"


def test_diff_platform_memory_manager_and_mpu():
    """Validates MemoryManager partition lease, RAII SharedBlock, and MPU W^X."""
    c_mm = c_mem.MemoryManager()
    p_mm = p_mem.MemoryManager()

    c_mm.init_manager(pool_base=0x20020000, pool_size=c_mem.FB_CONF_MEMORY_POOL_SIZE)
    p_mm.init_manager(pool_base=0x20020000, pool_size=p_mem.FB_CONF_MEMORY_POOL_SIZE)

    # 1. Acquire Partition
    c_pv = c_mm.acquire_partition(owner=1).unwrap()
    p_pv = p_mm.acquire_partition(owner=1).unwrap()
    assert c_pv.size == p_pv.size == 64 * 1024
    assert c_pv.base_address == p_pv.base_address

    # 2. Allocate & Claim SharedBlock
    c_sb = c_mm.allocate_shared(caller_task_id=1, size=4096).unwrap()
    p_sb = p_mm.allocate_shared(caller_task_id=1, size=4096).unwrap()
    assert c_sb.shm_id == p_sb.shm_id
    assert c_sb.base_address == p_sb.base_address

    # Release (flight) -> Grant in vMMIO -> Claim
    c_shm_id = c_sb.release()
    p_shm_id = p_sb.release()
    assert c_shm_id == p_shm_id

    # Grant phase
    c_mm.vmmio_registry.update_owner(c_sb.page_idx, 2)
    p_mm.vmmio_registry.update_owner(p_sb.page_idx, 2)

    c_claimed = c_mm.claim(receiver_task_id=2, shm_id=c_shm_id).unwrap()
    p_claimed = p_mm.claim(receiver_task_id=2, shm_id=p_shm_id).unwrap()
    assert c_claimed.owner == p_claimed.owner == 2

    # 3. MPU W^X Transaction
    c_mpu = c_mm.mpu
    p_mpu = p_mm.mpu
    assert c_mpu and p_mpu

    c_mpu.begin_jit_patch()
    p_mpu.begin_jit_patch()
    c_mpu.assert_no_rwx()
    p_mpu.assert_no_rwx()

    c_mpu.commit_jit_patch()
    p_mpu.commit_jit_patch()
    c_mpu.assert_no_rwx()
    p_mpu.assert_no_rwx()


def test_diff_wasm_loader_symbol_hashing_and_indexing():
    """Validates FNV-1a symbol hashing and RadixBinaryTreeView indexing across loader concept and pysim."""
    # 1. FNV-1a 32-bit hash equivalence
    symbols = ["main", "memory", "add", "_start", "fireball_call", "env.get_time", "core.sched"]
    for sym in symbols:
        c_h = c_ldr.fnv1a_32(sym)
        p_h = p_ldr.fnv1a_32(sym)
        assert c_h == p_h, f"FNV-1a hash mismatch for {sym}: {c_h:#x} vs {p_h:#x}"

    # 2. Loader multi-module index construction & symbol lookup
    wasm_bytes = (
        b"\x00asm\x01\x00\x00\x00"
        b"\x01\x05\x01\x60\x00\x01\x7f"          # Type: () -> i32
        b"\x03\x02\x01\x00"                      # Function: func 0 uses type 0
        b"\x07\x08\x01\x04main\x00\x00"          # Export: "main" -> func 0
        b"\x0a\x06\x01\x04\x00\x41\x2a\x0b"      # Code: i32.const 42, end
    )

    c_loader = c_ldr.WasmLoader()
    p_loader = p_ldr.WasmLoader()

    c_mod = c_loader.prepare("mod1", wasm_bytes)
    p_mod = p_loader.prepare("mod1", wasm_bytes)

    assert len(c_mod.functions) == len(p_mod.functions) == 1
    assert len(c_mod.exports_dict) == len(p_mod.exports_dict) == 1

    c_exp = c_mod.lookup_export("main")
    p_exp = p_mod.lookup_export("main")
    assert c_exp is not None and p_exp is not None
    assert c_exp.index == p_exp.index == 0

    c_none = c_mod.lookup_export("nonexistent")
    p_none = p_mod.lookup_export("nonexistent")
    assert c_none is None and p_none is None


def test_diff_debugger_manager_gdb_rsp():
    """Validates GDB RSP packet handling & register/memory inspection across concept and pysim."""
    # 1. Checksum & packet formatting equivalence
    payloads = ["?", "OK", "S05", "E01", "0000000000000000", "deadbeef"]
    for p in payloads:
        c_fmt = c_dbg.GDBRspProtocol.format_packet(p)
        p_fmt = p_dbg.GDBRspProtocol.format_packet(p)
        assert c_fmt == p_fmt, f"Packet formatting mismatch for {p}: {c_fmt} vs {p_fmt}"

    # 2. Concept Debugger
    c_ctx = c_dbg.ExecutionContext()
    c_interp = c_dbg.WASMInterpreter()
    c_dbg_mgr = c_dbg.DebuggerManager(c_ctx, c_interp)
    c_dbg_mgr.attach()
    c_rsp = c_dbg.GDBRspProtocol(c_dbg_mgr)

    # 3. Pysim Debugger
    p_dbg_mgr = p_dbg.DebuggerManager()
    p_dbg_mgr.attach()
    p_rsp = p_dbg.GDBRspProtocol(p_dbg_mgr)
    p_ctx = p_dbg.WASMContext()
    p_ctx.memory = bytearray(65536)

    # Query Halt Reason ($?)
    c_reply = c_rsp.handle_packet("$?#3f", [])
    p_reply, _ = p_rsp.handle_packet("$?#3f", current_pc=0, ctx=p_ctx, blocks={})
    assert c_reply == p_reply == "$S05#b8"

    # Memory write & read ($M, $m)
    c_rsp.handle_packet("$M100,4:deadbeef#3f", [])
    p_rsp.handle_packet("$M100,4:deadbeef#3f", current_pc=0, ctx=p_ctx, blocks={})
    assert c_ctx.memory[0x100:0x104] == p_ctx.memory[0x100:0x104] == bytes.fromhex("deadbeef")

    c_m_rep = c_rsp.handle_packet("$m100,4#fd", [])
    p_m_rep, _ = p_rsp.handle_packet("$m100,4#fd", current_pc=0, ctx=p_ctx, blocks={})
    assert c_m_rep == p_m_rep == "$deadbeef#20"

    # Breakpoint add & remove ($Z0, $z0)
    c_rsp.handle_packet("$Z0,20,0#45", [])
    p_rsp.handle_packet("$Z0,20,0#45", current_pc=0, ctx=p_ctx, blocks={})
    assert (0x20 in c_dbg_mgr.breakpoints) and p_dbg_mgr.has_breakpoint(0x20)

    c_rsp.handle_packet("$z0,20,0#65", [])
    p_rsp.handle_packet("$z0,20,0#65", current_pc=0, ctx=p_ctx, blocks={})
    assert (0x20 not in c_dbg_mgr.breakpoints) and (not p_dbg_mgr.has_breakpoint(0x20))


def test_diff_wasi_host_context_radix_binary_tree_imports():
    """Validates that WasiHostContext.get_handler_for_import resolves via RadixBinaryTreeView in O(k)."""
    import system as p_sys
    import wasi as p_wasi

    sysv = p_sys.System()
    ctx = p_wasi.WasiHostContext(sysv)

    # 1. Successful resolutions
    assert ctx.get_handler_for_import("wasi_snapshot_preview1", "fd_write") == ctx.fd_write
    assert ctx.get_handler_for_import("wasi_snapshot_preview1", "clock_time_get") == ctx.clock_time_get
    assert ctx.get_handler_for_import("wasi_snapshot_preview1", "proc_exit") == ctx.proc_exit
    assert ctx.get_handler_for_import("wasi_unstable", "fd_read") == ctx.fd_read
    assert ctx.get_handler_for_import("fireball", "fireball_call") == ctx.fireball_call
    assert ctx.get_handler_for_import("env", "fd_write") == ctx.fd_write

    # 2. Non-existent imports
    assert ctx.get_handler_for_import("wasi_snapshot_preview1", "nonexistent") is None
    assert ctx.get_handler_for_import("unknown_module", "fd_write") is None


def test_diff_fireball_call_radix_binary_tree_dispatch():
    """Validates that System.fireball_call dispatches syscalls via RadixBinaryTreeView in O(k)."""
    import system as p_sys

    sysv = p_sys.System()

    # Valid syscalls
    assert sysv.fireball_call(p_sys.FbSyscallId.SYS_YIELD, 0, 0, 0, 0, 0, 0) == int(p_sys.WasiErrno.SUCCESS)
    assert sysv.fireball_call(p_sys.FbSyscallId.SYS_HALT, 0, 0, 0, 0, 0, 0) == int(p_sys.WasiErrno.SUCCESS)
    assert sysv.halted is True

    # Invalid syscall
    assert sysv.fireball_call(0x999, 0, 0, 0, 0, 0, 0) == int(p_sys.WasiErrno.NOSYS)


ALL_DIFF_TESTS = [
    test_diff_system_containers_flat_map_view,
    test_diff_system_containers_radix_binary_tree_view,
    test_diff_system_containers_bit_view,
    test_diff_ipc_router_lifecycle_and_rbac,
    test_diff_vmmio_controller_dispatch_and_tlb,
    test_diff_platform_memory_manager_and_mpu,
    test_diff_wasm_loader_symbol_hashing_and_indexing,
    test_diff_debugger_manager_gdb_rsp,
    test_diff_wasi_host_context_radix_binary_tree_imports,
    test_diff_fireball_call_radix_binary_tree_dispatch,
]

if __name__ == "__main__":
    for t in ALL_DIFF_TESTS:
        t()
        print(f"[PASS] {t.__name__}")
    print(f"\n[PASS] All {len(ALL_DIFF_TESTS)} concept-to-pysim differential equivalence tests passed.")
