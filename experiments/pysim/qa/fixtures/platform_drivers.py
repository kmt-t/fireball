"""Test-only platform-driver configurations."""

from __future__ import annotations

from tier3_platform.drivers.platform_config import PlatformDriverConfiguration
from tier3_platform.drivers.hal.bindings import DEFAULT_WASI_HAL_BINDINGS
from tier3_platform.drivers.hal.stream import DedicatedLogSink, StreamTransport
from fixtures.uvwasi_reference import UvwasiReferenceContext


def create_reference_platform_drivers(
    backend: UvwasiReferenceContext | None = None,
) -> PlatformDriverConfiguration:
    """Create deterministic drivers without changing the product default."""
    stdout_transport = StreamTransport()
    selected_backend = backend if backend is not None else UvwasiReferenceContext()
    return PlatformDriverConfiguration(
        wasi_hal_bindings=DEFAULT_WASI_HAL_BINDINGS,
        stdout_transport=stdout_transport,
        logger_sink=DedicatedLogSink(),
        wasi_backend=selected_backend,
    )
