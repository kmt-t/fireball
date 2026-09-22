"""Static platform-driver composition for pysim."""

from __future__ import annotations

from dataclasses import dataclass

from hal_dispatch import StreamSink
from tier3_platform.drivers.debugger.transport import DebuggerSink
from tier3_platform.drivers.hal.bindings import DEFAULT_WASI_HAL_BINDINGS
from tier3_platform.drivers.hal.stream import DedicatedLogSink, StreamTransport
from tier3_platform.drivers.wasi.uvwasi import WasiPreview1Backend, create_uvwasi_backend
from wasi_bindings import WasiHalBindings


@dataclass(frozen=True, slots=True)
class PlatformDriverConfiguration:
    """Compile-time-style dependency bundle for one pysim platform build.

    ``logger_sink`` and ``debugger_sink`` are independent physical endpoints;
    neither is implicitly redirected to the guest stdout transport.
    """

    wasi_hal_bindings: WasiHalBindings
    stdout_transport: StreamTransport
    logger_sink: StreamSink
    wasi_backend: WasiPreview1Backend
    debugger_sink: DebuggerSink | None = None


def create_default_platform_drivers(
    logger_sink: StreamSink | None = None,
    debugger_sink: DebuggerSink | None = None,
) -> PlatformDriverConfiguration:
    """Create the default static driver composition with replaceable sinks."""
    stdout_transport = StreamTransport()
    selected_logger = logger_sink if logger_sink is not None else DedicatedLogSink()
    return PlatformDriverConfiguration(
        wasi_hal_bindings=DEFAULT_WASI_HAL_BINDINGS,
        stdout_transport=stdout_transport,
        logger_sink=selected_logger,
        wasi_backend=create_uvwasi_backend(),
        debugger_sink=debugger_sink,
    )
