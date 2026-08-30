"""
docs/specs/formal/jit_stencil_epilogue_model.py
pyModelChecking による JIT ステンシルのプロローグ/エピローグ・スピルフラッシュ規約の
(1) ダーティな TOS/NOS スタックキャッシュ（R4/R5）が未フラッシュのままトレースを抜けないこと
(2) プロローグで積んだ Callee-saved レジスタは、エピローグで必ず対称にポップされること
の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AtomicProposition, Not

BACKS = ["specs/jit_stencil_catalog.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    JIT ステンシル プロローグ/エピローグ規約の変異検査対応保護証明モデル
    - s_trace_entry: トレース入口（次のトレース呼び出し待ち）
    - s_trace_dirty: トレース本体実行によりスタックキャッシュ（R4/R5）がダーティ化
    - s_trace_clean_noop: スタックキャッシュを変更しないトレース（フラッシュ不要）
    - s_epilogue_flush: エピローグのスピルフラッシュ（STR x2 で正準アドレスへ書き戻し）
    - s_prologue_push: プロローグで Callee-saved レジスタをプッシュ
    - s_epilogue_pop: エピローグで対称にポップして呼び出し元へ復帰
    - s_exit_dirty: 違反状態（ダーティなキャッシュを未フラッシュのままトレースを抜けた）
    - s_return_unbalanced: 違反状態（プッシュしたレジスタをポップせずに復帰した）
    """
    S = [
        "s_trace_entry",
        "s_trace_dirty",
        "s_trace_clean_noop",
        "s_epilogue_flush",
        "s_prologue_push",
        "s_epilogue_pop",
        "s_exit_dirty",
        "s_return_unbalanced",
    ]
    S0 = {"s_trace_entry"}
    R = [
        ("s_trace_entry", "s_trace_dirty"),
        ("s_trace_dirty", "s_epilogue_flush"),
        ("s_epilogue_flush", "s_trace_entry"),
        ("s_trace_entry", "s_trace_clean_noop"),
        ("s_trace_clean_noop", "s_trace_entry"),
        ("s_trace_entry", "s_prologue_push"),
        ("s_prologue_push", "s_epilogue_pop"),
        ("s_epilogue_pop", "s_trace_entry"),
        # 違反状態の自己ループ（Kripke 構造は全域的でなければならない）
        ("s_exit_dirty", "s_exit_dirty"),
        ("s_return_unbalanced", "s_return_unbalanced"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. スピルフラッシュ（STR x2）を外すと、ダーティなキャッシュを抱えたまま抜けてしまう
        R = R + [("s_trace_dirty", "s_exit_dirty")]
        # 2. エピローグの対称ポップを外すと、プッシュしたレジスタを積んだまま復帰してしまう
        R = R + [("s_prologue_push", "s_return_unbalanced")]

    L = {
        "s_trace_entry": {"entry"},
        "s_trace_dirty": {"dirty"},
        "s_trace_clean_noop": {"clean"},
        "s_epilogue_flush": {"flushed"},
        "s_prologue_push": {"pushed"},
        "s_epilogue_pop": {"popped"},
        "s_exit_dirty": {"exit_dirty"},  # 違反状態
        "s_return_unbalanced": {"unbalanced"},  # 違反状態
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_exit_dirty = AtomicProposition("exit_dirty")
    bad_unbalanced = AtomicProposition("unbalanced")
    return [
        {
            "name": "dirty_cache_never_exits_unflushed",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_exit_dirty)),
            "violation": bad_exit_dirty,
            "expect": True,  # エピローグのスピルフラッシュにより、未フラッシュ退出状態は到達不能
        },
        {
            "name": "prologue_epilogue_always_balanced",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_unbalanced)),
            "violation": bad_unbalanced,
            "expect": True,  # 対称なプッシュ/ポップ規約により、不均衡な復帰状態は到達不能
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
