"""
docs/components/tier1_core/concepts/flat_view_concept.py
Reference Concept Implementation: the fireball container vocabulary
- Non-owning views over storage the owning component already holds
- flat_map_view : sorted keys -> value, coarse narrowing then binary search
- flat_set_view : sorted keys, membership only, carries no value span
- bit_view      : dense sub-byte state table, index-addressed, never searched
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from typing import Any

ALLOWED_BITS = (1, 2, 4)


class BitView:
    """
    bit_view<Bits>: a dense, index-addressed table of sub-byte states.
        Deliberately offers no search: this is the card marking shape, where the
        index *is* the question. Bits must divide 8 so one element never straddles a
        byte, which keeps a read down to a single load plus a shift and a mask.
    """

    def __init__(self, storage: bytearray, bits: int, origin: int = 0, count: int = 0):
        assert bits in ALLOWED_BITS, "Bits must be 1, 2 or 4"
        self.storage = storage
        self.bits = bits
        self.origin = origin  # bit offset of logical element 0
        self.count = count

    def size(self):
        return self.count

    def _bit_pos(self, i):
        assert 0 <= i < self.count, "index outside the view"
        return self.origin + i * self.bits

    def at(self, i):
        bit = self._bit_pos(i)
        mask = (1 << self.bits) - 1
        return (self.storage[bit >> 3] >> (bit & 7)) & mask

    def put(self, i, value):
        mask = (1 << self.bits) - 1
        assert 0 <= value <= mask, "value does not fit in Bits"
        bit = self._bit_pos(i)
        byte, shift = bit >> 3, bit & 7
        cleared = self.storage[byte] & ~(mask << shift) & 0xFF
        self.storage[byte] = cleared | (value << shift)

    def slice(self, first, last):
        """Narrow by index. The bit origin absorbs the remainder, so `first`
        does not have to land on a byte boundary."""
        assert 0 <= first <= last <= self.count, "a view may only ever shrink"
        return BitView(self.storage, self.bits, self.origin + first * self.bits, last - first)


class _SortedWindow:
    """Shared narrowing behaviour of the two sparse views."""

    def __init__(self, keys, first=0, last=None):
        self.keys = keys
        self.first = first
        self.last = len(keys) if last is None else last

    def size(self):
        return self.last - self.first

    def empty(self):
        return self.size() == 0

    def _bounds(self, lo, hi):
        return (
            bisect.bisect_left(self.keys, lo, self.first, self.last),
            bisect.bisect_right(self.keys, hi, self.first, self.last),
        )

    def _locate(self, key):
        i = bisect.bisect_left(self.keys, key, self.first, self.last)
        return i if i < self.last and self.keys[i] == key else None


class FlatMapView:
    """flat_map_view<Key, Value>: sorted pairs array (AoS), narrow-then-search, returns a value."""

    __slots__ = ("entries", "first", "last")

    def __init__(self, entries, first=0, last=None):
        self.entries = entries
        self.first = first
        self.last = len(self.entries) if last is None else last

    @property
    def keys(self):
        return [k for k, _ in self.entries[self.first : self.last]]

    @property
    def values(self):
        return [v for _, v in self.entries[self.first : self.last]]

    def size(self):
        return self.last - self.first

    def empty(self):
        return self.size() == 0

    def _bounds(self, lo, hi):
        return (
            bisect.bisect_left(self.entries, lo, self.first, self.last, key=lambda e: e[0]),
            bisect.bisect_right(self.entries, hi, self.first, self.last, key=lambda e: e[0]),
        )

    def _locate(self, key):
        i = bisect.bisect_left(self.entries, key, self.first, self.last, key=lambda e: e[0])
        return i if i < self.last and self.entries[i][0] == key else None

    def slice(self, first, last):
        assert self.first <= first <= last <= self.last, "a view may only ever shrink"
        return FlatMapView(self.entries, first, last)

    def narrow(self, lo, hi):
        lo_idx, hi_idx = self._bounds(lo, hi)
        return FlatMapView(self.entries, lo_idx, hi_idx)

    def find(self, key):
        """Binary search inside the current window only."""
        i = self._locate(key)
        return None if i is None else self.entries[i][1]


class StaticFlatMap:
    """
    Fixed-capacity owning sorted map stored in an AoS entry array (C++ std::array).
    Provides non-owning FlatMapView via .view().
    Leverages standard sorting algorithms and binary search.
    """

    __slots__ = ("_entries", "capacity")

    def __init__(self, capacity: int = 32):
        self.capacity = capacity
        self._entries: list[tuple[Any, Any]] = []

    def size(self) -> int:
        return len(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[tuple[Any, Any]]:
        return self._entries

    @property
    def keys(self) -> list[Any]:
        return [k for k, _ in self._entries]

    @property
    def values(self) -> list[Any]:
        return [v for _, v in self._entries]

    def is_sorted(self) -> bool:
        return all(
            self._entries[i][0] <= self._entries[i + 1][0] for i in range(len(self._entries) - 1)
        )

    def sort(self) -> StaticFlatMap:
        self._entries.sort(key=lambda e: e[0])
        return self

    def insert(self, key: Any, value: Any) -> bool:
        idx = bisect.bisect_left(self._entries, key, key=lambda e: e[0])
        if idx < len(self._entries) and self._entries[idx][0] == key:
            self._entries[idx] = (key, value)
            return False
        assert len(self._entries) < self.capacity, "StaticFlatMap capacity exceeded"
        self._entries.insert(idx, (key, value))
        return True

    def remove(self, key: Any) -> bool:
        idx = bisect.bisect_left(self._entries, key, key=lambda e: e[0])
        if idx < len(self._entries) and self._entries[idx][0] == key:
            self._entries.pop(idx)
            return True
        return False

    def erase(self, key: Any) -> bool:
        return self.remove(key)

    def view(self) -> FlatMapView:
        return FlatMapView(self._entries)

    def find(self, key: Any) -> Any | None:
        return self.view().find(key)

    def __contains__(self, key: Any) -> bool:
        return self.view().find(key) is not None


class FlatSetView(_SortedWindow):
    """
    flat_set_view<Key>: sorted keys only, answers membership.
        Carries no value span at all -- the question is whether the key is present,
        not what is stored against it.
    """

    def slice(self, first, last):
        assert self.first <= first <= last <= self.last, "a view may only ever shrink"
        return FlatSetView(self.keys, first, last)

    def narrow(self, lo, hi):
        return FlatSetView(self.keys, *self._bounds(lo, hi))

    def contains(self, key):
        return self._locate(key) is not None


class RadixBinaryTreeView:
    """fireball::radix_binary_tree_view<Key, Value, RadixShift>:
    Container combining an O(1) Radix Table (pure scalar start-index array)
    with bounded binary search on a sorted key-value array.
    Bucket bounds are: first = radix_table[prefix], last = radix_table[prefix + 1].
    """

    def __init__(
        self,
        keys: Sequence[int],
        values: Sequence[Any],
        radix_table: Sequence[int],
        radix_shift: int,
    ):
        self.map_view = FlatMapView(list(zip(keys, values, strict=False)))
        self.radix_table = radix_table  # pure scalar offsets array [0, 3, 6, ...]
        self.radix_shift = radix_shift

    def find(self, key: int) -> Any | None:
        prefix = key >> self.radix_shift
        if prefix < 0 or prefix + 1 >= len(self.radix_table):
            return None
        first = self.radix_table[prefix]
        last = self.radix_table[prefix + 1]
        if first >= last:
            return None
        return self.map_view.slice(first, last).find(key)


def lookup_jit_entry(
    view: FlatMapView | RadixBinaryTreeView,
    card_table: BitView,
    entry_group_bounds: Sequence[int],
    pc: int,
    card_shift: int,
    group_shift: int,
):
    """JIT entry lookup:
    1. O(1) card marking pre-filter: verify card state == 3 (COMPILED).
    2. O(1) Radix Table prefix lookup: slice to group bounds [first, last].
    3. Bounded local binary search on narrowed FlatMapView (RadixBinaryTree index model).
    """
    card_idx = pc >> card_shift
    if card_idx >= card_table.size() or card_table.at(card_idx) != 3:  # 3 = COMPILED
        return None
    if hasattr(view, "radix_table"):
        return view.find(pc)
    group_idx = pc >> group_shift
    if group_idx < 0 or group_idx + 1 >= len(entry_group_bounds):
        return None
    first = entry_group_bounds[group_idx]
    last = entry_group_bounds[group_idx + 1]
    if first >= last:
        return None
    return view.slice(first, last).find(pc)


def card_marking_table(storage: bytearray, card_count: int) -> BitView:
    """
    The 2-bit per-card state table: 4 cards per byte instead of one.
        Note this returns a BitView, not a FlatMapView -- card marking is answered
        by the index, never searched for.
    """
    return BitView(storage, bits=2, origin=0, count=card_count)


def breakpoint_set(sorted_pcs) -> FlatSetView:
    """Debugger breakpoints: the interpreter asks 'is this PC a breakpoint?',
    which is membership, not a lookup."""
    return FlatSetView(sorted_pcs)


# ==============================================================================
# Simulation / Verification Tests
# ==============================================================================


def test_two_bit_card_marking_packs_four_cards_per_byte():
    """A 2-bit card state table must cost 2 bits per card, not 8. This is the
    whole reason bit_view exists: at RAM 32KB a byte-per-card table is waste."""
    store = bytearray(4)  # 4 bytes -> 16 cards at 2 bits each
    cards = card_marking_table(store, card_count=16)
    assert cards.size() == 16
    for i in range(16):
        cards.put(i, i % 4)
    assert [cards.at(i) for i in range(16)] == [i % 4 for i in range(16)]
    assert len(store) == 4, "16 two-bit cards must fit in 4 bytes"


def test_packed_write_does_not_disturb_neighbours():
    """Four cards share a byte, so a write has to read-modify-write within it."""
    cards = card_marking_table(bytearray(4), card_count=16)
    for i in range(16):
        cards.put(i, i % 4)

    cards.put(5, 3)
    assert cards.at(4) == 0 and cards.at(5) == 3 and cards.at(6) == 2, (
        "writing one element corrupted an adjacent one in the same byte"
    )


def test_slice_does_not_require_byte_alignment():
    """The bit origin absorbs the remainder, so a caller may slice at any index
    rather than having to round to a byte boundary."""
    cards = card_marking_table(bytearray(4), card_count=16)
    for i in range(16):
        cards.put(i, i % 4)

    window = cards.slice(5, 9)  # starts mid-byte
    assert window.size() == 4
    assert [window.at(i) for i in range(4)] == [1, 2, 3, 0]
    assert window.slice(1, 3).at(0) == 2, "a nested slice lost its bit origin"


def test_bit_view_offers_no_search():
    """Card marking is answered by the index. Exposing find/contains here would
    invite treating a dense state table as something to search."""
    cards = card_marking_table(bytearray(4), card_count=16)
    assert not hasattr(cards, "find"), "bit_view must not offer a map lookup"
    assert not hasattr(cards, "contains"), "bit_view must not offer membership"


def _map_fixture():
    return FlatMapView([(10, 1), (20, 2), (30, 3), (40, 4), (50, 5), (60, 6)])


def test_narrowing_only_ever_shrinks_and_composes():
    """Monotonic shrinking is what makes multi-stage coarse indexes safe to
    compose: no stage can reintroduce an element an earlier stage excluded."""
    view = _map_fixture()
    narrowed = view.narrow(20, 40)
    assert narrowed.size() == 3
    assert narrowed.find(30) == 3
    assert narrowed.find(60) is None, "a key outside the window must not be found"
    assert narrowed.narrow(30, 30).size() == 1
    try:
        narrowed.slice(0, 6)
        raise AssertionError("slice widened the view beyond its parent")
    except AssertionError as e:
        assert "shrink" in str(e), e


def test_set_view_answers_membership_without_any_value_storage():
    """flat_set_view exists so a membership table need not allocate a value
    array at all -- the breakpoint list is keys and nothing else."""
    bps = breakpoint_set([0x100, 0x180, 0x240, 0x300])
    assert not hasattr(bps, "values"), "a set view must carry no value span"
    assert not hasattr(bps, "find"), "a set answers membership, not lookup"
    assert bps.contains(0x180) is True
    assert bps.contains(0x1C0) is False
    # Narrowing works the same way it does for the map view.
    window = bps.narrow(0x180, 0x240)
    assert window.size() == 2
    assert window.contains(0x300) is False, "a key outside the window must not be found"


def test_card_marking_prefilter_and_jit_entry_group_narrowing_lookup():
    """The 2-bit card marking table filters uncompiled PCs in O(1), JIT entry
    group index narrows the search slice in O(1), and FlatMapView binary search
    finds the entry in O(log n)."""
    view = _map_fixture()
    card_table = card_marking_table(bytearray(2), card_count=8)
    # card_shift=3 (8 bytes/card):
    # pc=30 -> card_idx = 30 >> 3 = 3
    # pc=60 -> card_idx = 60 >> 3 = 7
    card_table.put(3, 3)  # card 3 (covers pc 24-31) -> 3: COMPILED
    card_table.put(7, 3)  # card 7 (covers pc 56-63) -> 3: COMPILED
    entry_group_bounds = [0, 3, 6]  # group 0: [0, 3), group 1: [3, 6)
    assert (
        lookup_jit_entry(view, card_table, entry_group_bounds, pc=30, card_shift=3, group_shift=5)
        == 3
    )
    assert (
        lookup_jit_entry(view, card_table, entry_group_bounds, pc=60, card_shift=3, group_shift=5)
        == 6
    )
    assert (
        lookup_jit_entry(view, card_table, entry_group_bounds, pc=99, card_shift=3, group_shift=5)
        is None
    )


def test_bits_must_divide_a_byte():
    """3-bit elements would straddle bytes and force a two-load read, which the
    design deliberately excludes."""
    try:
        BitView(bytearray(4), bits=3, count=4)
        raise AssertionError("a non-divisor Bits was accepted")
    except AssertionError as e:
        assert "1, 2 or 4" in str(e), e


def test_radix_binary_tree_view():
    """RadixBinaryTree index model: Radix Table yields O(1) bounded segment [first, last],
    then local binary search finds entry in O(log n).
    Radix table is a compact scalar start-index array where bucket prefix bounds are [table[p], table[p+1]]."""
    keys = [10, 20, 30, 40, 50, 60]
    values = [1, 2, 3, 4, 5, 6]
    # Radix shift = 5 (bin size 32): bin 0 (0..31) -> [0, 3], bin 1 (32..63) -> [3, 6]
    # Compact scalar start-indices: [0, 3, 6] (size = num_bins + 1)
    radix_table = [0, 3, 6]
    rbt_view = RadixBinaryTreeView(keys, values, radix_table, radix_shift=5)
    assert rbt_view.find(20) == 2
    assert rbt_view.find(30) == 3
    assert rbt_view.find(50) == 5
    assert rbt_view.find(60) == 6
    assert rbt_view.find(25) is None
    assert rbt_view.find(100) is None


def test_static_flat_map_operations():
    m = StaticFlatMap(capacity=16)
    assert m.insert(30, 300)
    assert m.insert(10, 100)
    assert m.insert(20, 200)
    assert m.is_sorted()
    assert m.entries == [(10, 100), (20, 200), (30, 300)]
    assert m.find(20) == 200
    assert 20 in m
    assert 40 not in m
    assert m.remove(20)
    assert m.find(20) is None
    assert m.entries == [(10, 100), (30, 300)]


if __name__ == "__main__":
    test_two_bit_card_marking_packs_four_cards_per_byte()
    test_packed_write_does_not_disturb_neighbours()
    test_slice_does_not_require_byte_alignment()
    test_bit_view_offers_no_search()
    test_narrowing_only_ever_shrinks_and_composes()
    test_set_view_answers_membership_without_any_value_storage()
    test_card_marking_prefilter_and_jit_entry_group_narrowing_lookup()
    test_radix_binary_tree_view()
    test_bits_must_divide_a_byte()
    test_static_flat_map_operations()
    print("[PASS] All container vocabulary concept tests passed successfully.")
