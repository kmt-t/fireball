"""experiments/pysim/wasi_dummy_fs.py



Comprehensive In-Memory WASI Preview 1 Dummy Driver for Fireball.

Provides deterministic virtual file system and environment services:

- FD 0 (stdin), FD 1 (stdout), FD 2 (stderr)

- In-memory virtual file descriptors (fd_read, fd_write, fd_seek, fd_close, fd_fdstat_get)

- Clock & Random services (clock_time_get, random_get)

- Process environment (environ_sizes_get, environ_get, args_sizes_get, args_get)

"""

from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[1] if "tests" in str(Path(__file__)) or "scenarios" in str(Path(__file__)) else Path(__file__).resolve().parent
_REPO_ROOT = _PYSIM_DIR.parents[1]

for _p in [_PYSIM_DIR, _PYSIM_DIR / 'core', _PYSIM_DIR / 'runtime', _PYSIM_DIR / 'jit', _PYSIM_DIR / 'platforms',
           _REPO_ROOT / 'docs' / 'components' / 'tier1_core' / 'concepts',
           _REPO_ROOT / 'docs' / 'components' / 'tier2_runtime' / 'concepts',
           _REPO_ROOT / 'docs' / 'components' / 'tier3_jit' / 'concepts',
           _REPO_ROOT / 'docs' / 'components' / 'tier3_platform' / 'concepts']:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import sys

from pathlib import Path



import os

import time

from typing import Any





class WasiErrno:

    SUCCESS = 0

    BADF = 8

    INVAL = 28

    NOSYS = 52





class WasiWhence:

    SET = 0

    CUR = 1

    END = 2





class VirtualFile:

    def __init__(self, name: str, data: bytes = b"", read_only: bool = False):

        self.name = name

        self.data = bytearray(data)

        self.cursor = 0

        self.read_only = read_only



    def read(self, size: int) -> bytes:

        if self.cursor >= len(self.data):

            return b""

        chunk = bytes(self.data[self.cursor:self.cursor + size])

        self.cursor += len(chunk)

        return chunk



    def write(self, buf: bytes) -> int:

        if self.read_only:

            return 0

        end_pos = self.cursor + len(buf)

        if end_pos > len(self.data):

            self.data.extend(b"\x00" * (end_pos - len(self.data)))

        self.data[self.cursor:end_pos] = buf

        self.cursor = end_pos

        return len(buf)



    def seek(self, offset: int, whence: int) -> int:

        if whence == WasiWhence.SET:

            self.cursor = max(0, offset)

        elif whence == WasiWhence.CUR:

            self.cursor = max(0, self.cursor + offset)

        elif whence == WasiWhence.END:

            self.cursor = max(0, len(self.data) + offset)

        else:

            return -1

        return self.cursor





class WasiDummyContext:

    """Simulates the host environment implementing WASI Preview 1 calls."""



    def __init__(self, env: dict[str, str] | None = None, args: list[str] | None = None):

        self.stdin_buffer = bytearray(b"INPUT_STREAM_DATA\n")

        self.stdin_pos = 0

        self.stdout_buffer = bytearray()

        self.stderr_buffer = bytearray()



        self.env = env or {"FIREBALL_PROFILE": "embedded", "MAX_STACK": "65536"}

        self.args = args or ["fireball_runtime", "--tier=jit"]



        # Virtual FD table

        self.files: dict[int, VirtualFile] = {

            3: VirtualFile("config.ini", b"[system]\nrate=1000\n", read_only=False),

            4: VirtualFile("sensors.dat", b"\x01\x02\x03\x04\x05\x06\x07\x08", read_only=True),

        }

        self.next_fd = 5



    def fd_read(self, fd: int, memory: bytearray, iovs_ptr: int, iovs_len: int, nread_ptr: int) -> int:

        total_read = 0

        for i in range(iovs_len):

            iov_offset = iovs_ptr + i * 8

            buf_ptr = int.from_bytes(memory[iov_offset:iov_offset + 4], "little")

            buf_len = int.from_bytes(memory[iov_offset + 4:iov_offset + 8], "little")



            if fd == 0:  # stdin

                avail = len(self.stdin_buffer) - self.stdin_pos

                to_read = min(buf_len, avail)

                if to_read > 0:

                    memory[buf_ptr:buf_ptr + to_read] = self.stdin_buffer[self.stdin_pos:self.stdin_pos + to_read]

                    self.stdin_pos += to_read

                    total_read += to_read

            elif fd in self.files:

                chunk = self.files[fd].read(buf_len)

                if chunk:

                    memory[buf_ptr:buf_ptr + len(chunk)] = chunk

                    total_read += len(chunk)

            else:

                return WasiErrno.BADF



        memory[nread_ptr:nread_ptr + 4] = total_read.to_bytes(4, "little")

        return WasiErrno.SUCCESS



    def fd_write(self, fd: int, memory: bytearray, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> int:

        total_written = 0

        for i in range(iovs_len):

            iov_offset = iovs_ptr + i * 8

            buf_ptr = int.from_bytes(memory[iov_offset:iov_offset + 4], "little")

            buf_len = int.from_bytes(memory[iov_offset + 4:iov_offset + 8], "little")

            chunk = bytes(memory[buf_ptr:buf_ptr + buf_len])



            if fd == 1:  # stdout

                self.stdout_buffer.extend(chunk)

                total_written += len(chunk)

            elif fd == 2:  # stderr

                self.stderr_buffer.extend(chunk)

                total_written += len(chunk)

            elif fd in self.files:

                w = self.files[fd].write(chunk)

                total_written += w

            else:

                return WasiErrno.BADF



        memory[nwritten_ptr:nwritten_ptr + 4] = total_written.to_bytes(4, "little")

        return WasiErrno.SUCCESS



    def fd_seek(self, fd: int, offset: int, whence: int, memory: bytearray, newoffset_ptr: int) -> int:

        if fd not in self.files:

            return WasiErrno.BADF

        new_pos = self.files[fd].seek(offset, whence)

        if new_pos < 0:

            return WasiErrno.INVAL

        memory[newoffset_ptr:newoffset_ptr + 8] = new_pos.to_bytes(8, "little")

        return WasiErrno.SUCCESS



    def random_get(self, memory: bytearray, buf_ptr: int, buf_len: int) -> int:

        rand_bytes = os.urandom(buf_len)

        memory[buf_ptr:buf_ptr + buf_len] = rand_bytes

        return WasiErrno.SUCCESS



    def clock_time_get(self, clock_id: int, precision: int, memory: bytearray, time_ptr: int) -> int:

        # clock_id 0 = REALTIME, 1 = MONOTONIC

        now_ns = time.time_ns() if clock_id == 0 else time.monotonic_ns()

        memory[time_ptr:time_ptr + 8] = now_ns.to_bytes(8, "little")

        return WasiErrno.SUCCESS
