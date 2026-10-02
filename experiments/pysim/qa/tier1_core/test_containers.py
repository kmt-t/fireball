"""
静的コンテナの要求適合テスト。

正本: docs/components/tier1_core/system_containers.md
ケースと期待結果: docs/qa/tier1_core/system_containers_test_spec.md
生成テストは標準の dict / set と整数表現を独立した期待値として使う。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, overload

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from system_containers import (
    BitView,
    MutableBitStorage,
    MutableFlatMapStorage,
    MutableFlatSetStorage,
    MutableRadixBinaryTreeStorage,
    ReadOnlyBitStorage,
    ReadOnlyFlatMapStorage,
    ReadOnlyFlatMapView,
    ReadOnlyFlatSetStorage,
    ReadOnlyFlatSetView,
    ReadOnlyRadixBinaryTreeStorage,
    ReadOnlyRadixBinaryTreeView,
    StaticVector,
    bswap32,
    lookup_jit_entry,
)


class CountedEntries(Sequence[tuple[int, int]]):
    """実データの参照範囲と参照回数を記録する。検索結果の期待値は持たない。"""

    def __init__(self, entries: tuple[tuple[int, int], ...]) -> None:
        self.entries = entries
        self.read_indices: list[int] = []

    def __len__(self) -> int:
        return len(self.entries)

    @overload
    def __getitem__(self, index: int) -> tuple[int, int]:
        return self.entries[index]

    @overload
    def __getitem__(self, index: slice) -> tuple[tuple[int, int], ...]:
        return self.entries[index]

    def __getitem__(self, index: int | slice) -> tuple[int, int] | tuple[tuple[int, int], ...]:
        if isinstance(index, slice):
            indices = range(*index.indices(len(self)))
            self.read_indices.extend(indices)
        else:
            self.read_indices.append(index)
        return self.entries[index]


@pytest.mark.parametrize("count", (0, 1, 2, 3, 16, 257, 1024))
def test_cont_01_flat_map_view_find_binary_search(count: int) -> None:
    """TEST-CONT-01: 登録値と不在キーを判定し、参照回数を対数の上限内に保つ。"""
    entries = CountedEntries(tuple((index * 2, index + 100) for index in range(count)))
    view = ReadOnlyFlatMapView(entries)
    expected = dict(entries.entries)
    queries = (-1, 0, count, 2 * count - 2, 2 * count - 1, 2 * count)
    for key in queries:
        entries.read_indices.clear()
        assert view.find(key) == expected.get(key)
        # 二分探索の最大反復数に、存在確認と値取得の2参照を加える。
        assert len(entries.read_indices) <= count.bit_length() + 2
    assert view.size() == count
    assert view.empty() is (count == 0)


def test_cont_02_narrow_monotonic_shrinkage():
    """TEST-CONT-02: narrow(lo, hi) produces monotonic sub-window subset."""
    entries = [(10, 1), (20, 2), (30, 3), (40, 4), (50, 5), (60, 6), (70, 7), (80, 8)]
    v0 = ReadOnlyFlatMapView(entries)
    v1 = v0.narrow(20, 60)
    assert v1.size() == 5  # 20, 30, 40, 50, 60
    assert v1.find(20) == 2
    assert v1.find(60) == 6
    assert v1.find(10) is None
    v2 = v1.narrow(30, 45)
    assert v2.size() == 2  # 30, 40
    assert v2.find(30) == 3
    assert v2.find(40) == 4
    assert v2.find(20) is None
    assert v2.find(50) is None


def test_cont_03_slice_monotonic_shrinkage_and_bounds():
    """TEST-CONT-03: slice must only ever shrink within parent view bounds."""
    entries = [(10, 1), (20, 2), (30, 3), (40, 4), (50, 5)]
    v0 = ReadOnlyFlatMapView(entries)
    v1 = v0.slice(1, 4)
    assert v1.size() == 3
    assert v1.find(20) == 2
    assert v1.find(40) == 4
    with pytest.raises(AssertionError):
        v1.slice(0, 5)  # Expanding beyond v1's window [1, 4] must fail


def test_cont_04_flat_set_view_membership_only():
    """TEST-CONT-04: flat_set_view answers contains(key) with bool, carries no value span."""
    keys = [100, 200, 300, 400]
    set_view = ReadOnlyFlatSetView(keys)
    assert set_view.contains(200) is True
    assert set_view.contains(250) is False
    assert (300 in set_view) is True
    assert (50 in set_view) is False
    assert not hasattr(set_view, "values"), "flat_set_view must not carry a values span"


def test_cont_05_bit_view_adjacent_element_non_destructive():
    """TEST-CONT-05: bit_view put/at modifies targeted sub-byte element without corrupting adjacent elements."""
    storage = bytearray(4)  # 4 bytes = 16 2-bit elements
    bv = BitView(memoryview(storage), bits=2, origin=0, count=16)
    # Initial state all 0
    for i in range(16):
        assert bv.at(i) == 0

    # Write pattern to adjacent elements
    bv.put(0, 1)  # 01
    bv.put(1, 2)  # 10
    bv.put(2, 3)  # 11
    bv.put(3, 0)  # 00
    # Verify byte 0 is 0b00111001 = 0x39 (little-endian bit packing)
    assert storage[0] == (1 | (2 << 2) | (3 << 4) | (0 << 6))
    assert bv.at(0) == 1
    assert bv.at(1) == 2
    assert bv.at(2) == 3
    assert bv.at(3) == 0
    # Mutate middle element, ensure neighbors remain untouched
    bv.put(1, 3)
    assert bv.at(0) == 1
    assert bv.at(1) == 3
    assert bv.at(2) == 3
    assert bv.at(3) == 0


def test_cont_06_bit_view_unaligned_slice_origin_absorption():
    """TEST-CONT-06: bit_view.slice absorbs non-byte-aligned bit origins."""
    storage = bytearray(2)  # 8 2-bit elements
    bv = BitView(memoryview(storage), bits=2, origin=0, count=8)
    for i in range(8):
        bv.put(i, i % 4)

    # Slice starting at unaligned index 3 (bit offset = 6)
    sub = bv.slice(3, 7)
    assert sub.size() == 4
    assert sub.origin == 6
    assert sub.at(0) == bv.at(3)
    assert sub.at(1) == bv.at(4)
    assert sub.at(2) == bv.at(5)
    assert sub.at(3) == bv.at(6)


def test_cont_07_bit_view_allowed_bits_enforced():
    """TEST-CONT-07: bit_view allows only 1, 2, 4 bits dividing 8."""
    storage = bytearray(4)
    # Valid
    BitView(memoryview(storage), bits=1, count=32)
    BitView(memoryview(storage), bits=2, count=16)
    BitView(memoryview(storage), bits=4, count=8)
    # Invalid
    for invalid in (3, 5, 6, 7, 8):
        with pytest.raises(AssertionError):
            BitView(memoryview(storage), bits=invalid, count=4)


def test_cont_08_radix_binary_tree_view_coarse_radix_lookup() -> None:
    """TEST-CONT-08: 基数表で局所範囲を選び、空バケットでは実体を読まない。"""
    entries = CountedEntries(tuple((key, key + 1) for key in (*range(64), *range(512, 576))))
    tree = ReadOnlyRadixBinaryTreeView(entries, (0, 64, 64, 128), radix_shift=8)
    for key, expected, first, last in ((0, 1, 0, 64), (63, 64, 0, 64), (550, 551, 64, 128)):
        entries.read_indices.clear()
        assert tree.find(key) == expected
        assert all(first <= index < last for index in entries.read_indices)
        assert len(entries.read_indices) <= (last - first).bit_length() + 2
    for key in (256, 400, 768):
        entries.read_indices.clear()
        assert tree.find(key) is None
        assert entries.read_indices == []


def test_cont_09_jit_entry_lookup_card_table_prefilter() -> None:
    """TEST-CONT-09: COMPILED以外と表外PCでは疎マップにアクセスしない。"""
    card_table = MutableBitStorage(count=16, bits=2).view()
    entries = CountedEntries(((16, 160), (20, 200)))
    view = ReadOnlyFlatMapView(entries)
    for state in (0, 1, 2):
        card_table.put(2, state)
        entries.read_indices.clear()
        assert lookup_jit_entry(view, card_table, pc=16, card_shift=3) is None
        assert entries.read_indices == []
    card_table.put(2, 3)
    assert lookup_jit_entry(view, card_table, pc=16, card_shift=3) == 160
    # 同じCOMPILEDカードに含まれても、未登録PCを返してはならない。
    assert lookup_jit_entry(view, card_table, pc=18, card_shift=3) is None
    entries.read_indices.clear()
    assert lookup_jit_entry(view, card_table, pc=128, card_shift=3) is None
    assert entries.read_indices == []


def test_cont_10_container_type_separation():
    """TEST-CONT-10: flat_map_view and flat_set_view have strictly separated type responsibilities."""
    keys = [1, 2, 3]
    vals = [10, 20, 30]
    entries = list(zip(keys, vals, strict=True))
    m = ReadOnlyFlatMapView(entries)
    s = ReadOnlyFlatSetView(keys)
    assert type(m) is ReadOnlyFlatMapView
    assert type(s) is ReadOnlyFlatSetView
    assert type(s) is not ReadOnlyFlatMapView
    assert hasattr(m, "values")
    assert not hasattr(s, "values")


def test_cont_11_storage_and_view_ownership_separation():
    """TEST-CONT-11: Data storage ownership is strictly separated from non-owning views (AoS, Set, Radix, Bit).
    Mutations (insert, remove, put, fill) are performed strictly on Mutable Storages, never on Views."""
    # 1. FlatMap: ReadOnly vs Mutable Storage vs non-owning View
    ro_map = ReadOnlyFlatMapStorage.create([(10, "A"), (20, "B"), (30, "C")])
    v_ro = ro_map.view()
    assert v_ro.find(20) == "B"
    assert v_ro.entries is ro_map.entries
    assert not hasattr(v_ro, "insert")
    assert not hasattr(v_ro, "remove")
    with pytest.raises(AssertionError):
        ReadOnlyFlatMapStorage.create([(1, "first"), (1, "duplicate")])

    mut_map = MutableFlatMapStorage(capacity=8)
    assert mut_map.insert(100, "X")
    assert mut_map.insert(200, "Y")
    v_mut = mut_map.view()
    assert type(v_mut) is ReadOnlyFlatMapView
    assert not hasattr(mut_map, "find")
    assert v_mut.find(100) == "X"
    assert v_mut.find(200) == "Y"
    # Mutation on storage propagates to borrowed view
    assert mut_map.insert(150, "Z")
    assert v_mut.find(150) == "Z"
    assert mut_map.remove(100) == "X"
    assert v_mut.find(100) is None

    # 2. FlatSet: ReadOnly vs Mutable Storage vs non-owning View
    ro_set = ReadOnlyFlatSetStorage.create([1, 5, 10])
    v_set_ro = ro_set.view()
    assert v_set_ro.contains(5)
    assert v_set_ro.keys is ro_set.keys
    assert not hasattr(v_set_ro, "insert")
    assert not hasattr(v_set_ro, "remove")

    mut_set = MutableFlatSetStorage(capacity=8)
    assert mut_set.insert(42)
    assert mut_set.insert(99)
    v_set_mut = mut_set.view()
    assert type(v_set_mut) is ReadOnlyFlatSetView
    assert not hasattr(mut_set, "contains")
    assert v_set_mut.contains(42)
    assert mut_set.remove(42)
    assert not v_set_mut.contains(42)

    # 3. RadixBinaryTree: ReadOnly vs Mutable Storage vs non-owning View
    ro_radix = ReadOnlyRadixBinaryTreeStorage.create(
        keys=[10, 20, 30], values=["A", "B", "C"], radix_shift=4
    )
    rv1 = ro_radix.view()
    rv2 = ro_radix.view()
    assert rv1.find(20) == "B"
    assert rv2.find(30) == "C"
    assert rv1.entries is ro_radix.entries
    assert rv1.radix_table is ro_radix.radix_table
    assert ro_radix.entries == ((10, "A"), (20, "B"), (30, "C"))
    assert not hasattr(ro_radix, "keys")
    assert not hasattr(ro_radix, "values")
    assert not hasattr(rv1, "insert")
    assert not hasattr(rv1, "remove")

    radix_entries = StaticVector.of(((10, "A"), (20, "B")), capacity=2)
    ReadOnlyRadixBinaryTreeStorage.from_sorted_static_entries(radix_entries, radix_shift=4)
    with pytest.raises(AssertionError):
        radix_entries.append((30, "C"))

    mut_radix = MutableRadixBinaryTreeStorage(capacity=16, radix_shift=4)
    assert mut_radix.insert(10, "A")
    assert mut_radix.insert(30, "C")
    rv_mut = mut_radix.view()
    assert type(rv_mut) is ReadOnlyRadixBinaryTreeView
    assert not hasattr(mut_radix, "find")
    assert rv_mut.find(10) == "A"
    assert rv_mut.find(30) == "C"
    assert rv_mut.find(20) is None
    # Dynamic insert on mutable storage automatically updates radix table
    assert mut_radix.insert(20, "B")
    assert rv_mut.find(20) == "B"
    assert mut_radix.remove(10) == "A"
    assert rv_mut.find(10) is None

    # 4. BitStorage: ReadOnly vs Mutable Storage vs non-owning View
    ro_bit = ReadOnlyBitStorage(buffer=bytes([0b00001101]), bits=2, count=4)
    bv_ro = ro_bit.view()
    assert not hasattr(bv_ro, "put")
    assert bv_ro.at(0) == 1
    assert bv_ro.at(1) == 3
    assert bv_ro.storage is ro_bit.buffer
    narrowed_ro = bv_ro.slice(1, 3)
    assert len(narrowed_ro) == 2
    assert narrowed_ro.at(0) == 3

    mut_bit = MutableBitStorage(count=16, bits=2, default=0)
    mut_bit.put(2, 3)
    mut_bit.put(5, 1)
    bv_mut = mut_bit.view()
    assert bv_mut.at(2) == 3
    assert bv_mut.at(5) == 1
    assert bv_mut.at(0) == 0
    # Mutation on storage reflects in view
    mut_bit.fill(2)
    assert bv_mut.at(2) == 2
    assert bv_mut.at(0) == 2


def test_cont_12_read_only_flat_map_sorts_input_and_preserves_pairs() -> None:
    """TEST-CONT-12: 未整列の入力から生成し、全てのキーと値の対応を保存する。"""
    entries = ((50, "E"), (10, "A"), (40, "D"), (20, "B"), (30, "C"))
    storage = ReadOnlyFlatMapStorage.create(entries)
    view = storage.view()
    assert tuple(view.entries) == ((10, "A"), (20, "B"), (30, "C"), (40, "D"), (50, "E"))
    for key, expected in entries:
        assert view.find(key) == expected
    assert view.find(99) is None
    assert entries == ((50, "E"), (10, "A"), (40, "D"), (20, "B"), (30, "C"))


def test_cont_13_mutable_flat_map_sorted_insert_remove():
    """TEST-CONT-13: MutableFlatMapStorage maintains sorted order across arbitrary insert and remove calls."""
    storage = MutableFlatMapStorage(capacity=16)
    assert len(storage) == 0

    # Insert elements out of order
    assert storage.insert(30, "thirty") is True
    assert storage.insert(10, "ten") is True
    assert storage.insert(50, "fifty") is True
    assert storage.insert(20, "twenty") is True
    assert storage.insert(40, "forty") is True

    # Maintained sorted order at all times
    assert storage.is_sorted()
    storage_view = storage.view()
    assert list(storage_view.keys) == [10, 20, 30, 40, 50]
    assert list(storage_view.values) == ["ten", "twenty", "thirty", "forty", "fifty"]

    # Updating existing key replaces value, returns True (size stays 5)
    assert storage.insert(30, "THIRTY_UPDATED") is True
    assert len(storage) == 5
    assert list(storage_view.keys) == [10, 20, 30, 40, 50]
    assert list(storage_view.values) == ["ten", "twenty", "THIRTY_UPDATED", "forty", "fifty"]

    # Removal maintains sorted order
    assert storage.remove(10) == "ten"
    assert list(storage_view.keys) == [20, 30, 40, 50]
    assert list(storage_view.values) == ["twenty", "THIRTY_UPDATED", "forty", "fifty"]
    assert storage.is_sorted()

    assert storage.remove(30) == "THIRTY_UPDATED"
    assert list(storage_view.keys) == [20, 40, 50]
    assert list(storage_view.values) == ["twenty", "forty", "fifty"]
    assert storage.is_sorted()

    assert storage.remove(50) == "fifty"
    assert list(storage_view.keys) == [20, 40]
    assert list(storage_view.values) == ["twenty", "forty"]
    assert storage.is_sorted()

    assert storage.remove(999) is None
    assert len(storage) == 2

    # View remains valid and functional
    v = storage.view()
    assert v.find(20) == "twenty"
    assert v.find(40) == "forty"
    assert v.find(10) is None
    assert v.find(30) is None


def test_cont_14_mutable_storages_fixed_array_and_entry_count():
    """TEST-CONT-14 / GOTCHA-CONT-04: fixed buffers keep borrowed views live.

    Mutable storages track valid entry count and reject growth past capacity.
    """
    # 1. MutableFlatMapStorage
    m: MutableFlatMapStorage[int, str] = MutableFlatMapStorage(capacity=4)
    assert len(m._buffer) == 4
    assert m.count == 0
    assert len(m) == 0
    assert m._buffer == [None, None, None, None]

    assert m.insert(30, "thirty") is True
    assert m.insert(10, "ten") is True
    assert m.insert(40, "forty") is True
    assert m.insert(20, "twenty") is True
    assert m.count == 4
    assert len(m._buffer) == 4  # Array length strictly unchanged
    assert list(m.view().keys) == [10, 20, 30, 40]

    # Exceeding capacity returns False without altering storage
    assert m.insert(50, "fifty") is False
    assert m.count == 4
    assert len(m._buffer) == 4

    # In-place key update succeeds without increasing count
    assert m.insert(30, "THIRTY") is True
    assert m.count == 4
    assert m.view().find(30) == "THIRTY"

    # Removal shifts in-place and zeroes vacated trailing slot
    assert m.remove(20) == "twenty"
    assert m.count == 3
    assert len(m._buffer) == 4
    assert m._buffer[3] is None
    assert list(m.view().keys) == [10, 30, 40]

    # 2. MutableFlatSetStorage
    s: MutableFlatSetStorage[int] = MutableFlatSetStorage(capacity=3)
    assert len(s._buffer) == 3
    assert s.count == 0
    assert s._buffer == [None, None, None]

    assert s.insert(200) is True
    assert s.insert(100) is True
    assert s.insert(300) is True
    assert s.count == 3
    assert len(s._buffer) == 3

    # Exceeding capacity returns False
    assert s.insert(400) is False
    assert s.count == 3

    # Removal decrements count and sets vacated slot to None
    assert s.remove(100) is True
    assert s.count == 2
    assert len(s._buffer) == 3
    assert s._buffer[2] is None
    assert list(s.view().keys) == [200, 300]

    # 3. MutableRadixBinaryTreeStorage
    r: MutableRadixBinaryTreeStorage[str] = MutableRadixBinaryTreeStorage(capacity=3, radix_shift=4)
    assert len(r._buffer) == 3

    assert r.count == 0
    assert r._buffer == [None, None, None]

    assert r.insert(30, "C") is True
    assert r.insert(10, "A") is True
    assert r.insert(20, "B") is True
    assert r.count == 3
    assert len(r._buffer) == 3

    # Exceeding capacity returns False
    assert r.insert(40, "D") is False
    assert r.count == 3

    # Removal decrements count
    assert r.remove(10) == "A"
    assert r.count == 2
    assert len(r._buffer) == 3
    assert r._buffer[2] is None
    assert [entry[0] for entry in r.view().entries] == [20, 30]
    rv = r.view()
    assert rv.find(20) == "B"
    assert rv.find(30) == "C"
    assert rv.find(10) is None


@pytest.mark.parametrize("bits", (1, 2, 4))
def test_cont_15_read_only_bit_views_have_no_write_api(bits: int) -> None:
    """TEST-CONT-15: 読み取り専用ビューと部分ビューは値を保ち、putを提供しない。"""
    raw = bytes((0xA5, 0x3C))
    count = len(raw) * 8 // bits
    expected = tuple(
        (int.from_bytes(raw, "little") // (2 ** (bits * i))) % (2**bits) for i in range(count)
    )
    storage = ReadOnlyBitStorage(raw, bits, count)
    view = storage.view()
    sub = view.slice(1, count - 1)
    assert tuple(view.at(i) for i in range(count)) == expected
    assert tuple(sub.at(i) for i in range(len(sub))) == expected[1:-1]
    assert view.storage is storage.buffer
    assert sub.storage is storage.buffer
    assert not hasattr(view, "put")
    assert not hasattr(sub, "put")
    with pytest.raises(AssertionError):
        sub.at(len(sub))
    with pytest.raises(AssertionError):
        sub.slice(0, len(sub) + 1)


def test_cont_16_radix_projection_keeps_lookup_ranges_ordered_and_bounded():
    """TEST-CONT-16: 非単調射影で生成・変更しても登録値の検索と容量を保つ。"""
    keys = (0x00000001, 0x00000002, 0x01000000)
    values = ("one", "two", "high")
    storage = ReadOnlyRadixBinaryTreeStorage.create(
        keys,
        values,
        radix_shift=24,
        key_transform=bswap32,
    )
    view = storage.view()
    assert tuple(entry[0] for entry in storage.entries) == (
        0x01000000,
        0x00000001,
        0x00000002,
    )
    assert view.find(0x00000001) == "one"
    assert view.find(0x00000002) == "two"
    assert view.find(0x01000000) == "high"
    assert view.find(0x00000003) is None

    mutable = MutableRadixBinaryTreeStorage[str](
        capacity=3,
        radix_shift=24,
        key_transform=bswap32,
    )
    assert len(mutable.radix_table) == 256
    assert mutable.insert(0x00000002, "two")
    assert mutable.insert(0x01000000, "high")
    assert mutable.insert(0x00000001, "one")
    mutable_view = mutable.view()
    assert mutable_view.find(0x00000001) == "one"
    assert mutable_view.find(0x00000002) == "two"
    assert mutable_view.find(0x01000000) == "high"
    assert mutable.remove(0x00000001) == "one"
    assert mutable_view.find(0x00000001) is None
    assert mutable_view.find(0x00000002) == "two"


class LookupView(Protocol):
    @property
    def entries(self) -> Sequence[tuple[int, int]]: ...

    def find(self, key: int) -> int | None: ...


class LookupStorage(Protocol):
    _buffer: list[tuple[int, int] | None]

    @property
    def count(self) -> int: ...

    def size(self) -> int: ...

    def insert(self, key: int, value: int) -> bool: ...

    def remove(self, key: int) -> int | None: ...

    def clear(self) -> None: ...

    def view(self) -> LookupView: ...


def _identity(key: int) -> int:
    return key


def _independent_bswap(key: int) -> int:
    return int.from_bytes(key.to_bytes(4, "little"), "big")


def _assert_lookup_state(
    storage: LookupStorage,
    borrowed: LookupView,
    model: dict[int, int],
    buffer: list[tuple[int, int] | None],
    capacity: int,
    projection: Callable[[int], int],
    radix_table: Sequence[int] | None,
) -> None:
    expected = tuple(sorted(model.items(), key=lambda entry: (projection(entry[0]), entry[0])))
    assert storage.size() == storage.count == len(model)
    assert 0 <= storage.count <= capacity
    assert storage._buffer is buffer
    assert len(buffer) == capacity
    assert tuple(borrowed.entries) == expected
    assert buffer[len(model) :] == [None] * (capacity - len(model))
    for key in (*range(34), 256, 0x01000000, 0x02000000, *model):
        assert borrowed.find(key) == model.get(key)
    if radix_table is not None:
        # 桶を組み立てる製品アルゴリズムを写さず、各境界より前にある要素を数える。
        expected_boundaries = tuple(
            sum((projection(key) >> 24) < prefix for key in model)
            for prefix in range(len(radix_table))
        )
        assert tuple(radix_table) == expected_boundaries


_OPERATION_KEYS = st.one_of(st.integers(0, 31), st.sampled_from((256, 0x01000000)))
_LOOKUP_OPERATIONS = st.lists(
    st.tuples(
        st.sampled_from(("insert", "remove", "clear")), _OPERATION_KEYS, st.integers(-1000, 1000)
    ),
    min_size=1,
    max_size=60,
)


@pytest.mark.parametrize("kind", ("map", "radix", "radix_bswap"))
@settings(max_examples=80, deadline=None, print_blob=True)
@example(
    capacity=2,
    operations=[
        ("insert", 1, 10),
        ("insert", 2, 20),
        ("insert", 1, 11),
        ("insert", 3, 30),
        ("remove", 2, 0),
        ("insert", 3, 30),
        ("clear", 0, 0),
        ("insert", 2, 22),
    ],
)
@given(capacity=st.integers(0, 8), operations=_LOOKUP_OPERATIONS)
def test_cont_17_lookup_operation_histories_preserve_contract(
    kind: str, capacity: int, operations: list[tuple[str, int, int]]
) -> None:
    """TEST-CONT-17 / GOTCHA-CONT-04: 更新・拒否・削除・再利用の各段で全状態を照合する。"""
    radix_table: Sequence[int] | None = None
    projection = _identity
    storage: LookupStorage
    if kind == "map":
        storage = MutableFlatMapStorage[int, int](capacity=capacity)
    else:
        if kind == "radix_bswap":
            projection = _independent_bswap
        radix = MutableRadixBinaryTreeStorage[int](
            capacity=capacity,
            radix_shift=24,
            key_transform=bswap32 if kind == "radix_bswap" else None,
        )
        storage = radix
        radix_table = radix.radix_table
    borrowed = storage.view()  # 最初に借用し、操作後に作り直さない。
    buffer = storage._buffer
    model: dict[int, int] = {}
    _assert_lookup_state(storage, borrowed, model, buffer, capacity, projection, radix_table)
    for operation, key, value in operations:
        if operation == "insert":
            accepted = key in model or len(model) < capacity
            assert storage.insert(key, value) is accepted
            if accepted:
                model[key] = value
        elif operation == "remove":
            assert storage.remove(key) == model.pop(key, None)
        else:
            storage.clear()
            model.clear()
        _assert_lookup_state(storage, borrowed, model, buffer, capacity, projection, radix_table)


@settings(max_examples=80, deadline=None, print_blob=True)
@example(
    capacity=2,
    operations=[
        ("insert", 1),
        ("insert", 2),
        ("insert", 1),
        ("insert", 3),
        ("remove", 1),
        ("insert", 3),
        ("clear", 0),
        ("insert", 2),
    ],
)
@given(
    capacity=st.integers(0, 8),
    operations=st.lists(
        st.tuples(st.sampled_from(("insert", "remove", "clear")), st.integers(0, 15)),
        min_size=1,
        max_size=60,
    ),
)
def test_cont_18_set_operation_histories_preserve_contract(
    capacity: int, operations: list[tuple[str, int]]
) -> None:
    """TEST-CONT-18 / GOTCHA-CONT-04: 重複挿入と拒否を含め、集合と借用Viewを保全する。"""
    storage = MutableFlatSetStorage[int](capacity=capacity)
    borrowed = storage.view()
    buffer = storage._buffer
    model: set[int] = set()
    for operation, key in operations:
        if operation == "insert":
            accepted = key in model or len(model) < capacity
            assert storage.insert(key) is accepted
            if accepted:
                model.add(key)
        elif operation == "remove":
            assert storage.remove(key) is (key in model)
            model.discard(key)
        else:
            storage.clear()
            model.clear()
        assert storage.count == storage.size() == len(model)
        assert storage._buffer is buffer
        assert len(buffer) == capacity
        assert buffer[len(model) :] == [None] * (capacity - len(model))
        assert tuple(borrowed.keys) == tuple(sorted(model))
        assert borrowed.size() == len(model)
        assert borrowed.empty() is (len(model) == 0)
        for query in range(-1, 17):
            assert borrowed.contains(query) is (query in model)


@pytest.mark.parametrize("bits", (1, 2, 4))
@settings(max_examples=80, deadline=None, print_blob=True)
@given(raw=st.binary(min_size=1, max_size=8), data=st.data())
def test_cont_19_nested_bit_writes_preserve_every_other_element(
    bits: int, raw: bytes, data: st.DataObject
) -> None:
    """TEST-CONT-19: 多段sliceへの書き込みは指定要素だけを更新し、拒否時は全バイトを保つ。"""
    count = len(raw) * 8 // bits
    base = 2**bits
    model = [(int.from_bytes(raw, "little") // (base**i)) % base for i in range(count)]
    buffer = bytearray(raw)
    parent = BitView(memoryview(buffer), bits=bits, count=count)
    first = data.draw(st.integers(0, count - 1), label="first")
    last = data.draw(st.integers(first + 1, count), label="last")
    child = parent.slice(first, last)
    inner_first = data.draw(st.integers(0, len(child) - 1), label="inner_first")
    inner_last = data.draw(st.integers(inner_first + 1, len(child)), label="inner_last")
    nested = child.slice(inner_first, inner_last)
    offset = first + inner_first
    assert tuple(nested.at(i) for i in range(len(nested))) == tuple(
        model[offset : offset + len(nested)]
    )
    operations = data.draw(
        st.lists(
            st.tuples(
                st.integers(0, len(nested) - 1),
                st.integers(0, base - 1),
            ),
            min_size=1,
            max_size=30,
        ),
        label="writes",
    )
    for index, value in operations:
        nested.put(index, value)
        model[offset + index] = value
        expected_bytes = sum(value * base**i for i, value in enumerate(model)).to_bytes(
            len(raw), "little"
        )
        assert bytes(buffer) == expected_bytes
        assert tuple(parent.at(i) for i in range(count)) == tuple(model)
    before = bytes(buffer)
    for index in (-1, len(nested)):
        with pytest.raises(AssertionError):
            nested.put(index, 0)
        assert bytes(buffer) == before
    for value in (-1, base):
        with pytest.raises(AssertionError):
            nested.put(0, value)
        assert bytes(buffer) == before
    with pytest.raises(AssertionError):
        nested.slice(0, len(nested) + 1)
    assert bytes(buffer) == before


@settings(max_examples=100, deadline=None, print_blob=True)
@given(
    keys=st.lists(st.integers(-32, 32), unique=True, max_size=25),
    bounds=st.lists(st.tuples(st.integers(-40, 40), st.integers(-40, 40)), min_size=2, max_size=6),
)
def test_cont_20_narrowed_views_never_restore_excluded_keys(
    keys: list[int], bounds: list[tuple[int, int]]
) -> None:
    """TEST-CONT-20 / GOTCHA-CONT-02: 絞り込みを繰り返しても除外した要素を復元しない。"""
    expected = sorted(keys)
    map_view = ReadOnlyFlatMapStorage.create(tuple((key, key * 37 + 11) for key in keys)).view()
    set_view = ReadOnlyFlatSetStorage.create(tuple(keys)).view()
    for a, b in bounds:
        lo, hi = min(a, b), max(a, b)
        expected = [key for key in expected if lo <= key <= hi]
        map_view = map_view.narrow(lo, hi)
        set_view = set_view.narrow(lo, hi)
        assert tuple(map_view.entries) == tuple((key, key * 37 + 11) for key in expected)
        assert tuple(set_view.keys) == tuple(expected)
        assert map_view.size() == set_view.size() == len(expected)
        for query in range(-33, 34):
            assert map_view.find(query) == (query * 37 + 11 if query in expected else None)
            assert set_view.contains(query) is (query in expected)


@pytest.mark.parametrize("bits", (1, 2, 4))
def test_cont_21_bit_storage_fill_clear_updates_borrowed_view(bits: int) -> None:
    """TEST-CONT-21: fill/clearは全論理要素を更新し、既存の借用Viewへ反映する。"""
    count = 9  # 最終バイトを完全には使わない領域も含める。
    storage = MutableBitStorage(count=count, bits=bits, default=1)
    borrowed = storage.view()
    buffer = storage.buffer
    for value in (1, (1 << bits) - 1, 0):
        storage.fill(value)
        assert tuple(borrowed.at(i) for i in range(count)) == (value,) * count
        assert storage.buffer is buffer
        assert len(buffer) == (count * bits + 7) // 8
    storage.put(count - 1, 1)
    storage.clear()
    assert tuple(borrowed.at(i) for i in range(count)) == (0,) * count
    assert storage.buffer is buffer


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
