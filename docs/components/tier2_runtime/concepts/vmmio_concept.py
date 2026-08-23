"""
docs/components/tier2_runtime/concepts/vmmio_concept.py
Reference Concept Implementation: vMMIO 2-Level Page Table & Direct-Mapped TLB
- RAM Bypass Flag (Bit 31): O(1) linear-RAM fast path, no table walk at all
- Function Code (FC, bits[31:28]): 16-entry L1 directory selects the L2 table
- 2-level page table walk (L1 dir[16] -> L2 pt[16]) on TLB miss
- Direct-mapped Software TLB[16] keyed by ((VPN ^ VPN>>16) & 15): the FC is
  folded in, otherwise FC=12 page N and FC=14 page N collide in one slot
- Tier 1 linear RAM: Bit31 bypass PLUS a power-of-two mask bound check
- PTE permission check (VALID/READ/WRITE/EXEC + Owner ID) on every access,
  including on TLB hit — the TLB only skips the table walk, never the check
"""

from typing import Callable


class TrapCode:
    OUT_OF_BOUNDS = "TRAP_MEMORY_OUT_OF_BOUNDS"
    UNDEFINED_FC = "TRAP_UNDEFINED_FC"
    UNREGISTERED_PAGE = "TRAP_UNREGISTERED_PAGE"
    ACCESS_VIOLATION = "TRAP_ACCESS_VIOLATION"
    OWNER_MISMATCH = "TRAP_OWNER_MISMATCH"


# Function Codes (bits[31:28]) — see runtime_vmmio.md "アドレス分解の対応関係"
FC_STATIC_DEVICE = 0xC   # 0xC000_0000: SYSCTL / IPCR / VDMA (Tier 2, syscall dispatch)
FC_SHM = 0xE              # 0xE000_0000: Shared Memory, 16 slots (Tier 3, owner-checked)
FC_PASSTHROUGH = 0xF      # 0xF000_0000: Physical passthrough, 16 pages (Tier 3)

FB_TASK_ID_INVALID = 0x00
FB_TASK_ID_FLIGHT = 0xFF


class VmmioAddress:
    """Decodes a 32-bit guest address into its 5 fields. See runtime_vmmio.md §3.3."""

    def __init__(self, raw: int):
        self.raw = raw & 0xFFFF_FFFF

    def is_linear(self) -> bool:
        # Bit[31] == 0 -> guest RAM (Tier 1), fast-bypass vMMIO entirely.
        return (self.raw & 0x8000_0000) == 0

    def fc(self) -> int:
        return (self.raw >> 28) & 0xF

    def l3_metadata(self) -> int:
        return (self.raw >> 16) & 0xFFF

    def l2_idx(self) -> int:
        return (self.raw >> 12) & 0xF

    def offset(self) -> int:
        return self.raw & 0xFFF

    def vpn(self) -> int:
        return self.raw >> 12


class StaticDevicePTE:
    """FC=12 (Static Device). Syscall ID is carried in the address itself
    (l3_metadata), not the PTE — the PTE only holds the permission/type flags.
    """
    def __init__(self, read: bool = True, write: bool = True, cacheable: bool = False):
        self.read = read
        self.write = write
        self.cacheable = cacheable


class Tier3PTE:
    """FC=14/15 (SHM / PASSTHROUGH). 32-bit layout, no bit overlap:
    [31:12] PPN(20) | [11] VALID | [10] READ | [9] WRITE | [8] EXEC | [7:0] Owner ID
    """
    def __init__(self, phys_page: int, valid: bool, read: bool, write: bool,
                 exec_: bool, owner_id: int = FB_TASK_ID_INVALID):
        self.phys_page = phys_page
        self.valid = valid
        self.read = read
        self.write = write
        self.exec_ = exec_
        self.owner_id = owner_id


class VMMIOController:
    """2-level page table (L1 dir[16] -> L2 pt[16]) with a direct-mapped
    16-entry software TLB. All dispatch paths are O(1): no linear scans.
    """

    def __init__(self, guest_ram_size: int = 8192):     # FB_CONF_GUEST_RAM_SIZE
        # `{FastAddressCheck}`: for a power-of-two allocation the bound check is a
        # single mask, so the Tier 1 path stays O(1) and branch-predictable.
        if guest_ram_size <= 0 or (guest_ram_size & (guest_ram_size - 1)):
            raise ValueError("guest RAM size must be a power of two for the mask-based bound check")
        self.guest_ram_size = guest_ram_size
        self.guest_ram_mask = ~(guest_ram_size - 1) & 0xFFFF_FFFF

        # L1 directory: FC -> L2 table (dict of l2_idx -> PTE), or None if FC unmapped.
        self.l1_dir: list[dict[int, object] | None] = [None] * 16

        # Direct-mapped TLB: 16 slots, keyed by vpn & 15. Each slot caches (vpn, pte).
        self.tlb: list[dict] = [{"vpn": 0xFFFF_FFFF, "pte": None} for _ in range(16)]
        self.tlb_hits = 0
        self.tlb_misses = 0

        # Registered syscall dispatch handlers, keyed by (fc, l3_metadata).
        self.syscall_handlers: dict[tuple[int, int], Callable[[int, bool], None]] = {}

    # --- Static configuration (compile-time in the real system) ---

    def map_static_device(self, l2_idx: int, l3_metadata: int,
                          handler: Callable[[int, bool], None],
                          read: bool = True, write: bool = True):
        """Registers a Tier 2 static device page (FC=12)."""
        l2 = self.l1_dir[FC_STATIC_DEVICE]
        if l2 is None:
            l2 = {}
            self.l1_dir[FC_STATIC_DEVICE] = l2
        l2[l2_idx] = StaticDevicePTE(read=read, write=write)
        self.syscall_handlers[(FC_STATIC_DEVICE, l3_metadata)] = handler

    def map_shm_page(self, l2_idx: int, phys_page: int, owner_id: int):
        """Registers a Tier 3 SHM page (FC=14). Only the IPC Router may write here."""
        l2 = self.l1_dir[FC_SHM]
        if l2 is None:
            l2 = {}
            self.l1_dir[FC_SHM] = l2
        l2[l2_idx] = Tier3PTE(phys_page, valid=True, read=True, write=True,
                              exec_=False, owner_id=owner_id)

    def revoke_shm_owner(self, l2_idx: int):
        """IPC Router Revoke phase: mark the page in-flight and invalidate its TLB entry."""
        pte = self.l1_dir[FC_SHM][l2_idx]
        pte.owner_id = FB_TASK_ID_FLIGHT
        vpn = (0x8000_0000 | (FC_SHM << 28) | (l2_idx << 12)) >> 12
        tlb_idx = self.tlb_index(vpn)
        if self.tlb[tlb_idx]["vpn"] == vpn:
            self.tlb[tlb_idx] = {"vpn": 0xFFFF_FFFF, "pte": None}

    # --- Hot path: TLB lookup + page table walk ---

    @staticmethod
    def tlb_index(vpn: int) -> int:
        """Direct-mapped TLB hash with the Function Code folded in.

        The low 4 bits of a VPN are the L2 index [15:12], so `vpn & 15` alone
        makes FC=12 page 3 and FC=14 page 3 collide in the same slot — exactly
        the syscall/SHM interleave that IPC performs. `vpn >> 16` carries
        FC[31:28], so one XOR separates them. [runtime_vmmio.md §1-3]
        """
        return (vpn ^ (vpn >> 16)) & 15

    def _lookup_pte(self, addr: VmmioAddress):
        """Returns the PTE, or None when the FC or the page is unmapped."""
        vpn = addr.vpn()
        tlb_idx = self.tlb_index(vpn)
        slot = self.tlb[tlb_idx]

        if slot["vpn"] == vpn:
            self.tlb_hits += 1
            return slot["pte"]

        self.tlb_misses += 1
        l2 = self.l1_dir[addr.fc()]
        if l2 is None:
            return None  # UNDEFINED_FC
        pte = l2.get(addr.l2_idx())
        if pte is None:
            return None  # UNREGISTERED_PAGE

        # Refill: direct-mapped, unconditional overwrite (O(1), no eviction search).
        self.tlb[tlb_idx] = {"vpn": vpn, "pte": pte}
        return pte

    def access(self, raw_addr: int, is_write: bool, current_task_id: int = 0
               ) -> tuple[str, str]:
        """
        Full dispatch: RAM bypass -> TLB/walk -> permission check (always,
        TLB hit or not) -> syscall dispatch or physical access.
        Returns (status_code, detail).
        """
        addr = VmmioAddress(raw_addr)

        # 1. Fast RAM bypass (Tier 1) — O(1), never touches the page table.
        #    The bypass is NOT a free pass: `{MemoryBoundaryCheck}` still applies.
        if addr.is_linear():
            if addr.raw & self.guest_ram_mask:
                return (TrapCode.OUT_OF_BOUNDS,
                        f"guest address {addr.raw:#010x} exceeds "
                        f"FB_CONF_GUEST_RAM_SIZE ({self.guest_ram_size})")
            return ("OK_GUEST_RAM", "bypassed to linear guest RAM")

        # 2. TLB / L1-L2 walk.
        pte = self._lookup_pte(addr)
        if pte is None:
            if self.l1_dir[addr.fc()] is None:
                return (TrapCode.UNDEFINED_FC, f"FC {addr.fc():#x} has no L2 table")
            return (TrapCode.UNREGISTERED_PAGE, f"no PTE at L2 index {addr.l2_idx()}")

        # 3. Permission check — runs unconditionally, TLB hit or miss.
        #    The TLB only skips the table walk; it never skips this check.
        if isinstance(pte, StaticDevicePTE):
            if is_write and not pte.write:
                return (TrapCode.ACCESS_VIOLATION, "static device: write not permitted")
            if not is_write and not pte.read:
                return (TrapCode.ACCESS_VIOLATION, "static device: read not permitted")
            handler = self.syscall_handlers.get((addr.fc(), addr.l3_metadata()))
            if handler is None:
                return (TrapCode.UNREGISTERED_PAGE, "no syscall handler for this metadata")
            handler(addr.offset(), is_write)
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
                return (TrapCode.OWNER_MISMATCH, "page is in-flight (ownership transfer)")
            if pte.owner_id != current_task_id:
                return (TrapCode.OWNER_MISMATCH,
                       f"owner={pte.owner_id} != requester={current_task_id}")

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
    ctrl.map_static_device(l2_idx=0, l3_metadata=0x000,
                           handler=lambda off, w: dispatched.append((off, w)))

    addr = 0x8000_0000 | (FC_STATIC_DEVICE << 28) | (0 << 12) | 0x004
    status, _ = ctrl.access(addr, is_write=True)
    assert status == "OK_SYSCALL"
    assert dispatched == [(0x004, True)]


def test_tlb_hit_after_first_walk():
    ctrl = VMMIOController()
    ctrl.map_static_device(l2_idx=1, l3_metadata=0x001, handler=lambda o, w: None)
    addr = 0x8000_0000 | (FC_STATIC_DEVICE << 28) | (0x001 << 16) | (1 << 12)

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
    ctrl.map_shm_page(l2_idx=2, phys_page=0x1234, owner_id=7)
    addr = 0x8000_0000 | (FC_SHM << 28) | (2 << 12)

    # Owning task may access.
    status, _ = ctrl.access(addr, is_write=True, current_task_id=7)
    assert status == "OK_PHYSICAL"

    # A different task must not, even though the PTE was just cached in the TLB.
    status, _ = ctrl.access(addr, is_write=True, current_task_id=9)
    assert status == TrapCode.OWNER_MISMATCH


def test_revoke_invalidates_tlb_and_blocks_access_during_flight():
    ctrl = VMMIOController()
    ctrl.map_shm_page(l2_idx=3, phys_page=0x5678, owner_id=1)
    addr = 0x8000_0000 | (FC_SHM << 28) | (3 << 12)

    ctrl.access(addr, is_write=False, current_task_id=1)  # warms the TLB
    assert ctrl.tlb_hits == 0 and ctrl.tlb_misses == 1

    ctrl.revoke_shm_owner(l2_idx=3)

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

    assert ctrl.tlb_hits == 0 and ctrl.tlb_misses == 0,         "the Tier 1 path must never touch the page table"


def test_tlb_index_separates_function_codes():
    """FC=12 page 3 and FC=14 page 3 must not share a TLB slot."""
    idx = VMMIOController.tlb_index
    a = idx((0x8000_0000 | (FC_STATIC_DEVICE << 28) | (3 << 12)) >> 12)
    b = idx((0x8000_0000 | (FC_SHM << 28) | (3 << 12)) >> 12)
    c = idx((0x8000_0000 | (FC_PASSTHROUGH << 28) | (3 << 12)) >> 12)
    assert len({a, b, c}) == 3, f"FCs collide: {a}, {b}, {c}"


def test_interleaved_syscall_and_shm_keep_hitting_the_tlb():
    """The IPC pattern (syscall to IPCR, then touch SHM) must not thrash."""
    ctrl = VMMIOController()
    ctrl.map_static_device(l2_idx=3, l3_metadata=0x003, handler=lambda o, w: None)
    ctrl.map_shm_page(l2_idx=3, phys_page=0x900, owner_id=1)

    sysc = 0x8000_0000 | (FC_STATIC_DEVICE << 28) | (0x003 << 16) | (3 << 12)
    shm = 0x8000_0000 | (FC_SHM << 28) | (3 << 12)
    for _ in range(10):
        ctrl.access(sysc, is_write=True)
        ctrl.access(shm, is_write=True, current_task_id=1)

    total = ctrl.tlb_hits + ctrl.tlb_misses
    assert ctrl.tlb_hits / total >= 0.9,         f"expected >=90% hit rate, got {ctrl.tlb_hits}/{total}"


if __name__ == "__main__":
    test_ram_bypass_never_touches_page_table()
    test_static_device_syscall_dispatch()
    test_tlb_hit_after_first_walk()
    test_undefined_fc_traps()
    test_shm_owner_isolation()
    test_revoke_invalidates_tlb_and_blocks_access_during_flight()
    test_linear_ram_is_bounds_checked_not_waved_through()
    test_tlb_index_separates_function_codes()
    test_interleaved_syscall_and_shm_keep_hitting_the_tlb()
    print("[PASS] All vMMIO concept tests passed successfully.")
