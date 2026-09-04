"""
experiments/pysim/system_containers.py
Fireball System Container Vocabulary (Zero-allocation static container vocabulary).
Implements the 4 fundamental non-owning views and their corresponding ReadOnly & Mutable storages:
  Views:
    1. BitView: dense sub-byte state table (1, 2, 4 bits), O(1) index-addressed
    2. FlatMapView: sorted keys + values, O(log N) binary search with narrowing/slicing
    3. FlatSetView: sorted keys only, O(log N) membership query (no value span)
    4. RadixBinaryTreeView: O(1) Radix Table + bounded local binary search
  Storages:
    - Bit: ReadOnlyBitStorage, MutableBitStorage
    - FlatMap: ReadOnlyFlatMapStorage, MutableFlatMapStorage
    - FlatSet: ReadOnlyFlatSetStorage, MutableFlatSetStorage
    - RadixBinaryTree: ReadOnlyRadixBinaryTreeStorage, MutableRadixBinaryTreeStorage
  Others:
    - RingBuffer: fixed-capacity ring buffer with overwrite / fifo semantics
    - StaticVector: fixed-capacity sequential array
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

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
        CONT-GOTCHA-01: Bits must strictly divide 8 (1, 2, or 4) so that an element
        never straddles a byte boundary, ensuring atomic single-byte load/mask.
    """

    __slots__ = ("bits", "count", "origin", "storage")

    def __init__(self, storage: bytearray | bytes, bits: int, origin: int = 0, count: int = 0):
        if bits not in ALLOWED_BITS:
            raise ValueError(f"Bits must be 1, 2 or 4 (got {bits})")
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
            raise IndexError(f"index {i} outside bit_view of size {self.count}")
        return self.origin + i * self.bits

    def at(self, i: int) -> int:
        bit = self._bit_pos(i)
        mask = (1 << self.bits) - 1
        return (self.storage[bit >> 3] >> (bit & 7)) & mask

    def put(self, i: int, value: int) -> None:
        mask = (1 << self.bits) - 1
        if not (0 <= value <= mask):
            raise ValueError(f"value {value} does not fit in {self.bits} bits (max {mask})")
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
            raise ValueError(
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
        if bits not in ALLOWED_BITS:
            raise ValueError(f"Bits must be 1, 2 or 4 (got {bits})")
        self._buffer = buffer
        self.bits = bits
        self.count = count

    @property
    def buffer(self) -> bytes:
        return self._buffer

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
        if bits not in ALLOWED_BITS:
            raise ValueError(f"Bits must be 1, 2 or 4 (got {bits})")
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
            raise ValueError(f"value {value} does not fit in {self.bits} bits (max {mask})")
        if not (0 <= i < self.count):
            raise IndexError(f"index {i} outside mutable_bit_storage of size {self.count}")
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
# 2. _SortedWindow: Common base for sorted views
# ---------------------------------------------------------------------------


class _SortedWindow(Generic[KeyT]):
    __slots__ = ("_last", "first", "keys")

    def __init__(self, keys: Sequence[KeyT], first: int = 0, last: int | None = None):
        self.keys = keys
        self.first = first
        self._last = last

    @property
    def last(self) -> int:
        return len(self.keys) if self._last is None else min(self._last, len(self.keys))

    def size(self) -> int:
        return max(0, self.last - self.first)

    def __len__(self) -> int:
        return self.size()

    def empty(self) -> bool:
        return self.size() == 0

    def _bounds(self, lo: KeyT, hi: KeyT) -> tuple[int, int]:
        hi_bound = self.last
        first = bisect.bisect_left(self.keys, lo, self.first, hi_bound)
        last = bisect.bisect_right(self.keys, hi, self.first, hi_bound)
        return (first, last)

    def _locate(self, key: KeyT) -> int | None:
        hi_bound = self.last
        i = bisect.bisect_left(self.keys, key, self.first, hi_bound)
        return i if i < hi_bound and self.keys[i] == key else None


# ---------------------------------------------------------------------------
# 3. FlatMapView (fireball::flat_map_view<Key, Value>)
# ---------------------------------------------------------------------------
# docs/components/tier1_core/system_containers.md {3.3}: a non-owning view
# over a sorted key span plus a parallel value span (or, where Key/Value are
# both numeric, a single packed span) -- Key and Value are template
# parameters, so no key-type inspection happens in this class at all: a
# caller supplies whatever comparable Key the concrete usage needs
# (std::string_view for the IPC registry, uint32_t hashes for radix-indexed
# lookups, ...) and comparisons are just `<`/`==` on that type.


class FlatMapView(Generic[KeyT, ValT]):
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
        return self._entries[self.first : self.last]

    @property
    def keys(self) -> list[KeyT]:
        return [k for k, _ in self._entries[self.first : self.last]]

    @property
    def values(self) -> list[ValT]:
        return [v for _, v in self._entries[self.first : self.last]]

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

    def slice(self, first: int, last: int) -> FlatMapView[KeyT, ValT]:
        if not (self.first <= first <= last <= self.last):
            raise ValueError("a view may only ever shrink")
        return FlatMapView(self._entries, first, last)

    def narrow(self, lo: KeyT, hi: KeyT) -> FlatMapView[KeyT, ValT]:
        lo_idx, hi_idx = self._bounds(lo, hi)
        return FlatMapView(self._entries, lo_idx, hi_idx)

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
        if val is None:
            raise KeyError(key)
        return val

    def __contains__(self, key: KeyT) -> bool:
        return self._locate(key) is not None

    def __len__(self) -> int:
        return self.size()


@dataclass
class ReadOnlyFlatMapStorage(Generic[KeyT, ValT]):
    """
    fireball::read_only_flat_map_storage<Key, Value>:
    Immutable AoS storage owning sorted (Key, Value) entry array ({Type_Vocabulary}, {GLOBAL_Policy_Memory}).
    Zero allocation non-owning borrowing via view(). Does not permit insert/remove.
    """

    entries: tuple[tuple[KeyT, ValT], ...]

    @classmethod
    def create(cls, entries: Sequence[tuple[KeyT, ValT]]) -> ReadOnlyFlatMapStorage[KeyT, ValT]:
        sorted_entries = sorted(entries, key=lambda e: e[0])
        return cls(entries=tuple(sorted_entries))

    def view(self) -> FlatMapView[KeyT, ValT]:
        """Borrows a non-owning FlatMapView over this immutable storage."""
        return FlatMapView(self.entries)


# ---------------------------------------------------------------------------
# 4. FlatSetView (fireball::flat_set_view<Key>)
# ---------------------------------------------------------------------------


class FlatSetView(_SortedWindow[KeyT], Generic[KeyT]):
    """
    flat_set_view<Key>: sorted keys only, answers membership.
        Carries NO value span at all -- questions whether key is present.
    """

    def slice(self, first: int, last: int) -> FlatSetView[KeyT]:
        if not (self.first <= first <= last <= self.last):
            raise ValueError("a view may only ever shrink")
        return FlatSetView(self.keys, first, last)

    def narrow(self, lo: KeyT, hi: KeyT) -> FlatSetView[KeyT]:
        return FlatSetView(self.keys, *self._bounds(lo, hi))

    def contains(self, key: KeyT) -> bool:
        return self._locate(key) is not None

    def __contains__(self, key: KeyT) -> bool:
        return self.contains(key)


@dataclass
class ReadOnlyFlatSetStorage(Generic[KeyT]):
    """
    fireball::read_only_flat_set_storage<Key>:
    Immutable key set storage owning sorted Key array ({Type_Vocabulary}, {GLOBAL_Policy_Memory}).
    Zero allocation non-owning borrowing via view(). Does not permit insert/remove.
    """

    keys: tuple[KeyT, ...]

    @classmethod
    def create(cls, keys: Sequence[KeyT]) -> ReadOnlyFlatSetStorage[KeyT]:
        sorted_keys = sorted(set(keys))
        return cls(keys=tuple(sorted_keys))

    def view(self) -> FlatSetView[KeyT]:
        """Borrows a non-owning FlatSetView over this immutable storage."""
        return FlatSetView(self.keys)


# ---------------------------------------------------------------------------
# 5. RadixBinaryTreeView (fireball::radix_binary_tree_view<Key, Value, RadixShift, KeyProjection>)
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
    Backing storage for RadixBinaryTreeView.
    Owns memory buffers for sorted keys, values, and radix_table.
    Strictly separates storage ownership from non-owning view borrows ({Type_Vocabulary}, {GLOBAL_Policy_Memory}).
    """

    keys: list[int]
    values: list[ValT]
    radix_table: list[int]
    radix_shift: int
    entries: list[tuple[int, ValT]]
    key_transform: Callable[[int], int] | None = None

    @classmethod
    def create(
        cls,
        keys: Sequence[int],
        values: Sequence[ValT],
        radix_shift: int = 28,
        key_transform: Callable[[int], int] | None = None,
    ) -> ReadOnlyRadixBinaryTreeStorage[ValT]:
        paired = sorted(zip(keys, values, strict=False), key=lambda p: p[0])
        s_keys = [p[0] for p in paired]
        s_vals = [p[1] for p in paired]
        table = build_radix_table(s_keys, radix_shift=radix_shift, key_transform=key_transform)
        return cls(
            keys=s_keys,
            values=s_vals,
            radix_table=table,
            radix_shift=radix_shift,
            entries=paired,
            key_transform=key_transform,
        )

    def view(self) -> RadixBinaryTreeView[ValT]:
        """Borrows a non-owning RadixBinaryTreeView over this storage without copying."""
        return RadixBinaryTreeView(
            keys=self.keys,
            values=self.values,
            radix_table=self.radix_table,
            radix_shift=self.radix_shift,
            entries=self.entries,
            key_transform=self.key_transform,
        )


class _MutableRadixKeysView(Sequence[int]):
    __slots__ = ("_owner",)

    def __init__(self, owner: MutableRadixBinaryTreeStorage[ValT]):
        self._owner = owner

    def __len__(self) -> int:
        return self._owner._count

    def __iter__(self) -> Iterator[int]:
        for i in range(self._owner._count):
            item = self._owner._buffer[i]
            if item is not None:
                yield item[0]

    def __getitem__(self, idx: int | slice) -> int | Sequence[int]:
        if isinstance(idx, slice):
            start, stop, step = idx.indices(self._owner._count)
            return [
                self._owner._buffer[i][0]
                for i in range(start, stop, step)
                if self._owner._buffer[i] is not None
            ]
        if not (0 <= idx < self._owner._count):
            raise IndexError(f"index {idx} out of range (count={self._owner._count})")
        item = self._owner._buffer[idx]
        assert item is not None
        return item[0]


class _MutableRadixValuesView(Sequence[ValT], Generic[ValT]):
    __slots__ = ("_owner",)

    def __init__(self, owner: MutableRadixBinaryTreeStorage[ValT]):
        self._owner = owner

    def __len__(self) -> int:
        return self._owner._count

    def __iter__(self) -> Iterator[ValT]:
        for i in range(self._owner._count):
            item = self._owner._buffer[i]
            if item is not None:
                yield item[1]

    def __getitem__(self, idx: int | slice) -> ValT | Sequence[ValT]:
        if isinstance(idx, slice):
            start, stop, step = idx.indices(self._owner._count)
            return [
                self._owner._buffer[i][1]
                for i in range(start, stop, step)
                if self._owner._buffer[i] is not None
            ]
        if not (0 <= idx < self._owner._count):
            raise IndexError(f"index {idx} out of range (count={self._owner._count})")
        item = self._owner._buffer[idx]
        assert item is not None
        return item[1]


class _MutableRadixEntriesView(Sequence[tuple[int, ValT]], Generic[ValT]):
    __slots__ = ("_owner",)

    def __init__(self, owner: MutableRadixBinaryTreeStorage[ValT]):
        self._owner = owner

    def __len__(self) -> int:
        return self._owner._count

    def __iter__(self) -> Iterator[tuple[int, ValT]]:
        for i in range(self._owner._count):
            item = self._owner._buffer[i]
            if item is not None:
                yield item

    def __getitem__(self, idx: int | slice) -> tuple[int, ValT] | Sequence[tuple[int, ValT]]:
        if isinstance(idx, slice):
            start, stop, step = idx.indices(self._owner._count)
            return [
                self._owner._buffer[i]
                for i in range(start, stop, step)
                if self._owner._buffer[i] is not None
            ]
        if not (0 <= idx < self._owner._count):
            raise IndexError(f"index {idx} out of range (count={self._owner._count})")
        item = self._owner._buffer[idx]
        assert item is not None
        return item


class MutableRadixBinaryTreeStorage(Generic[ValT]):
    """
    fireball::mutable_radix_binary_tree_storage<Key, Value, RadixShift, KeyProjection>:
    Mutable storage container with pre-allocated fixed-length array of capacity elements ({GLOBAL_Policy_Memory}, {META_NoStdVector}).
    Tracks active entry count up to capacity without dynamic reallocation.
    All element-level mutations (insert, remove, clear) are performed strictly here, not in the non-owning RadixBinaryTreeView.
    Automatically maintains sorted entry order and updates Radix Table prefix bounds.
    """

    __slots__ = ("_buffer", "_count", "capacity", "key_transform", "radix_shift", "radix_table")

    def __init__(
        self,
        capacity: int = 64,
        radix_shift: int = 28,
        key_transform: Callable[[int], int] | None = None,
    ):
        self.capacity = capacity
        self.radix_shift = radix_shift
        self.key_transform = key_transform
        self._buffer: list[tuple[int, ValT] | None] = [None] * capacity
        self._count: int = 0
        self.radix_table: list[int] = [0, 0]

    def _rebuild_radix_table(self) -> None:
        if self._count == 0:
            self.radix_table[:] = [0, 0]
            return
        keys = [self._buffer[i][0] for i in range(self._count) if self._buffer[i] is not None]
        new_table = build_radix_table(
            keys, radix_shift=self.radix_shift, key_transform=self.key_transform
        )
        self.radix_table[:] = new_table

    def insert(self, key: int, value: ValT) -> bool:
        """Inserts or updates (key, value), maintaining sorted order and updating radix table."""
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda p: p[0] if p is not None else key
        )
        if idx < self._count and self._buffer[idx] is not None and self._buffer[idx][0] == key:
            self._buffer[idx] = (key, value)
            return True
        if self._count >= self.capacity:
            return False
        for j in range(self._count, idx, -1):
            self._buffer[j] = self._buffer[j - 1]
        self._buffer[idx] = (key, value)
        self._count += 1
        self._rebuild_radix_table()
        return True

    def remove(self, key: int) -> ValT | None:
        """Removes key and returns its value, updating radix table."""
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda p: p[0] if p is not None else key
        )
        if idx < self._count and self._buffer[idx] is not None and self._buffer[idx][0] == key:
            val = self._buffer[idx][1]
            for j in range(idx, self._count - 1):
                self._buffer[j] = self._buffer[j + 1]
            self._buffer[self._count - 1] = None
            self._count -= 1
            self._rebuild_radix_table()
            return val
        return None

    def clear(self) -> None:
        for i in range(self._count):
            self._buffer[i] = None
        self._count = 0
        self.radix_table[:] = [0, 0]

    def size(self) -> int:
        return self._count

    def __len__(self) -> int:
        return self._count

    @property
    def count(self) -> int:
        return self._count

    @property
    def keys(self) -> list[int]:
        return [self._buffer[i][0] for i in range(self._count) if self._buffer[i] is not None]

    @property
    def values(self) -> list[ValT]:
        return [self._buffer[i][1] for i in range(self._count) if self._buffer[i] is not None]

    @property
    def entries(self) -> list[tuple[int, ValT]]:
        return [self._buffer[i] for i in range(self._count) if self._buffer[i] is not None]

    def view(self) -> RadixBinaryTreeView[ValT]:
        """Borrows a non-owning RadixBinaryTreeView over this mutable storage."""
        return RadixBinaryTreeView(
            keys=_MutableRadixKeysView(self),
            values=_MutableRadixValuesView(self),
            radix_table=self.radix_table,
            radix_shift=self.radix_shift,
            entries=_MutableRadixEntriesView(self),
            key_transform=self.key_transform,
        )


class RadixBinaryTreeView(Generic[ValT]):
    """
    fireball::radix_binary_tree_view<Key, Value, RadixShift, KeyProjection>:
        Combines an O(1) Radix Table (coarse prefix lookup) with bounded local
        binary search on a sorted key-value array. Supports optional KeyProjection
        (such as bswap32) to project high-entropy lower bytes to radix prefix.
        Non-owning view: borrows references to external storage without taking ownership.
    """

    __slots__ = ("key_transform", "keys", "map_view", "radix_shift", "radix_table", "values")

    def __init__(
        self,
        keys: Sequence[int],
        values: Sequence[ValT],
        radix_table: Sequence[int],
        radix_shift: int,
        entries: Sequence[tuple[int, ValT]] | None = None,
        key_transform: Callable[[int], int] | None = None,
    ):
        assert len(radix_table) <= FB_CONF_MAX_RADIX_TABLE_SIZE, (
            f"Radix table size ({len(radix_table)}) exceeds embedded limit {FB_CONF_MAX_RADIX_TABLE_SIZE}!"
        )
        if entries is not None:
            self.keys = keys
            self.values = values
            self.map_view = FlatMapView(entries)
        else:
            paired = sorted(zip(keys, values, strict=False), key=lambda p: p[0])
            self.keys = [p[0] for p in paired]
            self.values = [p[1] for p in paired]
            self.map_view = FlatMapView(paired)
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
        if not self.keys:
            return None
        idx = bisect.bisect_right(self.keys, offset) - 1
        if 0 <= idx < len(self.values):
            entity = self.values[idx]
            try:
                if entity.start_offset <= offset < entity.end_offset:
                    return entity
            except AttributeError:
                pass
        return None


def _card_compiled(card_table: BitView, pc: int, card_shift: int) -> bool:
    """O(1) card marking pre-filter: True only once card state == 3 (COMPILED)."""
    card_idx = pc >> card_shift
    return card_idx < card_table.size() and card_table.at(card_idx) == 3


def lookup_jit_entry_flatmap(
    view: FlatMapView[int, ValT],
    card_table: BitView,
    entry_group_bounds: Sequence[int],
    pc: int,
    card_shift: int = 2,
    group_shift: int = 6,
) -> ValT | None:
    """
    JIT entry lookup over a plain FlatMapView, narrowed via caller-supplied
    group bounds:
        1. O(1) card marking pre-filter (4 bytes per card, card_shift=2).
        2. O(1) group-bounds slice (pure scalar offsets array where group i is [bounds[i], bounds[i+1])).
        3. Bounded local binary search on the narrowed FlatMapView.
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
    view: RadixBinaryTreeView[ValT],
    card_table: BitView,
    pc: int,
    card_shift: int = 2,
) -> ValT | None:
    """
    JIT entry lookup over a RadixBinaryTreeView, which narrows to its group
    bounds internally via its own Radix Table:
        1. O(1) card marking pre-filter (4 bytes per card, card_shift=2).
        2. O(1) Radix Table prefix lookup + bounded local binary search (view.find()).
    """

    if not _card_compiled(card_table, pc, card_shift):
        return None
    return view.find(pc)


# ---------------------------------------------------------------------------
# 6. MutableFlatMapStorage (fixed-capacity owning flat sorted map)
# ---------------------------------------------------------------------------


class _MutableMapBufferView(Sequence[tuple[KeyT, ValT]], Generic[KeyT, ValT]):
    __slots__ = ("_owner",)

    def __init__(self, owner: MutableFlatMapStorage[KeyT, ValT]):
        self._owner = owner

    def __len__(self) -> int:
        return self._owner._count

    def __iter__(self) -> Iterator[tuple[KeyT, ValT]]:
        for i in range(self._owner._count):
            item = self._owner._buffer[i]
            if item is not None:
                yield item

    def __getitem__(self, idx: int | slice) -> tuple[KeyT, ValT] | Sequence[tuple[KeyT, ValT]]:
        if isinstance(idx, slice):
            start, stop, step = idx.indices(self._owner._count)
            return [
                self._owner._buffer[i]
                for i in range(start, stop, step)
                if self._owner._buffer[i] is not None
            ]
        if not (0 <= idx < self._owner._count):
            raise IndexError(f"index {idx} out of range (count={self._owner._count})")
        item = self._owner._buffer[idx]
        assert item is not None
        return item


class MutableFlatMapStorage(Generic[KeyT, ValT]):
    """
    fireball::mutable_flat_map_storage<Key, Value, Capacity>:
    Fixed-capacity sorted map stored in a pre-allocated fixed-length array without dynamic reallocation.
    Tracks active entry count up to capacity ({GLOBAL_Policy_Memory}, {META_NoStdVector}).
    """

    __slots__ = ("_buffer", "_count", "capacity")

    def __init__(self, capacity: int = 32):
        self.capacity = capacity
        self._buffer: list[tuple[KeyT, ValT] | None] = [None] * capacity
        self._count: int = 0

    def size(self) -> int:
        return self._count

    def __len__(self) -> int:
        return self._count

    @property
    def count(self) -> int:
        return self._count

    def view(self) -> FlatMapView[KeyT, ValT]:
        return FlatMapView(_MutableMapBufferView(self))

    def find(self, key: KeyT) -> ValT | None:
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda e: e[0] if e is not None else key
        )
        if idx < self._count and self._buffer[idx] is not None and self._buffer[idx][0] == key:
            return self._buffer[idx][1]
        return None

    def __contains__(self, key: KeyT) -> bool:
        return self.find(key) is not None

    def __getitem__(self, key: KeyT) -> ValT:
        val = self.find(key)
        if val is None:
            raise KeyError(key)
        return val

    def insert(self, key: KeyT, value: ValT) -> bool:
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda e: e[0] if e is not None else key
        )
        if idx < self._count and self._buffer[idx] is not None and self._buffer[idx][0] == key:
            self._buffer[idx] = (key, value)
            return True
        if self._count >= self.capacity:
            return False
        for j in range(self._count, idx, -1):
            self._buffer[j] = self._buffer[j - 1]
        self._buffer[idx] = (key, value)
        self._count += 1
        return True

    def remove(self, key: KeyT) -> ValT | None:
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda e: e[0] if e is not None else key
        )
        if idx < self._count and self._buffer[idx] is not None and self._buffer[idx][0] == key:
            val = self._buffer[idx][1]
            for j in range(idx, self._count - 1):
                self._buffer[j] = self._buffer[j + 1]
            self._buffer[self._count - 1] = None
            self._count -= 1
            return val
        return None

    def clear(self) -> None:
        for i in range(self._count):
            self._buffer[i] = None
        self._count = 0

    def items(self) -> Iterator[tuple[KeyT, ValT]]:
        """Key-sorted (key, value) pairs -- always consistent with `view()`'s ordering."""
        for i in range(self._count):
            item = self._buffer[i]
            if item is not None:
                yield item

    @property
    def entries(self) -> list[tuple[KeyT, ValT]]:
        return [self._buffer[i] for i in range(self._count) if self._buffer[i] is not None]

    @property
    def keys(self) -> list[KeyT]:
        return [self._buffer[i][0] for i in range(self._count) if self._buffer[i] is not None]

    @property
    def values(self) -> list[ValT]:
        return [self._buffer[i][1] for i in range(self._count) if self._buffer[i] is not None]

    def is_sorted(self) -> bool:
        return all(
            self._buffer[i][0] <= self._buffer[i + 1][0]  # type: ignore[index]
            for i in range(self._count - 1)
        )


# ---------------------------------------------------------------------------
# 7. MutableFlatSetStorage (fixed-capacity owning flat sorted set)
# ---------------------------------------------------------------------------


class _MutableSetBufferView(Sequence[KeyT], Generic[KeyT]):
    __slots__ = ("_owner",)

    def __init__(self, owner: MutableFlatSetStorage[KeyT]):
        self._owner = owner

    def __len__(self) -> int:
        return self._owner._count

    def __iter__(self) -> Iterator[KeyT]:
        for i in range(self._owner._count):
            item = self._owner._buffer[i]
            if item is not None:
                yield item

    def __getitem__(self, idx: int | slice) -> KeyT | Sequence[KeyT]:
        if isinstance(idx, slice):
            start, stop, step = idx.indices(self._owner._count)
            return [
                self._owner._buffer[i]
                for i in range(start, stop, step)
                if self._owner._buffer[i] is not None
            ]
        if not (0 <= idx < self._owner._count):
            raise IndexError(f"index {idx} out of range (count={self._owner._count})")
        item = self._owner._buffer[idx]
        assert item is not None
        return item


class MutableFlatSetStorage(Generic[KeyT]):
    """Fixed-capacity sorted set stored in a pre-allocated flat array."""

    __slots__ = ("_buffer", "_count", "capacity")

    def __init__(self, capacity: int = 32):
        self.capacity = capacity
        self._buffer: list[KeyT | None] = [None] * capacity
        self._count: int = 0

    def size(self) -> int:
        return self._count

    def __len__(self) -> int:
        return self._count

    @property
    def count(self) -> int:
        return self._count

    def view(self) -> FlatSetView[KeyT]:
        return FlatSetView(_MutableSetBufferView(self))

    def contains(self, key: KeyT) -> bool:
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda k: k if k is not None else key
        )
        return idx < self._count and self._buffer[idx] == key

    def __contains__(self, key: KeyT) -> bool:
        return self.contains(key)

    def insert(self, key: KeyT) -> bool:
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda k: k if k is not None else key
        )
        if idx < self._count and self._buffer[idx] == key:
            return True
        if self._count >= self.capacity:
            return False
        for j in range(self._count, idx, -1):
            self._buffer[j] = self._buffer[j - 1]
        self._buffer[idx] = key
        self._count += 1
        return True

    def remove(self, key: KeyT) -> bool:
        idx = bisect.bisect_left(
            self._buffer, key, 0, self._count, key=lambda k: k if k is not None else key
        )
        if idx < self._count and self._buffer[idx] == key:
            for j in range(idx, self._count - 1):
                self._buffer[j] = self._buffer[j + 1]
            self._buffer[self._count - 1] = None
            self._count -= 1
            return True
        return False

    def clear(self) -> None:
        for i in range(self._count):
            self._buffer[i] = None
        self._count = 0

    @property
    def keys(self) -> list[KeyT]:
        return [self._buffer[i] for i in range(self._count) if self._buffer[i] is not None]


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

    def drain(self) -> list[T]:
        """Drain all elements in FIFO order."""
        out: list[T] = []
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
                raise ValueError(f"StaticVector.of: {len(items)} items exceed capacity {cap}")
        return vec

    def push_back(self, item: T) -> bool:
        if len(self._items) >= self.capacity:
            return False
        self._items.append(item)
        return True

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

    def at(self, index: int) -> T:
        return self._items[index]

    def size(self) -> int:
        return len(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, index: int) -> T:
        return self._items[index]

    def __contains__(self, item: T) -> bool:
        return item in self._items

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, StaticVector):
            return self._items == other._items
        if isinstance(other, list):
            return self._items == other
        return NotImplemented

    def __repr__(self) -> str:
        return f"StaticVector(capacity={self.capacity}, items={self._items!r})"
