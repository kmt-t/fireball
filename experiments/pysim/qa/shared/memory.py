"""QA snapshots of IPC storage through its existing ownership-checked reads."""

from memory_interface import SharedBlock


def snapshot_ipc_storage(block: SharedBlock) -> bytes:
    """Copy the complete uint64 IPC backing array only at a test observation point."""
    return b"".join(
        block.read_u64(index).to_bytes(8, "little") for index in range(block.u64_capacity())
    )
