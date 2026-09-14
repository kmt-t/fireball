"""
Fireball System Container Vocabulary.

The lookup families are deliberately defined as nine classes:
ReadOnly and Mutable Storage for FlatSet, FlatMap, and RadixBinaryTree, plus
one read-only borrowing View for each family. Storage owns the fixed backing
data and maintains ordering; View borrows that data and performs lookup and
narrowing. Bit storage, RingBuffer, and StaticVector are separate
dense/sequential containers.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from config import JIT_CARD_SHIFT

KeyT = TypeVar("KeyT")
ValT = TypeVar("ValT")
T = TypeVar("T")
ALLOWED_BITS = (1, 2, 4)

# ---------------------------------------------------------------------------
# 1. BitView (fireball::bit_view<Bits>)
# ---------------------------------------------------------------------------


class BitView:
    """
    bit_view<Bits>: a dense, index-addressed table of sub-byte states.
        GOTCHA-CONT-01: Bits must strictly divide 8 (1, 2, or 4) so that an element
        never straddles a byte boundary, ensuring atomic single-byte load/mask.
    """

    __slots__ = ("bits", "count", "origin", "storage")

    def __init__(self, storage: memoryview, bits: int, origin: int = 0, count: int = 0):
        if bits != 1 and bits != 2 and bits != 4:
            assert False, f"Bits must be 1, 2 or 4 (got {bits})"
        self.storage = storage
        self.bits = bits
        self.origin = origin  # bit offset of logical element 0
        self.count = count

    def size(self) -> int:
        return self.count

    def __len__(self) -> int:
        return self.count

    def _bit_pos(self, i: int) -> int:
        if not (0 <= i < self.count):
            assert False, f"index {i} outside bit_view of size {self.count}"
        return self.origin + i * self.bits

    def at(self, i: int) -> int:
        bit = self._bit_pos(i)
        mask = (1 << self.bits) - 1
        return (self.storage[bit >> 3] >> (bit & 7)) & mask

    def put(self, i: int, value: int) -> None:
        mask = (1 << self.bits) - 1
        if not (0 <= value <= mask):
            assert False, f"value {value} does not fit in {self.bits} bits (max {mask})"
        bit = self._bit_pos(i)
        byte_idx, shift = bit >> 3, bit & 7
        cleared = self.storage[byte_idx] & ~(mask << shift) & 0xFF
        self.storage[byte_idx] = cleared | ((value & mask) << shift)

    def slice(self, first: int, last: int) -> BitView:
        """
        Narrow by index. The bit origin absorbs the remainder, so `first`
                does not have to land on a byte boundary.
        """

        if not (0 <= first <= last <= self.count):
            assert False, (
                f"a view may only ever shrink (0 <= {first} <= {last} <= {self.count})"
            )
        return BitView(self.storage, self.bits, self.origin + first * self.bits, last - first)


class ReadOnlyBitStorage:
    """
    fireball::read_only_bit_storage<Bits, Count>:
    Immutable packed bit storage owning read-only bytes buffer ({Type_Vocabulary}, {GLOBAL_Policy_Memory}).
    """

    __slots__ = ("_buffer", "bits", "count")

    def __init__(self, buffer: bytes, bits: int, count: int):
        if bits != 1 and bits != 2 and bits != 4:
            assert False, f"Bits must be 1, 2 or 4 (got {bits})"
        self._buffer = buffer
        self.bits = bits
        self.count = count

    @property
    def buffer(self) -> bytes:
        return self._buffer

    def at(self, index: int) -> int:
        """Read one packed element without creating a borrowing view."""

        assert 0 <= index < self.count
        bit = index * self.bits
        mask = (1 << self.bits) - 1
        return (self._buffer[bit >> 3] >> (bit & 7)) & mask

    def view(self, origin: int = 0, count: int | None = None) -> BitView:
        return BitView(
            self._buffer, self.bits, origin=origin, count=count if count is not None else self.count
        )


class MutableBitStorage:
    """
    fireball::mutable_bit_storage<Bits, Count>:
    Mutable packed bit storage owning read-write bytearray buffer ({Type_Vocabulary}, {GLOBAL_Policy_Memory}).
    All element-level mutation operations (put, fill, clear) are performed strictly here, not in the non-owning BitView.
    """

    __slots__ = ("_buffer", "bits", "count")

    def __init__(self, count: int, bits: int = 1, default: int = 0):
        if bits != 1 and bits != 2 and bits != 4:
            assert False, f"Bits must be 1, 2 or 4 (got {bits})"
        self.count = count
        self.bits = bits
        total_bits = count * bits
        num_bytes = (total_bits + 7) // 8
        self._buffer = bytearray(num_bytes)
        if default != 0:
            self.fill(default)

    @property
    def buffer(self) -> bytearray:
        return self._buffer

    def put(self, i: int, value: int) -> None:
        mask = (1 << self.bits) - 1
        if not (0 <= value <= mask):
            assert False, f"value {value} does not fit in {self.bits} bits (max {mask})"
        if not (0 <= i < self.count):
            assert False, f"index {i} outside mutable_bit_storage of size {self.count}"
        bit = i * self.bits
        byte_idx, shift = bit >> 3, bit & 7
        cleared = self._buffer[byte_idx] & ~(mask << shift) & 0xFF
        self._buffer[byte_idx] = cleared | ((value & mask) << shift)

    def fill(self, value: int) -> None:
        mask = (1 << self.bits) - 1
        val = value & mask
        if self.bits == 1:
            byte_val = 0xFF if val else 0x00
        elif self.bits == 2:
            byte_val = (val << 6) | (val << 4) | (val << 2) | val
        else:  # 4
            byte_val = (val << 4) | val
        for i in range(len(self._buffer)):
            self._buffer[i] = byte_val

    def clear(self) -> None:
        self.fill(0)

    def view(self, origin: int = 0, count: int | None = None) -> BitView:
        return BitView(
            self._buffer, self.bits, origin=origin, count=count if count is not None else self.count
        )


 # ---------------------------------------------------------------------------
# 2. ReadOnly storage/view matrix: FlatSet
# ---------------------------------------------------------------------------


@dataclass
class ReadOnlyFlatSetStorage(Generic[KeyT]):
    """Owns an immutable, sorted, duplicate-free flat-set sequence."""

    keys: tuple[KeyT, ...]

    @classmethod
    def create(cls, keys: Sequence[KeyT]) -> ReadOnlyFlatSetStorage[KeyT]:
        sorted_keys = sorted(keys)
        unique_keys: list[KeyT] = []
        for key in sorted_keys:
            if not unique_keys or unique_keys[-1] != key:
                unique_keys.append(key)
        return cls(keys=tuple(unique_keys))

    def view(self) -> ReadOnlyFlatSetView[KeyT]:
        return ReadOnlyFlatSetView(self.keys)


class ReadOnlyFlatSetView(Generic[KeyT]):
    """Non-owning sorted-set view; membership and narrowing live here."""

    __slots__ = ("_keys", "_last", "first")

    def __init__(
        self,
        keys: Sequence[KeyT],
        first: int = 0,
        last: int | None = None,
    ):
        self._keys = keys
        self.first = first
        self._last = last

    @property
    def last(self) -> int:
        return len(self._keys) if self._last is None else min(self._last, len(self._keys))

    @property
    def keys(self) -> Sequence[KeyT]:
        if self.first == 0 and self.last == len(self._keys):
            return self._keys
        return tuple(self._keys[index] for index in range(self.first, self.last))

    def size(self) -> int:
        return max(0, self.last - self.first)

    def __len__(self) -> int:
        return self.size()

    def empty(self) -> bool:
        return self.size() == 0

    def _bounds(self, lo: KeyT, hi: KeyT) -> tuple[int, int]:
        first = bisect.bisect_left(self._keys, lo, self.first, self.last)
        last = bisect.bisect_right(self._keys, hi, self.first, self.last)
        return first, last

    def _locate(self, key: KeyT) -> int | None:
        i = bisect.bisect_left(self._keys, key, self.first, self.last)
        return i if i < self.last and self._keys[i] == key else None

    def slice(self, first: int, last: int) -> ReadOnlyFlatSetView[KeyT]:
        assert self.first <= first <= last <= self.last
        return ReadOnlyFlatSetView(self._keys, first, last)

    def narrow(self, lo: KeyT, hi: KeyT) -> ReadOnlyFlatSetView[KeyT]:
        return ReadOnlyFlatSetView(self._keys, *self._bounds(lo, hi))

    def contains(self, key: KeyT) -> bool:
        return self._locate(key) is not None

    def __contains__(self, key: KeyT) -> bool:
        return self.contains(key)


# ---------------------------------------------------------------------------
# 3. ReadOnly storage/view matrix: FlatMap
# ---------------------------------------------------------------------------


@dataclass
class ReadOnlyFlatMapStorage(Generic[KeyT, ValT]):
    """Owns an immutable, sorted flat-map sequence."""

    entries: tuple[tuple[KeyT, ValT], ...]

    @classmethod
    def create(cls, entries: Sequence[tuple[KeyT, ValT]]) -> ReadOnlyFlatMapStorage[KeyT, ValT]:
        return cls(entries=tuple(sorted(entries, key=lambda entry: entry[0])))

    def view(self) -> ReadOnlyFlatMapView[KeyT, ValT]:
        return ReadOnlyFlatMapView(self.entries)


class ReadOnlyFlatMapView(Generic[KeyT, ValT]):
    """
    flat_map_view<Key, Value>: non-owning view over an externally owned sorted array of (key, value) pairs (AoS).
    Narrow-then-search returns a value with O(log N) binary search on the entry key.
    The view holds only a borrowed reference to the entries sequence (span of 1 range, 2 words in C++).
    """

    __slots__ = ("_entries", "_last", "first")

    def __init__(
        self,
        entries: Sequence[tuple[KeyT, ValT]],
        first: int = 0,
        last: int | None = None,
    ):
        self._entries = entries
        self.first = first
        self._last = last

    @property
    def last(self) -> int:
        return len(self._entries) if self._last is None else min(self._last, len(self._entries))

    @property
    def entries(self) -> Sequence[tuple[KeyT, ValT]]:
        if self.first == 0 and self.last == len(self._entries):
            return self._entries
        # Mutable storage is an integer-indexed fixed-capacity sequence, not
        # a Python sliceable container. Materialize only a narrowed view;
        # the full view remains a zero-copy borrow.
        return tuple(self._entries[index] for index in range(self.first, self.last))

    @property
    def keys(self) -> list[KeyT]:
        return tuple(self._entries[index][0] for index in range(self.first, self.last))

    @property
    def values(self) -> list[ValT]:
        return tuple(self._entries[index][1] for index in range(self.first, self.last))

    def size(self) -> int:
        return self.last - self.first

    def empty(self) -> bool:
        return self.size() == 0

    def _bounds(self, lo: KeyT, hi: KeyT) -> tuple[int, int]:
        return (
            bisect.bisect_left(self._entries, lo, self.first, self.last, key=lambda e: e[0]),
            bisect.bisect_right(self._entries, hi, self.first, self.last, key=lambda e: e[0]),
        )

    def _locate(self, key: KeyT) -> int | None:
        i = bisect.bisect_left(self._entries, key, self.first, self.last, key=lambda e: e[0])
        return i if i < self.last and self._entries[i][0] == key else None

    def slice(self, first: int, last: int) -> ReadOnlyFlatMapView[KeyT, ValT]:
        assert self.first <= first <= last <= self.last
        return ReadOnlyFlatMapView(self._entries, first, last)

    def narrow(self, lo: KeyT, hi: KeyT) -> ReadOnlyFlatMapView[KeyT, ValT]:
        lo_idx, hi_idx = self._bounds(lo, hi)
        return ReadOnlyFlatMapView(self._entries, lo_idx, hi_idx)

    def find(self, key: KeyT) -> ValT | None:
        """Binary search inside the current window only (O(log N))."""
        i = self._locate(key)
        return None if i is None else self._entries[i][1]

    def find_index(self, key: KeyT) -> int:
        """Binary search returning index of key or -1 if not found (O(log N))."""
        i = self._locate(key)
        return -1 if i is None else i

    def __getitem__(self, key: KeyT) -> ValT:
        val = self.find(key)
        assert val is not None, key
        return val

    def __contains__(self, key: KeyT) -> bool:
        return self._locate(key) is not None

    def __len__(self) -> int:
        return self.size()


# ---------------------------------------------------------------------------
# 4. Radix table and ReadOnly storage/view matrix: RadixBinaryTree
# ---------------------------------------------------------------------------


def bswap32(v: int) -> int:
    """32-bit byte-order reversal for maximizing Radix table distribution on UnifiedPC."""
    return ((v & 0xFF) << 24) | ((v & 0xFF00) << 8) | ((v >> 8) & 0xFF00) | ((v >> 24) & 0xFF)


FB_CONF_MAX_RADIX_TABLE_SIZE = 256  # Embedded constraint: max 8-bit radix prefix


def build_radix_table(
    keys: Sequence[int],
    radix_shift: int,
    key_transform: Callable[[int], int] | None = None,
) -> list[int]:
    """
    Constructs a scalar radix offset table for radix_binary_tree_view.
    Prefix bounds: bucket p is [table[p], table[p+1]).
    Strictly bounded by FB_CONF_MAX_RADIX_TABLE_SIZE.
    """
    if not keys:
        return [0, 0]
    sorted_keys = sorted(keys)
    transformed = [key_transform(k) if key_transform is not None else k for k in sorted_keys]
    max_prefix = max(transformed) >> radix_shift
    table_size = max_prefix + 2
    assert table_size <= FB_CONF_MAX_RADIX_TABLE_SIZE, (
        f"Radix table size ({table_size}) exceeds embedded limit {FB_CONF_MAX_RADIX_TABLE_SIZE}! Adjust radix_shift."
    )
    table = [0] * table_size
    current_prefix = 0
    for idx, k in enumerate(transformed):
        prefix = k >> radix_shift
        while current_prefix < prefix:
            current_prefix += 1
            table[current_prefix] = idx
    for p in range(current_prefix + 1, table_size):
        table[p] = len(keys)
    return table


@dataclass
class ReadOnlyRadixBinaryTreeStorage(Generic[ValT]):
    """
    Owns sorted entries and the radix prefix table.
    Owns memory buffers for sorted keys, values, and radix_table.
    Strictly separates storage ownership from non-owning view borrows ({Type_Vocabulary}, {GLOBAL_Policy_Memory}).
    """

    keys: tuple[int, ...]
    values: tuple[ValT, ...]
    radix_table: tuple[int, ...]
    radix_shift: int
    entries: tuple[tuple[int, ValT], ...]
    key_transform: Callable[[int], int] | None = None

    @classmethod
    def create(
        cls,
        keys: Sequence[int],
        values: Sequence[ValT],
        radix_shift: int = 28,
        key_transform: Callable[[int], int] | None = None,
    ) -> ReadOnlyRadixBinaryTreeStorage[ValT]:
        paired = tuple(sorted(zip(keys, values, strict=False), key=lambda p: p[0]))
        s_keys = tuple(p[0] for p in paired)
        s_vals = tuple(p[1] for p in paired)
        table = tuple(build_radix_table(s_keys, radix_shift=radix_shift, key_transform=key_transform))
        return cls(
            keys=s_keys,
            values=s_vals,
            radix_table=table,
            radix_shift=radix_shift,
            entries=paired,
            key_transform=key_transform,
        )

    def view(self) -> ReadOnlyRadixBinaryTreeView[ValT]:
        """Borrows a non-owning view over this storage without copying."""
        return ReadOnlyRadixBinaryTreeView(
            entries=self.entries,
            radix_table=self.radix_table,
            radix_shift=self.radix_shift,
            key_transform=self.key_transform,
        )


class ReadOnlyRadixBinaryTreeView(Generic[ValT]):
    """
    fireball::radix_binary_tree_view<Key, Value, RadixShift, KeyProjection>:
        Combines an O(1) Radix Table (coarse prefix lookup) with bounded local
        binary search on a sorted key-value array. Supports optional KeyProjection
        (such as bswap32) to project high-entropy lower bytes to radix prefix.
        Non-owning view: borrows references to external storage without taking ownership.
    """

    __slots__ = ("entries", "key_transform", "map_view", "radix_shift", "radix_table")

    def __init__(
        self,
        entries: Sequence[tuple[int, ValT]],
        radix_table: Sequence[int],
        radix_shift: int,
        key_transform: Callable[[int], int] | None = None,
    ):
        assert len(radix_table) <= FB_CONF_MAX_RADIX_TABLE_SIZE, (
            f"Radix table size ({len(radix_table)}) exceeds embedded limit {FB_CONF_MAX_RADIX_TABLE_SIZE}!"
        )
        self.entries = entries
        self.map_view = ReadOnlyFlatMapView(entries)
        self.radix_table = radix_table
        self.radix_shift = radix_shift
        self.key_transform = key_transform

    def find(self, key: int) -> ValT | None:
        rk = self.key_transform(key) if self.key_transform is not None else key
        prefix = rk >> self.radix_shift
        if prefix < 0 or prefix + 1 >= len(self.radix_table):
            return None
        first = self.radix_table[prefix]
        last = self.radix_table[prefix + 1]
        if first >= last:
            return None
        return self.map_view.slice(first, last).find(key)

    def find_interval(self, offset: int) -> ValT | None:
        """
        Range lookup for interval keys [start, end) -- finds entity where entity.start_offset <= offset < entity.end_offset.
        """
        if not self.entries:
            return None
        idx = bisect.bisect_right(self.entries, offset, key=lambda entry: entry[0]) - 1
        if 0 <= idx < len(self.entries):
            entity = self.entries[idx][1]
            try:
                if entity.start_offset <= offset < entity.end_offset:
                    return entity
            except AttributeError:
                pass
        return None


# ---------------------------------------------------------------------------
# 5. Mutable storage: FlatSet (view() returns ReadOnlyFlatSetView)
# ---------------------------------------------------------------------------


class MutableFlatSetStorage(Generic[KeyT]):
    """Owns a fixed-capacity flat set and keeps active keys sorted."""

    __slots__ = ("_buffer", "_count", "capacity")

    def __init__(self, capacity: int = 32):
        assert capacity >= 0
        self.capacity = capacity
        self._buffer: list[KeyT | None] = [None] * capacity
        self._count = 0

    def size(self) -> int:
        return self._count

    def __len__(self) -> int:
        return self._count

    def __iter__(self) -> Iterator[KeyT]:
        for index in range(self._count):
            yield self[index]

    @property
    def count(self) -> int:
        return self._count

    def __getitem__(self, index: int) -> KeyT:
        if index < 0:
            index += self._count
        assert 0 <= index < self._count, index
        key = self._buffer[index]
        assert key is not None
        return key

    def view(self) -> ReadOnlyFlatSetView[KeyT]:
        return ReadOnlyFlatSetView(self)

    def insert(self, key: KeyT) -> bool:
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda value: value if value is not None else key
        )
        if idx < self._count and self._buffer[idx] == key:
            return True
        if self._count >= self.capacity:
            return False
        for index in range(self._count, idx, -1):
            self._buffer[index] = self._buffer[index - 1]
        self._buffer[idx] = key
        self._count += 1
        return True

    def remove(self, key: KeyT) -> bool:
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda value: value if value is not None else key
        )
        if idx >= self._count or self._buffer[idx] != key:
            return False
        for index in range(idx, self._count - 1):
            self._buffer[index] = self._buffer[index + 1]
        self._buffer[self._count - 1] = None
        self._count -= 1
        return True

    def clear(self) -> None:
        for index in range(self._count):
            self._buffer[index] = None
        self._count = 0


# ---------------------------------------------------------------------------
# 6. Mutable storage: FlatMap (view() returns ReadOnlyFlatMapView)
# ---------------------------------------------------------------------------


class MutableFlatMapStorage(Generic[KeyT, ValT]):
    """Owns a fixed-capacity flat map and keeps active entries sorted by key."""

    __slots__ = ("_buffer", "_count", "capacity")

    def __init__(self, capacity: int = 32):
        assert capacity >= 0
        self.capacity = capacity
        self._buffer: list[tuple[KeyT, ValT] | None] = [None] * capacity
        self._count = 0

    def size(self) -> int:
        return self._count

    def __len__(self) -> int:
        return self._count

    def __iter__(self) -> Iterator[tuple[KeyT, ValT]]:
        for index in range(self._count):
            yield self[index]

    @property
    def count(self) -> int:
        return self._count

    def __getitem__(self, index: int) -> tuple[KeyT, ValT]:
        if index < 0:
            index += self._count
        assert 0 <= index < self._count, index
        entry = self._buffer[index]
        assert entry is not None
        return entry

    def view(self) -> ReadOnlyFlatMapView[KeyT, ValT]:
        return ReadOnlyFlatMapView(self)

    def insert(self, key: KeyT, value: ValT) -> bool:
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda entry: entry[0] if entry is not None else key
        )
        if idx < self._count and self._buffer[idx] is not None and self._buffer[idx][0] == key:
            self._buffer[idx] = (key, value)
            return True
        if self._count >= self.capacity:
            return False
        for index in range(self._count, idx, -1):
            self._buffer[index] = self._buffer[index - 1]
        self._buffer[idx] = (key, value)
        self._count += 1
        return True

    def remove(self, key: KeyT) -> ValT | None:
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda entry: entry[0] if entry is not None else key
        )
        if idx >= self._count or self._buffer[idx] is None or self._buffer[idx][0] != key:
            return None
        value = self._buffer[idx][1]
        for index in range(idx, self._count - 1):
            self._buffer[index] = self._buffer[index + 1]
        self._buffer[self._count - 1] = None
        self._count -= 1
        return value

    def clear(self) -> None:
        for index in range(self._count):
            self._buffer[index] = None
        self._count = 0

    def is_sorted(self) -> bool:
        return all(self[index][0] <= self[index + 1][0] for index in range(self._count - 1))


# ---------------------------------------------------------------------------
# 7. Mutable storage: RadixBinaryTree (view() returns ReadOnlyRadixBinaryTreeView)
# ---------------------------------------------------------------------------


class MutableRadixBinaryTreeStorage(Sequence[tuple[int, ValT]], Generic[ValT]):
    """Owns fixed-capacity sorted entries and maintains the radix table."""

    __slots__ = ("_buffer", "_count", "capacity", "key_transform", "radix_shift", "radix_table")

    def __init__(
        self,
        capacity: int = 64,
        radix_shift: int = 28,
        key_transform: Callable[[int], int] | None = None,
    ):
        assert capacity >= 0
        self.capacity = capacity
        self.radix_shift = radix_shift
        self.key_transform = key_transform
        self._buffer: list[tuple[int, ValT] | None] = [None] * capacity
        self._count = 0
        self.radix_table: list[int] = [0, 0]

    def _rebuild_radix_table(self) -> None:
        keys = [self._buffer[index][0] for index in range(self._count)]
        self.radix_table[:] = build_radix_table(
            keys, radix_shift=self.radix_shift, key_transform=self.key_transform
        )

    def insert(self, key: int, value: ValT) -> bool:
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda entry: entry[0] if entry is not None else key
        )
        if idx < self._count and self._buffer[idx] is not None and self._buffer[idx][0] == key:
            self._buffer[idx] = (key, value)
            return True
        if self._count >= self.capacity:
            return False
        for index in range(self._count, idx, -1):
            self._buffer[index] = self._buffer[index - 1]
        self._buffer[idx] = (key, value)
        self._count += 1
        self._rebuild_radix_table()
        return True

    def remove(self, key: int) -> ValT | None:
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda entry: entry[0] if entry is not None else key
        )
        if idx >= self._count or self._buffer[idx] is None or self._buffer[idx][0] != key:
            return None
        value = self._buffer[idx][1]
        for index in range(idx, self._count - 1):
            self._buffer[index] = self._buffer[index + 1]
        self._buffer[self._count - 1] = None
        self._count -= 1
        self._rebuild_radix_table()
        return value

    def clear(self) -> None:
        for index in range(self._count):
            self._buffer[index] = None
        self._count = 0
        self.radix_table[:] = [0, 0]

    def size(self) -> int:
        return self._count

    def __len__(self) -> int:
        return self._count

    def __getitem__(self, index: int) -> tuple[int, ValT]:
        if index < 0:
            index += self._count
        assert 0 <= index < self._count, index
        entry = self._buffer[index]
        assert entry is not None
        return entry

    def __iter__(self) -> Iterator[tuple[int, ValT]]:
        for index in range(self._count):
            yield self[index]

    @property
    def count(self) -> int:
        return self._count

    def view(self) -> ReadOnlyRadixBinaryTreeView[ValT]:
        return ReadOnlyRadixBinaryTreeView(
            entries=self,
            radix_table=self.radix_table,
            radix_shift=self.radix_shift,
            key_transform=self.key_transform,
        )


def _card_compiled(card_table: BitView, pc: int, card_shift: int) -> bool:
    """O(1) card marking pre-filter: True only once card state == 3 (COMPILED)."""
    card_idx = pc >> card_shift
    return card_idx < card_table.size() and card_table.at(card_idx) == 3


def lookup_jit_entry_flatmap(
    view: ReadOnlyFlatMapView[int, ValT],
    card_table: BitView,
    entry_group_bounds: Sequence[int],
    pc: int,
    card_shift: int = JIT_CARD_SHIFT,
    group_shift: int = 6,
) -> ValT | None:
    """
    JIT entry lookup over a plain flat-map view, narrowed via caller-supplied
    group bounds:
        1. O(1) card marking pre-filter (4 bytes per card, card_shift=2).
        2. O(1) group-bounds slice (pure scalar offsets array where group i is [bounds[i], bounds[i+1])).
        3. Bounded local binary search on the narrowed ReadOnlyFlatMapView.
    """

    if not _card_compiled(card_table, pc, card_shift):
        return None
    group_idx = pc >> group_shift
    if group_idx < 0 or group_idx + 1 >= len(entry_group_bounds):
        return None
    first = entry_group_bounds[group_idx]
    last = entry_group_bounds[group_idx + 1]
    if first >= last:
        return None
    return view.slice(first, last).find(pc)


def lookup_jit_entry_radix(
    view: ReadOnlyRadixBinaryTreeView[ValT],
    card_table: BitView,
    pc: int,
    card_shift: int = JIT_CARD_SHIFT,
) -> ValT | None:
    """
    JIT entry lookup over a radix-binary-tree view, which narrows to its group
    bounds internally via its own Radix Table:
        1. O(1) card marking pre-filter (4 bytes per card, card_shift=2).
        2. O(1) Radix Table prefix lookup + bounded local binary search (view.find()).
    """

    if not _card_compiled(card_table, pc, card_shift):
        return None
    return view.find(pc)


# ---------------------------------------------------------------------------
# 8. RingBuffer (fixed-capacity circular ring buffer)
# ---------------------------------------------------------------------------


class RingBuffer(Generic[T]):
    """Fixed-size ring buffer with bounded storage and FIFO/overwrite behavior."""

    __slots__ = ("buf", "capacity", "count", "dropped", "head")

    def __init__(self, capacity: int = 32):
        self.capacity = capacity
        self.buf: list[T | None] = [None] * capacity
        self.head = 0
        self.count = 0
        self.dropped = 0

    def push(self, item: T) -> bool:
        """Push an item, overwriting oldest if full. Returns True if overwritten."""
        overwritten = False
        if self.count == self.capacity:
            overwritten = True
            self.dropped += 1
            # Overwrite at head
            self.buf[self.head] = item
            self.head = (self.head + 1) % self.capacity
        else:
            tail = (self.head + self.count) % self.capacity
            self.buf[tail] = item
            self.count += 1
        return overwritten

    def pop(self) -> T | None:
        """Pop the oldest item (FIFO)."""
        if self.count == 0:
            return None
        item = self.buf[self.head]
        self.buf[self.head] = None
        self.head = (self.head + 1) % self.capacity
        self.count -= 1
        return item

    def drain(self) -> StaticVector[T]:
        """Drain all elements in FIFO order into an exact-capacity vector."""
        out: StaticVector[T] = StaticVector(capacity=self.count)
        while self.count > 0:
            item = self.pop()
            if item is not None:
                out.append(item)
        return out

    def size(self) -> int:
        return self.count

    def __len__(self) -> int:
        return self.count

    def is_empty(self) -> bool:
        return self.count == 0

    @property
    def overwrite_count(self) -> int:
        return self.dropped


# ---------------------------------------------------------------------------
# 9. StaticVector (fixed-capacity sequential array)
# ---------------------------------------------------------------------------


class StaticVector(Generic[T]):
    """Fixed-capacity sequential storage without dynamic heap reallocation."""

    __slots__ = ("_items", "capacity")

    def __init__(self, capacity: int = 32):
        self.capacity = capacity
        self._items: list[T] = []

    @classmethod
    def of(cls, items: Sequence[T], capacity: int | None = None) -> StaticVector[T]:
        """Builds a StaticVector pre-populated with `items` (test/setup convenience)."""
        cap = capacity if capacity is not None else len(items)
        vec: StaticVector[T] = cls(capacity=cap)
        for item in items:
            if not vec.push_back(item):
                assert False, f"StaticVector.of: {len(items)} items exceed capacity {cap}"
        return vec

    def push_back(self, item: T) -> bool:
        if len(self._items) >= self.capacity:
            return False
        self._items.append(item)
        return True

    def append(self, item: T) -> None:
        """Append one item and fail fast when the fixed capacity is exhausted."""

        assert self.push_back(item)

    def extend(self, items: Iterable[T]) -> bool:
        pending = tuple(items)
        if len(self._items) + len(pending) > self.capacity:
            return False
        for item in pending:
            self._items.append(item)
        return True

    def insert_at(self, index: int, item: T) -> bool:
        if not (0 <= index <= len(self._items)) or len(self._items) >= self.capacity:
            return False
        self._items.append(item)
        for current in range(len(self._items) - 1, index, -1):
            self._items[current] = self._items[current - 1]
        self._items[index] = item
        return True

    def pop_at(self, index: int = -1) -> T:
        assert self._items, "pop from an empty StaticVector"
        return self._items.pop(index)

    def pop_back(self) -> T | None:
        return self._items.pop() if self._items else None

    def remove(self, item: T) -> bool:
        """Removes the first occurrence of `item`, shifting later entries down. False if absent."""
        try:
            self._items.remove(item)
        except ValueError:
            return False
        return True

    def clear(self) -> None:
        self._items.clear()

    def sort(self, *, key: Callable[[T], T] | None = None) -> None:
        self._items.sort(key=key)

    def at(self, index: int) -> T:
        return self._items[index]

    def size(self) -> int:
        return len(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, index: int) -> T:
        return self._items[index]

    def __setitem__(self, index: int, item: T) -> None:
        self._items[index] = item

    def __delitem__(self, index: int) -> None:
        del self._items[index]

    def contains(self, item: T) -> bool:
        for index in range(len(self._items)):
            if self._items[index] == item:
                return True
        return False

    def __contains__(self, item: T) -> bool:
        return self.contains(item)

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __eq__(self, other: Sequence[T]) -> bool:
        try:
            return self._items == other._items
        except AttributeError:
            return self._items == other

    def __repr__(self) -> str:
        return f"StaticVector(capacity={self.capacity}, items={self._items!r})"
