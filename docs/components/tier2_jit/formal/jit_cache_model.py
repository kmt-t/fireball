"""
docs/components/tier2_jit/formal/jit_cache_model.py
pyModelChecking による JIT 3面キャッシュ代謝・MPU W^X 実行安全性の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AF, And, Not, Imply, AtomicProposition

BACKS = ["components/tier2_jit/jit_compiler.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    JIT 3面キャッシュ（Active/Warm/Oldest）および MPU W^X 保護証明・変異検査対応モデル
    - s_idle: アイドル状態 (RO+X, clean)
    - s_compiling: JIT パッチ書き込み中 (MPU RW+XN, writing)
    - s_synced: DSB/ISB メモリバリア完了 (MPU RO+X, synced)
    - s_active_exec: Active バンクでネイティブ実行 (RO+X, executing, in_active)
    - s_warm_obs: Warm バンクで観測実行 (RO+X, executing, in_warm)
    - s_oldest_eval: Oldest 到達時の Hot 判定 (RO+X, in_oldest)
    - s_bad_rwx: 違反状態（MPU 設定ミスで W と X が同時に有効化した競合状態）
    """
    S = [
        "s_idle",
        "s_compiling",
        "s_synced",
        "s_active_exec",
        "s_warm_obs",
        "s_oldest_eval",
        "s_bad_rwx",
    ]
    S0 = {"s_idle"}
    R = [
        # コンパイル要求: RW+XN に切り替えて書き込み
        ("s_idle", "s_compiling"),
        # パッチ完了後 DSB/ISB バリア同期
        ("s_compiling", "s_synced"),
        # バリア完了後に実行開始 (Active)
        ("s_synced", "s_active_exec"),
        # Active 実行継続または世代ローテーションで Warm へ
        ("s_active_exec", "s_active_exec"),
        ("s_active_exec", "s_warm_obs"),
        # Warm 実行継続または最古 Oldest へ
        ("s_warm_obs", "s_warm_obs"),
        ("s_warm_obs", "s_oldest_eval"),
        # Oldest から再同期または破棄
        ("s_oldest_eval", "s_synced"),
        ("s_oldest_eval", "s_idle"),
        # 違反状態の自己ループ
        ("s_bad_rwx", "s_bad_rwx"),
    ]

    if not guards:
        # ガード無効時（変異検査）:
        # MPU W^X 切替やメモリバリアを怠ると、書き込み中に実行権限が残存して W^X 違反へ突入
        R = R + [("s_compiling", "s_bad_rwx")]

    L = {
        "s_idle": {"clean", "mpu_ro_x"},
        "s_compiling": {"writing", "mpu_rw_xn"},
        "s_synced": {"synced", "mpu_ro_x"},
        "s_active_exec": {"executing", "in_active", "mpu_ro_x"},
        "s_warm_obs": {"executing", "in_warm", "mpu_ro_x"},
        "s_oldest_eval": {"in_oldest", "mpu_ro_x"},
        "s_bad_rwx": {"writing", "executing"},  # W^X 違反状態
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
            "expect": True,  # バリア同期完了後は必ず実行状態へ進む (AF)
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck
    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
