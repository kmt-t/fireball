"""

experiments/pysim/leb128.py



LEB128 varint encode/decode, shared by the binary reader and the test-module

builder (see wasm_builder.py -- there is no wat2wasm/wasmtime in this

sandbox, so binaries used for testing are synthesized directly in Python).

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



def decode_unsigned(data: bytes, offset: int) -> tuple[int, int]:

    """Returns (value, new_offset)."""

    result = 0

    shift = 0

    while True:

        byte = data[offset]

        offset += 1

        result |= (byte & 0x7F) << shift

        if byte & 0x80 == 0:

            return result, offset

        shift += 7





def decode_signed(data: bytes, offset: int) -> tuple[int, int]:

    """Returns (value, new_offset). Used for i32.const/i64.const (sleb128)."""

    result = 0

    shift = 0

    while True:

        byte = data[offset]

        offset += 1

        result |= (byte & 0x7F) << shift

        shift += 7

        if byte & 0x80 == 0:

            if byte & 0x40 and shift < 64:

                result |= -(1 << shift)

            return result, offset





def encode_unsigned(value: int) -> bytes:

    assert value >= 0

    out = bytearray()

    while True:

        byte = value & 0x7F

        value >>= 7

        if value != 0:

            out.append(byte | 0x80)

        else:

            out.append(byte)

            return bytes(out)





def encode_signed(value: int) -> bytes:

    out = bytearray()

    more = True

    while more:

        byte = value & 0x7F

        value >>= 7

        if (value == 0 and (byte & 0x40) == 0) or (value == -1 and (byte & 0x40) != 0):

            more = False

        else:

            byte |= 0x80

        out.append(byte)

    return bytes(out)
