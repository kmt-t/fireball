from __future__ import annotations

"""
Unit tests for Tier 2 Runtime: Virtual MMIO Controller
Traceability: runtime_vmmio_test_spec.md
"""

import sys
from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _TESTS_DIR,
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from ipc_router import (
    DataType,
    ScopeKind,
    pack_key32,
)
from vmmio import (
    TrapCode,
    VMMIOController,
)


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def test_vmmio_01_three_tier_gate_dispatch():
    """VMMIO-01: 3-tier address gate resolves Linear RAM, Static Devices, and SHM/Passthrough."""
    ctrl = VMMIOController(guest_ram_size=64 * 1024)
    ctrl.map_static_device(0xC0000)
    ctrl.map_passthrough_page(vpn=0xF0000, phys_page=1)
    # Linear RAM (Tier 1)
    stat, detail = ctrl.access(raw_addr=0x1000, is_write=False, current_task_id=0)
    assert stat == "OK_GUEST_RAM"
    # Static Device (Tier 2, FC=12)
    stat, detail = ctrl.access(raw_addr=0xC000_0000, is_write=True, current_task_id=0)
    assert stat == "OK_SYSCALL"
    # Passthrough (Tier 3, FC=15)
    stat, detail = ctrl.access(raw_addr=0xF000_0000, is_write=False, current_task_id=0)
    assert stat == "OK_PHYSICAL"
    # Out of Bounds Linear RAM
    stat, _ = ctrl.access(raw_addr=0x10000, is_write=False, current_task_id=0)
    assert stat == TrapCode.OUT_OF_BOUNDS


def test_vmmio_02_fc14_shm_owner_isolation_and_flight():
    """VMMIO-02: FC=14 shared memory enforces owner_id match and traps FLIGHT state."""
    ctrl = VMMIOController(guest_ram_size=64 * 1024)
    ctrl.map_shm_page(vpn=0xE0000, phys_page=2, owner_id=1)
    # Owner 1 access OK
    stat, _ = ctrl.access(raw_addr=0xE000_0000, is_write=True, current_task_id=1)
    assert stat == "OK_PHYSICAL"
    # Rogue task 2 access TRAPS
    stat, _ = ctrl.access(raw_addr=0xE000_0000, is_write=True, current_task_id=2)
    assert stat == TrapCode.OWNER_MISMATCH
    # In-flight access TRAPS for all tasks
    ctrl.revoke_shm_owner(vpn=0xE0000)
    stat, _ = ctrl.access(raw_addr=0xE000_0000, is_write=True, current_task_id=1)
    assert stat == TrapCode.OWNER_MISMATCH


def test_vmmio_03_undefined_function_code_traps():
    """VMMIO-03: Undefined FC (0x0..0xB, 0xD) immediately traps."""
    ctrl = VMMIOController(guest_ram_size=64 * 1024)
    stat, _ = ctrl.access(raw_addr=0xD000_0000, is_write=False, current_task_id=1)
    assert stat == TrapCode.UNDEFINED_FC


# ===========================================================================
# 8. Tier 1 IPC Router & Zero-Copy SharedBlock Transfer (ipc_router_test_spec.md)
# ===========================================================================


_KEY_CMD = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=1)
_KEY_SHM_ID = pack_key32(ScopeKind.RESOURCE, DataType.UINT32, key_id=1)
_CMD_PIN_HIGH = 1


def _run_immediate(gen):
    """Drives an IPCRouter.send()/recv() generator that is expected to reject
    at Stage 1/2 (URI lookup / RBAC) -- i.e. never touch a CSP channel and so
    never actually block -- and returns its final (IpcStatus, ...) value."""
    try:
        next(gen)
    except StopIteration as e:
        return e.value
    raise AssertionError("expected immediate Stage 1/2 rejection, but the call blocked")


if __name__ == "__main__":
    test_vmmio_01_three_tier_gate_dispatch()
    test_vmmio_02_fc14_shm_owner_isolation_and_flight()
    test_vmmio_03_undefined_function_code_traps()
    print("[PASS] All 3 Virtual MMIO Controller tests passed.")
