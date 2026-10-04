"""Test-only platform-driver configurations."""

from __future__ import annotations

from qa.shared.fixtures.uvwasi_reference import UvwasiReferenceContext
from tier3_platform.drivers.hal.stream import StreamTransport
from tier3_platform.drivers.platform_config import PlatformDriverConfiguration
from tier3_platform.drivers.printk import PrintkBuffer, PrintkSink


def create_reference_platform_drivers(
    backend: UvwasiReferenceContext | None = None,
) -> PlatformDriverConfiguration:
    """Create deterministic drivers without changing the product default."""
    stdout_transport = StreamTransport()
    selected_backend = backend if backend is not None else UvwasiReferenceContext()
    return PlatformDriverConfiguration(
        stdout_transport=stdout_transport,
        printk=PrintkSink(PrintkBuffer()),
        wasi_backend=selected_backend,
    )
