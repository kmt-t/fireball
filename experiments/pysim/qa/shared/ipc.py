"""QA setup of pre-authorized channels through the public URI lookup contract."""

from ipc_router import IPCRouter, IPCStatus, Role
from scheduler import Channel, TaskState


def lookup_channel_as(router: IPCRouter, role: Role, uri: str) -> Channel:
    """Acquire a channel under a registered caller for spoofing or select tests."""
    scheduler = router.scheduler
    probe = scheduler.get_task(scheduler.spawn("lookup_probe", role=role))
    assert probe is not None
    scheduler.detach(probe)
    try:
        with scheduler.task_context(probe):
            status, channel = router.lookup(uri)
            assert status == IPCStatus.COMPLETED and channel is not None
            return channel
    finally:
        probe.state = TaskState.TERMINATED
