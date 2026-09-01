"""
experiments/pysim/system_containers.py
Fireball System Container Vocabulary (Zero-allocation static container vocabulary).
Implements the 4 fundamental non-owning views and static-capacity containers defined in
docs/components/tier1_core/system_containers.md:
  1. FlatMapView: sorted keys + values, O(log N) binary search with narrowing/slicing
  2. FlatSetView: sorted keys only, O(log N) membership query (no value span)
  3. RadixBinaryTreeView: O(1) Radix Table + bounded local binary search
  4. BitView: dense sub-byte state table (1, 2, 4 bits), O(1) index-addressed, non-destructive write
  5. StaticFlatMap: fixed-capacity sorted key-value store with zero dynamic reallocation
  6. StaticFlatSet: fixed-capacity sorted key set
  7. RingBuffer: fixed-capacity ring buffer with overwrite / fifo semantics
  8. StaticVector: fixed-capacity sequential array
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Iterator, Sequence
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
        Deliberately offers NO search: index IS the query (e.g. Card Marking table).
        Bits must divide 8 (1, 2, or 4) so an element never straddles a byte.
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


# ---------------------------------------------------------------------------
# 2. _SortedWindow: Common base for sorted views
# ---------------------------------------------------------------------------


class _SortedWindow(Generic[KeyT]):
    __slots__ = ("first", "keys", "last")

    def __init__(self, keys: Sequence[KeyT], first: int = 0, last: int | None = None):
        self.keys = keys
        self.first = first
        self.last = len(keys) if last is None else last

    def size(self) -> int:
        return max(0, self.last - self.first)

    def __len__(self) -> int:
        return self.size()

    def empty(self) -> bool:
        return self.size() == 0

    def _bounds(self, lo: KeyT, hi: KeyT) -> tuple[int, int]:
        first = bisect.bisect_left(self.keys, lo, self.first, self.last)
        last = bisect.bisect_right(self.keys, hi, self.first, self.last)
        return (first, last)

    def _locate(self, key: KeyT) -> int | None:
        i = bisect.bisect_left(self.keys, key, self.first, self.last)
        return i if i < self.last and self.keys[i] == key else None


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

    __slots__ = ("_entries", "first", "last")

    def __init__(
        self,
        entries: Sequence[tuple[KeyT, ValT]] | Sequence[KeyT],
        values: Sequence[ValT] | None = None,
        first: int = 0,
        last: int | None = None,
    ):
        if values is not None:
            # Backwards-compatible (keys, values) input: zip into AoS entries
            self._entries: Sequence[tuple[KeyT, ValT]] = list(zip(entries, values, strict=True))  # type: ignore[arg-type]
        else:
            self._entries = entries  # type: ignore[assignment]
        self.first = first
        self.last = len(self._entries) if last is None else last

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
        sub = self._entries[self.first : self.last]
        left = bisect.bisect_left(sub, lo, key=lambda e: e[0])
        right = bisect.bisect_right(sub, hi, key=lambda e: e[0])
        return (self.first + left, self.first + right)

    def _locate(self, key: KeyT) -> int | None:
        sub = self._entries[self.first : self.last]
        idx = bisect.bisect_left(sub, key, key=lambda e: e[0])
        if idx < len(sub) and sub[idx][0] == key:
            return self.first + idx
        return None

    def slice(self, first: int, last: int) -> FlatMapView[KeyT, ValT]:
        if not (self.first <= first <= last <= self.last):
            raise ValueError("a view may only ever shrink")
        return FlatMapView(self._entries, None, first, last)

    def narrow(self, lo: KeyT, hi: KeyT) -> FlatMapView[KeyT, ValT]:
        return FlatMapView(self._entries, None, *self._bounds(lo, hi))

    def find(self, key: KeyT) -> ValT | None:
        """Binary search inside the current window only (O(log N))."""
        i = self._locate(key)
        return None if i is None else self._entries[i][1]

    def __getitem__(self, key: KeyT) -> ValT:
        val = self.find(key)
        if val is None:
            raise KeyError(key)
        return val

    def __contains__(self, key: KeyT) -> bool:
        return self._locate(key) is not None

    def __len__(self) -> int:
        return self.size()


class FlatMapStorage(Generic[KeyT, ValT]):
    """
    Owning storage container for sorted (key, value) pair entries (AoS).
    Explicitly separates array data ownership from non-owning views (FlatMapView).
    Leverages standard sorting algorithms and binary search.
    """

    __slots__ = ("_capacity", "_entries")

    def __init__(
        self,
        entries: Sequence[tuple[KeyT, ValT]] | Sequence[KeyT] = (),
        values: Sequence[ValT] | None = None,
        sort: bool = False,
        capacity: int | None = None,
    ):
        if values is not None:
            # Backwards-compatible (keys, values) input
            self._entries: list[tuple[KeyT, ValT]] = list(zip(entries, values, strict=True))  # type: ignore[arg-type]
        else:
            self._entries = list(entries)  # type: ignore[arg-type]
        self._capacity = capacity
        if self._capacity is not None and len(self._entries) > self._capacity:
            raise OverflowError(
                f"initial size {len(self._entries)} exceeds capacity {self._capacity}"
            )
        if sort:
            self.sort()

    @property
    def entries(self) -> list[tuple[KeyT, ValT]]:
        return self._entries

    @property
    def keys(self) -> list[KeyT]:
        return [k for k, _ in self._entries]

    @property
    def values(self) -> list[ValT]:
        return [v for _, v in self._entries]

    def is_sorted(self) -> bool:
        """Returns True if entries are sorted by key in ascending order."""
        return all(
            self._entries[i][0] <= self._entries[i + 1][0] for i in range(len(self._entries) - 1)
        )

    def sort(self) -> FlatMapStorage[KeyT, ValT]:
        """
        Sorts entries in-place by key in ascending order using standard sort.
        In C++, this maps directly to std::sort(entries.begin(), entries.end()).
        """
        self._entries.sort(key=lambda e: e[0])
        return self

    def insert(self, key: KeyT, value: ValT) -> bool:
        """
        Inserts a key-value pair maintaining ascending key order.
        If key already exists, updates value and returns False (no size increase).
        If key is new, inserts at the sorted index and returns True.
        Raises OverflowError if capacity is exceeded.
        """
        idx = bisect.bisect_left(self._entries, key, key=lambda e: e[0])
        if idx < len(self._entries) and self._entries[idx][0] == key:
            self._entries[idx] = (key, value)
            return False
        if self._capacity is not None and len(self._entries) >= self._capacity:
            raise OverflowError(f"FlatMapStorage exceeded capacity {self._capacity}")
        self._entries.insert(idx, (key, value))
        return True

    def remove(self, key: KeyT) -> bool:
        """
        Removes key and its corresponding entry maintaining sorted order.
        Returns True if found and removed, False otherwise.
        """
        idx = bisect.bisect_left(self._entries, key, key=lambda e: e[0])
        if idx < len(self._entries) and self._entries[idx][0] == key:
            self._entries.pop(idx)
            return True
        return False

    def erase(self, key: KeyT) -> bool:
        """Alias for remove() matching C++ naming."""
        return self.remove(key)

    def view(self) -> FlatMapView[KeyT, ValT]:
        """Returns a non-owning FlatMapView borrowing the owned entries array."""
        return FlatMapView(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, key: KeyT) -> ValT:
        return self.view()[key]

    def find(self, key: KeyT) -> ValT | None:
        return self.view().find(key)


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


# ---------------------------------------------------------------------------
# 5. RadixBinaryTreeView (fireball::radix_binary_tree_view<Key, Value, RadixShift, KeyProjection>)
# ---------------------------------------------------------------------------


def bswap32(v: int) -> int:
    """32-bit byte-order reversal for maximizing Radix table distribution on UnifiedPC."""
    return ((v & 0xFF) << 24) | ((v & 0xFF00) << 8) | ((v >> 8) & 0xFF00) | ((v >> 24) & 0xFF)


class RadixBinaryTreeView(Generic[ValT]):
    """
    fireball::radix_binary_tree_view<Key, Value, RadixShift, KeyProjection>:
        Combines an O(1) Radix Table (coarse prefix lookup) with bounded local
        binary search on a sorted key-value array. Supports optional KeyProjection
        (such as bswap32) to project high-entropy lower bytes to radix prefix.
    """

    __slots__ = ("key_transform", "map_view", "radix_shift", "radix_table")

    def __init__(
        self,
        keys: Sequence[int],
        values: Sequence[ValT],
        radix_table: Sequence[int],
        radix_shift: int,
        key_transform: Callable[[int], int] | None = None,
    ):
        self.map_view = FlatMapView(keys, values)
        self.radix_table = radix_table  # pure scalar offsets array [0, 3, 6, ...]
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


def _card_compiled(card_table: BitView, pc: int, card_shift: int) -> bool:
    """O(1) card marking pre-filter: True only once card state == 3 (COMPILED)."""
    card_idx = pc >> card_shift
    return card_idx < card_table.size() and card_table.at(card_idx) == 3


def lookup_jit_entry_flatmap(
    view: FlatMapView[int, ValT],
    card_table: BitView,
    entry_group_bounds: Sequence[tuple[int, int]],
    pc: int,
    card_shift: int = 8,
    group_shift: int = 6,
) -> ValT | None:
    """
    JIT entry lookup over a plain FlatMapView, narrowed via caller-supplied
    group bounds:
        1. O(1) card marking pre-filter.
        2. O(1) group-bounds slice (caller-computed, e.g. a separate Radix Table).
        3. Bounded local binary search on the narrowed FlatMapView.
    """

    if not _card_compiled(card_table, pc, card_shift):
        return None
    group_idx = pc >> group_shift
    if group_idx >= len(entry_group_bounds):
        return None
    first, last = entry_group_bounds[group_idx]
    if first >= last:
        return None
    return view.slice(first, last).find(pc)


def lookup_jit_entry_radix(
    view: RadixBinaryTreeView[ValT],
    card_table: BitView,
    pc: int,
    card_shift: int = 8,
) -> ValT | None:
    """
    JIT entry lookup over a RadixBinaryTreeView, which narrows to its group
    bounds internally via its own Radix Table:
        1. O(1) card marking pre-filter.
        2. O(1) Radix Table prefix lookup + bounded local binary search (view.find()).
    """

    if not _card_compiled(card_table, pc, card_shift):
        return None
    return view.find(pc)


# ---------------------------------------------------------------------------
# 6. StaticFlatMap (fixed-capacity owning flat sorted map)
# ---------------------------------------------------------------------------


class StaticFlatMap(Generic[KeyT, ValT]):
    """Fixed-capacity sorted map stored in parallel arrays without dynamic reallocation."""

    __slots__ = ("_keys", "_values", "capacity")

    def __init__(self, capacity: int = 32):
        self.capacity = capacity
        self._keys: list[KeyT] = []
        self._values: list[ValT] = []

    def size(self) -> int:
        return len(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def view(self) -> FlatMapView[KeyT, ValT]:
        return FlatMapView(self._keys, self._values)

    def find(self, key: KeyT) -> ValT | None:
        idx = bisect.bisect_left(self._keys, key)
        if idx < len(self._keys) and self._keys[idx] == key:
            return self._values[idx]
        return None

    def __contains__(self, key: KeyT) -> bool:
        return self.find(key) is not None

    def __getitem__(self, key: KeyT) -> ValT:
        val = self.find(key)
        if val is None:
            raise KeyError(key)
        return val

    def insert(self, key: KeyT, value: ValT) -> bool:
        idx = bisect.bisect_left(self._keys, key)
        if idx < len(self._keys) and self._keys[idx] == key:
            self._values[idx] = value
            return True
        if len(self._keys) >= self.capacity:
            return False
        self._keys.insert(idx, key)
        self._values.insert(idx, value)
        return True

    def remove(self, key: KeyT) -> ValT | None:
        idx = bisect.bisect_left(self._keys, key)
        if idx < len(self._keys) and self._keys[idx] == key:
            self._keys.pop(idx)
            return self._values.pop(idx)
        return None

    def clear(self) -> None:
        self._keys.clear()
        self._values.clear()

    def items(self) -> Iterator[tuple[KeyT, ValT]]:
        """Key-sorted (key, value) pairs -- always consistent with `view()`'s ordering."""
        return zip(self._keys, self._values, strict=True)


# ---------------------------------------------------------------------------
# 7. StaticFlatSet (fixed-capacity owning flat sorted set)
# ---------------------------------------------------------------------------


class StaticFlatSet(Generic[KeyT]):
    """Fixed-capacity sorted set stored in a flat array."""

    __slots__ = ("_keys", "capacity")

    def __init__(self, capacity: int = 32):
        self.capacity = capacity
        self._keys: list[KeyT] = []

    def size(self) -> int:
        return len(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def view(self) -> FlatSetView[KeyT]:
        return FlatSetView(self._keys)

    def contains(self, key: KeyT) -> bool:
        idx = bisect.bisect_left(self._keys, key)
        return idx < len(self._keys) and self._keys[idx] == key

    def __contains__(self, key: KeyT) -> bool:
        return self.contains(key)

    def insert(self, key: KeyT) -> bool:
        idx = bisect.bisect_left(self._keys, key)
        if idx < len(self._keys) and self._keys[idx] == key:
            return True
        if len(self._keys) >= self.capacity:
            return False
        self._keys.insert(idx, key)
        return True

    def remove(self, key: KeyT) -> bool:
        idx = bisect.bisect_left(self._keys, key)
        if idx < len(self._keys) and self._keys[idx] == key:
            self._keys.pop(idx)
            return True
        return False

    def clear(self) -> None:
        self._keys.clear()


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


# ---------------------------------------------------------------------------
# 9. StaticVector (fixed-capacity sequential array)
# ---------------------------------------------------------------------------


class StaticVector(Generic[T]):
    """Fixed-capacity sequential storage without dynamic heap reallocation."""

    __slots__ = ("_items", "capacity")

    def __init__(self, capacity: int = 32):
        self.capacity = capacity
        self._items: list[T] = []

    def push_back(self, item: T) -> bool:
        if len(self._items) >= self.capacity:
            return False
        self._items.append(item)
        return True

    def pop_back(self) -> T | None:
        return self._items.pop() if self._items else None

    def at(self, index: int) -> T:
        return self._items[index]

    def size(self) -> int:
        return len(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, index: int) -> T:
        return self._items[index]

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)
