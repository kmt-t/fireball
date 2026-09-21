"""Static platform-driver composition for pysim."""

from __future__ import annotations

from dataclasses import dataclass

from hal_dispatch import StreamSink
from tier3_platform.drivers.hal.bindings import DEFAULT_WASI_HAL_BINDINGS
from tier3_platform.drivers.hal.stream import StreamTransport
from tier3_platform.drivers.wasi.uvwasi import WasiPreview1Backend, create_uvwasi_backend
from wasi_bindings import WasiHalBindings


@dataclass(frozen=True, slots=True)
class PlatformDriverConfiguration:
    """Compile-time-style dependency bundle for one pysim platform build."""

    wasi_hal_bindings: WasiHalBindings
    stdout_transport: StreamTransport
    logger_transport: StreamSink
    wasi_backend: WasiPreview1Backend


def create_default_platform_drivers(logger_transport: StreamSink | None = None) -> PlatformDriverConfiguration:
    """Create the default static driver composition."""
    stdout_transport = StreamTransport()
    selected_logger = logger_transport if logger_transport is not None else stdout_transport
    return PlatformDriverConfiguration(
        wasi_hal_bindings=DEFAULT_WASI_HAL_BINDINGS,
        stdout_transport=stdout_transport,
        logger_transport=selected_logger,
        wasi_backend=create_uvwasi_backend(),
    )
