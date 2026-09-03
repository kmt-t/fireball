from __future__ import annotations

"""
Unit tests for Tier 1 Core: System Containers & Views
Traceability: system_containers_test_spec.md
"""

import sys
from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _TESTS_DIR,
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from system_containers import (
    BitView,
    FlatMapView,
    FlatSetView,
    MutableBitStorage,
    MutableFlatMapStorage,
    MutableFlatSetStorage,
    MutableRadixBinaryTreeStorage,
    RadixBinaryTreeView,
    ReadOnlyBitStorage,
    ReadOnlyFlatMapStorage,
    ReadOnlyFlatSetStorage,
    ReadOnlyRadixBinaryTreeStorage,
    lookup_jit_entry_radix,
)


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def test_cont_01_flat_map_view_find_binary_search():
    """CONT-01: flat_map_view.find performs O(log n) binary search returning value or None."""
    entries = [(10, 100), (20, 200), (30, 300), (40, 400), (50, 500), (60, 600)]
    view = FlatMapView(entries)
    assert view.find(30) == 300
    assert view.find(10) == 100
    assert view.find(60) == 600
    assert view.find(25) is None
    assert view.find(5) is None
    assert view.find(70) is None
    assert view.size() == 6
    assert not view.empty()


def test_cont_02_narrow_monotonic_shrinkage():
    """CONT-02: narrow(lo, hi) produces monotonic sub-window subset."""
    entries = [(10, 1), (20, 2), (30, 3), (40, 4), (50, 5), (60, 6), (70, 7), (80, 8)]
    v0 = FlatMapView(entries)
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
    """CONT-03: slice must only ever shrink within parent view bounds."""
    entries = [(10, 1), (20, 2), (30, 3), (40, 4), (50, 5)]
    v0 = FlatMapView(entries)
    v1 = v0.slice(1, 4)
    assert v1.size() == 3
    assert v1.find(20) == 2
    assert v1.find(40) == 4
    try:
        v1.slice(0, 5)  # Expanding beyond v1's window [1, 4] must fail
        raise AssertionError("Expected ValueError when expanding slice")
    except ValueError:
        pass


def test_cont_04_flat_set_view_membership_only():
    """CONT-04: flat_set_view answers contains(key) with bool, carries no value span."""
    keys = [100, 200, 300, 400]
    set_view = FlatSetView(keys)
    assert set_view.contains(200) is True
    assert set_view.contains(250) is False
    assert (300 in set_view) is True
    assert (50 in set_view) is False
    assert not hasattr(set_view, "values"), "flat_set_view must not carry a values span"


def test_cont_05_bit_view_adjacent_element_non_destructive():
    """CONT-05: bit_view put/at modifies targeted sub-byte element without corrupting adjacent elements."""
    storage = bytearray(4)  # 4 bytes = 16 2-bit elements
    bv = BitView(storage, bits=2, origin=0, count=16)
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
    """CONT-06: bit_view.slice absorbs non-byte-aligned bit origins."""
    storage = bytearray(2)  # 8 2-bit elements
    bv = BitView(storage, bits=2, origin=0, count=8)
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
    """CONT-07: bit_view allows only 1, 2, 4 bits dividing 8."""
    storage = bytearray(4)
    # Valid
    BitView(storage, bits=1, count=32)
    BitView(storage, bits=2, count=16)
    BitView(storage, bits=4, count=8)
    # Invalid
    for invalid in (3, 5, 6, 7, 8):
        try:
            BitView(storage, bits=invalid, count=4)
            raise AssertionError(f"Expected ValueError for invalid Bits={invalid}")
        except ValueError:
            pass


def test_cont_08_radix_binary_tree_view_coarse_radix_lookup():
    """CONT-08: radix_binary_tree_view uses O(1) Radix Table prefix + local binary search."""
    keys = [0x0010, 0x0020, 0x0110, 0x0120, 0x0130, 0x0210]
    values = ["T0_A", "T0_B", "T1_A", "T1_B", "T1_C", "T2_A"]
    # Radix shift = 8 -> prefix = pc >> 8
    # Prefix 0: [0, 2), Prefix 1: [2, 5), Prefix 2: [5, 6)
    radix_table = [0, 2, 5, 6]
    tree = RadixBinaryTreeView(keys, values, radix_table, radix_shift=8)
    assert tree.find(0x0120) == "T1_B"
    assert tree.find(0x0010) == "T0_A"
    assert tree.find(0x0210) == "T2_A"
    assert tree.find(0x0199) is None
    assert tree.find(0x0300) is None


def test_cont_09_jit_entry_lookup_card_table_prefilter():
    """CONT-09: lookup_jit_entry performs O(1) Card Marking check before searching (card_shift=3, 8B/card)."""
    card_storage = bytearray(4)
    card_table = BitView(card_storage, bits=2, origin=0, count=16)
    keys = [0x0010, 0x0020]
    values = ["NATIVE_0010", "NATIVE_0020"]
    # Prefix 0: empty [0, 0), Prefix 1: [0, 1), Prefix 2: [1, 2)
    radix_table = [0, 0, 1, 2]
    tree = RadixBinaryTreeView(keys, values, radix_table, radix_shift=4)
    # PC 0x0010 (16) is card 2 (16 >> 3). Currently UNEXECUTED (0) -> lookup returns None without search
    assert lookup_jit_entry_radix(tree, card_table, pc=0x0010, card_shift=3) is None
    # Mark card 2 as COMPILED (3)
    card_table.put(2, 3)
    assert lookup_jit_entry_radix(tree, card_table, pc=0x0010, card_shift=3) == "NATIVE_0010"


def test_cont_10_container_type_separation():
    """CONT-10: flat_map_view and flat_set_view have strictly separated type responsibilities."""
    keys = [1, 2, 3]
    vals = [10, 20, 30]
    entries = list(zip(keys, vals, strict=False))
    m = FlatMapView(entries)
    s = FlatSetView(keys)
    assert type(m) is FlatMapView
    assert type(s) is FlatSetView
    assert type(s) is not FlatMapView
    assert hasattr(m, "values")
    assert not hasattr(s, "values")


def test_cont_11_storage_and_view_ownership_separation():
    """CONT-11: Data storage ownership is strictly separated from non-owning views (AoS, Set, Radix, Bit).
    Mutations (insert, remove, put, fill) are performed strictly on Mutable Storages, never on Views."""
    # 1. FlatMap: ReadOnly vs Mutable Storage vs non-owning View
    ro_map = ReadOnlyFlatMapStorage.create([(10, "A"), (20, "B"), (30, "C")])
    v_ro = ro_map.view()
    assert v_ro.find(20) == "B"
    assert v_ro.entries is ro_map.entries
    assert not hasattr(v_ro, "insert")
    assert not hasattr(v_ro, "remove")

    mut_map = MutableFlatMapStorage(capacity=8)
    assert mut_map.insert(100, "X")
    assert mut_map.insert(200, "Y")
    v_mut = mut_map.view()
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
    assert rv1.keys is ro_radix.keys
    assert rv1.values is ro_radix.values
    assert rv1.radix_table is ro_radix.radix_table
    assert not hasattr(rv1, "insert")
    assert not hasattr(rv1, "remove")

    mut_radix = MutableRadixBinaryTreeStorage(capacity=16, radix_shift=4)
    assert mut_radix.insert(10, "A")
    assert mut_radix.insert(30, "C")
    rv_mut = mut_radix.view()
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
    assert bv_ro.at(0) == 1
    assert bv_ro.at(1) == 3
    assert bv_ro.storage is ro_bit.buffer

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


def test_cont_12_mutable_flat_map_storage_standard_sort():
    """CONT-12: MutableFlatMapStorage manages fixed-capacity sorted entries (AoS) and presents FlatMapView."""
    entries = [(50, "E"), (10, "A"), (40, "D"), (20, "B"), (30, "C")]
    sorted_entries = sorted(entries, key=lambda x: x[0])
    map_storage = MutableFlatMapStorage(capacity=8)
    for k, v in sorted_entries:
        map_storage.insert(k, v)
    assert map_storage.is_sorted()
    assert map_storage.keys == [10, 20, 30, 40, 50]
    assert map_storage.values == ["A", "B", "C", "D", "E"]
    assert map_storage.entries == [(10, "A"), (20, "B"), (30, "C"), (40, "D"), (50, "E")]

    # View correctly finds via binary search
    v = map_storage.view()
    assert v.find(10) == "A"
    assert v.find(30) == "C"
    assert v.find(50) == "E"
    assert v.find(99) is None


def test_cont_13_mutable_flat_map_sorted_insert_remove():
    """CONT-13: MutableFlatMapStorage maintains sorted order across arbitrary insert and remove calls."""
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
    assert storage.keys == [10, 20, 30, 40, 50]
    assert storage.values == ["ten", "twenty", "thirty", "forty", "fifty"]

    # Updating existing key replaces value, returns True (size stays 5)
    assert storage.insert(30, "THIRTY_UPDATED") is True
    assert len(storage) == 5
    assert storage.keys == [10, 20, 30, 40, 50]
    assert storage.values == ["ten", "twenty", "THIRTY_UPDATED", "forty", "fifty"]

    # Removal maintains sorted order
    assert storage.remove(10) == "ten"
    assert storage.keys == [20, 30, 40, 50]
    assert storage.values == ["twenty", "THIRTY_UPDATED", "forty", "fifty"]
    assert storage.is_sorted()

    assert storage.remove(30) == "THIRTY_UPDATED"
    assert storage.keys == [20, 40, 50]
    assert storage.values == ["twenty", "forty", "fifty"]
    assert storage.is_sorted()

    assert storage.remove(50) == "fifty"
    assert storage.keys == [20, 40]
    assert storage.values == ["twenty", "forty"]
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
    """CONT-14: Mutable storages allocate fixed-length arrays upfront, track valid entry count,

    and allow insertions up to capacity without dynamic array reallocation.
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
    assert m.keys == [10, 20, 30, 40]

    # Exceeding capacity returns False without altering storage
    assert m.insert(50, "fifty") is False
    assert m.count == 4
    assert len(m._buffer) == 4

    # In-place key update succeeds without increasing count
    assert m.insert(30, "THIRTY") is True
    assert m.count == 4
    assert m.find(30) == "THIRTY"

    # Removal shifts in-place and zeroes vacated trailing slot
    assert m.remove(20) == "twenty"
    assert m.count == 3
    assert len(m._buffer) == 4
    assert m._buffer[3] is None
    assert m.keys == [10, 30, 40]

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
    assert s.keys == [200, 300]

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
    assert r.keys == [20, 30]
    rv = r.view()
    assert rv.find(20) == "B"
    assert rv.find(30) == "C"
    assert rv.find(10) is None


# ===========================================================================
# Cooperative Multitasking & Idle-Hook Integration (YIELD / IDLE / TIER)
# ===========================================================================


if __name__ == "__main__":
    test_cont_01_flat_map_view_find_binary_search()
    test_cont_02_narrow_monotonic_shrinkage()
    test_cont_03_slice_monotonic_shrinkage_and_bounds()
    test_cont_04_flat_set_view_membership_only()
    test_cont_05_bit_view_adjacent_element_non_destructive()
    test_cont_06_bit_view_unaligned_slice_origin_absorption()
    test_cont_07_bit_view_allowed_bits_enforced()
    test_cont_08_radix_binary_tree_view_coarse_radix_lookup()
    test_cont_09_jit_entry_lookup_card_table_prefilter()
    test_cont_10_container_type_separation()
    test_cont_11_storage_and_view_ownership_separation()
    test_cont_12_mutable_flat_map_storage_standard_sort()
    test_cont_13_mutable_flat_map_sorted_insert_remove()
    test_cont_14_mutable_storages_fixed_array_and_entry_count()
    print("[PASS] All 14 System Containers & Views tests passed.")
