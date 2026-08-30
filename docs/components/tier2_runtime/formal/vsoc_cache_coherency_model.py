"""
docs/components/tier2_runtime/formal/vsoc_cache_coherency_model.py
pyModelChecking による vSoC JIT キャッシュ整合性・Debugger 介入安全性・ローテーション
リソース有界性の形式検証（証明・変異検査対応）モデル

runtime_vsoc.md 6.1 が主張する不変条件のうち、実行状態と Safepoint 応答性
（vsoc_state_model.py が担当）を除く 3 件をこのモデルで証明する。世代スタンプと
リソース回収というキャッシュ寿命の関心事だけを切り出すことで、実行エンジンの
状態機械と混ぜて状態空間を爆発させることを避けている（document_structure.md 2.1
「検証可能性 (Verification Tractability) の維持」）。
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AF, Not, Imply, AtomicProposition

BACKS = [
    "components/tier2_runtime/runtime_vsoc.md",
    "components/tier2_runtime/debug_manager.md",
    "components/tier3_jit/jit_compiler.md",
    "components/tier3_platform/platform_memory.md",
]


def build_model(*, guards: bool = True) -> Kripke:
    """
    JIT キャッシュ世代管理・デバッガ flush 因果順序・バンク回収の保護証明モデル
    正常状態:
    - s_interp: インタープリタ実行中（JIT キャッシュは参照のみ）
    - s_exec_fresh: 現行世代 (generation cookie 一致) の JIT トレースを実行中
    - s_rotate: Active 満杯による 3面リングローテーション実行中
    - s_reclaimed: Oldest バンクの Purge とエントリ表スロット回収が完了
    - s_dbg_write: デバッガがゲストメモリを書き換え、全既存トレースが陳腐化 (dirty)
    - s_safepoint: Safepoint でデバッガ介入フラグを検出
    - s_flushing: 全バンクの無効化（generation cookie インクリメント）実行中
    - s_flushed: flush 完了、キャッシュ空
    違反状態:
    - s_exec_stale: デバッガ書き込み後、flush 完了前に旧世代コードを実行した状態
    - s_gen_regressed: generation cookie がバンク間で逆行・不一致になった状態
    - s_leaked_bank: ローテーションでバンクを破棄したがエントリ表スロットを回収しなかった状態
    - s_flush_stalled: dirty のまま flush が永久に完了しない状態
    """
    S = [
        # --- 正常状態 ---
        "s_interp",
        "s_exec_fresh",
        "s_rotate",
        "s_reclaimed",
        "s_dbg_write",
        "s_safepoint",
        "s_flushing",
        "s_flushed",
        # --- 違反状態 ---
        "s_exec_stale",
        "s_gen_regressed",
        "s_leaked_bank",
        "s_flush_stalled",
    ]
    S0 = {"s_interp"}
    R = [
        # 通常実行サイクル
        ("s_interp", "s_exec_fresh"),  # lookup ヒット ➔ 現行世代トレースへ
        ("s_exec_fresh", "s_interp"),  # トレース脱出
        # 3面リングローテーション（Oldest の Purge と回収は不可分に行う）
        ("s_interp", "s_rotate"),
        ("s_rotate", "s_reclaimed"),
        ("s_reclaimed", "s_interp"),
        # デバッガ介入 ➔ Safepoint 検出 ➔ flush ➔ インタープリタへフォールバック
        ("s_interp", "s_dbg_write"),
        ("s_exec_fresh", "s_dbg_write"),
        ("s_dbg_write", "s_safepoint"),
        ("s_safepoint", "s_flushing"),
        ("s_flushing", "s_flushed"),
        ("s_flushed", "s_interp"),
        # 違反状態の自己ループ
        ("s_exec_stale", "s_exec_stale"),
        ("s_gen_regressed", "s_gen_regressed"),
        ("s_leaked_bank", "s_leaked_bank"),
        ("s_flush_stalled", "s_flush_stalled"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. Safepoint での generation cookie 照合を省くと、デバッガ書き込み後・flush 完了前に
        #    登録済み exec_trace ポインタから旧世代コードへ再突入できてしまう
        R = R + [("s_dbg_write", "s_exec_stale")]
        # 2. generation cookie を全バンク一括でインクリメントせず個別更新にすると、
        #    flush 途中でバンク間の世代が食い違い、単調性が壊れる
        R = R + [("s_flushing", "s_gen_regressed")]
        # 3. ローテーション時に Oldest バンクの Purge だけ行いエントリ表スロットの回収を
        #    怠ると、回収されないスロットが蓄積する（リソースリーク）
        R = R + [("s_rotate", "s_leaked_bank")]
        # 4. flush を Safepoint で即時実行せず遅延可能にすると、dirty のまま
        #    flush が完了しない経路が生じる
        R = R + [("s_safepoint", "s_flush_stalled")]

    L = {
        "s_interp": {"interp_mode", "gen_consistent", "all_banks_accounted"},
        "s_exec_fresh": {"executing", "fresh", "gen_consistent", "all_banks_accounted"},
        "s_rotate": {"rotating", "gen_consistent"},
        "s_reclaimed": {"reclaimed", "gen_consistent", "all_banks_accounted"},
        "s_dbg_write": {"dirty", "debug_pending", "gen_consistent"},
        "s_safepoint": {"dirty", "safepoint", "gen_consistent"},
        "s_flushing": {"dirty", "flushing", "gen_consistent"},
        "s_flushed": {
            "flushed",
            "cache_empty",
            "gen_consistent",
            "all_banks_accounted",
        },
        # --- 違反状態のラベル ---
        "s_exec_stale": {"executing", "stale_code", "dirty"},
        "s_gen_regressed": {"gen_regressed"},
        "s_leaked_bank": {"rotating", "leaked"},
        "s_flush_stalled": {"dirty", "flush_stalled"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    return [
        {
            "name": "no_stale_code_after_debugger_write",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("stale_code"))),
            "violation": AtomicProposition("stale_code"),
            # runtime_vsoc.md 6.1「Debugger 安全性」:
            # デバッガがメモリを変更した後、キャッシュ flush が完了するまで旧コードは実行されない。
            "expect": True,
        },
        {
            "name": "cache_generation_never_regresses",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("gen_regressed"))),
            "violation": AtomicProposition("gen_regressed"),
            # runtime_vsoc.md 6.1「キャッシュ整合性」:
            # generation cookie を全バンク一括更新することで、バンク間の世代不一致は到達不能。
            "expect": True,
        },
        {
            "name": "rotation_reclaims_every_bank",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("leaked"))),
            "violation": AtomicProposition("leaked"),
            # runtime_vsoc.md 6.1「リソース有界性」:
            # Purge とエントリ表スロット回収を不可分に行うため、未回収スロットは到達不能。
            "expect": True,
        },
        {
            "name": "debugger_flush_completes",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("dirty"),
                    AF(AtomicProposition("flushed")),
                )
            ),
            "violation": AtomicProposition("flush_stalled"),
            # dirty になった以上、flush は必ず完了する (AF)。
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
