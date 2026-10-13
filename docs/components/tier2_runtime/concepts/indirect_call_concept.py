"""不変テーブルから選択した関数だけを構造的なシグネチャで照合する。"""

BACKS = ["components/tier2_runtime/interpreter.md", "specs/wasm_instruction_set.md"]

type FunctionSignature = tuple[tuple[int, ...], tuple[int, ...]]


class IndirectCallTrap(Exception):
    """選択先の範囲、初期化、型の実行時エラーを示す。"""


def resolve_indirect_target(
    table: tuple[int | None, ...],
    function_signatures: tuple[FunctionSignature, ...],
    slot: int,
    expected: FunctionSignature,
) -> int:
    """ロード済みの関数索引は有効であり、呼出し先を返すまで本体を実行しない。"""
    assert 0 <= slot <= 0xFFFF_FFFF
    if slot >= len(table):
        raise IndirectCallTrap("TABLE_INDEX_OUT_OF_BOUNDS")
    target = table[slot]
    if target is None:
        raise IndirectCallTrap("TABLE_SLOT_UNINITIALIZED")
    assert 0 <= target < len(function_signatures)
    if expected != function_signatures[target]:
        raise IndirectCallTrap("INDIRECT_CALL_TYPE_MISMATCH")
    return target


def test_mixed_table_and_structural_signatures() -> None:
    """未選択の別型は許可し、別定義の同じ構造は同じ型として扱う。"""
    i32_signature: FunctionSignature = ((0x7F,), (0x7F,))
    i64_signature: FunctionSignature = ((0x7E,), (0x7F,))
    duplicate: FunctionSignature = ((0x7F,), (0x7F,))
    signatures = (i32_signature, i64_signature, duplicate)
    table = (0, 1, 2, None)
    assert resolve_indirect_target(table, signatures, 0, i32_signature) == 0
    assert resolve_indirect_target(table, signatures, 1, i64_signature) == 1
    assert resolve_indirect_target(table, signatures, 2, i32_signature) == 2
    for slot, code in (
        (1, "INDIRECT_CALL_TYPE_MISMATCH"),
        (3, "TABLE_SLOT_UNINITIALIZED"),
        (0xFFFF_FFFF, "TABLE_INDEX_OUT_OF_BOUNDS"),
    ):
        try:
            resolve_indirect_target(table, signatures, slot, i32_signature)
        except IndirectCallTrap as failure:
            assert failure.args == (code,)
        else:
            raise AssertionError(code)
    assert table == (0, 1, 2, None)


if __name__ == "__main__":
    test_mixed_table_and_structural_signatures()
    print("[PASS] selected indirect-call target and structural signature")
