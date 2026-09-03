"""
experiments/pysim/vmmio.py
vMMIO FlatMap Page Table & Direct-Mapped TLB simulation.
- RAM Bypass Flag (Bit 31): O(1) linear-RAM fast path, no table lookup
- FlatMap PTE storage: maps 20-bit VPN -> PTE
- Direct-mapped Software TLB[16] keyed by Folding XOR Hash over 20-bit VPN:
  diffuses all 20 bits (including Function Code) into a 4-bit slot index (0..15)
- Tier 1 linear RAM: Bit31 bypass PLUS a size-comparison bound check (no mask, no
  power-of-two constraint on guest_ram_size) — traps to the interpreter on OOB
- PTE permission check (VALID/READ/WRITE/EXEC + Owner ID) on every access,
  including on TLB hit — the TLB only skips the table lookup, never the check
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from system_containers import MutableFlatMapStorage

if TYPE_CHECKING:
    from memory import MemoryManager

# docs/components/tier1_core/system_config.md {META_FlatMapIndexed}: max PTE
# count the FlatMap page table can hold.
FB_CONF_VMMIO_MAX_PTES = 32


class TrapCode:
    OUT_OF_BOUNDS = "TRAP_MEMORY_OUT_OF_BOUNDS"
    UNDEFINED_FC = "TRAP_UNDEFINED_FC"
    UNREGISTERED_PAGE = "TRAP_UNREGISTERED_PAGE"
    ACCESS_VIOLATION = "TRAP_ACCESS_VIOLATION"
    OWNER_MISMATCH = "TRAP_OWNER_MISMATCH"


# Function Codes (bits[31:28]) — see runtime_vmmio.md "アドレス分解の対応関係"
FC_STATIC_DEVICE = 0xC  # 0xC000_0000: SYSCTL / IPCR / VDMA (Tier 2, syscall dispatch)
FC_SHM = 0xE  # 0xE000_0000: Shared Memory (Tier 3, owner-checked)
FC_PASSTHROUGH = 0xF  # 0xF000_0000: Physical passthrough (Tier 3)
FB_TASK_ID_INVALID = 0x00
FB_TASK_ID_FLIGHT = 0xFF


class VmmioAddress:
    """Decodes a 32-bit guest address into fields. See runtime_vmmio.md §3.3."""

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

    def __init__(
        self,
        handler: Callable[[int, int, bool], None] | None = None,
        read: bool = True,
        write: bool = True,
        cacheable: bool = False,
    ):
        self.handler = handler
        self.read = read
        self.write = write
        self.cacheable = cacheable


class Tier3PTE:
    """
    FC=14/15 (SHM / PASSTHROUGH). 32-bit layout, no bit overlap:
        [31:12] PPN(20) | [11] VALID | [10] READ | [9] WRITE | [8] EXEC | [7:0] Owner ID
    """

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


@dataclass
class TLBSlot:
    vpn: int = 0xFFFF_FFFF
    pte: StaticDevicePTE | Tier3PTE | None = None


class VMMIOController:
    """
    FlatMap Page Table (vpn -> PTE) with a direct-mapped 16-entry software TLB.
        TLB hits provide O(1) hot-path access, while TLB misses look up the FlatMap.
    """

    def __init__(self, guest_ram_size: int = 8192):  # FB_CONF_GUEST_RAM_SIZE

        if guest_ram_size <= 0:
            raise ValueError("guest RAM size must be positive")
        self.guest_ram_size = guest_ram_size
        # FlatMap PTE storage: vpn (20-bit) -> PTE, capacity-bounded per
        # system_config.md's FB_CONF_VMMIO_MAX_PTES (a fixed static array in
        # C++, so a MutableFlatMapStorage here, never a dict).
        self.ptes: MutableFlatMapStorage[int, StaticDevicePTE | Tier3PTE] = MutableFlatMapStorage(
            capacity=FB_CONF_VMMIO_MAX_PTES
        )
        # Direct-mapped TLB: 16 slots, keyed by 4-bit Folding XOR Hash over 20-bit VPN.
        self.tlb: list[TLBSlot] = [TLBSlot() for _ in range(16)]
        self.tlb_hits = 0
        self.tlb_misses = 0

    # --- Static & Dynamic PTE Registration (FlatMap) ---
    def map_static_device(
        self,
        vpn: int,
        handler: Callable[[int, int, bool], None] | None = None,
        read: bool = True,
        write: bool = True,
    ):
        """Registers a Tier 2 static device page (FC=12) into FlatMap."""
        self.ptes.insert(vpn, StaticDevicePTE(handler=handler, read=read, write=write))

    def map_shm_page(self, vpn: int, phys_page: int, owner_id: int):
        """Registers a Tier 3 SHM page (FC=14) into FlatMap."""
        self.ptes.insert(
            vpn,
            Tier3PTE(
                phys_page=phys_page,
                valid=True,
                read=True,
                write=True,
                exec_=False,
                owner_id=owner_id,
            ),
        )

    def map_passthrough_page(self, vpn: int, phys_page: int, read: bool = True, write: bool = True):
        """Registers a Tier 3 Passthrough page (FC=15) into FlatMap."""
        self.ptes.insert(
            vpn,
            Tier3PTE(
                phys_page=phys_page,
                valid=True,
                read=read,
                write=write,
                exec_=True,
                owner_id=0,
            ),
        )

    def revoke_shm_owner(self, vpn: int):
        """IPC Router Revoke phase: mark the page in-flight and invalidate its TLB entry."""
        pte = self.ptes.find(vpn)
        # SHM/PASSTHROUGH FC (bits [19:16] of the VPN) is Tier3PTE by
        # construction (map_shm_page/map_passthrough_page are the only
        # writers for that FC range) -- checked structurally from the VPN's
        # own encoding, not via isinstance (no RTTI in the target build).
        if pte is not None and ((vpn >> 16) & 0xF) in (FC_SHM, FC_PASSTHROUGH):
            pte.owner_id = FB_TASK_ID_FLIGHT

        tlb_idx = self.tlb_index(vpn)
        if self.tlb[tlb_idx].vpn == vpn:
            self.tlb[tlb_idx] = TLBSlot()

    def update_shm_owner(self, vpn: int, new_owner_id: int) -> bool:
        """Updates the owner_id of an FC=14 SHM page and flushes its TLB entry."""
        pte = self.ptes.find(vpn)
        if pte is not None and ((vpn >> 16) & 0xF) in (FC_SHM, FC_PASSTHROUGH):
            pte.owner_id = new_owner_id
            self.flush_tlb_entry(vpn)
            return True
        return False

    def unmap_shm_page(self, vpn: int) -> None:
        """Unregisters an FC=14 SHM page and flushes its TLB entry."""
        pte = self.ptes.find(vpn)
        if pte is not None and ((vpn >> 16) & 0xF) in (FC_SHM, FC_PASSTHROUGH):
            pte.valid = False
            self.flush_tlb_entry(vpn)

    def register_to_memory_manager(self, memory_manager: MemoryManager) -> None:
        """Registers vMMIO FC=14 SHM page table listeners into MemoryManager."""
        from memory import PageMappingCallbacks

        def _to_vpn(page_idx: int) -> int:
            return (0xE000_0000 >> 12) + page_idx

        memory_manager.register_page_mapping_callbacks(
            PageMappingCallbacks(
                on_map_page=lambda page_idx, _addr, owner_id: self.map_shm_page(
                    _to_vpn(page_idx), phys_page=page_idx, owner_id=owner_id
                ),
                on_update_owner=lambda page_idx, _addr, new_owner_id: self.update_shm_owner(
                    _to_vpn(page_idx), new_owner_id
                ),
                on_revoke=lambda page_idx, _addr: self.revoke_shm_owner(_to_vpn(page_idx)),
                on_unmap_page=lambda page_idx, _addr: self.unmap_shm_page(_to_vpn(page_idx)),
            )
        )

    def flush_tlb(self) -> None:
        self.tlb = [TLBSlot() for _ in range(16)]

    def flush_tlb_entry(self, vpn: int) -> None:
        tlb_idx = self.tlb_index(vpn)
        if self.tlb[tlb_idx].vpn == vpn:
            self.tlb[tlb_idx] = TLBSlot()

    # --- Hot path: TLB lookup + FlatMap fallback ---
    @staticmethod
    def tlb_index(vpn: int) -> int:
        """
        4-bit Folding XOR Hash over 20-bit VPN.
                Diffuses all 20 bits of the VPN (FC, Page, Subfields) into a 4-bit TLB slot (0..15).
        """

        return (vpn ^ (vpn >> 4) ^ (vpn >> 8) ^ (vpn >> 12) ^ (vpn >> 16)) & 15

    def _lookup_pte(self, addr: VmmioAddress):
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
        # Refill: direct-mapped, unconditional overwrite (O(1), no eviction search).
        self.tlb[tlb_idx] = TLBSlot(vpn=vpn, pte=pte)
        return pte

    def access(self, raw_addr: int, is_write: bool, current_task_id: int = 0) -> tuple[str, str]:
        """
        Full dispatch: RAM bypass -> TLB/FlatMap -> permission check (always,
        TLB hit or not) -> syscall dispatch or physical access.
        Returns (status_code, detail).
        """

        addr = VmmioAddress(raw_addr)
        # 1. Fast RAM bypass (Tier 1) — O(1), never touches the page table.
        if addr.is_linear():
            if addr.raw >= self.guest_ram_size:
                return (
                    TrapCode.OUT_OF_BOUNDS,
                    f"guest address {addr.raw:#010x} exceeds "
                    f"FB_CONF_GUEST_RAM_SIZE ({self.guest_ram_size})",
                )
            return ("OK_GUEST_RAM", "bypassed to linear guest RAM")
        # 2. TLB / FlatMap lookup.
        pte = self._lookup_pte(addr)
        if pte is None:
            # Check known valid FCs for proper trap classification
            if addr.fc() not in (FC_STATIC_DEVICE, FC_SHM, FC_PASSTHROUGH):
                return (
                    TrapCode.UNDEFINED_FC,
                    f"FC {addr.fc():#x} is not a valid vMMIO region",
                )
            return (TrapCode.UNREGISTERED_PAGE, f"no PTE at VPN {addr.vpn():#x}")
        # 3. Permission check — runs unconditionally, TLB hit or miss.
        # The FC already at hand (from the address itself, decoded before
        # any table lookup) determines the PTE's shape -- checked from that,
        # not via isinstance (no RTTI in the target build).
        if addr.fc() == FC_STATIC_DEVICE:
            if is_write and not pte.write:
                return (TrapCode.ACCESS_VIOLATION, "static device: write not permitted")
            if not is_write and not pte.read:
                return (TrapCode.ACCESS_VIOLATION, "static device: read not permitted")
            if pte.handler is not None:
                pte.handler(addr.syscall_metadata(), addr.offset(), is_write)
            return ("OK_SYSCALL", "dispatched to static device handler")
        # Tier3PTE (SHM / PASSTHROUGH)
        if not pte.valid:
            return (TrapCode.ACCESS_VIOLATION, "page marked invalid")
        if is_write and not pte.write:
            return (TrapCode.ACCESS_VIOLATION, "write not permitted")
        if not is_write and not pte.read:
            return (TrapCode.ACCESS_VIOLATION, "read not permitted")
        if addr.fc() == FC_SHM:
            if pte.owner_id == FB_TASK_ID_FLIGHT:
                return (
                    TrapCode.OWNER_MISMATCH,
                    "page is in-flight (ownership transfer)",
                )

            if pte.owner_id != current_task_id:
                return (
                    TrapCode.OWNER_MISMATCH,
                    f"owner={pte.owner_id} != requester={current_task_id}",
                )

        phys_addr = (pte.phys_page << 12) | addr.offset()
        return ("OK_PHYSICAL", f"physical access at {phys_addr:#010x}")
