"""Static platform-driver composition for pysim."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tier1_core.printk import Printk, PrintkWriter
from tier3_platform.drivers.debugger.transport import DebuggerSink
from tier3_platform.drivers.hal.stream import StreamTransport
from tier3_platform.drivers.printk import PrintkBuffer, PrintkSink
from tier3_platform.drivers.wasi.uvwasi import WasiPreview1Backend, create_uvwasi_backend


@dataclass(frozen=True, slots=True)
class PlatformDriverConfiguration:
    """Compile-time-style dependency bundle for one pysim platform build.

    ``printk`` and ``debugger_sink`` are independent physical endpoints;
    neither is implicitly redirected to the guest stdout transport.
    """

    stdout_transport: StreamTransport
    printk: Printk
    wasi_backend: WasiPreview1Backend
    debugger_sink: DebuggerSink | None = None


def create_default_platform_drivers(
    decode_record: Callable[[memoryview], str],
    printk_sink: PrintkWriter | None = None,
    debugger_sink: DebuggerSink | None = None,
) -> PlatformDriverConfiguration:
    """Create the default static driver composition with replaceable sinks."""
    stdout_transport = StreamTransport()
    selected_printk = printk_sink if printk_sink is not None else PrintkBuffer()
    return PlatformDriverConfiguration(
        stdout_transport=stdout_transport,
        printk=PrintkSink(selected_printk, decode_record),
        wasi_backend=create_uvwasi_backend(),
        debugger_sink=debugger_sink,
    )
