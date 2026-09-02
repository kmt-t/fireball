"""
docs/components/tier2_runtime/formal/vsoc_cache_coherency_model.py
pyModelChecking による vSoC JIT キャッシュ整合性・Debugger 介入安全性・
共有メモリ権限剥奪時 TLB フラッシュ（VMMIO-GOTCHA-03）・常駐トレース二重コンパイル抑止（JITR-GOTCHA-01）
およびローテーションリソース有界性の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, AtomicProposition, Imply, Not

BACKS = [
    "components/tier2_runtime/runtime_vsoc.md",
    "components/tier2_runtime/debug_manager.md",
    "components/tier3_jit/jit_compiler.md",
    "components/tier3_platform/platform_memory.md",
]


def build_model(*, guards: bool = True) -> Kripke:
    """
    JIT キャッシュ世代管理・デバッガ flush 因果順序・共有メモリ Revoke 連動・バンク回収の保護証明モデル
    - s_interp: インタープリタ実行中（JIT キャッシュは参照のみ）
    - s_exec_fresh: 現行世代 (generation cookie 一致) の JIT トレースを実行中
    - s_check_resident: JITR-GOTCHA-01: コンパイル前にキャッシュ常駐を確認中
    - s_skip_compile: 既に常駐済みのためコンパイルを抑止して直接実行へ
    - s_rotate: Active 満杯による 3面リングローテーション実行中
    - s_reclaimed: Oldest バンクの Purge とエントリ表スロット回収が完了
    - s_dbg_write: デバッガがゲストメモリを書き換え、全既存トレースが陳腐化 (dirty)
    - s_shm_revoke: VMMIO-GOTCHA-03: 共有メモリ権限剥奪トランザクション発生 (dirty)
    - s_safepoint: Safepoint でデバッガ介入または Revoke フラグを検出
    - s_flushing: 全バンク無効化および TLB フラッシュ実行中
    - s_flushed: flush 完了、キャッシュ整合性回復
    - s_exec_stale: 違反状態（デバッガ書き込み後、flush 完了前に旧世代コードを実行した状態）
    - s_gen_regressed: 違反状態（generation cookie がバンク間で逆行・不一致になった状態）
    - s_leaked_bank: 違反状態（ローテーションでバンクを破棄したがエントリ表スロットを回収しなかった状態）
    - s_flush_stalled: 違反状態（dirty のまま flush が永久に完了しない状態）
    - s_duplicate_compile: 違反状態（常駐済みトレースを重複コンパイルしてキャッシュを浪費した状態）
    """
    S = [
        # --- 正常状態 ---
        "s_interp",
        "s_exec_fresh",
        "s_check_resident",
        "s_skip_compile",
        "s_rotate",
        "s_reclaimed",
        "s_dbg_write",
        "s_shm_revoke",
        "s_safepoint",
        "s_flushing",
        "s_flushed",
        # --- 違反状態 ---
        "s_exec_stale",
        "s_gen_regressed",
        "s_leaked_bank",
        "s_flush_stalled",
        "s_duplicate_compile",
    ]
    S0 = {"s_interp"}
    R = [
        # 通常実行サイクル
        ("s_interp", "s_exec_fresh"),  # lookup ヒット ➔ 現行世代トレースへ
        ("s_exec_fresh", "s_interp"),  # トレース脱出
        # JITR-GOTCHA-01: 常駐済みトレースの二重コンパイル抑止
        ("s_interp", "s_check_resident"),
        ("s_check_resident", "s_skip_compile"),
        ("s_skip_compile", "s_exec_fresh"),
        # 3面リングローテーション（Oldest の Purge と回収は不可分に行う）
        ("s_interp", "s_rotate"),
        ("s_rotate", "s_reclaimed"),
        ("s_reclaimed", "s_interp"),
        # デバッガ書き込み ➔ Safepoint ➔ flush
        ("s_interp", "s_dbg_write"),
        ("s_exec_fresh", "s_dbg_write"),
        ("s_dbg_write", "s_safepoint"),
        # VMMIO-GOTCHA-03: 共有メモリ Revoke ➔ Safepoint ➔ flush
        ("s_interp", "s_shm_revoke"),
        ("s_exec_fresh", "s_shm_revoke"),
        ("s_shm_revoke", "s_safepoint"),
        ("s_safepoint", "s_flushing"),
        ("s_flushing", "s_flushed"),
        ("s_flushed", "s_interp"),
        # 違反状態の自己ループ
        ("s_exec_stale", "s_exec_stale"),
        ("s_gen_regressed", "s_gen_regressed"),
        ("s_leaked_bank", "s_leaked_bank"),
        ("s_flush_stalled", "s_flush_stalled"),
        ("s_duplicate_compile", "s_duplicate_compile"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. Safepoint での generation cookie 照合を省くと、旧世代コードへ再突入
        R = [*R, ("s_dbg_write", "s_exec_stale")]
        R = [*R, ("s_shm_revoke", "s_exec_stale")]
        # 2. generation cookie を個別更新にすると単調性が壊れる
        R = [*R, ("s_flushing", "s_gen_regressed")]
        # 3. エントリ表スロット回収を怠るとリソースリーク
        R = [*R, ("s_rotate", "s_leaked_bank")]
        # 4. flush を遅延可能にすると dirty のまま未完了
        R = [*R, ("s_safepoint", "s_flush_stalled")]
        # 5. JITR-GOTCHA-01: キャッシュ常駐確認を怠ると二重コンパイルが発生
        R = [*R, ("s_check_resident", "s_duplicate_compile")]

    L = {
        "s_interp": {"interp_mode", "gen_consistent", "all_banks_accounted"},
        "s_exec_fresh": {"executing", "fresh", "gen_consistent", "all_banks_accounted"},
        "s_check_resident": {"checking_cache", "gen_consistent"},
        "s_skip_compile": {"compile_suppressed", "gen_consistent"},
        "s_rotate": {"rotating", "gen_consistent"},
        "s_reclaimed": {"reclaimed", "gen_consistent", "all_banks_accounted"},
        "s_dbg_write": {"dirty", "debug_pending", "gen_consistent"},
        "s_shm_revoke": {"dirty", "revoke_pending", "gen_consistent"},
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
        "s_leaked_bank": {"leaked_bank"},
        "s_flush_stalled": {"flush_stalled", "dirty"},
        "s_duplicate_compile": {"duplicate_compile"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_stale = AtomicProposition("stale_code")
    bad_regress = AtomicProposition("gen_regressed")
    bad_leak = AtomicProposition("leaked_bank")
    bad_stall = AtomicProposition("flush_stalled")
    bad_duplicate = AtomicProposition("duplicate_compile")
    dirty = AtomicProposition("dirty")
    flushed = AtomicProposition("flushed")
    return [
        {
            "name": "debugger_memory_write_invalidates_stale_traces",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_stale)),
            "violation": bad_stale,
            "expect": True,  # デバッガ書き込み後や Revoke 後に旧世代コードが実行されることはない
        },
        {
            "name": "generation_monotonicity_across_banks",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_regress)),
            "violation": bad_regress,
            "expect": True,  # generation cookie は全バンクで単調かつ整合的に更新される
        },
        {
            "name": "bounded_cache_rotation_memory",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_leak)),
            "violation": bad_leak,
            "expect": True,  # 3面ローテーションで破棄されたバンクのエントリ表スロットは漏れなく回収される
        },
        {
            "name": "dirty_cache_always_flushes_promptly",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(dirty, AF(flushed))),
            "violation": bad_stall,
            "expect": True,  # デバッグ書き込みや Revoke で汚れたキャッシュは必ず有界時間内に flush される
        },
        {
            "name": "resident_trace_duplicate_compile_suppression",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_duplicate)),
            "violation": bad_duplicate,
            "expect": True,  # JITR-GOTCHA-01: 常駐済みトレースに対する二重コンパイルは完全に抑止される
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
