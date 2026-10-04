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
import ctypes
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from bump_allocator import BumpAllocator
from config import JIT_CARD_SHIFT

KeyT = TypeVar("KeyT")
ValT = TypeVar("ValT")
T = TypeVar("T")
ALLOWED_BITS = (1, 2, 4)


class CtypesU32Buffer:
    """Fixed-width backing for scalar arrays shared with native code.

    Generic `StaticVector[T]` values cannot use this storage: they may contain
    Python records whose layout is not a C ABI. Native users must opt into this
    concrete unsigned-32-bit buffer.
    """

    __slots__ = ("_allocator", "_arena_offset", "_arena_size", "_values", "capacity")

    def __init__(self, capacity: int, allocator: BumpAllocator | None = None) -> None:
        assert capacity >= 0
        self.capacity = capacity
        self._values = (ctypes.c_uint32 * capacity)()
        self._allocator: BumpAllocator | None = None
        self._arena_offset: int | None = None
        self._arena_size = 0
        if allocator is not None:
            self.bind_allocator(allocator)

    @property
    def arena_offset(self) -> int | None:
        return self._arena_offset

    @property
    def arena_size(self) -> int:
        return self._arena_size

    def bind_allocator(self, allocator: BumpAllocator) -> None:
        """Record this C-compatible buffer in the owning runtime arena."""

        if self._allocator is allocator:
            return
        self._arena_size = ctypes.sizeof(self._values)
        self._arena_offset = (
            allocator.allocate(self._arena_size, ctypes.alignment(ctypes.c_uint32))
            if self._arena_size
            else None
        )
        self._allocator = allocator

    @property
    def native_address(self) -> int:
        """Address of the stable C-compatible backing array."""

        return 0 if self.capacity == 0 else ctypes.addressof(self._values)

    def at(self, index: int) -> int:
        assert 0 <= index < self.capacity
        return int(self._values[index])

    def put(self, index: int, value: int) -> None:
        assert 0 <= index < self.capacity
        assert 0 <= value <= 0xFFFF_FFFF
        self._values[index] = value

    def __len__(self) -> int:
        return self.capacity


def freeze_sequence(items: Iterable[T]) -> tuple[T, ...]:
    """Freeze a bounded setup sequence for an immutable ROM-style view."""

    return tuple(items)


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
            assert False, f"a view may only ever shrink (0 <= {first} <= {last} <= {self.count})"
        return BitView(self.storage, self.bits, self.origin + first * self.bits, last - first)


class ReadOnlyBitView:
    """Borrowed packed-bit view that exposes reads and narrowing only."""

    __slots__ = ("bits", "count", "origin", "storage")

    def __init__(self, storage: bytes, bits: int, origin: int = 0, count: int = 0):
        self.storage = storage
        self.bits = bits
        self.origin = origin
        self.count = count

    def __len__(self) -> int:
        return self.count

    def _bit_pos(self, index: int) -> int:
        assert 0 <= index < self.count, (
            f"index {index} outside read-only bit view of size {self.count}"
        )
        return self.origin + index * self.bits

    def at(self, index: int) -> int:
        bit = self._bit_pos(index)
        mask = (1 << self.bits) - 1
        return (self.storage[bit >> 3] >> (bit & 7)) & mask

    def slice(self, first: int, last: int) -> ReadOnlyBitView:
        assert 0 <= first <= last <= self.count, (
            f"a read-only view may only shrink (0 <= {first} <= {last} <= {self.count})"
        )
        return ReadOnlyBitView(
            self.storage,
            self.bits,
            origin=self.origin + first * self.bits,
            count=last - first,
        )


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

    def view(self, origin: int = 0, count: int | None = None) -> ReadOnlyBitView:
        return ReadOnlyBitView(
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


@dataclass(slots=True)
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


@dataclass(frozen=True, slots=True)
class ReadOnlyFlatMapStorage(Generic[KeyT, ValT]):
    """Owns an immutable, sorted flat-map sequence."""

    entries: Sequence[tuple[KeyT, ValT]]

    @classmethod
    def create(cls, entries: Sequence[tuple[KeyT, ValT]]) -> ReadOnlyFlatMapStorage[KeyT, ValT]:
        sorted_entries = tuple(sorted(entries, key=lambda entry: entry[0]))
        for index in range(1, len(sorted_entries)):
            assert sorted_entries[index - 1][0] != sorted_entries[index][0], (
                f"duplicate flat-map key {sorted_entries[index][0]!r}"
            )
        return cls(entries=sorted_entries)

    @classmethod
    def from_sorted_static_entries(
        cls,
        entries: StaticVector[tuple[KeyT, ValT]],
    ) -> ReadOnlyFlatMapStorage[KeyT, ValT]:
        """Takes ownership of an already sorted fixed vector without copying it."""
        for index in range(1, len(entries)):
            previous = entries[index - 1][0]
            current = entries[index][0]
            assert previous < current, f"flat-map keys must be unique and sorted: {current!r}"
        return cls(entries=entries.freeze())

    def view(self) -> ReadOnlyFlatMapView[KeyT, ValT]:
        return ReadOnlyFlatMapView(self.entries)


class FlatMapEntryRange(Generic[KeyT, ValT]):
    """Borrow an integer-indexed subrange without materializing its entries."""

    __slots__ = ("_entries", "first", "last")

    def __init__(self, entries: Sequence[tuple[KeyT, ValT]], first: int, last: int):
        self._entries = entries
        self.first = first
        self.last = last

    def __len__(self) -> int:
        assert self.last <= len(self._entries), "borrowed range escapes its storage"
        return self.last - self.first

    def __getitem__(self, index: int) -> tuple[KeyT, ValT]:
        assert 0 <= index < len(self)
        return self._entries[self.first + index]

    def __iter__(self) -> Iterator[tuple[KeyT, ValT]]:
        for index in range(len(self)):
            yield self[index]


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
        if self._last is None:
            return len(self._entries)
        assert self._last <= len(self._entries), "borrowed range escapes its storage"
        return self._last

    @property
    def entries(self) -> Sequence[tuple[KeyT, ValT]]:
        if self.first == 0 and self.last == len(self._entries):
            return self._entries
        return FlatMapEntryRange(self._entries, self.first, self.last)

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


def fold_mix32(v: int) -> int:
    """
    Bijective 32-bit multiplicative mixer (Fibonacci hashing, `0x9E3779B1`
    is the odd golden-ratio constant). Unlike `bswap32`, which only
    relocates whole bytes, multiplication carries entropy from every input
    bit into the high bits of the product, so a 32-bit WASM PC spreads its
    Code-section offset across the radix prefix. The high bits of the product
    are the well-mixed ones, matching `ReadOnlyRadixBinaryTreeStorage`'s
    `radix_shift`-from-the-top prefix extraction.
    """
    return (v * 0x9E3779B1) & 0xFFFF_FFFF


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
    sorted_keys = sorted(
        keys,
        key=(lambda key: (key_transform(key), key))
        if key_transform is not None
        else (lambda key: (key, key)),
    )
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


def _build_radix_table_for_sorted_entries(
    entries: Sequence[tuple[int, ValT]],
    radix_shift: int,
    key_transform: Callable[[int], int] | None,
) -> list[int]:
    """Builds radix bounds directly from the canonical sorted entry array."""
    if not entries:
        return [0, 0]
    max_prefix = -1
    for key, _value in entries:
        transformed_key = key_transform(key) if key_transform is not None else key
        prefix = transformed_key >> radix_shift
        if prefix > max_prefix:
            max_prefix = prefix
    table_size = max_prefix + 2
    assert table_size <= FB_CONF_MAX_RADIX_TABLE_SIZE, (
        f"Radix table size ({table_size}) exceeds embedded limit {FB_CONF_MAX_RADIX_TABLE_SIZE}! Adjust radix_shift."
    )
    table = [0] * table_size
    current_prefix = 0
    for index, (key, _value) in enumerate(entries):
        transformed_key = key_transform(key) if key_transform is not None else key
        prefix = transformed_key >> radix_shift
        while current_prefix < prefix:
            current_prefix += 1
            table[current_prefix] = index
    for prefix in range(current_prefix + 1, table_size):
        table[prefix] = len(entries)
    return table


@dataclass(frozen=True, slots=True)
class ReadOnlyRadixBinaryTreeStorage(Generic[ValT]):
    """
    Owns one sorted entry array and the radix prefix table.
    Strictly separates storage ownership from non-owning view borrows ({Type_Vocabulary}, {GLOBAL_Policy_Memory}).
    """

    radix_table: tuple[int, ...]
    radix_shift: int
    entries: Sequence[tuple[int, ValT]]
    key_transform: Callable[[int], int] | None = None

    @classmethod
    def create(
        cls,
        keys: Sequence[int],
        values: Sequence[ValT],
        radix_shift: int = 28,
        key_transform: Callable[[int], int] | None = None,
    ) -> ReadOnlyRadixBinaryTreeStorage[ValT]:
        paired = tuple(
            sorted(
                zip(keys, values, strict=True),
                key=lambda pair: (
                    key_transform(pair[0]) if key_transform is not None else pair[0],
                    pair[0],
                ),
            )
        )
        return cls.from_sorted_entries(
            paired,
            radix_shift=radix_shift,
            key_transform=key_transform,
        )

    @classmethod
    def from_sorted_entries(
        cls,
        entries: tuple[tuple[int, ValT], ...],
        radix_shift: int = 28,
        key_transform: Callable[[int], int] | None = None,
    ) -> ReadOnlyRadixBinaryTreeStorage[ValT]:
        """Takes one immutable tuple of sorted entries and builds its radix table."""
        return cls._from_ordered_entries(entries, radix_shift, key_transform)

    @classmethod
    def from_sorted_static_entries(
        cls,
        entries: StaticVector[tuple[int, ValT]],
        radix_shift: int = 28,
        key_transform: Callable[[int], int] | None = None,
    ) -> ReadOnlyRadixBinaryTreeStorage[ValT]:
        """Takes ownership of sorted fixed storage without making a second entry array."""
        return cls._from_ordered_entries(entries.freeze(), radix_shift, key_transform)

    @classmethod
    def _from_ordered_entries(
        cls,
        entries: Sequence[tuple[int, ValT]],
        radix_shift: int,
        key_transform: Callable[[int], int] | None,
    ) -> ReadOnlyRadixBinaryTreeStorage[ValT]:
        for index in range(1, len(entries)):
            previous_key = entries[index - 1][0]
            current_key = entries[index][0]
            previous_order = (
                key_transform(previous_key) if key_transform is not None else previous_key,
                previous_key,
            )
            current_order = (
                key_transform(current_key) if key_transform is not None else current_key,
                current_key,
            )
            assert previous_order <= current_order
        table = tuple(_build_radix_table_for_sorted_entries(entries, radix_shift, key_transform))
        return cls(
            radix_table=table,
            radix_shift=radix_shift,
            entries=entries,
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

    __slots__ = ("entries", "key_transform", "radix_shift", "radix_table")

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
        low = self._lower_bound(key, first, last)
        if low < last and self.entries[low][0] == key:
            return self.entries[low][1]
        return None

    def _lower_bound(self, key: int, first: int, last: int) -> int:
        """Return the first projected-order entry not less than ``key``."""
        projected_key = self.key_transform(key) if self.key_transform is not None else key
        target_order = (projected_key, key)
        low = first
        high = last
        while low < high:
            middle = (low + high) // 2
            middle_key = self.entries[middle][0]
            projected_middle = (
                self.key_transform(middle_key) if self.key_transform is not None else middle_key
            )
            if (projected_middle, middle_key) < target_order:
                low = middle + 1
            else:
                high = middle
        return low

    def find_matching(self, key: int, predicate: Callable[[ValT], bool]) -> ValT | None:
        """Find one value among equal keys that also satisfies a collision check."""
        rk = self.key_transform(key) if self.key_transform is not None else key
        prefix = rk >> self.radix_shift
        if prefix < 0 or prefix + 1 >= len(self.radix_table):
            return None
        first = self.radix_table[prefix]
        last = self.radix_table[prefix + 1]
        if first >= last:
            return None

        low = self._lower_bound(key, first, last)
        while low < last and self.entries[low][0] == key:
            value = self.entries[low][1]
            if predicate(value):
                return value
            low += 1
        return None

    def find_interval(self, offset: int) -> ValT | None:
        """
        Range lookup for interval keys [start, end) -- finds entity where entity.start_offset <= offset < entity.end_offset.
        """
        assert self.key_transform is None, "interval lookup requires raw-key ordering"
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
    """Owns sorted keys in fixed storage.

    GOTCHA-CONT-04: Shift entries in place so borrowed views keep observing the
    same buffer while its active count changes; never resize the backing storage.
    """

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
    """Owns sorted entries in fixed storage.

    GOTCHA-CONT-04: Shift entries in place so borrowed views keep observing the
    same buffer while its active count changes; never resize the backing storage.
    """

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
            self._buffer,
            key,
            0,
            self._count,
            key=lambda entry: entry[0] if entry is not None else key,
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
            self._buffer,
            key,
            0,
            self._count,
            key=lambda entry: entry[0] if entry is not None else key,
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
    """Owns fixed-capacity sorted entries and maintains the radix table.

    GOTCHA-CONT-04: In-place shifts preserve the borrowed view's backing buffer
    and keep its active range synchronized without dynamic storage growth.
    """

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
        self.radix_table: StaticVector[int] = StaticVector(capacity=FB_CONF_MAX_RADIX_TABLE_SIZE)
        for _ in range(FB_CONF_MAX_RADIX_TABLE_SIZE):
            self.radix_table.append(0)

    def _order_key(self, key: int) -> tuple[int, int]:
        projected = self.key_transform(key) if self.key_transform is not None else key
        return projected, key

    def _rebuild_radix_table(self) -> None:
        if self._count == 0:
            for index in range(FB_CONF_MAX_RADIX_TABLE_SIZE):
                self.radix_table[index] = 0
            return
        last_entry = self._buffer[self._count - 1]
        assert last_entry is not None
        max_prefix = self._order_key(last_entry[0])[0] >> self.radix_shift
        table_size = max_prefix + 2
        assert table_size <= FB_CONF_MAX_RADIX_TABLE_SIZE, (
            f"Radix table size ({table_size}) exceeds embedded limit {FB_CONF_MAX_RADIX_TABLE_SIZE}! Adjust radix_shift."
        )
        current_prefix = 0
        for index in range(self._count):
            entry = self._buffer[index]
            assert entry is not None
            prefix = self._order_key(entry[0])[0] >> self.radix_shift
            while current_prefix < prefix:
                current_prefix += 1
                self.radix_table[current_prefix] = index
        for prefix in range(current_prefix + 1, table_size):
            self.radix_table[prefix] = self._count
        for prefix in range(table_size, FB_CONF_MAX_RADIX_TABLE_SIZE):
            self.radix_table[prefix] = self._count

    def insert(self, key: int, value: ValT) -> bool:
        order_key = self._order_key(key)
        idx = bisect.bisect_left(
            self._buffer,
            order_key,
            0,
            self._count,
            key=lambda entry: self._order_key(entry[0]) if entry is not None else order_key,
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
        order_key = self._order_key(key)
        idx = bisect.bisect_left(
            self._buffer,
            order_key,
            0,
            self._count,
            key=lambda entry: self._order_key(entry[0]) if entry is not None else order_key,
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
        for index in range(FB_CONF_MAX_RADIX_TABLE_SIZE):
            self.radix_table[index] = 0

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


def lookup_jit_entry(
    view: ReadOnlyFlatMapView[int, ValT],
    card_table: BitView,
    pc: int,
    card_shift: int = JIT_CARD_SHIFT,
) -> ValT | None:
    """
    Sparse JIT entry lookup: O(1) card prefilter followed by binary search.
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

    __slots__ = ("_arena_offset", "_arena_size", "_frozen", "_items", "capacity")

    def __init__(
        self,
        capacity: int = 32,
        *,
        arena_offset: int | None = None,
        arena_size: int = 0,
    ):
        self.capacity = capacity
        self._items: list[T] = []
        self._frozen = False
        # The reference simulator keeps Python records in _items and records
        # only the non-owning target-arena span; the allocator owns the bytes.
        assert arena_size >= 0
        assert arena_offset is not None or arena_size == 0
        assert arena_offset is None or arena_offset >= 0
        self._arena_offset = arena_offset
        self._arena_size = arena_size

    @property
    def arena_offset(self) -> int | None:
        """Return the non-owning offset of this vector's arena backing."""

        return self._arena_offset

    @property
    def arena_size(self) -> int:
        """Return the byte size of this vector's arena backing reservation."""

        return self._arena_size

    def freeze(self) -> StaticVector[T]:
        """Disables mutation and transfers this bounded sequence to read-only storage."""
        self._frozen = True
        return self

    @classmethod
    def of(cls, items: Iterable[T], capacity: int | None = None) -> StaticVector[T]:
        """Builds a StaticVector pre-populated with `items` (test/setup convenience)."""
        cap = capacity if capacity is not None else len(items)
        vec: StaticVector[T] = cls(capacity=cap)
        for item in items:
            if not vec.push_back(item):
                assert False, f"StaticVector.of: {len(items)} items exceed capacity {cap}"
        return vec

    def push_back(self, item: T) -> bool:
        assert not self._frozen, "cannot mutate a frozen StaticVector"
        if len(self._items) >= self.capacity:
            return False
        self._items.append(item)
        return True

    def append(self, item: T) -> None:
        """Append one item and fail fast when the fixed capacity is exhausted."""

        pushed = self.push_back(item)
        assert pushed

    def extend(self, items: Sequence[T]) -> bool:
        assert not self._frozen, "cannot mutate a frozen StaticVector"
        item_count = len(items)
        if len(self._items) + item_count > self.capacity:
            return False
        for index in range(item_count):
            self._items.append(items[index])
        return True

    def reverse_in_place(self) -> None:
        """Reverse the populated range without constructing a temporary sequence."""

        assert not self._frozen, "cannot mutate a frozen StaticVector"

        left = 0
        right = len(self._items) - 1
        while left < right:
            temporary = self._items[left]
            self._items[left] = self._items[right]
            self._items[right] = temporary
            left += 1
            right -= 1

    def insert_at(self, index: int, item: T) -> bool:
        assert not self._frozen, "cannot mutate a frozen StaticVector"
        if not (0 <= index <= len(self._items)) or len(self._items) >= self.capacity:
            return False
        self._items.append(item)
        for current in range(len(self._items) - 1, index, -1):
            self._items[current] = self._items[current - 1]
        self._items[index] = item
        return True

    def pop_at(self, index: int = -1) -> T:
        assert not self._frozen, "cannot mutate a frozen StaticVector"
        assert self._items, "pop from an empty StaticVector"
        return self._items.pop(index)

    def pop_back(self) -> T | None:
        assert not self._frozen, "cannot mutate a frozen StaticVector"
        return self._items.pop() if self._items else None

    def remove(self, item: T) -> bool:
        """Removes the first occurrence of `item`, shifting later entries down. False if absent."""
        assert not self._frozen, "cannot mutate a frozen StaticVector"
        try:
            self._items.remove(item)
        except ValueError:
            return False
        return True

    def clear(self) -> None:
        assert not self._frozen, "cannot mutate a frozen StaticVector"
        self._items.clear()

    def sort(self, *, key: Callable[[T], T] | None = None) -> None:
        assert not self._frozen, "cannot mutate a frozen StaticVector"
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
        assert not self._frozen, "cannot mutate a frozen StaticVector"
        self._items[index] = item

    def __delitem__(self, index: int) -> None:
        assert not self._frozen, "cannot mutate a frozen StaticVector"
        del self._items[index]

    def relocate_arena(self, offset_delta: int) -> None:
        """Move recorded target storage when its owning module changes arenas."""

        if self._arena_offset is not None:
            relocated_offset = self._arena_offset + offset_delta
            assert relocated_offset >= 0
            self._arena_offset = relocated_offset

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
