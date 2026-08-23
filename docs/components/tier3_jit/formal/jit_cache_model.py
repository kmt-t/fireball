"""
docs/components/tier3_jit/formal/jit_cache_model.py
pyModelChecking による JIT 3面キャッシュ代謝・MPU W^X・遅延チェイニング安全性・2-bit Hotspot FSM の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AF, And, Not, Imply, AtomicProposition

BACKS = [
    "components/tier2_runtime/concepts/runtime_engine_concept.py",
    "components/tier3_jit/jit_compiler.md",
    "components/tier3_jit/jit_engine_copy_patch.md",
    "components/tier3_jit/jit_runtime_hotspot.md",
    "components/tier3_platform/platform_memory.md",
]


def build_model(*, guards: bool = True) -> Kripke:
    """
    JIT 3面キャッシュ代謝・遅延チェイニング安全性・2-bit Hotspot FSM・MPU W^X 統合形式検証モデル

    状態定義:
    - s_idle: アイドル状態 (RO+X, clean)
    - s_compiling: JIT パッチ書き込み中 (MPU RW+XN, writing)
    - s_synced: DSB/ISB メモリバリア完了 (MPU RO+X, synced)
    - s_active_exec: Active バンクでネイティブ実行 (RO+X, executing, in_active)
    - s_warm_obs: Warm バンクで観測実行 (RO+X, executing, in_warm)
    - s_oldest_eval: Oldest 到達時の Hot 判定 (RO+X, in_oldest)
    - s_chained_active_warm: 新規 Active トレースが Warm 常駐ターゲットへチェイン結合 (RO+X, executing, chained)
    - s_swept_to_stub: rotate() 時にダングリング掃引が働き、スタブ復帰へ安全に無効化 (RO+X, swept, stub_return)
    - c_unexecuted: カード 00: UNEXECUTED (未実行)
    - c_executed: カード 01: EXECUTED (実行済み・再コンパイル可能)
    - c_hot: カード 10: HOT (頻度検出・コンパイル待ち)
    - c_compiled: カード 11: COMPILED (JIT コンパイル完了)
    - c_evicted: キャッシュ Eviction 発生状態
    - s_bad_rwx: W^X 違反状態（MPU 設定ミスで W と X が同時に有効化）
    - s_deadlock: バリア同期後に実行へ進めないデッドロック状態
    - s_dangling_chain: ターゲットが消去されたのにリンクが残存したダングリングチェイン違反状態
    - s_bad_skip_hot: HOT を経由せずに COMPILED へ飛んだ単調性違反状態
    - s_bad_permanent_deopt: Evict されたのに再コンパイル不能なまま取り残された永続デオプト違反状態
    """
    S = [
        "s_idle",
        "s_compiling",
        "s_synced",
        "s_active_exec",
        "s_warm_obs",
        "s_oldest_eval",
        "s_chained_active_warm",
        "s_swept_to_stub",
        "c_unexecuted",
        "c_executed",
        "c_hot",
        "c_compiled",
        "c_evicted",
        "s_bad_rwx",
        "s_deadlock",
        "s_dangling_chain",
        "s_bad_skip_hot",
        "s_bad_permanent_deopt",
    ]
    S0 = {"s_idle"}
    R = [
        # --- MPU W^X & 3面キャッシュ代謝サイクル ---
        ("s_idle", "s_compiling"),
        ("s_idle", "c_unexecuted"),
        ("s_compiling", "s_synced"),
        ("s_synced", "s_active_exec"),
        ("s_synced", "s_chained_active_warm"),
        ("s_active_exec", "s_active_exec"),
        ("s_active_exec", "s_warm_obs"),
        ("s_warm_obs", "s_warm_obs"),
        ("s_warm_obs", "s_oldest_eval"),
        ("s_oldest_eval", "s_synced"),
        ("s_oldest_eval", "s_idle"),
        # --- 遅延チェイニング & ダングリング掃引サイクル ---
        ("s_chained_active_warm", "s_chained_active_warm"),
        ("s_chained_active_warm", "s_swept_to_stub"),
        ("s_swept_to_stub", "s_active_exec"),
        ("s_swept_to_stub", "s_idle"),
        # --- 2-bit Hotspot FSM サイクル ---
        ("c_unexecuted", "c_executed"),
        ("c_executed", "c_hot"),
        ("c_hot", "c_compiled"),
        ("c_compiled", "s_active_exec"),
        ("c_compiled", "c_evicted"),
        ("c_evicted", "c_executed"),
        # --- 違反状態の自己ループ ---
        ("s_bad_rwx", "s_bad_rwx"),
        ("s_deadlock", "s_deadlock"),
        ("s_dangling_chain", "s_dangling_chain"),
        ("s_bad_skip_hot", "s_bad_skip_hot"),
        ("s_bad_permanent_deopt", "s_bad_permanent_deopt"),
    ]

    if not guards:
        # ガード無効時（変異検査）:
        # 1. MPU W^X ガード無効 -> 書き込み中に実行権限が残存
        R.append(("s_compiling", "s_bad_rwx"))
        # 2. Liveness ガード無効 -> 同期完了後にデッドロック
        R.append(("s_synced", "s_deadlock"))
        # 3. ダングリング掃引無効 (_sweep_dangling_chains 無効) -> ターゲット喪失後もリンク残存
        R.append(("s_chained_active_warm", "s_dangling_chain"))
        # 4. Hotspot FSM 単調性ガード無効 -> HOT を経由せず直接 COMPILED
        R.append(("c_executed", "s_bad_skip_hot"))
        # 5. Eviction 復帰ガード無効 (mark_evicted 無効) -> 再コンパイル不能で永続デオプト
        R.append(("c_evicted", "s_bad_permanent_deopt"))

    L = {
        "s_idle": {"clean", "mpu_ro_x", "idle"},
        "s_compiling": {"writing", "mpu_rw_xn"},
        "s_synced": {"synced", "mpu_ro_x"},
        "s_active_exec": {"executing", "in_active", "mpu_ro_x"},
        "s_warm_obs": {"executing", "in_warm", "mpu_ro_x"},
        "s_oldest_eval": {"in_oldest", "mpu_ro_x"},
        "s_chained_active_warm": {"executing", "chained", "target_in_warm", "mpu_ro_x"},
        "s_swept_to_stub": {"executing", "swept", "stub_return", "mpu_ro_x"},
        "c_unexecuted": {"unexecuted", "mpu_ro_x"},
        "c_executed": {"executed", "recompilable", "mpu_ro_x"},
        "c_hot": {"hot", "recompilable", "mpu_ro_x"},
        "c_compiled": {"compiled", "mpu_ro_x"},
        "c_evicted": {"evicted", "mpu_ro_x"},
        "s_bad_rwx": {"writing", "executing", "bad_rwx"},
        "s_deadlock": {"deadlock"},
        "s_dangling_chain": {"dangling_chain", "bad_chain"},
        "s_bad_skip_hot": {"bad_skip_hot", "compiled"},
        "s_bad_permanent_deopt": {"bad_permanent_deopt", "evicted"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_wx = And(AtomicProposition("writing"), AtomicProposition("executing"))
    return [
        {
            "name": "w_xor_x_safety_proof",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_wx)),
            "violation": bad_wx,
            "expect": True,  # MPU W^X 分離により書き込みと実行の同時有効状態は到達不能
        },
        {
            "name": "cache_liveness",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("synced"),
                    AF(AtomicProposition("executing")),
                )
            ),
            "violation": AtomicProposition("deadlock"),
            "expect": True,  # バリア同期完了後は必ず実行状態へ進む (AF)
        },
        {
            "name": "no_dangling_chain",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("dangling_chain"))),
            "violation": AtomicProposition("dangling_chain"),
            "expect": True,  # _sweep_dangling_chains により消去ターゲットへのダングリング参照は到達不能
        },
        {
            "name": "compiled_requires_hot_transit",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("bad_skip_hot"))),
            "violation": AtomicProposition("bad_skip_hot"),
            "expect": True,  # 2-bit FSM において COMPILED は必ず HOT を経由して到達
        },
        {
            "name": "eviction_always_recompilable",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("evicted"),
                    AF(AtomicProposition("recompilable")),
                )
            ),
            "violation": AtomicProposition("bad_permanent_deopt"),
            "expect": True,  # キャッシュ破棄(Eviction)後は必ず EXECUTED(再コンパイル可能)へ復帰する
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck
    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")

