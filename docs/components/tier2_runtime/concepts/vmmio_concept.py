"""
docs/components/tier2_runtime/concepts/vmmio_concept.py
Reference Concept Implementation: vMMIO FlatMap Page Table & Direct-Mapped TLB
- RAM Bypass Flag (Bit 31): O(1) linear-RAM fast path, no table lookup
- FlatMap PTE storage: maps 20-bit VPN -> PTE
- Direct-mapped Software TLB[16] keyed by Folding XOR Hash over 20-bit VPN:
  diffuses all 20 bits (including Function Code) into a 4-bit slot index (0..15)
- Tier 1 linear RAM: Bit31 bypass PLUS a size-comparison bound check (no mask, no
  power-of-two constraint on guest_ram_size) — traps to the interpreter on OOB
- PTE permission check (VALID/READ/WRITE/EXEC + Owner ID) on every access,
  including on TLB hit — the TLB only skips the table lookup, never the check
"""

from collections.abc import Callable


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


class VMMIOController:
    """
    FlatMap Page Table (vpn -> PTE) with a direct-mapped 16-entry software TLB.
        TLB hits provide O(1) hot-path access, while TLB misses look up the FlatMap.
    """

    def __init__(self, guest_ram_size: int = 8192):  # FB_CONF_GUEST_RAM_SIZE
        # `{FastAddressCheck}`: a direct size comparison (addr >= guest_ram_size), not a
        # mask. This is still O(1) and branch-predictable, and unlike a mask it places
        # no power-of-two constraint on guest_ram_size and never silently wraps an
        # out-of-bounds address back into range — see `{MemoryBoundaryCheck}`.
        if guest_ram_size <= 0:
            raise ValueError("guest RAM size must be positive")
        self.guest_ram_size = guest_ram_size
        # FlatMap PTE storage: vpn (20-bit) -> PTE
        self.ptes: dict[int, object] = {}
        # Direct-mapped TLB: 16 slots, keyed by 4-bit Folding XOR Hash over 20-bit VPN.
        self.tlb: list[dict] = [{"vpn": 0xFFFF_FFFF, "pte": None} for _ in range(16)]
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
        self.ptes[vpn] = StaticDevicePTE(handler=handler, read=read, write=write)

    def map_shm_page(self, vpn: int, phys_page: int, owner_id: int):
        """Registers a Tier 3 SHM page (FC=14) into FlatMap."""
        self.ptes[vpn] = Tier3PTE(
            phys_page=phys_page,
            valid=True,
            read=True,
            write=True,
            exec_=False,
            owner_id=owner_id,
        )

    def map_passthrough_page(self, vpn: int, phys_page: int, read: bool = True, write: bool = True):
        """Registers a Tier 3 Passthrough page (FC=15) into FlatMap."""
        self.ptes[vpn] = Tier3PTE(
            phys_page=phys_page,
            valid=True,
            read=read,
            write=write,
            exec_=True,
            owner_id=0,
        )

    def revoke_shm_owner(self, vpn: int):
        """IPC Router Revoke phase: mark the page in-flight and invalidate its TLB entry."""
        pte = self.ptes.get(vpn)
        if pte is not None and isinstance(pte, Tier3PTE):
            pte.owner_id = FB_TASK_ID_FLIGHT
        tlb_idx = self.tlb_index(vpn)
        if self.tlb[tlb_idx]["vpn"] == vpn:
            self.tlb[tlb_idx] = {"vpn": 0xFFFF_FFFF, "pte": None}

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
        if slot["vpn"] == vpn:
            self.tlb_hits += 1
            return slot["pte"]
        self.tlb_misses += 1
        # FlatMap lookup
        pte = self.ptes.get(vpn)
        if pte is None:
            return None  # UNREGISTERED_PAGE or UNDEFINED_FC
        # Refill: direct-mapped, unconditional overwrite (O(1), no eviction search).
        self.tlb[tlb_idx] = {"vpn": vpn, "pte": pte}
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
        if isinstance(pte, StaticDevicePTE):
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


# ==============================================================================
# Simulation / Verification Tests
# ==============================================================================


def test_ram_bypass_never_touches_page_table():
    ctrl = VMMIOController()
    status, _ = ctrl.access(0x0000_1000, is_write=False)
    assert status == "OK_GUEST_RAM"
    assert ctrl.tlb_hits == 0 and ctrl.tlb_misses == 0


def test_static_device_syscall_dispatch():
    ctrl = VMMIOController()
    dispatched = []
    vpn = (0xC000_0000 | (0x042 << 16)) >> 12
    ctrl.map_static_device(
        vpn=vpn, handler=lambda sys_id, off, w: dispatched.append((sys_id, off, w))
    )
    addr = 0xC042_0004
    status, _ = ctrl.access(addr, is_write=True)
    assert status == "OK_SYSCALL"
    assert dispatched == [(0x042, 0x004, True)]


def test_tlb_hit_after_first_walk():
    ctrl = VMMIOController()
    ctrl.map_static_device(vpn=0xC0001, handler=lambda sys_id, o, w: None)
    addr = 0xC000_1000
    status1, _ = ctrl.access(addr, is_write=False)
    assert status1 == "OK_SYSCALL"
    assert ctrl.tlb_misses == 1 and ctrl.tlb_hits == 0
    status2, _ = ctrl.access(addr, is_write=False)
    assert status2 == "OK_SYSCALL"
    assert ctrl.tlb_hits == 1, "second access to the same page must hit the TLB"


def test_undefined_fc_traps():
    ctrl = VMMIOController()
    status, _ = ctrl.access(0x8000_0000 | (0xD << 28), is_write=False)  # FC=13, reserved
    assert status == TrapCode.UNDEFINED_FC


def test_shm_owner_isolation():
    ctrl = VMMIOController()
    ctrl.map_shm_page(vpn=0xE0002, phys_page=0x1234, owner_id=7)
    addr = 0xE000_2000
    # Owning task may access.
    status, _ = ctrl.access(addr, is_write=True, current_task_id=7)
    assert status == "OK_PHYSICAL"
    # A different task must not, even though the PTE was just cached in the TLB.
    status, _ = ctrl.access(addr, is_write=True, current_task_id=9)
    assert status == TrapCode.OWNER_MISMATCH


def test_revoke_invalidates_tlb_and_blocks_access_during_flight():
    ctrl = VMMIOController()
    ctrl.map_shm_page(vpn=0xE0003, phys_page=0x5678, owner_id=1)
    addr = 0xE000_3000
    ctrl.access(addr, is_write=False, current_task_id=1)  # warms the TLB
    assert ctrl.tlb_hits == 0 and ctrl.tlb_misses == 1
    ctrl.revoke_shm_owner(vpn=0xE0003)
    # In-flight: neither the old owner nor anyone else may access it.
    status, _ = ctrl.access(addr, is_write=False, current_task_id=1)
    assert status == TrapCode.OWNER_MISMATCH
    # The revoke must have forced a fresh walk (TLB entry was invalidated).
    assert ctrl.tlb_misses == 2


def test_linear_ram_is_bounds_checked_not_waved_through():
    """The Bit31 bypass must still enforce `{MemoryBoundaryCheck}`."""
    ctrl = VMMIOController(guest_ram_size=8192)
    ok, _ = ctrl.access(0x0000_1FFF, is_write=True)
    assert ok == "OK_GUEST_RAM", "last in-range byte must be accepted"
    for bad in (0x0000_2000, 0x0001_0000, 0x7FFF_FFFF):
        st, _ = ctrl.access(bad, is_write=True)
        assert st == TrapCode.OUT_OF_BOUNDS, f"{bad:#x} is past the 8KB allocation"
    assert ctrl.tlb_hits == 0 and ctrl.tlb_misses == 0, (
        "the Tier 1 path must never touch the page table"
    )


def test_linear_ram_bound_check_works_for_non_power_of_two_size():
    """Comparison-based bounds checking (no mask) must not constrain guest_ram_size to a
    power of two. This is exactly the gap the earlier mask-based design left open: a mask
    derived from a non-power-of-two size does not land on the real boundary."""
    ctrl = VMMIOController(guest_ram_size=12288)  # 12KB — not a power of two
    ok, _ = ctrl.access(12287, is_write=False)
    assert ok == "OK_GUEST_RAM", "last in-range byte (size-1) must be accepted"
    st, _ = ctrl.access(12288, is_write=False)
    assert st == TrapCode.OUT_OF_BOUNDS, "the first byte past the real 12KB boundary must trap"


def test_tlb_index_separates_function_codes():
    """FC=12 page 3 and FC=14 page 3 must not share a TLB slot."""
    idx = VMMIOController.tlb_index
    a = idx(0xC0003)
    b = idx(0xE0003)
    c = idx(0xF0003)
    assert len({a, b, c}) == 3, f"FCs collide: {a}, {b}, {c}"


def test_interleaved_syscall_and_shm_keep_hitting_the_tlb():
    """The IPC pattern (syscall to IPCR, then touch SHM) must not thrash."""
    ctrl = VMMIOController()
    ctrl.map_static_device(vpn=0xC0003, handler=lambda sys_id, o, w: None)
    ctrl.map_shm_page(vpn=0xE0003, phys_page=0x900, owner_id=1)
    sysc = 0xC000_3000
    shm = 0xE000_3000
    for _ in range(10):
        ctrl.access(sysc, is_write=True)
        ctrl.access(shm, is_write=True, current_task_id=1)

    total = ctrl.tlb_hits + ctrl.tlb_misses
    assert ctrl.tlb_hits / total >= 0.9, f"expected >=90% hit rate, got {ctrl.tlb_hits}/{total}"


def test_flatmap_pte_registration_and_tlb_caching():
    """FlatMap stores PTEs, with direct-mapped TLB acceleration."""
    ctrl = VMMIOController()
    # Register 32 distinct SHM pages
    for p in range(32):
        ctrl.map_shm_page(vpn=0xE0000 + p, phys_page=0x1000 + p, owner_id=42)

    assert len(ctrl.ptes) == 32, "all 32 pages must be stored in FlatMap"
    # Access all 32 pages
    for p in range(32):
        addr = 0xE000_0000 | (p << 12) | 0x10
        st, msg = ctrl.access(addr, is_write=False, current_task_id=42)
        assert st == "OK_PHYSICAL"
        assert (
            f"0x{0x1000010 + (p << 12):08x}" in msg or f"0x{(0x1000 + p) << 12 | 0x10:08x}" in msg
        )

    # Repeated access to a hot working set of 8 pages achieves 100% TLB hits
    for p in range(8):
        addr = 0xE000_0000 | (p << 12)
        ctrl.access(addr, is_write=False, current_task_id=42)

    before_hits = ctrl.tlb_hits
    for _ in range(10):
        for p in range(8):
            addr = 0xE000_0000 | (p << 12)
            st, _ = ctrl.access(addr, is_write=False, current_task_id=42)
            assert st == "OK_PHYSICAL"
    assert ctrl.tlb_hits == before_hits + 80, "working set in TLB must achieve 100% hit rate"


if __name__ == "__main__":
    test_ram_bypass_never_touches_page_table()
    test_static_device_syscall_dispatch()
    test_tlb_hit_after_first_walk()
    test_undefined_fc_traps()
    test_shm_owner_isolation()
    test_revoke_invalidates_tlb_and_blocks_access_during_flight()
    test_linear_ram_is_bounds_checked_not_waved_through()
    test_linear_ram_bound_check_works_for_non_power_of_two_size()
    test_tlb_index_separates_function_codes()
    test_interleaved_syscall_and_shm_keep_hitting_the_tlb()
    test_flatmap_pte_registration_and_tlb_caching()
    print("[PASS] All vMMIO concept tests passed successfully.")
