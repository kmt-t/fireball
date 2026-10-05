"""vDMA behavior tests: TEST-VSOC-62..73 and TEST-SYS-20..22.

Real NativeInterpreter, COOS, HAL buffers, and MemoryManager are used.
Expected bytes come from pre-operation snapshots, never the transfer service.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import combinations, product
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from ipc_router import Role
from qa.private.tier2_runtime.vdma_mock import COMPLETION_EVENT, ControlledVdmaMock, DmaPhase
from qa.shared.helpers import wat_to_wasm
from scheduler import ChannelAction, TaskState
from system import FB_CONF_VSOC_PASSTHROUGH_BASE, System
from tier2_runtime.hal.dispatch import HalBufferMapStatus
from tier2_runtime.interpreter.interpreter import (
    InterpreterBindings,
    NativeInterpreter,
    Trap,
    TrapCode,
)
from tier2_runtime.syscall.hostcall import RuntimeHostCallGateway, VdmaTransfer, WasiErrno
from tier2_runtime.vmmio.controller import VMMIO_PAGE_SIZE, VmmioStatus
from tier2_runtime.wasm.reader import parse
from tier3_platform.drivers.wasi.context import WasiHostContext


class Region(StrEnum):
    LINEAR = "L"
    DYNAMIC = "D"
    SHM = "S"
    PASSTHROUGH = "P"


class Entry(StrEnum):
    COPY = "copy"
    IMPORT = "import"


@dataclass(frozen=True)
class NormalRow:
    row_id: str
    entry: Entry
    source: Region
    destination: Region
    count: int
    source_offset: int
    destination_offset: int
    tlb: str


# runtime_vsoc_test_spec.md N01..N32. These rows are independently checked below.
NORMAL_ROWS = tuple(
    NormalRow(f"N{index:02d}", Entry(entry), Region(src), Region(dst), n, so, do, tlb)
    for index, (entry, src, dst, n, so, do, tlb) in enumerate(
        (
            ("copy", "D", "D", 1, 4, 68, "n/a"),
            ("copy", "D", "L", 1, 4, 68, "n/a"),
            ("copy", "D", "P", 1, 4, 68, "warm"),
            ("copy", "D", "S", 4, 5, 69, "cold"),
            ("copy", "L", "D", 1, 5, 69, "n/a"),
            ("copy", "L", "L", 1, 4, 68, "n/a"),
            ("copy", "L", "P", 4, 4, 68, "cold"),
            ("copy", "L", "S", 16, 5, 68, "warm"),
            ("copy", "P", "D", 16, 4, 68, "cold"),
            ("copy", "P", "L", 4, 4, 68, "warm"),
            ("copy", "P", "P", 4, 4, 69, "warm"),
            ("copy", "P", "S", 1, 4, 68, "cold"),
            ("copy", "S", "D", 1, 4, 68, "cold"),
            ("copy", "S", "L", 1, 5, 69, "cold"),
            ("copy", "S", "P", 16, 4, 68, "cold"),
            ("copy", "S", "S", 4, 4, 68, "cold"),
            ("import", "D", "D", 4, 4, 68, "n/a"),
            ("import", "D", "L", 16, 4, 68, "n/a"),
            ("import", "D", "P", 1, 4, 68, "cold"),
            ("import", "D", "S", 1, 4, 68, "cold"),
            ("import", "L", "D", 1, 4, 68, "n/a"),
            ("import", "L", "L", 1, 4, 68, "n/a"),
            ("import", "L", "P", 1, 4, 68, "cold"),
            ("import", "L", "S", 1, 4, 68, "cold"),
            ("import", "P", "D", 1, 4, 68, "cold"),
            ("import", "P", "L", 1, 4, 68, "cold"),
            ("import", "P", "P", 1, 5, 68, "cold"),
            ("import", "P", "S", 1, 4, 68, "cold"),
            ("import", "S", "D", 16, 4, 69, "warm"),
            ("import", "S", "L", 1, 4, 68, "cold"),
            ("import", "S", "P", 1, 4, 68, "cold"),
            ("import", "S", "S", 1, 4, 68, "cold"),
        ),
        1,
    )
)

MARKER = 0xE000
OBSERVED_BYTE = MARKER + 4
PHYSICAL_BASE = 0x2000
ALIAS_SHM_BASE = 0xE001_0000


@dataclass(frozen=True)
class Snapshot:
    guest: bytes
    dynamic: bytes
    shared: bytes
    physical: bytes
    control: bytes
    mappings: tuple[tuple[int, int, int, int, bool, bool, bool], ...]


@dataclass(frozen=True)
class Outcome:
    results: tuple[int, ...]
    trap: Trap | None = None


class VdmaFixture:
    def __init__(self, system: System, entry: Entry) -> None:
        self.system = system
        self.entry = entry
        self.owner_id = system.scheduler.current_task_id
        assert self.owner_id != 0
        self.host = WasiHostContext(system)
        self.memory = self.host.guest_memory
        self.memory[:] = b"\xa5" * len(self.memory)
        self.memory[:128] = bytes((37 * i + 11) & 255 for i in range(128))
        self.clear_observation()
        self.handle = system.pool.buffer(1)
        assert system.pool.map_for_io(self.handle.buffer_id) == HalBufferMapStatus.MAPPED
        self.dynamic_size = self.handle.capacity
        system.pool.view_for_driver(1, 0, self.dynamic_size)[:] = bytes(
            (17 * i + 19) & 255 for i in range(self.dynamic_size)
        )
        self.block = system.memory_manager.allocate_shared(256).unwrap()
        self.shared_address = self.block.get_address()
        self.shared_base = 0xE000_0000 + self.block.page_idx * VMMIO_PAGE_SIZE
        for index in range(self.block.size):
            self.block.write_u8(index, (13 * index + 23) & 255)
        system.phys_mem[:] = b"\x6d" * len(system.phys_mem)
        system.phys_mem[PHYSICAL_BASE : PHYSICAL_BASE + 256] = bytes(
            (29 * i + 31) & 255 for i in range(256)
        )
        import_decl = (
            '(import "fireball" "vdma_start" (func $dma (param i32 i32 i32) (result i32)))'
            if entry == Entry.IMPORT
            else ""
        )
        transfer = (
            "local.get $src local.get $dst local.get $n call $dma local.set $status"
            if entry == Entry.IMPORT
            else "local.get $dst local.get $src local.get $n memory.copy"
        )
        module = parse(
            wat_to_wasm(f"""(module {import_decl}
              (memory (export "memory") 1)
              (func (export "transfer")
                (param $src i32) (param $dst i32) (param $n i32) (param $probe i32)
                (result i32) (local $status i32)
                {transfer}
                local.get $status i32.eqz if
                  local.get $n if local.get $probe if
                    i32.const {OBSERVED_BYTE} local.get $dst i32.load8_u i32.store
                  end end
                  i32.const {MARKER} i32.const 1 i32.store
                end
                local.get $status)
              (func (export "store") (param i32 i32) (result i32)
                local.get 0 local.get 1 i32.store align=1
                local.get 0 i32.load align=1))""")
        )
        self.function_index = module.export_func_index("transfer")
        self.store_function_index = module.export_func_index("store")
        self.interpreter = NativeInterpreter(
            module,
            InterpreterBindings.with_memory_and_functions(
                self.memory,
                self.host.build_interpreter_host_functions(module),
                vdma_transfer=system.vdma_transfer,
            ),
            vmmio=system.vmmio,
            phys_mem=system.phys_mem,
            bump_allocator=system.runtime_engine.bump_allocator,
        )

    def clear_observation(self) -> None:
        self.memory[MARKER : OBSERVED_BYTE + 4] = bytes(8)

    def install_transfer_port(self, transfer: VdmaTransfer) -> None:
        """Compose the existing callable port with a controlled external target."""
        gateway = self.system.host_calls
        self.system.host_calls = RuntimeHostCallGateway(
            self.system.scheduler,
            gateway._syscall_handlers,
            self.system.runtime_engine,
            transfer,
        )
        module = self.interpreter.module
        self.interpreter = NativeInterpreter(
            module,
            InterpreterBindings.with_memory_and_functions(
                self.memory,
                self.host.build_interpreter_host_functions(module),
                vdma_transfer=transfer,
            ),
            vmmio=self.system.vmmio,
            phys_mem=self.system.phys_mem,
            bump_allocator=self.system.runtime_engine.bump_allocator,
        )

    def address(self, region: Region, offset: int = 0) -> int:
        bases = {
            Region.LINEAR: 0,
            Region.DYNAMIC: self.handle.virtual_address,
            Region.SHM: self.shared_base,
            Region.PASSTHROUGH: FB_CONF_VSOC_PASSTHROUGH_BASE + PHYSICAL_BASE,
        }
        return bases[region] + offset

    def location(self, region: Region, offset: int) -> tuple[str, int]:
        # Fixture-owned physical layout, independent of the vMMIO resolver.
        layouts = {
            Region.LINEAR: ("guest", offset),
            Region.DYNAMIC: ("dynamic", offset),
            Region.SHM: ("shared", self.shared_address + offset),
            Region.PASSTHROUGH: ("physical", PHYSICAL_BASE + offset),
        }
        return layouts[region]

    def snapshot(self) -> Snapshot:
        mappings = tuple(
            (
                vpn,
                pte.physical_base_addr,
                pte.mapping_size,
                pte.owner_id,
                pte.valid,
                pte.read,
                pte.write,
            )
            for vpn, pte in self.system.vmmio.ptes.view().entries
            if vpn >> 16 >= 13
        )
        return Snapshot(
            bytes(self.memory),
            # Trusted test observer of the HAL-owned slot, including while unmapped.
            # Copy its bytes; retain no guest view across unmap/remap.
            bytes(self.handle._storage),
            bytes(self.system.memory_manager.shm_storage),
            bytes(self.system.phys_mem),
            bytes(self.system.ipcr_regs),
            mappings,
        )

    def expected_copy(
        self,
        before: Snapshot,
        source: Region,
        destination: Region,
        source_offset: int,
        destination_offset: int,
        count: int,
        *,
        probe: bool = True,
    ) -> Snapshot:
        buffers = {
            "guest": bytearray(before.guest),
            "dynamic": bytearray(before.dynamic),
            "shared": bytearray(before.shared),
            "physical": bytearray(before.physical),
        }
        source_name, source_start = self.location(source, source_offset)
        target_name, target_start = self.location(destination, destination_offset)
        original = bytes(buffers[source_name][source_start : source_start + count])
        assert len(original) == count
        buffers[target_name][target_start : target_start + count] = original
        buffers["guest"][MARKER : MARKER + 4] = (1).to_bytes(4, "little")
        if count > 0 and probe:
            buffers["guest"][OBSERVED_BYTE : OBSERVED_BYTE + 4] = original[0].to_bytes(4, "little")
        return replace(
            before,
            guest=bytes(buffers["guest"]),
            dynamic=bytes(buffers["dynamic"]),
            shared=bytes(buffers["shared"]),
            physical=bytes(buffers["physical"]),
        )

    def write_source(self, region: Region, offset: int, data: bytes) -> None:
        if region == Region.LINEAR:
            self.memory[offset : offset + len(data)] = data
        elif region == Region.DYNAMIC:
            self.system.pool.view_for_driver(1, offset, len(data))[:] = data
        elif region == Region.SHM:
            for index, value in enumerate(data):
                self.block.write_u8(offset + index, value)
        else:
            start = PHYSICAL_BASE + offset
            self.system.phys_mem[start : start + len(data)] = data

    def prepare_tlb(self, regions: tuple[Region, ...], warm: bool) -> None:
        self.system.vmmio.flush_tlb()
        addresses = tuple(
            self.address(region) for region in regions if region in (Region.SHM, Region.PASSTHROUGH)
        )
        for address in addresses:
            if warm:
                status, _ = self.system.vmmio.access(address, is_write=False)
                assert status == VmmioStatus.OK_PHYSICAL
        for address in addresses:
            vpn = address >> 12
            slot = self.system.vmmio.tlb[self.system.vmmio.tlb_index(vpn)]
            if warm:
                assert slot.vpn == vpn and slot.pte is not None
            else:
                assert slot.vpn != vpn

    def transfer(
        self,
        source: int,
        destination: int,
        count: int,
        *,
        probe: bool = True,
        inspect_trap: bool = False,
    ) -> Generator[tuple[ChannelAction, None], None, Outcome]:
        args = (source, destination, count, int(probe))
        if not inspect_trap:
            results = yield from self.system.run_guest(self.interpreter, self.function_index, args)
            return Outcome(tuple(int(value) for value in results))
        # Inspect the actual structured trap at the existing runtime boundary.
        # complete_call converts it to fail-fast AssertionError; do not catch that.
        state = self.interpreter.start(self.function_index, args)
        while not state.finished:
            boundary = self.system.runtime_engine.run(self.interpreter, state)
            state = boundary.call_state
            if not state.finished and boundary.yield_requested:
                yield (ChannelAction.YIELD, None)
        results = () if state.results is None else tuple(int(value) for value in state.results)
        return Outcome(results, state.trap)

    def assert_rejected(self, outcome: Outcome, before: Snapshot, *, linear: bool = False) -> None:
        if self.entry == Entry.COPY:
            assert outcome.trap is not None
            assert outcome.trap.code == (
                TrapCode.MEMORY_OUT_OF_BOUNDS if linear else TrapCode.VMMIO_ACCESS
            )
            assert outcome.results == ()
        else:
            assert outcome.trap is None
            assert len(outcome.results) == 1 and outcome.results[0] != 0
        assert self.snapshot() == before


def run_case(
    entry: Entry,
    scenario: Callable[[VdmaFixture], Generator[tuple[ChannelAction, None], None, None]],
) -> None:
    system = System()

    def guest() -> Generator[tuple[ChannelAction, None], None, None]:
        fixture = VdmaFixture(system, entry)
        try:
            yield from scenario(fixture)
        finally:
            if system.pool.can_view(fixture.handle, 0, 1):
                system.pool.unmap_after_io(fixture.handle.buffer_id)
            fixture.block.drop()

    try:
        task_id = system.scheduler.spawn("vdma_contract_guest", guest(), role=Role.RUNTIME)
        system.scheduler.run_until_idle()
        task = system.scheduler.get_task(task_id)
        assert task is not None and task.state == TaskState.TERMINATED
    finally:
        system.shutdown()


def test_normal_rows_cover_all_valid_pairs_and_endpoint_directions() -> None:
    """TEST-VSOC-67: finite row coverage is checked separately from byte transfer."""
    candidates = tuple(
        (*prefix, tlb)
        for prefix in product(
            tuple(Entry), tuple(Region), tuple(Region), (1, 4, 16), (4, 5), (68, 69)
        )
        for tlb in (
            ("cold", "warm")
            if prefix[1] in (Region.SHM, Region.PASSTHROUGH)
            or prefix[2] in (Region.SHM, Region.PASSTHROUGH)
            else ("n/a",)
        )
    )
    selected = tuple(
        (
            row.entry,
            row.source,
            row.destination,
            row.count,
            row.source_offset,
            row.destination_offset,
            row.tlb,
        )
        for row in NORMAL_ROWS
    )
    assert all(row in candidates for row in selected)
    expected_pairs = {
        (i, j, row[i], row[j]) for row in candidates for i, j in combinations(range(7), 2)
    }
    actual_pairs = {
        (i, j, row[i], row[j]) for row in selected for i, j in combinations(range(7), 2)
    }
    assert actual_pairs == expected_pairs
    assert {row[:3] for row in selected} == set(product(tuple(Entry), tuple(Region), tuple(Region)))
    assert all(
        row.source_offset + row.count <= row.destination_offset
        and row.destination_offset + row.count <= 128
        for row in NORMAL_ROWS
    )


def test_documented_rows_match_executable_matrix() -> None:
    """TEST-VSOC-67: document row IDs and inputs cannot drift from executable cases."""
    document = (
        Path(__file__).resolve().parents[4] / "docs/qa/tier2_runtime/runtime_vsoc_test_spec.md"
    )
    documented = tuple(
        tuple(cell.strip() for cell in line.strip("|").split("|"))
        for line in document.read_text().splitlines()
        if line.startswith("| N")
    )
    executable = tuple(
        (
            row.row_id,
            row.entry.value,
            row.source.value,
            row.destination.value,
            str(row.count),
            str(row.source_offset),
            str(row.destination_offset),
            row.tlb,
        )
        for row in NORMAL_ROWS
    )
    assert documented == executable


@pytest.mark.parametrize("entry", tuple(Entry))
def test_guest_store_and_dma_use_same_managed_shm(entry: Entry) -> None:
    """TEST-VSOC-67/72: guest scalar store, SharedBlock and DMA share the actual backing."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        before = fixture.snapshot()
        value = 0xAABBCCDD
        result = yield from fixture.system.run_guest(
            fixture.interpreter, fixture.store_function_index, (fixture.shared_base + 65, value)
        )
        assert tuple(int(item) & 0xFFFFFFFF for item in result) == (value,)
        shared = bytearray(before.shared)
        shared[fixture.shared_address + 65 : fixture.shared_address + 69] = value.to_bytes(
            4, "little"
        )
        expected = replace(before, shared=bytes(shared))
        assert fixture.snapshot() == expected
        assert fixture.block.read_bytes(65, 4) == value.to_bytes(4, "little")
        transfer = yield from fixture.transfer(fixture.shared_base + 65, 2048, 4)
        assert transfer == Outcome((0,))
        assert fixture.snapshot() == fixture.expected_copy(
            expected, Region.SHM, Region.LINEAR, 65, 2048, 4
        )

    run_case(entry, scenario)


@pytest.mark.parametrize("row", NORMAL_ROWS, ids=lambda row: row.row_id)
def test_all_endpoint_directions_complete_before_guest_load(row: NormalRow) -> None:
    """TEST-VSOC-67 / TEST-SYS-20: N01..N32 use real managed SHM and HAL storage."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        fixture.prepare_tlb((row.source, row.destination), row.tlb == "warm")
        before = fixture.snapshot()
        expected = fixture.expected_copy(
            before,
            row.source,
            row.destination,
            row.source_offset,
            row.destination_offset,
            row.count,
        )
        result = yield from fixture.transfer(
            fixture.address(row.source, row.source_offset),
            fixture.address(row.destination, row.destination_offset),
            row.count,
        )
        assert result == Outcome((0,))
        assert fixture.snapshot() == expected

    run_case(row.entry, scenario)


@settings(max_examples=64, deadline=None, derandomize=True, print_blob=True)
@given(
    entry=st.sampled_from(tuple(Entry)),
    source=st.sampled_from(tuple(Region)),
    destination=st.sampled_from(tuple(Region)),
    source_offset=st.integers(0, 31),
    destination_offset=st.integers(64, 95),
    data=st.binary(min_size=1, max_size=32),
)
def test_transfer_bytes_match_independent_snapshot(
    entry: Entry,
    source: Region,
    destination: Region,
    source_offset: int,
    destination_offset: int,
    data: bytes,
) -> None:
    """TEST-VSOC-67: arbitrary bytes and unaligned offsets preserve all other storage."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        fixture.write_source(source, source_offset, data)
        before = fixture.snapshot()
        expected = fixture.expected_copy(
            before, source, destination, source_offset, destination_offset, len(data)
        )
        result = yield from fixture.transfer(
            fixture.address(source, source_offset),
            fixture.address(destination, destination_offset),
            len(data),
        )
        assert result == Outcome((0,))
        assert fixture.snapshot() == expected

    run_case(entry, scenario)


@pytest.mark.parametrize("entry", tuple(Entry))
@pytest.mark.parametrize("invalid_source", (False, True), ids=("destination", "source"))
@pytest.mark.parametrize(
    "region,boundary",
    tuple(
        (region, boundary)
        for region in Region
        for boundary in (
            "last-byte",
            "one-byte-over",
            "at-end",
            "after-end",
            "whole-region",
            "oversized",
        )
        if region != Region.LINEAR or boundary not in ("whole-region", "oversized")
    ),
)
def test_full_endpoint_bounds_and_reuse(
    entry: Entry, region: Region, invalid_source: bool, boundary: str
) -> None:
    """TEST-VSOC-64: one endpoint fails; full backing and mapping are preserved."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        capacity = {
            Region.LINEAR: len(fixture.memory),
            Region.DYNAMIC: fixture.dynamic_size,
            Region.SHM: fixture.block.size,
            Region.PASSTHROUGH: VMMIO_PAGE_SIZE,
        }[region]
        offset, count = {
            "last-byte": (capacity - 1, 1),
            "one-byte-over": (capacity - 1, 2),
            "at-end": (capacity, 1),
            "after-end": (capacity + 1, 1),
            "whole-region": (0, capacity),
            "oversized": (0, capacity + 1),
        }[boundary]
        source, destination = (region, Region.LINEAR) if invalid_source else (Region.LINEAR, region)
        so, do = (offset, 2048) if invalid_source else (2048, offset)
        before = fixture.snapshot()
        result = yield from fixture.transfer(
            fixture.address(source, so),
            fixture.address(destination, do),
            count,
            inspect_trap=entry == Entry.COPY,
        )
        # PASSTHROUGH's adjacent PTE is mapped: a request starting there is valid.
        if boundary in ("last-byte", "whole-region") or (
            region == Region.PASSTHROUGH and boundary in ("at-end", "after-end")
        ):
            assert result == Outcome((0,))
            assert fixture.snapshot() == fixture.expected_copy(
                before, source, destination, so, do, count
            )
        else:
            fixture.assert_rejected(result, before, linear=region == Region.LINEAR)
            expected = fixture.expected_copy(before, source, destination, 4, 68, 4)
            valid = yield from fixture.transfer(
                fixture.address(source, 4), fixture.address(destination, 68), 4
            )
            assert valid == Outcome((0,))
            assert fixture.snapshot() == expected

    run_case(entry, scenario)


@pytest.mark.parametrize("entry", tuple(Entry))
@pytest.mark.parametrize("source", tuple(Region))
@pytest.mark.parametrize("destination", tuple(Region))
def test_two_invalid_endpoints_preserve_state_without_error_priority(
    entry: Entry, source: Region, destination: Region
) -> None:
    """TEST-VSOC-64: reject both invalid ranges without imposing an error precedence."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        capacity = {
            Region.LINEAR: len(fixture.memory),
            Region.DYNAMIC: fixture.dynamic_size,
            Region.SHM: fixture.block.size,
            Region.PASSTHROUGH: VMMIO_PAGE_SIZE,
        }
        before = fixture.snapshot()
        result = yield from fixture.transfer(
            fixture.address(source, capacity[source] - 1),
            fixture.address(destination, capacity[destination] - 1),
            2,
            inspect_trap=entry == Entry.COPY,
        )
        if entry == Entry.COPY:
            expected_codes = {
                TrapCode.MEMORY_OUT_OF_BOUNDS if region == Region.LINEAR else TrapCode.VMMIO_ACCESS
                for region in (source, destination)
            }
            assert result.trap is not None and result.trap.code in expected_codes
            assert result.results == ()
        else:
            assert result.trap is None
            assert len(result.results) == 1 and result.results[0] != 0
        assert fixture.snapshot() == before
        valid = yield from fixture.transfer(
            fixture.address(source, 4), fixture.address(destination, 68), 4
        )
        assert valid == Outcome((0,))
        assert fixture.snapshot() == fixture.expected_copy(before, source, destination, 4, 68, 4)

    run_case(entry, scenario)


@pytest.mark.parametrize("entry", tuple(Entry))
@pytest.mark.parametrize("invalid_source", (False, True))
def test_u32_overflow_rejected_before_mutation(entry: Entry, invalid_source: bool) -> None:
    """TEST-VSOC-64: a mapped high address cannot wrap its byte range to zero."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        fixture.system.vmmio.map_passthrough_page(0xFFFFF, PHYSICAL_BASE >> 12)
        src, dst = (0xFFFFFFF0, 2048) if invalid_source else (2048, 0xFFFFFFF0)
        before = fixture.snapshot()
        result = yield from fixture.transfer(src, dst, 32, inspect_trap=entry == Entry.COPY)
        fixture.assert_rejected(result, before)

    run_case(entry, scenario)


@pytest.mark.parametrize("entry,count", ((Entry.COPY, 0), (Entry.COPY, 4), (Entry.IMPORT, 4)))
@pytest.mark.parametrize("source_denied", (False, True), ids=("write-gate", "read-gate"))
@pytest.mark.parametrize("allowed", (False, True), ids=("denied", "allowed"))
@pytest.mark.parametrize("warm", (False, True), ids=("cold", "warm"))
def test_permissions_follow_transfer_direction(
    entry: Entry, count: int, source_denied: bool, allowed: bool, warm: bool
) -> None:
    """TEST-VSOC-68: read-only source/write-only target are valid, including TLB hits."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        read, write = (allowed, not allowed) if source_denied else (not allowed, allowed)
        address = fixture.address(Region.PASSTHROUGH)
        fixture.system.vmmio.map_passthrough_page(
            address >> 12, PHYSICAL_BASE >> 12, read=read, write=write
        )
        if warm:
            status, _ = fixture.system.vmmio.access(address, is_write=not read)
            assert status == VmmioStatus.OK_PHYSICAL
            slot = fixture.system.vmmio.tlb[fixture.system.vmmio.tlb_index(address >> 12)]
            assert slot.vpn == address >> 12 and slot.pte is not None
        src, dst = (
            (Region.PASSTHROUGH, Region.LINEAR)
            if source_denied
            else (Region.LINEAR, Region.PASSTHROUGH)
        )
        before = fixture.snapshot()
        result = yield from fixture.transfer(
            fixture.address(src, 4),
            fixture.address(dst, 68),
            count,
            probe=False,
            inspect_trap=entry == Entry.COPY,
        )
        if allowed:
            assert result == Outcome((0,))
            assert fixture.snapshot() == fixture.expected_copy(
                before, src, dst, 4, 68, count, probe=False
            )
        else:
            fixture.assert_rejected(result, before)

            fixture.system.vmmio.map_passthrough_page(address >> 12, PHYSICAL_BASE >> 12)
            restored = fixture.snapshot()
            valid = yield from fixture.transfer(
                fixture.address(src, 4), fixture.address(dst, 68), 4
            )
            assert valid == Outcome((0,))
            assert fixture.snapshot() == fixture.expected_copy(restored, src, dst, 4, 68, 4)

    run_case(entry, scenario)


@pytest.mark.parametrize("entry,count", ((Entry.COPY, 0), (Entry.COPY, 4), (Entry.IMPORT, 4)))
@pytest.mark.parametrize(
    "region,warm", ((Region.DYNAMIC, False), (Region.SHM, False), (Region.SHM, True))
)
@pytest.mark.parametrize("invalid_source", (False, True))
def test_non_owner_rejection_preserves_storage_and_owner_access(
    entry: Entry, count: int, region: Region, warm: bool, invalid_source: bool
) -> None:
    """TEST-VSOC-68 / TEST-SYS-21: both directions and real compiled guest imports."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        fixture.prepare_tlb((region,), warm)
        foreign_id = fixture.system.scheduler.spawn("vdma_non_owner", role=Role.RUNTIME)
        foreign = fixture.system.scheduler.get_task(foreign_id)
        assert foreign is not None
        fixture.system.scheduler.detach(foreign)
        source, destination = (region, Region.LINEAR) if invalid_source else (Region.LINEAR, region)
        before = fixture.snapshot()
        with fixture.system.scheduler.task_context(foreign):
            result = yield from fixture.transfer(
                fixture.address(source, 4),
                fixture.address(destination, 68),
                count,
                inspect_trap=entry == Entry.COPY,
            )
            fixture.assert_rejected(result, before)
        valid = yield from fixture.transfer(
            fixture.address(source, 4), fixture.address(destination, 68), 4
        )
        assert valid == Outcome((0,))
        assert fixture.snapshot() == fixture.expected_copy(before, source, destination, 4, 68, 4)

    run_case(entry, scenario)


@settings(max_examples=48, deadline=None, derandomize=True, print_blob=True)
@example(source_is_passthrough=True, delta=1, data=bytes(range(64)))
@example(source_is_passthrough=False, delta=-1, data=bytes(range(64)))
@example(source_is_passthrough=True, delta=0, data=b"same-physical-range")
@given(
    source_is_passthrough=st.booleans(),
    delta=st.sampled_from((-1, 0, 1)),
    data=st.binary(min_size=1, max_size=64),
)
def test_physical_alias_copy_has_memmove_semantics(
    source_is_passthrough: bool, delta: int, data: bytes
) -> None:
    """TEST-VSOC-69: virtual ordering differs from the overlapping physical ranges."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        fixture.system.vmmio.map_shm_page(
            ALIAS_SHM_BASE >> 12,
            physical_addr=PHYSICAL_BASE,
            owner_id=fixture.owner_id,
            mapping_size=256,
        )
        source_base, destination_base = (
            (fixture.address(Region.PASSTHROUGH), ALIAS_SHM_BASE)
            if source_is_passthrough
            else (ALIAS_SHM_BASE, fixture.address(Region.PASSTHROUGH))
        )
        src_offset, dst_offset = 64, 64 + delta
        fixture.system.phys_mem[
            PHYSICAL_BASE + src_offset : PHYSICAL_BASE + src_offset + len(data)
        ] = data
        before = fixture.snapshot()
        physical = bytearray(before.physical)
        physical[PHYSICAL_BASE + dst_offset : PHYSICAL_BASE + dst_offset + len(data)] = (
            before.physical[PHYSICAL_BASE + src_offset : PHYSICAL_BASE + src_offset + len(data)]
        )
        guest = bytearray(before.guest)
        guest[MARKER : MARKER + 4] = (1).to_bytes(4, "little")
        guest[OBSERVED_BYTE : OBSERVED_BYTE + 4] = data[0].to_bytes(4, "little")
        result = yield from fixture.transfer(
            source_base + src_offset, destination_base + dst_offset, len(data)
        )
        assert result == Outcome((0,))
        assert fixture.snapshot() == replace(before, guest=bytes(guest), physical=bytes(physical))

    run_case(Entry.COPY, scenario)


@pytest.mark.parametrize("invalid_source", (False, True))
@pytest.mark.parametrize("count", (0, 1))
@pytest.mark.parametrize("address", (0xC0001000, 0x80000000, 0xD0007000, 0xE0011000, 0xF1000000))
def test_copy_rejects_unsupported_and_unmapped_endpoints(
    invalid_source: bool, count: int, address: int
) -> None:
    """TEST-VSOC-70/71: zero length cannot bypass endpoint checks or touch IPCR."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        src, dst = (address, 2048) if invalid_source else (2048, address)
        before = fixture.snapshot()
        result = yield from fixture.transfer(src, dst, count, inspect_trap=True)
        fixture.assert_rejected(result, before)

    run_case(Entry.COPY, scenario)


@pytest.mark.parametrize("source_end", (False, True))
@pytest.mark.parametrize("beyond_end", (False, True))
def test_zero_length_copy_checks_linear_endpoints(source_end: bool, beyond_end: bool) -> None:
    """TEST-VSOC-70: memory end is valid for zero bytes; end+1 traps."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        offset = len(fixture.memory) + int(beyond_end)
        so, do = (offset, 2048) if source_end else (2048, offset)
        before = fixture.snapshot()
        result = yield from fixture.transfer(so, do, 0, inspect_trap=True)
        if beyond_end:
            fixture.assert_rejected(result, before, linear=True)
        else:
            assert result == Outcome((0,))
            assert fixture.snapshot() == fixture.expected_copy(
                before, Region.LINEAR, Region.LINEAR, so, do, 0
            )

    run_case(Entry.COPY, scenario)


@pytest.mark.parametrize("region", tuple(Region))
@pytest.mark.parametrize("source_endpoint", (False, True))
def test_zero_length_copy_accepts_valid_endpoints(region: Region, source_endpoint: bool) -> None:
    """TEST-VSOC-70: valid mapped endpoints accept zero bytes without touching storage."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        source, destination = (
            (region, Region.LINEAR) if source_endpoint else (Region.LINEAR, region)
        )
        before = fixture.snapshot()
        result = yield from fixture.transfer(
            fixture.address(source, 4), fixture.address(destination, 68), 0
        )
        assert result == Outcome((0,))
        assert fixture.snapshot() == fixture.expected_copy(before, source, destination, 4, 68, 0)

    run_case(Entry.COPY, scenario)


@settings(max_examples=48, deadline=None, derandomize=True, print_blob=True)
@given(
    entry=st.sampled_from(tuple(Entry)),
    region=st.sampled_from(tuple(Region)),
    invalid_source=st.booleans(),
    excess=st.integers(0, 16),
    count=st.integers(2, 16),
)
def test_generated_bounds_rejection_and_valid_reuse(
    entry: Entry, region: Region, invalid_source: bool, excess: int, count: int
) -> None:
    """TEST-VSOC-64: invalid generators retain a valid counterpart and independent oracle."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        capacity = {
            Region.LINEAR: len(fixture.memory),
            Region.DYNAMIC: fixture.dynamic_size,
            Region.SHM: fixture.block.size,
            Region.PASSTHROUGH: VMMIO_PAGE_SIZE,
        }[region]
        offset = capacity - 1 if region == Region.PASSTHROUGH else capacity - 1 + excess
        src, dst = (
            (fixture.address(region, offset), 2048)
            if invalid_source
            else (2048, fixture.address(region, offset))
        )
        before = fixture.snapshot()
        result = yield from fixture.transfer(src, dst, count, inspect_trap=entry == Entry.COPY)
        fixture.assert_rejected(result, before, linear=region == Region.LINEAR)
        source, destination = (region, Region.LINEAR) if invalid_source else (Region.LINEAR, region)
        valid = yield from fixture.transfer(
            fixture.address(source, 4), fixture.address(destination, 68), 4
        )
        assert valid == Outcome((0,))
        assert fixture.snapshot() == fixture.expected_copy(before, source, destination, 4, 68, 4)

    run_case(entry, scenario)


@settings(max_examples=32, deadline=None, derandomize=True, print_blob=True)
@given(
    entry=st.sampled_from(tuple(Entry)),
    operations=st.lists(
        st.sampled_from(("map", "unmap", "read", "write")), min_size=1, max_size=12
    ),
)
def test_dynamic_mapping_histories_preserve_bytes_and_access(
    entry: Entry, operations: list[str]
) -> None:
    """TEST-VSOC-72: every legal mapping/transfer step agrees with a small state model."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        mapped = True
        vpn = fixture.handle.virtual_address >> 12
        initial_mapping = next(
            mapping for mapping in fixture.snapshot().mappings if mapping[0] == vpn
        )
        for index, operation in enumerate(("unmap", "read", "map", "write", *operations)):
            if (operation == "map" and mapped) or (operation == "unmap" and not mapped):
                continue
            fixture.clear_observation()
            before = fixture.snapshot()
            if operation == "map":
                assert fixture.system.pool.map_for_io(1) == HalBufferMapStatus.MAPPED
                mapped = True
                expected_mappings = tuple(sorted((*before.mappings, initial_mapping)))
                assert fixture.snapshot() == replace(before, mappings=expected_mappings)
            elif operation == "unmap":
                fixture.system.pool.unmap_after_io(1)
                mapped = False
                assert fixture.snapshot() == replace(
                    before,
                    mappings=tuple(mapping for mapping in before.mappings if mapping[0] != vpn),
                )
            else:
                src, dst = (
                    (Region.DYNAMIC, Region.LINEAR)
                    if operation == "read"
                    else (Region.LINEAR, Region.DYNAMIC)
                )
                if operation == "write":
                    fixture.memory[4:12] = bytes((index + 37 * i) & 255 for i in range(8))
                    before = fixture.snapshot()
                result = yield from fixture.transfer(
                    fixture.address(src, 4),
                    fixture.address(dst, 68),
                    8,
                    inspect_trap=entry == Entry.COPY,
                )
                if mapped:
                    assert result == Outcome((0,))
                    assert fixture.snapshot() == fixture.expected_copy(before, src, dst, 4, 68, 8)
                else:
                    fixture.assert_rejected(result, before)
        if not mapped:
            assert fixture.system.pool.map_for_io(1) == HalBufferMapStatus.MAPPED

    run_case(entry, scenario)


@pytest.mark.parametrize("entry", tuple(Entry))
def test_real_shm_release_grant_claim_preserves_data_and_rejects_old_owner(entry: Entry) -> None:
    """TEST-VSOC-72: actual SharedBlock ownership and DMA-visible bytes share storage."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        before = fixture.snapshot()
        result = yield from fixture.transfer(4, fixture.shared_base + 68, 8)
        assert result == Outcome((0,))
        assert fixture.snapshot() == fixture.expected_copy(
            before, Region.LINEAR, Region.SHM, 4, 68, 8
        )
        assert fixture.block.read_bytes(68, 8) == before.guest[4:12]
        fixture.prepare_tlb((Region.SHM,), True)
        shm_id = fixture.block.release()
        assert fixture.system.vmmio.ptes.view().find(fixture.shared_base >> 12) is None
        fixture.clear_observation()
        released = fixture.snapshot()
        rejected = yield from fixture.transfer(
            fixture.shared_base + 68, 2048, 8, inspect_trap=entry == Entry.COPY
        )
        fixture.assert_rejected(rejected, released)
        receiver_id = fixture.system.scheduler.spawn("vdma_shm_receiver", role=Role.RUNTIME)
        receiver = fixture.system.scheduler.get_task(receiver_id)
        assert receiver is not None
        fixture.system.scheduler.detach(receiver)
        with fixture.system.scheduler.task_context(receiver):
            assert fixture.system.memory_manager.grant_shared(shm_id)
            claimed = fixture.system.memory_manager.claim(shm_id).unwrap()
            assert claimed.get_owner() == receiver_id
            assert claimed.read_bytes(68, 8) == before.guest[4:12]
        granted = fixture.snapshot()
        old_owner = yield from fixture.transfer(
            fixture.shared_base + 68, 2048, 8, inspect_trap=entry == Entry.COPY
        )
        fixture.assert_rejected(old_owner, granted)
        with fixture.system.scheduler.task_context(receiver):
            current = fixture.snapshot()
            received = yield from fixture.transfer(fixture.shared_base + 68, 2048, 8)
            assert received == Outcome((0,))
            assert fixture.snapshot() == fixture.expected_copy(
                current, Region.SHM, Region.LINEAR, 68, 2048, 8
            )
            claimed.drop()

    run_case(entry, scenario)


def storage_after_prefix(
    fixture: VdmaFixture, before: Snapshot, destination: Region, count: int
) -> Snapshot:
    # No guest instruction after the service call may have executed yet.
    expected = fixture.expected_copy(before, Region.LINEAR, destination, 4, 68, count, probe=False)
    return replace(expected, guest=before.guest)


def exercise_controlled_completion(
    entry: Entry, destination: Region, deferred: bool, coherent: bool, delay: int, data: bytes
) -> None:
    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        fixture.write_source(Region.LINEAR, 4, data)
        before = fixture.snapshot()
        observed: list[DmaPhase] = []

        def observer(phase: DmaPhase, written: int) -> None:
            observed.append(phase)
            owner = fixture.system.scheduler.get_task(fixture.owner_id)
            assert owner is not None
            # The external service has not returned: real guest progress/load
            # must still be absent even when DMA has already written a prefix.
            cpu_prefix = written if coherent or phase == DmaPhase.CPU_VISIBLE else 0
            assert fixture.snapshot() == storage_after_prefix(
                fixture, before, destination, cpu_prefix
            )
            if (
                deferred
                and target.producer_id != 0
                and phase
                in (DmaPhase.PENDING, DmaPhase.PARTIAL, DmaPhase.DEVICE_COMPLETE, DmaPhase.NOTIFIED)
            ):
                assert owner.state == TaskState.BLOCKED
                assert owner.waiting_irq == COMPLETION_EVENT.vector_id
                assert owner.pending_interrupt_event is None
                assert fixture.system.scheduler.current_task_id == target.producer_id
            else:
                assert owner.state == TaskState.RUNNING
            if phase == DmaPhase.NOTIFIED:
                assert len(fixture.system.scheduler.interrupt_event_queue) == 1

        target = ControlledVdmaMock(
            fixture.system, observer, deferred=deferred, coherent=coherent, delay_steps=delay
        )
        fixture.install_transfer_port(target)
        result = yield from fixture.transfer(4, fixture.address(destination, 68), len(data))
        assert result == Outcome((0,))
        assert fixture.snapshot() == fixture.expected_copy(
            before, Region.LINEAR, destination, 4, 68, len(data)
        )
        assert target.requests == [(4, fixture.address(destination, 68), len(data))]
        assert target.busy_request is None
        assert DmaPhase.PARTIAL in observed and DmaPhase.BEFORE_VISIBLE in observed
        assert observed[-1] == DmaPhase.CPU_VISIBLE
        expected_actions = [
            "clean-source",
            "barrier-before",
            "start",
            "partial-write",
            "device-complete",
        ]
        if deferred:
            producer = fixture.system.scheduler.get_task(target.producer_id)
            assert producer is not None and producer.state == TaskState.TERMINATED
            assert target.wait_events == [COMPLETION_EVENT]
            assert DmaPhase.NOTIFIED in observed
            assert fixture.system.scheduler.dropped_irqs == 1
            expected_actions.append("notify")
        else:
            assert target.wait_events == [] and target.producer_id == 0
        expected_actions.extend(("invalidate-destination", "barrier-after", "return-success"))
        # Confirm the scripted external contract; these actions belong to the
        # double and are not evidence of production driver's cache instructions.
        assert target.actions == expected_actions

    run_case(entry, scenario)


@pytest.mark.parametrize("entry", tuple(Entry))
@pytest.mark.parametrize("destination", (Region.DYNAMIC, Region.SHM, Region.PASSTHROUGH))
@pytest.mark.parametrize("deferred,delay", ((False, 0), (True, 0), (True, 7)))
@pytest.mark.parametrize("coherent", (False, True))
def test_controlled_target_completes_and_becomes_visible_before_guest_resumes(
    entry: Entry, destination: Region, deferred: bool, delay: int, coherent: bool
) -> None:
    """TEST-VSOC-62/65/73 / TEST-SYS-22: real guest, gateway and COOS; external vDMA double."""
    exercise_controlled_completion(
        entry, destination, deferred, coherent, delay, b"\x71\x00\xff\xa7\x42\x19\xe5\x81"
    )


@settings(max_examples=32, deadline=None, derandomize=True, print_blob=True)
@given(
    entry=st.sampled_from(tuple(Entry)),
    destination=st.sampled_from((Region.DYNAMIC, Region.SHM, Region.PASSTHROUGH)),
    coherent=st.booleans(),
    delay=st.integers(0, 24),
    data=st.binary(min_size=4, max_size=32),
)
def test_generated_pending_prefixes_keep_guest_suspended(
    entry: Entry, destination: Region, coherent: bool, delay: int, data: bytes
) -> None:
    """TEST-VSOC-62/65/73: bounded delayed histories do not assume finite external completion."""
    exercise_controlled_completion(entry, destination, True, coherent, delay, data)


@pytest.mark.parametrize("entry", tuple(Entry))
@pytest.mark.parametrize("destination", (Region.DYNAMIC, Region.SHM, Region.PASSTHROUGH))
@pytest.mark.parametrize("deferred", (False, True))
@pytest.mark.parametrize("written", (0, 3))
def test_started_target_failure_preserves_partial_writes_and_skips_success(
    entry: Entry, destination: Region, deferred: bool, written: int
) -> None:
    """TEST-VSOC-63: an IO failure after start is not success and does not imply rollback."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        before = fixture.snapshot()
        phases: list[DmaPhase] = []

        def observer(phase: DmaPhase, prefix: int) -> None:
            phases.append(phase)
            assert fixture.snapshot() == storage_after_prefix(fixture, before, destination, prefix)

        target = ControlledVdmaMock(
            fixture.system, observer, deferred=deferred, coherent=True, fail_after=written
        )
        fixture.install_transfer_port(target)
        result = yield from fixture.transfer(
            4, fixture.address(destination, 68), 8, inspect_trap=entry == Entry.COPY
        )
        if entry == Entry.COPY:
            assert result.results == () and result.trap is not None
            assert result.trap.code == TrapCode.VMMIO_ACCESS
            assert result.trap.detail == int(WasiErrno.IO)
        else:
            assert result == Outcome((int(WasiErrno.IO),))
        assert fixture.snapshot() == storage_after_prefix(fixture, before, destination, written)
        assert phases[-1] == DmaPhase.FAILED
        assert "return-success" not in target.actions
        assert target.requests == [(4, fixture.address(destination, 68), 8)]

    run_case(entry, scenario)


@pytest.mark.parametrize("entry", tuple(Entry))
@pytest.mark.parametrize("destination", (Region.DYNAMIC, Region.SHM, Region.PASSTHROUGH))
def test_occupied_target_rejects_without_destroying_prior_transfer_and_reuses_after_stop(
    entry: Entry, destination: Region
) -> None:
    """TEST-VSOC-66: AGAIN applies only to this occupied mock engine; stop is explicit."""

    def scenario(fixture: VdmaFixture) -> Generator[tuple[ChannelAction, None], None, None]:
        def observer(_phase: DmaPhase, _written: int) -> None:
            assert fixture.memory[MARKER : OBSERVED_BYTE + 4] == bytes(8)

        target = ControlledVdmaMock(fixture.system, observer, deferred=False, coherent=True)
        target.occupy(32, fixture.address(destination, 132), 8)
        active = target.transfer
        assert active is not None
        saved_engine = (
            target.busy_request,
            bytes(active.source_bus),
            bytes(active.destination_bus),
            target.written,
        )
        before = fixture.snapshot()
        fixture.install_transfer_port(target)
        rejected = yield from fixture.transfer(
            4, fixture.address(destination, 68), 8, inspect_trap=entry == Entry.COPY
        )
        if entry == Entry.COPY:
            assert rejected.results == () and rejected.trap is not None
            assert rejected.trap.code == TrapCode.VMMIO_ACCESS
            assert rejected.trap.detail == int(WasiErrno.AGAIN)
        else:
            assert rejected == Outcome((int(WasiErrno.AGAIN),))
        assert fixture.snapshot() == before
        assert target.transfer is active
        assert (
            target.busy_request,
            bytes(active.source_bus),
            bytes(active.destination_bus),
            target.written,
        ) == saved_engine
        assert target.actions == [] and target.wait_events == []
        target.release_engine()  # Explicit device stop; not a timeout assumption.
        stopped = fixture.snapshot()
        assert stopped == before
        completed = yield from fixture.transfer(4, fixture.address(destination, 68), 8)
        assert completed == Outcome((0,))
        assert fixture.snapshot() == fixture.expected_copy(
            stopped, Region.LINEAR, destination, 4, 68, 8
        )
        assert target.requests == [(4, fixture.address(destination, 68), 8)] * 2

    run_case(entry, scenario)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
