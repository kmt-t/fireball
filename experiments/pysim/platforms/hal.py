"""

experiments/pysim/hal.py



Real (not mocked) HAL underlayer for the pysim experiment.



- UartTransport: a genuine OS-level byte pipe (socket.socketpair), standing

  in for the physical UART/ITM line. Bytes written here really cross a

  kernel-buffered duplex socket, so a full/blocked transport is an actual

  socket condition, not an in-memory flag someone forgot to flip.

- ShmBufferPool: acquire_buffer()/release_buffer() backed by a plain

  bytearray per slot. The point being tested -- "a guest can only touch a

  buffer via a handle the pool has authorized, never via a raw pointer" --

  is a property of the *lookup discipline* (every access goes through

  _resolve()'s ownership/bounds check), not of the byte storage being real

  OS shared memory, so a bytearray proves it exactly as well without the

  extra process-boundary machinery.

- Timer: wall-clock timer via time.monotonic_ns(), matching

  wasi:clocks/monotonic-clock's nanosecond contract.



This intentionally sets aside C++ naming/type conventions and is not wired

into the C++ build. It exists to pressure-test whether the *design* in

docs/components/tier1_interface/interface_wit.md and

docs/components/tier3_platform/platform_hal.md actually holds together when

something has to really run.

"""

from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[1] if any(d in str(Path(__file__)) for d in ("tests", "scenarios", "core", "runtime", "jit", "platforms")) else Path(__file__).resolve().parent

for _p in [_PYSIM_DIR, _PYSIM_DIR / 'core', _PYSIM_DIR / 'runtime', _PYSIM_DIR / 'jit', _PYSIM_DIR / 'platforms']:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import sys

from pathlib import Path



import socket

import threading

import time

import uuid

from dataclasses import dataclass, field





class HalError(Exception):

    """Base class for HAL-level failures that must map to a recovery-strategy-category."""





class ShmTrap(HalError):

    """A guest touched a shared-memory handle it does not own, or a slice

    escaped the handle's acquired bounds. Mirrors runtime_vmmio.md 4.6's

    vMMIO PTE ownership trap -- a real MMU would fault here."""





# ---------------------------------------------------------------------------

# UART / console transport

# ---------------------------------------------------------------------------



class UartTransport:

    """One physical serial line, modeled as a real duplex OS socket pair.



    `device_sock` is the "wire" a real UART peripheral would drive;

    `host_sock` is what a host-side terminal/log collector reads from.

    Nothing here is a Python list standing in for hardware: bytes written

    via write() genuinely traverse a kernel socket buffer.

    """



    def __init__(self):

        self.device_sock, self.host_sock = socket.socketpair()

        self.device_sock.settimeout(0.2)

        self.host_sock.settimeout(0.2)

        self._lock = threading.Lock()

        self.bytes_written = 0



    def write(self, data: bytes) -> int:

        """Physical transmit: blocks on the real socket buffer if full."""

        with self._lock:

            n = self.device_sock.send(data)

            self.bytes_written += n

            return n



    def drain(self) -> bytes:

        """Host-side read of everything currently sitting on the wire."""

        chunks: list[bytes] = []

        try:

            while True:

                chunk = self.host_sock.recv(4096)

                if not chunk:

                    break

                chunks.append(chunk)

                if len(chunk) < 4096:

                    break

        except (BlockingIOError, socket.timeout):

            pass

        return b"".join(chunks)



    def close(self) -> None:

        self.device_sock.close()

        self.host_sock.close()





# ---------------------------------------------------------------------------

# Shared-memory buffer pool (the HAL / vMMIO SHM region)

# ---------------------------------------------------------------------------



FB_CONF_HAL_BUFFER_SIZE = 256   # docs/components/tier1_core/system_config.md 3.3.3

FB_CONF_HAL_MAX_BUFFERS = 4     # docs/components/tier1_core/system_config.md 3.3.3





@dataclass

class ShmHandle:

    """What acquire_buffer() actually returns: an opaque *name*, not a

    pointer. Handing this value to code running as a different owner is

    meaningless -- there is no address inside it that could be dereferenced

    as guest linear memory, only a lookup key the pool checks against an

    owner table before it will hand back a byte."""

    name: str

    owner_task: int

    capacity: int

    _storage: bytearray = field(repr=False, compare=False)





class ShmBufferPool:

    """`acquire_buffer()` backed by FB_CONF_HAL_MAX_BUFFERS fixed-size slots

    of at most FB_CONF_HAL_BUFFER_SIZE bytes each: a static pool, not a

    dynamic allocator (platform_hal.md 5.1's "静的固定長バッファプール").

    """



    def __init__(self):

        self._slots: list[ShmHandle | None] = [None] * FB_CONF_HAL_MAX_BUFFERS



    def acquire_buffer(self, task_id: int, size: int) -> ShmHandle:

        if size <= 0 or size > FB_CONF_HAL_BUFFER_SIZE:

            raise ValueError(

                f"acquire_buffer(size={size}) exceeds FB_CONF_HAL_BUFFER_SIZE={FB_CONF_HAL_BUFFER_SIZE}"

            )

        slot_idx = -1

        for i, s in enumerate(self._slots):

            if s is None:

                slot_idx = i

                break

        if slot_idx < 0:

            raise HalError("HAL buffer pool exhausted (FB_CONF_HAL_MAX_BUFFERS)")



        name = f"fb_shm_{uuid.uuid4().hex[:12]}"

        handle = ShmHandle(name=name, owner_task=task_id, capacity=size, _storage=bytearray(size))

        self._slots[slot_idx] = handle

        return handle



    def release_buffer(self, task_id: int, handle: ShmHandle) -> None:

        for i, s in enumerate(self._slots):

            if s is not None and s.name == handle.name:

                if s.owner_task != task_id:

                    raise ShmTrap(f"task {task_id} cannot release {handle.name}: not the owner")

                self._slots[i] = None

                return

        raise ShmTrap(f"task {task_id} cannot release {handle.name}: not found")



    def _resolve(self, task_id: int, handle: ShmHandle) -> ShmHandle:

        for s in self._slots:

            if s is not None and s.name == handle.name:

                if s.owner_task != task_id:

                    raise ShmTrap(

                        f"task {task_id} does not own {handle.name} (owner={s.owner_task}); "

                        "no linear-memory pointer would ever bypass this check"

                    )

                return s

        raise ShmTrap(f"handle {handle.name} does not exist (stale, or never acquired)")



    def close_all(self) -> None:

        for i in range(len(self._slots)):

            self._slots[i] = None



    def view(self, task_id: int, handle: ShmHandle, offset: int, length: int) -> memoryview:

        """Resolves a bounds-checked (offset, length) window inside `handle`.



        This is what interface_wit.md 5.3's `shm-slice{handle, offset, len}`

        actually resolves to at the HAL layer.

        """

        record = self._resolve(task_id, handle)

        if offset < 0 or length < 0 or offset + length > record.capacity:

            raise ShmTrap(

                f"shm-slice(offset={offset}, len={length}) escapes {handle.name}'s "

                f"acquired capacity ({record.capacity} bytes)"

            )

        return memoryview(record._storage)[offset: offset + length]





# ---------------------------------------------------------------------------

# Timer

# ---------------------------------------------------------------------------



class Timer:

    """wasi:clocks/monotonic-clock, backed by the real system clock."""



    def get_now_ns(self) -> int:

        return time.monotonic_ns()



    def subscribe(self, nanos: int, callback) -> threading.Timer:

        t = threading.Timer(nanos / 1e9, callback)

        t.daemon = True

        t.start()

        return t
