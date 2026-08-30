"""
docs/components/tier3_jit/formal/jit_cache_model.py
pyModelChecking による JIT 3面キャッシュ代謝・MPU W^X・遅延チェイニング安全性・2-bit Hotspot FSM の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AF, And, Not, Imply, AtomicProposition

BACKS = [
    "components/tier2_runtime/concepts/runtime_engine_concept.py",
    "components/tier3_jit/jit_compiler.md",
    "components/tier3_jit/jit_runtime.md",
    "components/tier3_platform/platform_memory.md",
]


def build_model(*, guards: bool = True) -> Kripke:
    """
    JIT 3面キャッシュ代謝・遅延チェイニング安全性・2-bit Hotspot FSM・MPU W^X 統合形式検証モデル
    遅延チェイニング安全性モデル (第13信 §89 準拠):
    - 状態は 3つ組 (age_source, age_target, linked) で表現
      - age_source ∈ {0(Active), 1(Warm), 2(Oldest), 3(dead)}
      - age_target ∈ {0(Active), 1(Warm), 2(Oldest), 3(dead)}
      - linked ∈ {1(yes), 0(no)}
    - 初期状態: ch_s0_t0_l1 (Active内チェイン), ch_s0_t1_l1 (ActiveからWarmへチェイン)
    - rotate() 遷移規則:
      1. 掃引 (_sweep_dangling_chains): guards=True 時、linked=1 かつ age_target >= 2 なら linked <- 0
      2. 加齢 (rotate): age_source, age_target をそれぞれ min(3, age + 1)
    - 違反状態 (dangling_chain): linked=1 ∧ age_source != 3 ∧ age_target == 3
    """
    S = [
        # --- 正常状態 ---
        "s_idle",
        "s_compiling",
        "s_synced",
        "s_active_exec",
        "s_warm_obs",
        "s_oldest_eval",
        "c_unexecuted",
        "c_executed",
        "c_hot",
        "c_compiled",
        "c_evicted",
        # --- 遅延チェイニング世代状態 ---
        "ch_s0_t0_l1",  # Active内チェイン (src=0, tgt=0, linked=1)
        "ch_s0_t1_l1",  # Active->Warmチェイン (src=0, tgt=1, linked=1)
        "ch_s1_t1_l1",  # 1世代経過 (src=1, tgt=1, linked=1)
        "ch_s1_t2_l0",  # guards=True: Warm->Oldestで掃引され unlinked=0 に無効化
        "ch_s2_t2_l0",  # 2世代経過 (src=2, tgt=2, linked=0)
        "ch_s2_t3_l0",  # 掃引済み安全状態 (src=2, tgt=3(dead), linked=0)
        "ch_s3_t3_l0",  # 終端安全状態 (src=3(dead), tgt=3(dead), linked=0)
        # --- 違反状態（ガード有効時は到達不能、無効時に到達可能） ---
        "s_bad_rwx",
        "s_deadlock",
        "s_bad_skip_hot",
        "s_bad_permanent_deopt",
        "s_dangling_chain",  # guards=False で ch_s0_t1_l1 から到達するダングリング違反状態 (src=2, tgt=3, linked=1)
    ]
    S0 = {"s_idle", "ch_s0_t0_l1", "ch_s0_t1_l1"}
    R = [
        # --- MPU W^X & 3面キャッシュ代謝サイクル ---
        ("s_idle", "s_compiling"),
        ("s_idle", "c_unexecuted"),
        ("s_compiling", "s_synced"),
        ("s_synced", "s_active_exec"),
        ("s_active_exec", "s_active_exec"),
        ("s_active_exec", "s_warm_obs"),
        ("s_warm_obs", "s_warm_obs"),
        ("s_warm_obs", "s_oldest_eval"),
        ("s_oldest_eval", "s_synced"),
        ("s_oldest_eval", "s_idle"),
        # --- 2-bit Hotspot FSM サイクル ---
        ("c_unexecuted", "c_executed"),
        ("c_executed", "c_hot"),
        ("c_hot", "c_compiled"),
        ("c_compiled", "s_active_exec"),
        ("c_compiled", "c_evicted"),
        ("c_evicted", "c_executed"),
        # --- 遅延チェイニング共通遷移 ---
        ("ch_s0_t0_l1", "ch_s1_t1_l1"),
        ("ch_s1_t1_l1", "ch_s2_t2_l0"),
        ("ch_s1_t2_l0", "ch_s2_t3_l0"),
        ("ch_s2_t2_l0", "ch_s3_t3_l0"),
        ("ch_s2_t3_l0", "ch_s3_t3_l0"),
        ("ch_s3_t3_l0", "ch_s3_t3_l0"),
        # --- 違反状態の自己ループ ---
        ("s_bad_rwx", "s_bad_rwx"),
        ("s_deadlock", "s_deadlock"),
        ("s_bad_skip_hot", "s_bad_skip_hot"),
        ("s_bad_permanent_deopt", "s_bad_permanent_deopt"),
        ("s_dangling_chain", "s_dangling_chain"),
    ]
    if guards:
        # ガード有効時: ch_s0_t1_l1 は掃引により ch_s1_t2_l0 (unlinked) へ安全遷移
        R.append(("ch_s0_t1_l1", "ch_s1_t2_l0"))
    else:
        # ガード無効時（変異検査）:
        # 1. ダングリング掃引無効: ch_s0_t1_l1 が linked=1 のまま s_dangling_chain へ到達
        R.append(("ch_s0_t1_l1", "s_dangling_chain"))
        # 2. MPU W^X ガード無効
        R.append(("s_compiling", "s_bad_rwx"))
        # 3. Liveness ガード無効
        R.append(("s_synced", "s_deadlock"))
        # 4. Hotspot FSM 単調性ガード無効
        R.append(("c_executed", "s_bad_skip_hot"))
        # 5. Eviction 復帰ガード無効
        R.append(("c_evicted", "s_bad_permanent_deopt"))

    L = {
        "s_idle": {"clean", "mpu_ro_x", "idle"},
        "s_compiling": {"writing", "mpu_rw_xn"},
        "s_synced": {"synced", "mpu_ro_x"},
        "s_active_exec": {"executing", "in_active", "mpu_ro_x"},
        "s_warm_obs": {"executing", "in_warm", "mpu_ro_x"},
        "s_oldest_eval": {"in_oldest", "mpu_ro_x"},
        "c_unexecuted": {"unexecuted", "mpu_ro_x"},
        "c_executed": {"executed", "recompilable", "mpu_ro_x"},
        "c_hot": {"hot", "recompilable", "mpu_ro_x"},
        "c_compiled": {"compiled", "mpu_ro_x"},
        "c_evicted": {"evicted", "mpu_ro_x"},
        "ch_s0_t0_l1": {"linked", "mpu_ro_x"},
        "ch_s0_t1_l1": {"linked", "mpu_ro_x"},
        "ch_s1_t1_l1": {"linked", "mpu_ro_x"},
        "ch_s1_t2_l0": {"unlinked", "mpu_ro_x"},
        "ch_s2_t2_l0": {"unlinked", "mpu_ro_x"},
        "ch_s2_t3_l0": {"unlinked", "mpu_ro_x"},
        "ch_s3_t3_l0": {"unlinked", "mpu_ro_x"},
        "s_bad_rwx": {"writing", "executing", "bad_rwx"},
        "s_deadlock": {"deadlock"},
        "s_bad_skip_hot": {"bad_skip_hot", "compiled"},
        "s_bad_permanent_deopt": {"bad_permanent_deopt", "evicted"},
        "s_dangling_chain": {"dangling_chain", "bad_chain", "linked"},
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
