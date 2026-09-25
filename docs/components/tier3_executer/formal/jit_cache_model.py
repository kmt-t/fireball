"""
docs/components/tier3_executer/formal/jit_cache_model.py
pyModelChecking による JIT 3面キャッシュ代謝・抽象W^X状態・遅延チェイニング安全性・2-bit Hotspot FSM の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, And, AtomicProposition, Imply, Not, Or

BACKS = [
    "components/tier3_executer/jit_compiler.md",
    "components/tier3_executer/jit_runtime.md",
    "components/tier2_runtime/runtime_memory.md",
]


def build_model(*, guards: bool = True, aging_guard: bool = True) -> Kripke:
    """
    aging_guard: False のとき、エイジングスイープに関する変異だけを有効化する
    （guards=True のまま単独で変異検査を行うための独立スイッチ）。guards=False は全変異を含む。
    JIT 3面キャッシュ代謝・遅延チェイニング安全性・2-bit Hotspot FSM・抽象W^X状態の統合形式検証モデル
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
        "c_compile_failed",
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
        "s_bad_compile_failure_retry",
        "s_dangling_chain",  # guards=False で ch_s0_t1_l1 から到達するダングリング違反状態 (src=2, tgt=3, linked=1)
        # --- エイジングスイープ違反状態（{JIT_CardAgingSweep}） ---
        "s_bad_aged_compiled",  # スイープが COMPILED（常駐トレース）を戻した
        "s_bad_aged_hot",  # スイープが HOT（コンパイル待ち列）を戻した
        "s_bad_aging_stall",  # EXECUTED がスイープされず永久に残る
    ]
    S0 = {"s_idle", "ch_s0_t0_l1", "ch_s0_t1_l1"}
    R = [
        # --- 抽象W^X状態 & 3面キャッシュ代謝サイクル ---
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
        ("c_hot", "c_compile_failed"),
        ("c_compile_failed", "c_compile_failed"),
        ("c_compiled", "s_active_exec"),
        ("c_compiled", "c_evicted"),
        ("c_evicted", "c_unexecuted"),
        # --- エイジングスイープ: EXECUTED だけを UNEXECUTED へ戻す ---
        ("c_executed", "c_unexecuted"),
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
        ("s_bad_compile_failure_retry", "s_bad_compile_failure_retry"),
        ("s_dangling_chain", "s_dangling_chain"),
        ("s_bad_aged_compiled", "s_bad_aged_compiled"),
        ("s_bad_aged_hot", "s_bad_aged_hot"),
        ("s_bad_aging_stall", "s_bad_aging_stall"),
    ]
    if not guards or not aging_guard:
        # エイジング変異（独立検査可能）:
        # 1. スイープが COMPILED まで戻す（常駐トレースのカードを失う）
        R.append(("c_compiled", "s_bad_aged_compiled"))
        # 2. スイープが HOT まで戻す（コンパイル待ち列と状態が食い違う）
        R.append(("c_hot", "s_bad_aged_hot"))
        # 3. スイープが EXECUTED を巡回しない（減衰が起きない）
        R.append(("c_executed", "s_bad_aging_stall"))
    if guards:
        # ガード有効時: ch_s0_t1_l1 は掃引により ch_s1_t2_l0 (unlinked) へ安全遷移
        R.append(("ch_s0_t1_l1", "ch_s1_t2_l0"))
    else:
        # ガード無効時（変異検査）:
        # 1. ダングリング掃引無効: ch_s0_t1_l1 が linked=1 のまま s_dangling_chain へ到達
        R.append(("ch_s0_t1_l1", "s_dangling_chain"))
        # 2. 抽象W^Xガード無効
        R.append(("s_compiling", "s_bad_rwx"))
        # 3. Liveness ガード無効
        R.append(("s_synced", "s_deadlock"))
        # 4. Hotspot FSM 単調性ガード無効
        R.append(("c_executed", "s_bad_skip_hot"))
        # 5. Eviction 復帰ガード無効
        R.append(("c_evicted", "s_bad_permanent_deopt"))
        # 6. Compile failure後のTrackable Mask解除を無効化
        R.append(("c_hot", "s_bad_compile_failure_retry"))

    L = {
        "s_idle": {"clean", "ro_x", "idle"},
        "s_compiling": {"writing", "rw_xn"},
        "s_synced": {"synced", "ro_x"},
        "s_active_exec": {"executing", "in_active", "ro_x"},
        "s_warm_obs": {"executing", "in_warm", "ro_x"},
        "s_oldest_eval": {"in_oldest", "ro_x"},
        "c_unexecuted": {"unexecuted", "ro_x"},
        "c_executed": {"executed", "recompilable", "ro_x"},
        "c_hot": {"hot", "recompilable", "ro_x"},
        "c_compiled": {"compiled", "ro_x"},
        "c_compile_failed": {"hot", "compile_failed", "target_excluded", "ro_x"},
        "c_evicted": {"evicted", "ro_x"},
        "ch_s0_t0_l1": {"linked", "ro_x"},
        "ch_s0_t1_l1": {"linked", "ro_x"},
        "ch_s1_t1_l1": {"linked", "ro_x"},
        "ch_s1_t2_l0": {"unlinked", "ro_x"},
        "ch_s2_t2_l0": {"unlinked", "ro_x"},
        "ch_s2_t3_l0": {"unlinked", "ro_x"},
        "ch_s3_t3_l0": {"unlinked", "ro_x"},
        "s_bad_rwx": {"writing", "executing", "bad_rwx"},
        "s_deadlock": {"deadlock"},
        "s_bad_skip_hot": {"bad_skip_hot", "compiled"},
        "s_bad_permanent_deopt": {"bad_permanent_deopt", "evicted"},
        "s_bad_compile_failure_retry": {"compile_failed", "compile_retry_enabled"},
        "s_dangling_chain": {"dangling_chain", "bad_chain", "linked"},
        "s_bad_aged_compiled": {"bad_aged_compiled", "unexecuted"},
        "s_bad_aged_hot": {"bad_aged_hot", "unexecuted"},
        "s_bad_aging_stall": {"executed", "aging_stalled"},
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
            "expect": True,  # 抽象W^X不変条件により書き込みと実行の同時有効状態は到達不能
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
            "name": "compile_failure_excludes_block_from_retries",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("compile_failed"),
                    AtomicProposition("target_excluded"),
                )
            ),
            "violation": AtomicProposition("compile_retry_enabled"),
            "expect": True,  # Compile failure clears Trackable Mask; eviction remains separately recompilable
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
            "expect": True,  # キャッシュ破棄(Eviction)後は UNEXECUTED を経て EXECUTED(再コンパイル可能)へ復帰する (TEST-JITR-04)
        },
        {
            "name": "aging_never_drops_compiled",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("bad_aged_compiled"))),
            "violation": AtomicProposition("bad_aged_compiled"),
            "expect": True,  # エイジングスイープは COMPILED（常駐トレース）を戻さない (GOTCHA-JITR-09)
            "isolated_mutation": "aging",
        },
        {
            "name": "aging_never_drops_hot",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("bad_aged_hot"))),
            "violation": AtomicProposition("bad_aged_hot"),
            "expect": True,  # エイジングスイープは HOT（コンパイル待ち列）を戻さない (GOTCHA-JITR-09)
            "isolated_mutation": "aging",
        },
        {
            "name": "executed_card_eventually_ages_or_promotes",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("executed"),
                    AF(Or(AtomicProposition("unexecuted"), AtomicProposition("hot"))),
                )
            ),
            "violation": AtomicProposition("aging_stalled"),
            "expect": True,  # EXECUTED は有限回のローテーションの内に減衰するか HOT へ進む
            "isolated_mutation": "aging",
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    print("=== Formal Verification: JIT Cache Model (guards=True) ===")
    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        assert passed == prop["expect"], f"Property {prop['name']} verification failed!"
        print(f"  [{'PASS' if passed else 'FAIL'}] {prop['name']}")

    print("=== Mutation Testing: JIT Cache Model (guards=False) ===")
    km_mut = build_model(guards=False)
    for prop in properties():
        res_mut = modelcheck(km_mut, prop["formula"])
        violated = not km_mut.S0.issubset(res_mut)
        assert violated, f"Mutation for {prop['name']} was NOT detected!"
        print(f"  [PASS (Refuted as expected)] {prop['name']}")

    print("=== Isolated Mutation Testing: aging sweep only (guards=True, aging_guard=False) ===")
    km_aging = build_model(guards=True, aging_guard=False)
    for prop in properties():
        res_aging = modelcheck(km_aging, prop["formula"])
        holds = km_aging.S0.issubset(res_aging)
        if prop.get("isolated_mutation") == "aging":
            assert not holds, f"Aging mutation for {prop['name']} was NOT detected!"
            print(f"  [PASS (Refuted by aging mutation only)] {prop['name']}")
        else:
            assert holds, f"Aging mutation leaked into unrelated property {prop['name']}!"
            print(f"  [PASS (Unaffected)] {prop['name']}")
