"""
experiments/pysim/tier2_runtime/vmmio.py
vMMIO FlatMap Page Table & Direct-Mapped TLB simulation.
- RAM Bypass Flag (Bit 31): O(1) linear-RAM fast path, no table lookup
- FlatMap PTE storage: maps 20-bit VPN -> PTE (64 entries)
- Direct-mapped Software TLB[32] keyed by Folding XOR Hash over 20-bit VPN:
  folds 20 -> 10 -> 5 and selects a 5-bit slot index (0..31)
- Tier 1 linear RAM: Bit31 bypass PLUS a size-comparison bound check (no mask, no
  power-of-two constraint on guest_ram_size) — traps to the interpreter on OOB
- PTE permission check (VALID/READ/WRITE/EXEC + Owner ID) on every access,
  including on TLB hit — the TLB only skips the table lookup, never the check
- Static-device value accesses can dispatch through a registered syscall vector
  table and return the handler's u32 result to the interpreter
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from scheduler import Scheduler
from system_containers import MutableFlatMapStorage, StaticVector

if TYPE_CHECKING:
    from memory_interface import MemoryManager

# docs/components/tier1_core/system_config.md {META_FlatMapIndexed}: max PTE
# count the FlatMap page table can hold.
FB_CONF_VMMIO_MAX_PTES = 64


class VmmioStatus(IntEnum):
    OK_GUEST_RAM = 0
    OK_SYSCALL = 1
    OK_PHYSICAL = 2
    OUT_OF_BOUNDS = 3
    UNDEFINED_FC = 4
    UNREGISTERED_PAGE = 5
    ACCESS_VIOLATION = 6
    OWNER_MISMATCH = 5  # Aliased per ADR_PageGranularPermissionIsolation.


TrapCode = VmmioStatus


VmmioVectorHandler = Callable[[int, int, bool], int | None]


# Function Codes (bits[31:28]) — see runtime_vmmio.md "アドレス分解の対応関係"
FC_STATIC_DEVICE = 0xC  # 0xC000_0000: SYSCTL / IPCR / VDMA (Tier 2, syscall dispatch)
FC_DYNAMIC = 0xD  # 0xD000_0000: HAL-owned bounded dynamic buffers
FC_SHM = 0xE  # 0xE000_0000: Shared Memory (Tier 3, page-isolated via unmap)
FC_PASSTHROUGH = 0xF  # 0xF000_0000: Physical passthrough (Tier 3)
VMMIO_PAGE_SHIFT = 12
VMMIO_PAGE_SIZE = 1 << VMMIO_PAGE_SHIFT
FB_TASK_ID_INVALID = 0x00
FB_TASK_ID_FLIGHT = 0xFF


class VmmioAddress:
    """Decodes a 32-bit guest address into fields. See runtime_vmmio.md §3.3."""

    __slots__ = ("raw",)

    def __init__(self, raw: int):
        self.raw = raw & 0xFFFF_FFFF

    def is_linear(self) -> bool:
        # Bit[31] == 0 -> guest RAM (Tier 1), fast-bypass vMMIO entirely.
        return (self.raw & 0x8000_0000) == 0

    def fc(self) -> int:
        return (self.raw >> 28) & 0xF

    def syscall_metadata(self) -> int:
        # Syscall Metadata / Syscall ID: bits [27:16] (12 bits)
        return (self.raw >> 16) & 0xFFF

    def vpn(self) -> int:
        # 20-bit Virtual Page Number (VPN)
        return self.raw >> 12

    def offset(self) -> int:
        return self.raw & 0xFFF


class StaticDevicePTE:
    """FC=12 (Static Device). Holds permission flags and optional handler."""

    __slots__ = ("cacheable", "handler", "read", "value_handler", "write")

    def __init__(
        self,
        handler: Callable[[int, int, bool], None] | None = None,
        read: bool = True,
        write: bool = True,
        cacheable: bool = False,
        value_handler: VmmioVectorHandler | None = None,
    ):
        self.handler = handler
        self.value_handler = value_handler
        self.read = read
        self.write = write
        self.cacheable = cacheable


class Tier3PTE:
    """
    FC=14/15 (SHM / PASSTHROUGH). 32-bit layout, no bit overlap:
        [31:12] PPN(20) | [11] VALID | [10] READ | [9] WRITE | [8] EXEC | [7:0] Reserved
    PTE does not store owner_id; access control is enforced by presence of mapping (unmap on revoke).
    """

    __slots__ = ("exec_", "owner_id", "phys_page", "read", "valid", "write")

    def __init__(
        self,
        phys_page: int,
        valid: bool = True,
        read: bool = True,
        write: bool = True,
        exec_: bool = False,
        owner_id: int = FB_TASK_ID_INVALID,
    ):
        self.phys_page = phys_page
        self.valid = valid
        self.read = read
        self.write = write
        self.exec_ = exec_
        self.owner_id = owner_id


@dataclass(slots=True)
class TLBSlot:
    vpn: int = 0xFFFF_FFFF
    pte: StaticDevicePTE | Tier3PTE | None = None


class VMMIOController:
    """
    FlatMap Page Table (vpn -> PTE, 64 entries) with a direct-mapped 32-entry
    software TLB.
        TLB hits provide O(1) hot-path access, while TLB misses look up the FlatMap.
    """

    __slots__ = (
        "guest_ram_size",
        "ptes",
        "scheduler",
        "syscall_vector_table",
        "tlb",
        "tlb_hits",
        "tlb_misses",
    )

    def __init__(self, guest_ram_size: int = 8192, *, scheduler: Scheduler):  # FB_CONF_GUEST_RAM_SIZE

        if guest_ram_size <= 0:
            raise ValueError("guest RAM size must be positive")
        self.scheduler = scheduler
        self.guest_ram_size = guest_ram_size
        # FlatMap PTE storage: vpn (20-bit) -> PTE, capacity-bounded per
        # system_config.md's FB_CONF_VMMIO_MAX_PTES (a fixed static array in
        # C++, so a MutableFlatMapStorage here, never a dict).
        self.ptes: MutableFlatMapStorage[int, StaticDevicePTE | Tier3PTE] = MutableFlatMapStorage(
            capacity=FB_CONF_VMMIO_MAX_PTES
        )
        # Direct-mapped TLB: 32 slots, keyed by a repeatedly folded XOR over
        # the 20-bit VPN.
        self.tlb: StaticVector[TLBSlot] = StaticVector.of(
            tuple(TLBSlot() for _ in range(32)), capacity=32
        )
        self.tlb_hits = 0
        self.tlb_misses = 0
        self.syscall_vector_table: tuple[VmmioVectorHandler | None, ...] = ()

    # --- Static & Dynamic PTE Registration (FlatMap) ---
    def map_static_device(
        self,
        vpn: int,
        handler: Callable[[int, int, bool], None] | None = None,
        read: bool = True,
        write: bool = True,
        value_handler: VmmioVectorHandler | None = None,
    ) -> None:
        """Registers a Tier 2 static device page (FC=12) into FlatMap."""
        assert self.ptes.insert(
            vpn,
            StaticDevicePTE(
                handler=handler,
                read=read,
                write=write,
                value_handler=value_handler,
            ),
        ), "vMMIO PTE table capacity exceeded"

    def register_vector_table(
        self, vector_table: Sequence[VmmioVectorHandler | None]
    ) -> None:
        """Register the interpreter-visible static-vMMIO syscall vector table."""
        assert len(vector_table) <= 4096
        self.syscall_vector_table = tuple(vector_table)

    def map_shm_page(self, vpn: int, phys_page: int, owner_id: int = 0) -> None:
        """Registers a Tier 3 SHM page (FC=14) into FlatMap."""
        if self.ptes.find(vpn) is not None:
            self.ptes.remove(vpn)
        assert self.ptes.insert(
            vpn,
            Tier3PTE(
                phys_page=phys_page,
                valid=True,
                read=True,
                write=True,
                exec_=False,
                owner_id=owner_id,
            ),
        ), "vMMIO PTE table capacity exceeded"
        self.flush_tlb_entry(vpn)

    def map_dynamic_page(self, vpn: int, phys_page: int) -> None:
        """Maps one HAL-owned page in the FC=13 DYNAMIC region."""
        assert (vpn >> 16) == FC_DYNAMIC, "DYNAMIC VPN is outside FC=13"
        if self.ptes.find(vpn) is not None:
            self.ptes.remove(vpn)
        assert self.ptes.insert(
            vpn,
            Tier3PTE(
                phys_page=phys_page,
                valid=True,
                read=True,
                write=True,
                exec_=False,
            ),
        ), "vMMIO PTE table capacity exceeded"
        self.flush_tlb_entry(vpn)

    def unmap_dynamic_page(self, vpn: int) -> None:
        """Unmaps one HAL-owned DYNAMIC page and invalidates its TLB entry."""
        assert (vpn >> 16) == FC_DYNAMIC, "DYNAMIC VPN is outside FC=13"
        if self.ptes.find(vpn) is not None:
            self.ptes.remove(vpn)
        self.flush_tlb_entry(vpn)

    def map_passthrough_page(
        self, vpn: int, phys_page: int, read: bool = True, write: bool = True
    ) -> None:
        """Registers a Tier 3 Passthrough page (FC=15) into FlatMap."""
        if self.ptes.find(vpn) is not None:
            self.ptes.remove(vpn)
        assert self.ptes.insert(
            vpn,
            Tier3PTE(
                phys_page=phys_page,
                valid=True,
                read=read,
                write=write,
                exec_=True,
            ),
        ), "vMMIO PTE table capacity exceeded"
        self.flush_tlb_entry(vpn)

    def revoke_shm_owner(self, vpn: int) -> None:
        """
        IPC Router Revoke phase: physically unmaps the page from vMMIO and flushes its TLB entry.
        Subsequent accesses will trap via TRAP_UNREGISTERED_PAGE (ADR_PageGranularPermissionIsolation).
        """
        if self.ptes.find(vpn) is not None:
            self.ptes.remove(vpn)
        self.flush_tlb_entry(vpn)

    def unmap_shm_page(self, vpn: int) -> None:
        """Unregisters an FC=14 SHM page and flushes its TLB entry."""
        if self.ptes.find(vpn) is not None:
            self.ptes.remove(vpn)
        self.flush_tlb_entry(vpn)

    def register_to_memory_manager(self, memory_manager: MemoryManager) -> None:
        """Registers vMMIO FC=14 SHM page table listeners into MemoryManager."""
        from memory_interface import PageMappingCallbacks

        def _to_vpn(page_idx: int) -> int:
            return (0xE000_0000 >> 12) + page_idx

        memory_manager.register_page_mapping_callbacks(
            PageMappingCallbacks(
                on_map_page=lambda page_idx, _addr, owner_id: self.map_shm_page(
                    _to_vpn(page_idx), phys_page=page_idx, owner_id=owner_id
                ),
                on_owner_changed=lambda page_idx, _addr, _previous_owner_id, _new_owner_id: self.unmap_shm_page(
                    _to_vpn(page_idx)
                ),
                on_unmap_page=lambda page_idx, _addr: self.unmap_shm_page(_to_vpn(page_idx)),
            )
        )

    def flush_tlb(self) -> None:
        """In-place flush of all 32 TLB slots without reallocation."""
        for slot in self.tlb:
            slot.vpn = 0xFFFF_FFFF
            slot.pte = None

    def flush_tlb_entry(self, vpn: int) -> None:
        """In-place flush of a specific TLB slot."""
        tlb_idx = self.tlb_index(vpn)
        slot = self.tlb[tlb_idx]
        if slot.vpn == vpn:
            slot.vpn = 0xFFFF_FFFF
            slot.pte = None

    # --- Hot path: TLB lookup + FlatMap fallback ---
    @staticmethod
    def tlb_index(vpn: int) -> int:
        """
        Fold the 20-bit VPN 20 -> 10 -> 5 with two XORs and select a 5-bit
        slot for the 32-entry TLB.
        """

        temp = vpn ^ (vpn >> 10)
        temp = temp ^ (temp >> 5)
        return temp & 0x1F

    def _lookup_pte(self, addr: VmmioAddress) -> StaticDevicePTE | Tier3PTE | None:
        """Returns the PTE from TLB (O(1)) or falls back to FlatMap."""
        vpn = addr.vpn()
        tlb_idx = self.tlb_index(vpn)
        slot = self.tlb[tlb_idx]
        if slot.vpn == vpn:
            self.tlb_hits += 1
            return slot.pte
        self.tlb_misses += 1
        # FlatMap lookup
        pte = self.ptes.find(vpn)
        if pte is None:
            return None
        # Refill: in-place overwrite (O(1), zero allocation).
        slot.vpn = vpn
        slot.pte = pte
        return pte

    def access(
        self, raw_addr: int, is_write: bool, value: int | None = None
    ) -> tuple[VmmioStatus, int]:
        """
        Full dispatch: RAM bypass -> TLB/FlatMap -> permission check (always,
        TLB hit or not) -> syscall dispatch or physical access.
        Returns (status, physical_address_or_syscall_result). A value supplied
        for a static-device access is dispatched to its registered handler and
        returned in the second field.
        """

        current_task_id = self.scheduler.current_task_id
        addr = VmmioAddress(raw_addr)
        # 1. Fast RAM bypass (Tier 1) — O(1), never touches the page table.
        if addr.is_linear():
            if addr.raw >= self.guest_ram_size:
                return (
                    TrapCode.OUT_OF_BOUNDS,
                    0,
                )
            return (VmmioStatus.OK_GUEST_RAM, 0)
        # 2. TLB / FlatMap lookup.
        pte = self._lookup_pte(addr)
        if pte is None:
            # Check known valid FCs for proper trap classification
            if not (
                addr.fc() == FC_STATIC_DEVICE
                or addr.fc() == FC_DYNAMIC
                or addr.fc() == FC_SHM
                or addr.fc() == FC_PASSTHROUGH
            ):
                return (
                    TrapCode.UNDEFINED_FC,
                    0,
                )
            return (TrapCode.UNREGISTERED_PAGE, 0)
        # 3. Permission check — runs unconditionally, TLB hit or miss.
        # The FC already at hand (from the address itself, decoded before
        # any table lookup) determines the PTE's shape -- checked from that,
        # not via isinstance (no RTTI in the target build).
        if addr.fc() == FC_STATIC_DEVICE:
            if is_write and not pte.write:
                return (TrapCode.ACCESS_VIOLATION, 0)
            if not is_write and not pte.read:
                return (TrapCode.ACCESS_VIOLATION, 0)
            if value is not None and pte.value_handler is not None:
                result = pte.value_handler(
                    addr.offset(), value & 0xFFFF_FFFF, is_write
                )
                return (VmmioStatus.OK_SYSCALL, 0 if result is None else result)
            vector_id = addr.syscall_metadata()
            if (
                value is not None
                and vector_id < len(self.syscall_vector_table)
                and self.syscall_vector_table[vector_id] is not None
            ):
                vector_handler = self.syscall_vector_table[vector_id]
                assert vector_handler is not None
                result = vector_handler(addr.offset(), value & 0xFFFF_FFFF, is_write)
                return (VmmioStatus.OK_SYSCALL, 0 if result is None else result)
            if pte.handler is not None:
                pte.handler(addr.syscall_metadata(), addr.offset(), is_write)
            return (VmmioStatus.OK_SYSCALL, 0)
        # Tier3PTE (SHM / PASSTHROUGH)
        if not pte.valid:
            return (TrapCode.ACCESS_VIOLATION, 0)
        if is_write and not pte.write:
            return (TrapCode.ACCESS_VIOLATION, 0)
        if not is_write and not pte.read:
            return (TrapCode.ACCESS_VIOLATION, 0)
        if addr.fc() == FC_SHM:
            if pte.owner_id == FB_TASK_ID_FLIGHT:
                return (
                    TrapCode.OWNER_MISMATCH,
                    0,
                )
            if current_task_id != 0 and pte.owner_id != 0 and pte.owner_id != current_task_id:
                return (
                    TrapCode.OWNER_MISMATCH,
                    0,
                )

        phys_addr = (pte.phys_page << 12) | addr.offset()
        return (VmmioStatus.OK_PHYSICAL, phys_addr)
