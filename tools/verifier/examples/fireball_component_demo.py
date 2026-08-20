"""
examples/fireball_component_demo.py: FireballVerifier を使用したコンポーネント形式検証デモ
"""

from tools.verifier.fireball_verifier import FireballModel, CppTestGenerator


def run_fireball_verification():
    # 1. Fireball コンポーネントモデルの定義（要求キーワード紐付け）
    model = FireballModel("RingBufferQueue", keywords=["{COMP_RINGBUF_001}", "{REQ_BUFFER_OVERFLOW_001}"])

    # 初期状態: 容量 max=2, 現在数 count=0
    model.set_init_state(count=0, max_capacity=2)

    # 遷移ルールの定義
    model.rule(
        name="Push",
        when=lambda s: s["count"] < s["max_capacity"],
        action=lambda s: {**s, "count": s["count"] + 1}
    )

    model.rule(
        name="Pop",
        when=lambda s: s["count"] > 0,
        action=lambda s: {**s, "count": s["count"] - 1}
    )

    # 不変条件 (Invariant) の定義
    model.invariant(
        name="BufferNotOverflow",
        condition=lambda s: s["count"] <= s["max_capacity"],
        description="バッファ要素数が最大容量を超えないこと"
    )

    model.invariant(
        name="StrictSingleItemLimit",
        condition=lambda s: s["count"] <= 1,
        description="単一要素制限違反デモ（反例生成テスト）"
    )

    model.invariant(
        name="BufferNonNegative",
        condition=lambda s: s["count"] >= 0,
        description="バッファ要素数が負にならないこと"
    )

    # 2. モデル検査の実行
    results = model.verify()

    print(f"=== Fireball モデル検査結果 [{model.name}] ===")
    for res in results:
        status = "PASS" if res.is_valid else "FAIL"
        print(f"[{status}] {res.property_name} (探索状態数: {res.checked_states_count})")
        print(f"       Traceability Keywords: {res.keywords}")
        if not res.is_valid:
            print(f"       Error: {res.error_message}")
            cpp_test = CppTestGenerator.generate_cpp_test(res, class_name="RingBufferTest")
            print("\n--- 自動生成された C++ 再現テスト ---")
            print(cpp_test)


if __name__ == "__main__":
    run_fireball_verification()
