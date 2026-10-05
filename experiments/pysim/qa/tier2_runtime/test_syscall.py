from __future__ import annotations

"""
Unit tests for Tier 2 Runtime: Syscall & WASI Environment
Traceability: runtime_syscall_test_spec.md
"""

import ctypes
import struct
import time
from pathlib import Path

import pytest

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


from ipc_router import (
    FB_URI_HAL_STDOUT,
    IPCMessage,
    IPCStatus,
    OwnershipState,
    Role,
    bytes_to_kv_entries,
    kv_entries_to_bytes,
)
from qa.shared.fixtures.platform_drivers import create_reference_platform_drivers
from qa.shared.fixtures.uvwasi_reference import UvwasiReferenceContext
from qa.shared.helpers import make_native_interpreter, wat_to_wasm
from scheduler import ChannelAction, TaskState
from system import (
    FB_CONF_GUEST_RAM_SIZE,
    FB_CONF_VSOC_PASSTHROUGH_BASE,
    FbSyscallId,
    System,
    WasiErrno,
)
from tier2_runtime.vsoc.virq import FB_CONF_VIRQ_MAX_NODES, INVALID_FUNCTION_INDEX, VirqNode
from tier2_runtime.wasm.module import Module
from tier2_runtime.wasm.reader import parse


def test_syscall_01_unknown_id_returns_nosys():
    """TEST-SYS-90 / GOTCHA-SYS-01: unknown IDs return NOSYS without stopping the system."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        assert not sysv.halted and not sysv.reset_requested
        assert sysv.fireball_call(0xDEAD, 0, 0, 0, 0, 0, 0) == WasiErrno.NOSYS
        assert not sysv.halted and not sysv.reset_requested
    finally:
        sysv.shutdown()


def test_syscall_16_trigger_set_pin_reserved_nosys():
    """TEST-SYS-16: TRIGGER_SET_PIN is a registered ID with no pysim GPIO register
    backing yet; it must be safely undispatched (NOSYS), not crash or panic."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        assert sysv.fireball_call(FbSyscallId.TRIGGER_SET_PIN, 0, 1, 0, 0, 0, 0) == WasiErrno.NOSYS
    finally:
        sysv.shutdown()


def test_syscall_02_host_call_system_control():
    """TEST-SYS-02, TEST-SYS-03: HALT and RESET change their specified system state."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        assert sysv.fireball_call(FbSyscallId.SYS_RESET, 0, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.reset_requested
        assert sysv.fireball_call(FbSyscallId.SYS_HALT, 0, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.halted
    finally:
        sysv.shutdown()


def test_syscall_04_guest_yield_hands_off_to_ready_task():
    """TEST-SYS-01: SYS_YIELD returns to COOS at a resumable guest boundary."""
    from scheduler import ChannelAction, TaskState
    from tier2_runtime.wasm.reader import parse
    from tier3_platform.drivers.wasi.context import WasiHostContext

    wasm = wat_to_wasm(
        """
        (module
          (import "fireball" "fireball_call"
            (func $fb (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
          (memory (export "memory") 1)
          (func (export "main") (local $count i32)
            i32.const 1 i32.const 0 i32.const 0 i32.const 0
            i32.const 0 i32.const 0 i32.const 0 call $fb drop
            loop $again
              local.get $count
              i32.const 1
              i32.add
              local.tee $count
              i32.const 3
              i32.lt_s
              br_if $again
            end
            i32.const 0
            i32.const 1
            i32.store
          )
        )
        """
    )
    module = parse(wasm)
    system = System()
    try:
        host = WasiHostContext(system)
        host_functions = host.build_interpreter_host_functions(module)
        interpreter = make_native_interpreter(
            module,
            memory=host.guest_memory,
            host_functions=host_functions,
            bump_allocator=system.runtime_engine.bump_allocator,
        )
        guest_id = system.scheduler.spawn(
            "yielding_guest",
            system.run_guest(interpreter, module.export_func_index("main"), ()),
        )
        observed_guest_state: list[TaskState] = []
        observed_guest_memory: list[int] = []

        def monitor_task():
            guest = system.scheduler.get_task(guest_id)
            assert guest is not None
            observed_guest_state.append(guest.state)
            observed_guest_memory.append(int.from_bytes(host.guest_memory[:4], "little"))
            yield (ChannelAction.YIELD, None)

        system.scheduler.spawn("yield_observer", monitor_task())
        system.scheduler.run_until_idle()

        guest = system.scheduler.get_task(guest_id)
        assert guest is not None and guest.state == TaskState.TERMINATED
        assert observed_guest_state == [TaskState.READY]
        assert observed_guest_memory == [0]
        assert int.from_bytes(host.guest_memory[:4], "little") == 1
    finally:
        system.shutdown()


def test_syscall_03_mmio_read_write():
    """TEST-SYS-10, 11, 12, 93: MMIO data uses the output pointer, including errno-valued data."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        guest_mem = bytearray(64)
        sysv.bind_runtime(guest_mem)
        addr = FB_CONF_VSOC_PASSTHROUGH_BASE
        assert (
            sysv.fireball_call(FbSyscallId.MMIO_WRITE32, addr, 0xCAFEBABE, 0, 0, 0, 0)
            == WasiErrno.SUCCESS
        )
        assert bytes(sysv.phys_mem[:4]) == b"\xbe\xba\xfe\xca"
        assert sysv.fireball_call(FbSyscallId.MMIO_READ32, addr, 8, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert int.from_bytes(guest_mem[8:12], "little") == 0xCAFEBABE
        # A successful MMIO value can equal an errno number. The raw return
        # remains SUCCESS and the data travels through value_out_ptr.
        assert (
            sysv.fireball_call(FbSyscallId.MMIO_WRITE32, addr, WasiErrno.FAULT, 0, 0, 0, 0)
            == WasiErrno.SUCCESS
        )
        assert bytes(sysv.phys_mem[:4]) == b"\x15\x00\x00\x00"
        assert sysv.fireball_call(FbSyscallId.MMIO_READ32, addr, 8, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert int.from_bytes(guest_mem[8:12], "little") == WasiErrno.FAULT
        memory_before = bytes(guest_mem)
        physical_before = bytes(sysv.phys_mem)
        assert (
            sysv.fireball_call(FbSyscallId.MMIO_READ32, addr, len(guest_mem) - 2, 0, 0, 0, 0)
            == WasiErrno.FAULT
        )
        assert bytes(guest_mem) == memory_before
        assert bytes(sysv.phys_mem) == physical_before
    finally:
        sysv.shutdown()


def test_syscall_11_mmio_read32_out_of_bounds():
    """TEST-SYS-11: MMIO_READ32 on a linear guest-RAM address beyond FB_CONF_GUEST_RAM_SIZE is rejected."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        guest_mem = bytearray(b"\xa5" * 16)
        sysv.bind_runtime(guest_mem)
        before = bytes(guest_mem)
        oob_addr = FB_CONF_GUEST_RAM_SIZE + 0x1000  # bit31=0 (linear), past guest RAM
        assert (
            sysv.fireball_call(FbSyscallId.MMIO_READ32, oob_addr, 8, 0, 0, 0, 0) == WasiErrno.FAULT
        )
        assert bytes(guest_mem) == before
    finally:
        sysv.shutdown()


def test_syscall_12_mmio_write32_permission_denied():
    """TEST-SYS-12: MMIO_WRITE32 to a page mapped read-only is rejected."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        vpn = (FB_CONF_VSOC_PASSTHROUGH_BASE >> 12) + 100  # fresh page, unused at startup
        sysv.vmmio.map_passthrough_page(vpn=vpn, phys_page=0, write=False)
        addr = vpn << 12
        physical_before = bytes(sysv.phys_mem)
        assert (
            sysv.fireball_call(FbSyscallId.MMIO_WRITE32, addr, 0xDEAD, 0, 0, 0, 0) == WasiErrno.PERM
        )
        assert bytes(sysv.phys_mem) == physical_before
    finally:
        sysv.shutdown()


def test_syscall_13_mmio_read8_write8():
    """TEST-SYS-13: MMIO_READ8/MMIO_WRITE8 round-trip at 8-bit width."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        guest_mem = bytearray(b"\xa5" * 16)
        sysv.bind_runtime(guest_mem)
        addr = FB_CONF_VSOC_PASSTHROUGH_BASE
        sysv.phys_mem[:8] = b"\xa5" * 8
        assert (
            sysv.fireball_call(FbSyscallId.MMIO_WRITE8, addr, 0xAB, 0, 0, 0, 0) == WasiErrno.SUCCESS
        )
        assert bytes(sysv.phys_mem[:8]) == b"\xab" + b"\xa5" * 7
        assert sysv.fireball_call(FbSyscallId.MMIO_READ8, addr, 8, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert int.from_bytes(guest_mem[8:12], "little") == 0xAB
        assert guest_mem == b"\xa5" * 8 + b"\xab\x00\x00\x00" + b"\xa5" * 4
    finally:
        sysv.shutdown()


def test_syscall_14_mmio_bulk_read_write_invalid_size():
    """TEST-SYS-14: MMIO_BULK_READ/WRITE with an oversized byte_count is rejected."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        guest_mem = bytearray(b"\xa5" * 64)
        sysv.bind_runtime(guest_mem)
        memory_before = bytes(guest_mem)
        physical_before = bytes(sysv.phys_mem)
        addr = FB_CONF_VSOC_PASSTHROUGH_BASE
        huge_count = 0x20000  # exceeds the entire PASSTHROUGH backing array
        assert (
            sysv.fireball_call(FbSyscallId.MMIO_BULK_READ, addr, 0, huge_count, 0, 0, 0)
            == WasiErrno.FAULT
        )
        assert bytes(guest_mem) == memory_before
        assert bytes(sysv.phys_mem) == physical_before
        assert (
            sysv.fireball_call(FbSyscallId.MMIO_BULK_WRITE, addr, 0, huge_count, 0, 0, 0)
            == WasiErrno.FAULT
        )
        assert bytes(guest_mem) == memory_before
        assert bytes(sysv.phys_mem) == physical_before
    finally:
        sysv.shutdown()


def test_syscall_15_mmio_bulk_read_dest_offset_out_of_bounds():
    """TEST-SYS-15: MMIO_BULK_READ rejects a dest_offset beyond guest RAM and writes nothing."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        guest_mem = bytearray(b"\xaa" * 16)
        sysv.bind_runtime(guest_mem)
        addr = FB_CONF_VSOC_PASSTHROUGH_BASE
        assert (
            sysv.fireball_call(FbSyscallId.MMIO_BULK_READ, addr, 100, 4, 0, 0, 0) == WasiErrno.FAULT
        )
        assert guest_mem == bytearray(b"\xaa" * 16), (
            "no partial write should occur on out-of-bounds dest_offset"
        )
    finally:
        sysv.shutdown()


def test_syscall_16_mmio_access_width_stays_inside_shm_mapping():
    """TEST-SYS-94: a valid start address does not permit a width beyond the SHM mapping."""
    sysv = System()
    runtime_task = sysv.start_runtime_task(name="test_runtime_task")
    try:
        vpn = 0xE000_3000 >> 12
        physical_addr = 0x1000
        sysv.vmmio.map_shm_page(
            vpn=vpn,
            physical_addr=physical_addr,
            owner_id=runtime_task.task_id,
            mapping_size=4,
        )
        addr = vpn << 12
        sysv.phys_mem[:] = b"\xa5" * len(sysv.phys_mem)
        sysv.phys_mem[physical_addr : physical_addr + 4] = b"safe"

        assert (
            sysv.fireball_call(FbSyscallId.MMIO_WRITE32, addr, 0x11223344, 0, 0, 0, 0)
            == WasiErrno.SUCCESS
        )
        assert bytes(sysv.phys_mem[physical_addr : physical_addr + 4]) == b"\x44\x33\x22\x11"
        before = bytes(sysv.phys_mem)
        assert (
            sysv.fireball_call(FbSyscallId.MMIO_WRITE32, addr + 1, 0xAABBCCDD, 0, 0, 0, 0)
            == WasiErrno.FAULT
        )
        assert bytes(sysv.phys_mem) == before
    finally:
        sysv.shutdown()


@pytest.mark.parametrize("destination_kind", ("passthrough", "dynamic"))
@pytest.mark.parametrize("transport", ("import", "memory.copy"))
def test_syscall_04_vdma_host_call_transfer(destination_kind: str, transport: str) -> None:
    """TEST-SYS-20: 実guestの成功復帰直後にCPU読出しし、HAL実体まで確認する。"""
    from tier2_runtime.hal.dispatch import HalBufferMapStatus
    from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter
    from tier2_runtime.wasm.reader import parse
    from tier3_platform.drivers.wasi.context import WasiHostContext

    system = System()
    try:
        destination = FB_CONF_VSOC_PASSTHROUGH_BASE + 0x1003
        handle = system.pool.buffer(1)
        if destination_kind == "dynamic":
            destination = handle.virtual_address + 3
        operation = (
            f"i32.const 16 i32.const {destination} i32.const 4 call $dma i32.const 0 i32.ne if unreachable end"
            if transport == "import"
            else f"i32.const {destination} i32.const 16 i32.const 4 memory.copy"
        )
        import_decl = (
            '(import "fireball" "vdma_start" (func $dma (param i32 i32 i32) (result i32)))'
            if transport == "import"
            else ""
        )
        module = parse(
            wat_to_wasm(f"""
          (module {import_decl}
            (memory (export "memory") 1)
            (func (export "main")
              {operation}
              i32.const 32 i32.const {destination} i32.load align=1 i32.store))
        """)
        )
        host = WasiHostContext(system)
        host.guest_memory[16:20] = b"\x44\x33\x22\x11"
        bindings = InterpreterBindings.with_memory_and_functions(
            host.guest_memory,
            host.build_interpreter_host_functions(module),
            vdma_transfer=system.vdma_transfer,
        )
        interpreter = NativeInterpreter(
            module,
            bindings,
            vmmio=system.vmmio,
            phys_mem=system.phys_mem,
            bump_allocator=system.runtime_engine.bump_allocator,
        )
        observed: list[bytes] = []

        def guest():
            if destination_kind == "dynamic":
                assert system.pool.map_for_io(handle.buffer_id) == HalBufferMapStatus.MAPPED
                system.pool.view(handle, 0, handle.capacity)[:] = b"\xa5" * handle.capacity
            else:
                system.phys_mem[0x1000:0x1010] = b"\xa5" * 16
            yield from system.run_guest(interpreter, module.export_func_index("main"), ())
            observed.append(bytes(host.guest_memory[32:36]))
            if destination_kind == "dynamic":
                actual = bytes(system.pool.view(handle, 0, handle.capacity))
                assert actual == b"\xa5" * 3 + b"\x44\x33\x22\x11" + b"\xa5" * (handle.capacity - 7)
                system.pool.unmap_after_io(handle.buffer_id)
            else:
                assert (
                    bytes(system.phys_mem[0x1000:0x1010])
                    == b"\xa5" * 3 + b"\x44\x33\x22\x11" + b"\xa5" * 9
                )

        task_id = system.scheduler.spawn("dma_guest", guest(), role=Role.RUNTIME)
        system.scheduler.run_until_idle()
        task = system.scheduler.get_task(task_id)
        assert task is not None and task.state == TaskState.TERMINATED
        assert observed == [b"\x44\x33\x22\x11"]
        assert bytes(host.guest_memory[16:20]) == b"\x44\x33\x22\x11"
    finally:
        system.shutdown()


@pytest.mark.parametrize("offset,count", ((255, 2), (256, 1), (0, 257)))
def test_vdma_dynamic_bounds_rejection_preserves_real_buffer(offset: int, count: int) -> None:
    """TEST-VMMIO-28/31: 4KB PTE内でも256byte実バッファ外の転送を拒否する。"""
    from tier2_runtime.hal.dispatch import HalBufferMapStatus
    from tier3_platform.drivers.wasi.context import WasiHostContext

    system = System()
    task = system.start_runtime_task(name="dynamic_owner")
    try:
        host = WasiHostContext(system)
        host.guest_memory[:] = b"\x5a" * len(host.guest_memory)
        handle = system.pool.buffer(1)
        assert system.pool.map_for_io(handle.buffer_id) == HalBufferMapStatus.MAPPED
        view = system.pool.view(handle, 0, handle.capacity)
        view[:] = b"\xa5" * handle.capacity
        before = bytes(view)
        guest_before = bytes(host.guest_memory)
        pte = system.vmmio.ptes.view().find(handle.virtual_address >> 12)
        assert pte is not None
        assert (
            system.host_calls.vdma_start(0, handle.virtual_address + offset, count)
            != WasiErrno.SUCCESS
        )
        assert bytes(view) == before
        assert bytes(host.guest_memory) == guest_before
        assert system.vmmio.ptes.view().find(handle.virtual_address >> 12) is pte
        assert pte.owner_id == task.task_id
    finally:
        system.shutdown()


@pytest.mark.parametrize("operation", ("load", "store"))
@pytest.mark.parametrize("width", (1, 2, 4, 8))
def test_dynamic_guest_access_checks_full_instruction_width(operation: str, width: int) -> None:
    """TEST-VMMIO-28: 実guest命令の全幅を検査し、実容量外なら更新前にtrapする。"""
    from tier2_runtime.hal.dispatch import HalBufferMapStatus
    from tier2_runtime.interpreter.interpreter import (
        InterpreterBindings,
        NativeInterpreter,
    )
    from tier2_runtime.interpreter.interpreter import (
        TrapCode as InterpreterTrapCode,
    )
    from tier2_runtime.vmmio.controller import TrapCode as VmmioTrapCode
    from tier2_runtime.wasm.reader import parse

    system = System()
    owner = system.start_runtime_task(name="dynamic_width_owner")
    try:
        handle = system.pool.buffer(1)
        assert system.pool.map_for_io(handle.buffer_id) == HalBufferMapStatus.MAPPED
        view = system.pool.view(handle, 0, handle.capacity)
        original = bytes(index & 0xFF for index in range(handle.capacity))
        scalar = "i64" if width == 8 else "i32"
        suffix = "8_u" if width == 1 else "16_u" if width == 2 else ""
        if operation == "load":
            instruction = f"{scalar}.load{suffix} align=1"
            result_type = scalar
        else:
            suffix = "8" if width == 1 else "16" if width == 2 else ""
            instruction = f"{scalar}.const 17 {scalar}.store{suffix} align=1 i32.const 19"
            result_type = "i32"
        module = parse(
            wat_to_wasm(f"""
          (module (memory 1)
            (func (param i32) (result {result_type}) local.get 0 {instruction}))
        """)
        )
        guest_memory = bytearray(b"\x5a" * 65536)
        interpreter = NativeInterpreter(
            module,
            InterpreterBindings.with_memory(guest_memory),
            vmmio=system.vmmio,
            phys_mem=system.phys_mem,
        )
        pte = system.vmmio.ptes.view().find(handle.virtual_address >> 12)
        assert pte is not None
        for overflow in (False, True):
            view[:] = original
            offset = handle.capacity - width + int(overflow)
            state = interpreter.start(0, [handle.virtual_address + offset])
            while not state.finished:
                interpreter.step(state)
            expected = bytearray(original)
            if overflow:
                assert state.trap is not None
                assert state.trap.code == InterpreterTrapCode.VMMIO_ACCESS
                assert state.trap.detail == VmmioTrapCode.OUT_OF_BOUNDS
                assert state.results is None
            else:
                assert state.trap is None and state.results is not None
                if operation == "load":
                    assert tuple(state.results) == (
                        int.from_bytes(original[offset:], "little", signed=width in (4, 8)),
                    )
                else:
                    expected[offset:] = (17).to_bytes(width, "little")
                    assert tuple(state.results) == (19,)
            assert bytes(view) == bytes(expected)
            assert guest_memory == b"\x5a" * 65536
            assert system.vmmio.ptes.view().find(handle.virtual_address >> 12) is pte
            assert pte.owner_id == owner.task_id
    finally:
        system.shutdown()


@pytest.mark.parametrize("warm_tlb", (False, True), ids=("cold", "warm"))
def test_syscall_21_vdma_rejects_non_owner_shm_without_mutation(warm_tlb: bool) -> None:
    """TEST-SYS-21: non-owners cannot DMA into SHM, including a cached owner's PTE."""
    from tier3_platform.drivers.wasi.context import WasiHostContext

    sysv = System()
    owner = sysv.start_runtime_task(name="shm_owner")
    try:
        guest_memory = bytearray(b"source-data" + b"\xa5" * 53)
        host = WasiHostContext(sysv, guest_memory=guest_memory)
        transfer = host.get_handler_for_import("fireball", "vdma_start")
        assert transfer is not None
        destination = 0xE000_3000
        physical_offset = 0x1000
        sysv.vmmio.map_shm_page(
            vpn=destination >> 12,
            physical_addr=physical_offset,
            owner_id=owner.task_id,
            mapping_size=16,
        )
        sysv.phys_mem[physical_offset : physical_offset + 16] = b"unchanged-buffer"
        if warm_tlb:
            assert transfer(0, destination, 4) == WasiErrno.SUCCESS
            assert bytes(sysv.phys_mem[physical_offset : physical_offset + 4]) == b"sour"

        intruder_id = sysv.scheduler.spawn("non_owner", role=Role.RUNTIME)
        intruder = sysv.scheduler.get_task(intruder_id)
        assert intruder is not None
        memory_before = bytes(guest_memory)
        physical_before = bytes(sysv.phys_mem)
        pte = sysv.vmmio.ptes.view().find(destination >> 12)
        assert pte is not None
        mapping_before = (pte.owner_id, pte.physical_base_addr, pte.mapping_size)

        with sysv.scheduler.task_context(intruder):
            # The dedicated vDMA contract requires rejection, but does not
            # choose one particular WASI errno for an ownership failure.
            result = transfer(0, destination, 11)
            assert WasiErrno(result) != WasiErrno.SUCCESS
        assert bytes(sysv.phys_mem) == physical_before
        assert bytes(guest_memory) == memory_before
        remaining_pte = sysv.vmmio.ptes.view().find(destination >> 12)
        assert remaining_pte is not None
        assert (
            remaining_pte.owner_id,
            remaining_pte.physical_base_addr,
            remaining_pte.mapping_size,
        ) == mapping_before

        # A rejected transfer must leave the valid owner's mapping usable.
        assert transfer(0, destination, 11) == WasiErrno.SUCCESS
        assert bytes(sysv.phys_mem[physical_offset : physical_offset + 11]) == b"source-data"
        assert (
            bytes(sysv.phys_mem[physical_offset + 11 : physical_offset + 16])
            == (physical_before[physical_offset + 11 : physical_offset + 16])
        )
    finally:
        sysv.shutdown()


def _make_syscall_virq_module() -> Module:
    return parse(
        memoryview(
            wat_to_wasm(
                "(module "
                "(func (param i32 i32 i32 i32 i32) (result i32) i32.const 0) "
                "(func (param i32 i32 i32 i32 i32) (result i32) i32.const 0) "
                "(func (param i32) (result i32) i32.const 0))"
            )
        )
    )


def test_syscall_05_virq_registration_host_calls():
    """TEST-SYS-30: host registration replaces the active function only at commit."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        sysv.runtime_engine.register_module_blocks(_make_syscall_virq_module())
        from tier3_platform.drivers.wasi.context import WasiHostContext

        host = WasiHostContext(sysv, guest_memory=bytearray(64))
        virq_register = host.get_handler_for_import("fireball", "virq_register")
        assert virq_register is not None
        dispatcher = sysv.runtime_engine._virq
        assert dispatcher is not None
        root = int(VirqNode.ROOT)
        active_before = dispatcher.active_functions
        assert virq_register(root, 0) == WasiErrno.SUCCESS
        assert dispatcher.active_functions == active_before
        assert dispatcher._pending_functions[root] == 0
        sysv.runtime_engine.commit_virq_registrations()
        assert dispatcher.active_functions[root] == 0

        assert virq_register(root, 1) == WasiErrno.SUCCESS
        assert dispatcher.active_functions[root] == 0
        assert dispatcher._pending_functions[root] == 1
        sysv.runtime_engine.commit_virq_registrations()
        assert dispatcher.active_functions[root] == 1
        assert dispatcher.active_functions[1:] == active_before[1:]
    finally:
        sysv.shutdown()


def test_syscall_31_virq_unregister_is_deferred_until_commit() -> None:
    """TEST-SYS-31: host unregister stages removal while the current registration stays active."""
    from tier3_platform.drivers.wasi.context import WasiHostContext

    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        sysv.runtime_engine.register_module_blocks(_make_syscall_virq_module())
        host = WasiHostContext(sysv, guest_memory=bytearray(64))
        register = host.get_handler_for_import("fireball", "virq_register")
        unregister = host.get_handler_for_import("fireball", "virq_unregister")
        assert register is not None and unregister is not None
        dispatcher = sysv.runtime_engine._virq
        assert dispatcher is not None
        root = int(VirqNode.ROOT)
        assert register(root, 0) == WasiErrno.SUCCESS
        sysv.runtime_engine.commit_virq_registrations()
        active_before = dispatcher.active_functions

        assert unregister(root) == WasiErrno.SUCCESS
        assert dispatcher.active_functions == active_before
        assert dispatcher._pending_functions[root] == INVALID_FUNCTION_INDEX
        sysv.runtime_engine.commit_virq_registrations()
        assert dispatcher.active_functions[root] == INVALID_FUNCTION_INDEX
        assert dispatcher.active_functions[1:] == active_before[1:]
    finally:
        sysv.shutdown()


@pytest.mark.parametrize(
    ("node_id", "function_index"),
    ((FB_CONF_VIRQ_MAX_NODES, 0), (0xFFFF_FFFF, 0), (0, 3), (0, 2)),
    ids=("node-boundary", "node-u32-max", "missing-function", "wrong-signature"),
)
def test_syscall_32_invalid_virq_registration_preserves_active_and_pending(
    node_id: int, function_index: int
) -> None:
    """TEST-SYS-32: invalid host registration cannot erase an active or pending valid request."""
    from tier3_platform.drivers.wasi.context import WasiHostContext

    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        sysv.runtime_engine.register_module_blocks(_make_syscall_virq_module())
        host = WasiHostContext(sysv, guest_memory=bytearray(64))
        register = host.get_handler_for_import("fireball", "virq_register")
        assert register is not None
        dispatcher = sysv.runtime_engine._virq
        assert dispatcher is not None
        assert register(0, 0) == WasiErrno.SUCCESS
        sysv.runtime_engine.commit_virq_registrations()
        assert register(0, 1) == WasiErrno.SUCCESS
        active_before = dispatcher.active_functions
        pending_before = tuple(dispatcher._pending_functions)
        sources_before = dispatcher.source_table

        assert register(node_id, function_index) == WasiErrno.INVAL
        assert dispatcher.active_functions == active_before
        assert tuple(dispatcher._pending_functions) == pending_before
        assert dispatcher.source_table == sources_before
        sysv.runtime_engine.commit_virq_registrations()
        assert dispatcher.active_functions == pending_before
    finally:
        sysv.shutdown()


@pytest.mark.parametrize("node_id", (FB_CONF_VIRQ_MAX_NODES, 0xFFFF_FFFF))
def test_syscall_32_invalid_virq_unregister_preserves_active_and_pending(node_id: int) -> None:
    """TEST-SYS-32: an invalid unregister request preserves both registration tables."""
    from tier3_platform.drivers.wasi.context import WasiHostContext

    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        sysv.runtime_engine.register_module_blocks(_make_syscall_virq_module())
        host = WasiHostContext(sysv, guest_memory=bytearray(64))
        register = host.get_handler_for_import("fireball", "virq_register")
        unregister = host.get_handler_for_import("fireball", "virq_unregister")
        assert register is not None and unregister is not None
        dispatcher = sysv.runtime_engine._virq
        assert dispatcher is not None
        assert register(0, 0) == WasiErrno.SUCCESS
        sysv.runtime_engine.commit_virq_registrations()
        assert register(0, 1) == WasiErrno.SUCCESS
        active_before = dispatcher.active_functions
        pending_before = tuple(dispatcher._pending_functions)
        sources_before = dispatcher.source_table

        assert unregister(node_id) == WasiErrno.INVAL
        assert dispatcher.active_functions == active_before
        assert tuple(dispatcher._pending_functions) == pending_before
        assert dispatcher.source_table == sources_before
        sysv.runtime_engine.commit_virq_registrations()
        assert dispatcher.active_functions == pending_before
    finally:
        sysv.shutdown()


def test_syscall_06_ipc_lookup_send_recv():
    """
    TEST-SYS-40, 42, 43 (payload limit), 44, 46..49, 51: IPC host-call contracts.
    The guest task's own
    execution *is* the IPC_SEND/IPC_RECV call (runtime_syscall.md: a host call
    runs inside the calling task's coroutine), so it genuinely waits for its
    CSP counterpart -- no EAGAIN/polling (ipc_router.md §5.1). The
    receiver/sender coroutines below are spawned before the guest's call only
    so the rendezvous resolves within that one call, not because the guest
    call itself would otherwise fail.
    """
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        uri = "fireball://hal/gpio/0"
        uri_bytes = uri.encode()
        payload = b"SET_GPIO"

        guest_mem = bytearray(128)
        guest_mem[0 : len(uri_bytes)] = uri_bytes
        guest_mem[64 : 64 + len(payload)] = payload
        sysv.bind_runtime(guest_mem)
        guest_task = sysv.scheduler.current_task
        assert guest_task is not None
        assert (
            sysv.fireball_call(FbSyscallId.IPC_LOOKUP, 0, len(uri_bytes), len(guest_mem), 0, 0, 0)
            == WasiErrno.FAULT
        )
        assert len(sysv._channel_table) == 0
        assert (
            sysv.fireball_call(FbSyscallId.IPC_LOOKUP, 0, len(uri_bytes), 120, 0, 0, 0)
            == WasiErrno.SUCCESS
        )
        handle = int.from_bytes(guest_mem[120:124], "little")
        assert handle > 0

        # The byte bridge uses one KV pair for length metadata and one per
        # four-byte chunk: 28 bytes fit exactly; 29 must be rejected before
        # constructing the fixed-size shared message block.
        maximum_payload = bytes(range(28))
        guest_mem[64 : 64 + len(maximum_payload)] = maximum_payload
        guest_mem[32 : 32 + 29] = bytes(range(29))
        assert (
            sysv.fireball_call(FbSyscallId.IPC_SEND, handle, 32, 29, 0, 0, 0) == WasiErrno.MSGSIZE
        )
        guest_mem[32:36] = b"noop"
        assert (
            sysv.fireball_call(FbSyscallId.IPC_SEND, handle, 32, 4, len(guest_mem) - 2, 0, 0)
            == WasiErrno.FAULT
        )

        # -- IPC_SEND: a HAL_GPIO receiver coroutine blocks first (nobody
        # is sending yet), then the guest's IPC_SEND completes the rendezvous
        # synchronously the instant it calls in.
        sent: list[IPCMessage] = []

        def hal_receiver():
            status, msg = yield from sysv.ipc.recv()
            sent.append(msg)
            assert (yield from sysv.ipc.reply(msg, 0)) == IPCStatus.COMPLETED

        recv_id = sysv.scheduler.spawn("hal_receiver", hal_receiver(), role=Role.HAL_GPIO)
        sysv.scheduler.run_until_idle()
        assert sysv.scheduler.get_task(recv_id).state.name == "SUSPENDED_CSP"
        sysv.scheduler.current_task = guest_task

        guest_mem[64 : 64 + len(maximum_payload)] = maximum_payload
        assert (
            sysv.fireball_call(FbSyscallId.IPC_SEND, handle, 64, len(maximum_payload), 124, 0, 0)
            == WasiErrno.SUCCESS
        )
        assert sent and kv_entries_to_bytes(sent[0].entries, max_len=28) == maximum_payload
        assert int.from_bytes(guest_mem[124:128], "little") == 0

        # -- IPC_RECV: a DEBUGGER sender coroutine blocks first, so the
        # guest's IPC_RECV completes the rendezvous the instant it calls in.
        # Set guest task role to CORE_SERVICE so it is authorized to receive on DEBUGGER->CORE_SERVICE edge
        guest_task.role = Role.CORE_SERVICE
        guest_task.role = Role.RUNTIME
        assert sysv.fireball_call(FbSyscallId.IPC_RECV, 0, 96, 28, 124, 0, 0) == WasiErrno.PERM
        guest_task.role = Role.CORE_SERVICE

        core_uri = "fireball://core/coos/0"
        core_uri_bytes = core_uri.encode()
        guest_mem[32 : 32 + len(core_uri_bytes)] = core_uri_bytes
        assert (
            sysv.fireball_call(FbSyscallId.IPC_LOOKUP, 32, len(core_uri_bytes), 120, 0, 0, 0)
            == WasiErrno.PERM
        )
        reply = bytes(range(21))
        sent_status = []
        received_messages: list[IPCMessage] = []

        def debugger_sender():
            status, ch = sysv.ipc.lookup(core_uri)
            assert status == IPCStatus.COMPLETED and ch is not None
            msg = IPCMessage.from_entries(
                bytes_to_kv_entries(reply), memory_manager=sysv.memory_manager
            )
            received_messages.append(msg)
            status, _ = yield from sysv.ipc.send(ch, msg)
            sent_status.append(status)

        sysv.scheduler.spawn("debugger_sender", debugger_sender(), role=Role.DEBUGGER)
        sysv.scheduler.run_until_idle()
        sysv.scheduler.current_task = guest_task
        assert received_messages and received_messages[0].ownership == OwnershipState.IN_FLIGHT
        assert (
            sysv.fireball_call(FbSyscallId.IPC_RECV, 0, len(guest_mem) - 4, 8, 124, 0, 0)
            == WasiErrno.FAULT
        )
        assert received_messages[0].ownership == OwnershipState.IN_FLIGHT
        assert sent_status == []

        assert sysv.fireball_call(FbSyscallId.IPC_RECV, 0, 96, 3, 124, 0, 0) == WasiErrno.MSGSIZE
        assert received_messages[0].ownership == OwnershipState.IN_FLIGHT
        assert sent_status == []

        assert sysv.fireball_call(FbSyscallId.IPC_RECV, 0, 96, 28, 124, 0, 0) == WasiErrno.SUCCESS
        recv_len = int.from_bytes(guest_mem[124:128], "little")
        assert recv_len == len(reply)
        assert bytes(guest_mem[96 : 96 + recv_len]) == reply
        assert sysv.fireball_call(FbSyscallId.IPC_REPLY, 0, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sent_status == [IPCStatus.COMPLETED]
    finally:
        sysv.shutdown()


@pytest.mark.parametrize(
    "length_output", (30, 32, 45, 59), ids=("before-start", "same-start", "inside", "last-byte")
)
def test_syscall_50_overlapping_recv_outputs_reject_without_consuming_sender(
    length_output: int,
) -> None:
    """TEST-SYS-50: overlapping output ranges reject before CSP and preserve the waiting message."""
    sysv = System()
    receiver = sysv.start_runtime_task(name="core_receiver", role=Role.CORE_SERVICE)
    try:
        guest_memory = bytearray(b"\xa5" * 128)
        sysv.bind_runtime(guest_memory, role=Role.CORE_SERVICE)
        payload = b"message-still-waiting"
        messages: list[IPCMessage] = []
        completed: list[IPCStatus] = []

        def debugger_sender():
            status, channel = sysv.ipc.lookup("fireball://core/coos/0")
            assert status == IPCStatus.COMPLETED and channel is not None
            message = IPCMessage.from_entries(
                bytes_to_kv_entries(payload), memory_manager=sysv.memory_manager
            )
            messages.append(message)
            status, _ = yield from sysv.ipc.send(channel, message)
            completed.append(status)

        sender_id = sysv.scheduler.spawn("debugger_sender", debugger_sender(), role=Role.DEBUGGER)
        sysv.scheduler.run_until_idle()
        sender = sysv.scheduler.get_task(sender_id)
        assert sender is not None and sender.state == TaskState.SUSPENDED_CSP
        assert len(messages) == 1 and messages[0].ownership == OwnershipState.IN_FLIGHT
        sysv.scheduler.current_task = receiver
        receiver_state = receiver.state
        memory_before = bytes(guest_memory)

        assert (
            sysv.fireball_call(FbSyscallId.IPC_RECV, 0, 32, 28, length_output, 0, 0)
            == WasiErrno.INVAL
        )
        assert bytes(guest_memory) == memory_before
        assert receiver.state == receiver_state
        assert receiver.pending_reply is None
        assert sender.state == TaskState.SUSPENDED_CSP
        assert messages[0].ownership == OwnershipState.IN_FLIGHT
        assert completed == []

        # The rejected call did not consume the message: a valid receive
        # obtains the exact payload, and only the reply releases its sender.
        assert sysv.fireball_call(FbSyscallId.IPC_RECV, 0, 96, 28, 124, 0, 0) == WasiErrno.SUCCESS
        assert int.from_bytes(guest_memory[124:128], "little") == len(payload)
        assert bytes(guest_memory[96 : 96 + len(payload)]) == payload
        assert bytes(guest_memory[96 + len(payload) : 124]) == b"\xa5" * (28 - len(payload))
        assert completed == []
        assert sysv.fireball_call(FbSyscallId.IPC_REPLY, 0, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert completed == [IPCStatus.COMPLETED]
    finally:
        sysv.shutdown()


@pytest.mark.parametrize("response_sender_first", (False, True))
def test_ipc_reply_host_call_drives_both_response_arrival_orders(
    response_sender_first: bool,
) -> None:
    """IPC_REPLY waits for response Grant and restores the caller's coroutine."""
    sysv = System()
    receiver = sysv.start_runtime_task(name="core_receiver", role=Role.CORE_SERVICE)
    sender_id = sysv.scheduler.spawn("debugger_sender", role=Role.DEBUGGER)
    sender = sysv.scheduler.get_task(sender_id)
    assert sender is not None
    sysv.scheduler.detach(sender)
    try:
        with sysv.scheduler.task_context(sender):
            status, channel = sysv.ipc.lookup("fireball://core/coos/0")
            assert status == IPCStatus.COMPLETED and channel is not None
            message = IPCMessage.from_entries(((1, 117),), memory_manager=sysv.memory_manager)
            request = sysv.ipc.send(channel, message)
            assert next(request) == (ChannelAction.BLOCK, None)
            sender.coro = request
        with sysv.scheduler.task_context(receiver):
            receive = sysv.ipc.recv()
            with pytest.raises(StopIteration) as received:
                next(receive)
            assert received.value.value == (IPCStatus.COMPLETED, message)
            assert message.ownership == OwnershipState.RECEIVER_OWNS
            assert message.response_code == 0xFFFF_FFFF
            receiver.pending_reply = message
        if not response_sender_first:
            sysv.scheduler.detach(sender)
            with sysv.scheduler.task_context(sender):
                assert next(request) == (ChannelAction.BLOCK, None)
            assert channel.reply_waiter_task is sender
        caller_executions: list[bool] = []

        def caller_continuation():
            caller_executions.append(True)
            yield None

        caller_coroutine = caller_continuation()
        receiver.coro = caller_coroutine
        with sysv.scheduler.task_context(receiver):
            assert (
                sysv.fireball_call(FbSyscallId.IPC_REPLY, 0, 0x23, 0, 0, 0, 0) == WasiErrno.SUCCESS
            )
        assert receiver.coro is caller_coroutine
        assert caller_executions == []
        assert receiver.pending_reply is None and receiver.result is None
        assert sender.result == (IPCStatus.COMPLETED, message)
        assert message.ownership == OwnershipState.SENDER_OWNS
        assert message.response_code == 0x23
        assert channel.reply_sender_task is None and channel.reply_payload is None
        assert channel.waiter_task is None and channel.reply_waiter_task is None
        with sysv.scheduler.task_context(sender):
            assert message.get(1) == 117
    finally:
        sysv.shutdown()


def test_syscall_07_wasi_fd_write():
    """TEST-SYS-80: WASI_FD_WRITE writes one iovec to standard output and reports its size."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        from tier3_platform.drivers.hal.dummy import DummyDriver
        from tier3_platform.drivers.wasi.context import WasiHostContext

        guest_mem = bytearray(64)
        message = b"hello from wasm\n"
        guest_mem[32 : 32 + len(message)] = message
        struct.pack_into("<II", guest_mem, 0, 32, len(message))
        WasiHostContext(sysv, guest_memory=guest_mem)
        sysv.start_hal_driver(DummyDriver(sysv.pool, transport=sysv.transport), FB_URI_HAL_STDOUT)
        assert sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 1, 0, 1, 48, 0, 0) == WasiErrno.SUCCESS
        assert sysv.transport.drain_output() == message
        nwritten = struct.unpack_from("<I", guest_mem, 48)[0]
        assert nwritten == len(message)
    finally:
        sysv.shutdown()


def test_wasi_01_fd_write_scatter_gather():
    """TEST-SYS-80: WASI_FD_WRITE supports scatter-gather output with multiple iovecs."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        from tier3_platform.drivers.hal.dummy import DummyDriver
        from tier3_platform.drivers.wasi.context import WasiHostContext

        guest_mem = bytearray(128)
        chunk1 = b"FIREBALL_"
        chunk2 = b"WASI_SCATTER_GATHER\n"
        guest_mem[32 : 32 + len(chunk1)] = chunk1
        guest_mem[64 : 64 + len(chunk2)] = chunk2
        # 2 iovecs at offset 0 and 8
        struct.pack_into("<II", guest_mem, 0, 32, len(chunk1))
        struct.pack_into("<II", guest_mem, 8, 64, len(chunk2))
        WasiHostContext(sysv, guest_memory=guest_mem)
        sysv.start_hal_driver(DummyDriver(sysv.pool, transport=sysv.transport), FB_URI_HAL_STDOUT)
        # Write to stdout (fd=1) with 2 iovecs, result at offset 100
        assert (
            sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 1, 0, 2, 100, 0, 0) == WasiErrno.SUCCESS
        )
        assert sysv.transport.drain_output() == chunk1 + chunk2
        nwritten = struct.unpack_from("<I", guest_mem, 100)[0]
        assert nwritten == len(chunk1) + len(chunk2)
    finally:
        sysv.shutdown()


def test_wasi_01b_fd_write_prevalidates_all_iovecs():
    """GOTCHA-SYS-03: an invalid later iovec cannot partially write output."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        from tier3_platform.drivers.hal.dummy import DummyDriver
        from tier3_platform.drivers.wasi.context import WasiHostContext

        guest_mem = bytearray(128)
        guest_mem[32:35] = b"bad"
        struct.pack_into("<II", guest_mem, 0, 32, 3)
        struct.pack_into("<II", guest_mem, 8, 200, 1)
        struct.pack_into("<I", guest_mem, 120, 0xA5A5A5A5)
        WasiHostContext(sysv, guest_memory=guest_mem)
        sysv.start_hal_driver(DummyDriver(sysv.pool, transport=sysv.transport), FB_URI_HAL_STDOUT)
        before = bytes(guest_mem)

        assert sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 1, 0, 2, 120, 0, 0) == WasiErrno.FAULT
        assert sysv.transport.drain_output() == b""
        assert bytes(guest_mem) == before
        stdio_task = sysv.hal_task_for(FB_URI_HAL_STDOUT)
        assert stdio_task is not None and stdio_task.processed_count == 0
    finally:
        sysv.shutdown()


def test_wasi_02_fd_read_eof():
    """TEST-SYS-81: WASI_FD_READ reports 0 bytes read (EOF) without crashing."""
    backend = UvwasiReferenceContext()
    backend.stdin_pos = len(backend.stdin_buffer)
    sysv = System(drivers=create_reference_platform_drivers(backend))
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        from tier3_platform.drivers.hal.dummy import DummyDriver
        from tier3_platform.drivers.wasi.context import WasiHostContext

        guest_mem = bytearray(b"\xa5" * 64)
        struct.pack_into("<II", guest_mem, 0, 16, 32)
        expected = bytearray(guest_mem)
        expected[48:52] = bytes(4)
        stdin_before = bytes(backend.stdin_buffer)
        position_before = backend.stdin_pos
        WasiHostContext(sysv, guest_memory=guest_mem)
        sysv.start_hal_driver(DummyDriver(sysv.pool, transport=sysv.transport), FB_URI_HAL_STDOUT)
        assert sysv.fireball_call(FbSyscallId.WASI_FD_READ, 0, 0, 1, 48, 0, 0) == WasiErrno.SUCCESS
        nread = struct.unpack_from("<I", guest_mem, 48)[0]
        assert nread == 0  # Standard WASI EOF
        assert guest_mem == expected
        assert bytes(backend.stdin_buffer) == stdin_before
        assert backend.stdin_pos == position_before
    finally:
        sysv.shutdown()


def test_wasi_03_fd_close():
    """TEST-SYS-82: WASI_FD_CLOSE closes a uvwasi-managed descriptor."""
    from tier3_platform.drivers.wasi.context import WasiHostContext

    backend = UvwasiReferenceContext()
    opened = backend.files.view().find(3)
    kept = backend.files.view().find(4)
    assert opened is not None and kept is not None
    sysv = System(drivers=create_reference_platform_drivers(backend))
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        WasiHostContext(sysv, guest_memory=bytearray(64))
        assert sysv.fireball_call(FbSyscallId.WASI_FD_CLOSE, 3, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert backend.files.view().find(3) is None
        assert backend.files.view().find(4) is kept
        assert sysv.fireball_call(FbSyscallId.WASI_FD_CLOSE, 3, 0, 0, 0, 0, 0) == WasiErrno.BADF
        assert backend.files.view().find(4) is kept
    finally:
        sysv.shutdown()


def test_wasi_04_clock_time_get_monotonic():
    """TEST-SYS-83: WASI_CLOCK_TIME_GET writes monotonic 64-bit nanosecond timestamp to guest memory."""
    from tier3_platform.drivers.wasi.context import WasiHostContext

    sysv = System(drivers=create_reference_platform_drivers())
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        guest_mem = bytearray(b"\xa5" * 64)
        WasiHostContext(sysv, guest_memory=guest_mem)
        lower = time.monotonic_ns()
        assert (
            sysv.fireball_call(FbSyscallId.WASI_CLOCK_TIME_GET, 1, 0, 16, 0, 0, 0)
            == WasiErrno.SUCCESS
        )
        upper = time.monotonic_ns()
        t1 = struct.unpack_from("<Q", guest_mem, 16)[0]
        assert lower <= t1 <= upper
        time.sleep(0.001)
        lower = time.monotonic_ns()
        assert (
            sysv.fireball_call(FbSyscallId.WASI_CLOCK_TIME_GET, 1, 0, 24, 0, 0, 0)
            == WasiErrno.SUCCESS
        )
        upper = time.monotonic_ns()
        t2 = struct.unpack_from("<Q", guest_mem, 24)[0]
        assert lower <= t2 <= upper
        assert t2 >= t1, "WASI monotonic clock must be monotonically non-decreasing"
        expected = bytearray(b"\xa5" * 64)
        struct.pack_into("<QQ", expected, 16, t1, t2)
        assert guest_mem == expected
    finally:
        sysv.shutdown()


def test_wasi_05_proc_exit():
    """TEST-SYS-84: WASI_PROC_EXIT sets system halted state and exit code."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        assert sysv.halted is False
        assert (
            sysv.fireball_call(FbSyscallId.WASI_PROC_EXIT, 42, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        )
        assert sysv.halted is True
        assert sysv.exit_code == 42
    finally:
        sysv.shutdown()


def test_wasi_06_random_get():
    """TEST-SYS-85: backendの既知16byteを指定範囲へ渡し、範囲外を保存する。"""
    from unittest.mock import patch

    from tier3_platform.drivers.wasi.context import WasiHostContext

    sysv = System(drivers=create_reference_platform_drivers())
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        guest_mem = bytearray(b"\xa5" * 64)
        WasiHostContext(sysv, guest_memory=guest_mem)
        payload = bytes.fromhex("00 12 34 56 78 9a bc de ff 01 23 45 67 89 ab cd")
        with patch(
            "qa.shared.fixtures.uvwasi_reference.os.urandom", return_value=payload
        ) as entropy:
            assert (
                sysv.fireball_call(FbSyscallId.WASI_RANDOM_GET, 8, 16, 0, 0, 0, 0)
                == WasiErrno.SUCCESS
            )
            entropy.assert_called_once_with(16)
        assert guest_mem == b"\xa5" * 8 + payload + b"\xa5" * 40
    finally:
        sysv.shutdown()


def test_wasi_07_invalid_fd_returns_badf():
    """TEST-SYS-91: WASI_FD_WRITE to invalid fd (e.g. fd=99) returns EBADF."""
    sysv = System(drivers=create_reference_platform_drivers())
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        from tier3_platform.drivers.wasi.context import WasiHostContext

        guest_mem = bytearray(64)
        struct.pack_into("<II", guest_mem, 0, 16, 8)
        WasiHostContext(sysv, guest_memory=guest_mem)
        before = bytes(guest_mem)
        res = sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 99, 0, 1, 48, 0, 0)
        assert res == WasiErrno.BADF
        assert bytes(guest_mem) == before
        assert sysv.transport.drain_output() == b""
    finally:
        sysv.shutdown()


def test_wasi_08_out_of_bounds_offset_returns_fault():
    """TEST-SYS-92: Out-of-bounds guest memory offset in WASI call returns EFAULT instantly."""
    sysv = System()
    sysv.start_runtime_task(name="test_runtime_task")
    try:
        from tier3_platform.drivers.wasi.context import WasiHostContext

        guest_mem = bytearray(b"\xa5" * 64)
        WasiHostContext(sysv, guest_memory=guest_mem)
        before = bytes(guest_mem)
        # iovs_ptr way past 64 bytes
        res = sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 1, 0x10000, 1, 48, 0, 0)
        assert res == WasiErrno.FAULT
        assert bytes(guest_mem) == before
        assert sysv.transport.drain_output() == b""
    finally:
        sysv.shutdown()


def test_wasi_jit_trampoline_invokes_the_registered_handler():
    """The JIT trampoline resolves and invokes the same WASI host handler."""
    from tier2_runtime.wasm.reader import parse
    from tier3_platform.drivers.wasi.context import WasiHostContext

    module = parse(
        wat_to_wasm('(module (import "wasi_snapshot_preview1" "proc_exit" (func (param i32))))')
    )
    sysv = System()
    try:
        context = WasiHostContext(sysv)
        trampolines = context.build_jit_trampolines(module)
        assert len(trampolines) == 1
        address = trampolines[0]
        assert address is not None and address != 0
        native_fn = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_uint32)(address)
        assert native_fn(7) == 0
        assert sysv.halted is True
        assert sysv.exit_code == 7
    finally:
        sysv.shutdown()


# ===========================================================================
# 10. WASM Instruction Set & Interpreter (interpreter_test_spec.md, wasm_instruction_set_test_spec.md)
# ===========================================================================


if __name__ == "__main__":
    raise SystemExit(pytest.main(["-q", __file__]))
