"""
tools/verifier/components/jit_cache_safety.py
JITCacheDoubleBufferVerifier: JIT コードキャッシュ (Active/Old) ダブルバッファモデル
・Active および Old 双方での直接実行をサポート
・実行カウンタ (Hotness Counter) により、繰り返し使われる Hot コードのみ Active へ昇格 (Promote)
・1回限りの Cold コードは GC Swap 時に破棄 (Evict)
キーワード: {JIT_DoubleBuffer_Cache}, {Challenge_JITCacheEfficiency}
"""

from tools.verifier.fireball_verifier import FireballModel, VerificationResult


def verify_jit_cache_safety() -> VerificationResult:
    """
    Active/Old 両実行サポート & Hotness カウンタ昇格ポリシーのモデル検査
    """
    model = FireballModel(
        name="JITCacheDoubleBufferModel",
        keywords=["{JIT_DoubleBuffer_Cache}", "{Challenge_JITCacheEfficiency}"]
    )

    HOT_THRESHOLD = 2

    # 状態:
    #   active_bank: 'bank_A' | 'bank_B'
    #   old_bank: 'bank_B' | 'bank_A'
    #   code_location: 'active' | 'old' | 'evicted'
    #   pc_pointing: 'active' | 'old' | 'interpreter'
    #   exec_count: 実行回数
    model.set_init_state(
        active_bank="bank_A",
        old_bank="bank_B",
        code_location="old",       # 前回の Swap で Old に配置された状態からスタート
        pc_pointing="old",         # Old 側のコードを実行可能
        exec_count=1,
    )

    # --- Rule 1: Execute Active Code ---
    model.rule(
        name="Execute_Active_Code",
        when=lambda s: s["pc_pointing"] == "active" and s["code_location"] == "active",
        action=lambda s: {**s, "exec_count": s["exec_count"] + 1}
    )

    # --- Rule 2: Execute Old Code (Oldのコードも実行可能) ---
    model.rule(
        name="Execute_Old_Code",
        when=lambda s: s["pc_pointing"] == "old" and s["code_location"] == "old",
        action=lambda s: {**s, "exec_count": s["exec_count"] + 1}
    )

    # --- Rule 3: Promote Hot Code (Hot到達時に Old から Active へコピー昇格) ---
    model.rule(
        name="Promote_Hot_Code_To_Active",
        when=lambda s: s["code_location"] == "old" and s["exec_count"] >= HOT_THRESHOLD,
        action=lambda s: {**s, "code_location": "active", "pc_pointing": "active"}
    )

    # --- Rule 4: Fallback to Interpreter ---
    model.rule(
        name="Fallback_To_Interpreter",
        when=lambda s: s["pc_pointing"] != "interpreter",
        action=lambda s: {**s, "pc_pointing": "interpreter"}
    )

    # --- Rule 5: GC Swap (Active が一杯になり Swap 発生) ---
    def gc_swap_action(s: dict) -> dict:
        new_active = s["old_bank"]
        new_old = s["active_bank"]
        
        # Old に留まっていたコードは破棄 (Evicted)
        new_location = "old" if s["code_location"] == "active" else "evicted"
        
        return {
            **s,
            "active_bank": new_active,
            "old_bank": new_old,
            "code_location": new_location,
            "pc_pointing": "interpreter",
        }

    model.rule(
        name="GC_Swap",
        when=lambda s: s["pc_pointing"] == "interpreter",
        action=gc_swap_action
    )

    # --- Rule 6: Re-entry JIT ---
    model.rule(
        name="Reentry_JIT",
        when=lambda s: s["pc_pointing"] == "interpreter" and s["code_location"] in ("active", "old"),
        action=lambda s: {**s, "pc_pointing": s["code_location"]}
    )

    # 不変式 1: Hot に達したコードが誤って Swap 時に Evict されないこと
    model.invariant(
        name="HotCodePromotedToActive",
        condition=lambda s: not (
            s["exec_count"] >= HOT_THRESHOLD and s["code_location"] == "evicted"
        ),
        description="Hot カウンタに達したコードは Swap 時に破棄されず Active へ保持されること"
    )

    # 不変式 2: 破棄された領域（Evicted）を実行しようとしないこと
    model.invariant(
        name="NoExecutionOfEvictedCode",
        condition=lambda s: not (
            s["code_location"] == "evicted" and s["pc_pointing"] != "interpreter"
        ),
        description="Evict (破棄) されたコードへの dangling アクセスが発生しないこと"
    )

    results = model.verify()
    return results[0]


if __name__ == "__main__":
    res = verify_jit_cache_safety()
    print(f"[{'PASS' if res.is_valid else 'FAIL'}] {res.property_name} (キーワード: {res.keywords})")

