"""
docs/components/tier1_core/formal/coos_channel_model.py
pyModelChecking による COOS CSP チャネル・同期ランデブー・デッドロック不在・二重所有不在の形式検証（証明・変異検査対応）モデル
"""

from typing import Literal, TypedDict

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, And, AtomicProposition, Formula, Imply, Not

BACKS = [
    "components/tier1_core/os_coos.md",
    "components/tier1_core/os_scheduler.md",
]


DEFAULT_MAX_HANDOFFS = 4  # 正本: docs/components/tier1_core/system_config.md


class FormalProperty(TypedDict):
    name: str
    kind: Literal["safety", "liveness"]
    logic: Literal["CTL"]
    formula: Formula
    violation: Formula
    expect: bool


def build_model(*, guards: bool = True, max_handoffs: int = DEFAULT_MAX_HANDOFFS) -> Kripke:
    """同期ランデブー、所有権、相互待ち、ハンドオフ上限をモデル化する。

    上限到達時は、対象より先に READY キューにいたタスクを1つディスパッチする。
    実時間の応答上限や、協調yieldしないタスクの公平性はこのモデルの対象外。
    """
    assert max_handoffs > 0, "max_handoffs must be positive"

    handoff_states = [f"s_handoff_count_{count}" for count in range(1, max_handoffs + 1)]
    states = [
        "s_main_loop",
        "s_sender_running",
        "s_sender_blocked",
        "s_matching_receiver_running",
        "s_sender_resumed",
        "s_receiver_running",
        "s_receiver_blocked",
        "s_matching_sender_running",
        "s_receiver_resumed",
        *handoff_states,
        "s_limit_handoff",
        "s_forced_yield",
        "s_other_ready_dispatched",
        "s_generation_pending",
        "s_round_snapshot",
        "s_generation_observing",
        "s_generation_complete",
        "s_deadlock",
        "s_double_owned",
        "s_handoff_livelock",
        "s_generation_lost",
    ]
    transitions = [
        # 送信先着: 待機中の送信者を受信側が起床し、上限未到達なら送信者へ戻す。
        ("s_main_loop", "s_sender_running"),
        ("s_sender_running", "s_sender_blocked"),
        ("s_sender_blocked", "s_matching_receiver_running"),
        ("s_matching_receiver_running", "s_sender_resumed"),
        # 受信先着: 待機中の受信者を送信側が起床し、上限未到達なら受信者へ戻す。
        ("s_main_loop", "s_receiver_running"),
        ("s_receiver_running", "s_receiver_blocked"),
        ("s_receiver_blocked", "s_matching_sender_running"),
        ("s_matching_sender_running", "s_receiver_resumed"),
        ("s_sender_resumed", handoff_states[0]),
        ("s_receiver_resumed", handoff_states[0]),
        (f"s_handoff_count_{max_handoffs}", "s_limit_handoff"),
        ("s_limit_handoff", "s_forced_yield"),
        ("s_forced_yield", "s_other_ready_dispatched"),
        ("s_other_ready_dispatched", "s_main_loop"),
        # 割り込み再スケジュール世代: FIFOドレイン後に対象を固定し、
        # 各対象が一度観測してから要求を完了する。
        ("s_main_loop", "s_generation_pending"),
        ("s_generation_pending", "s_round_snapshot"),
        ("s_round_snapshot", "s_generation_observing"),
        ("s_generation_observing", "s_generation_complete"),
        ("s_generation_complete", "s_main_loop"),
        # 到達可能な違反状態は Kripke 構造の全状態に後続状態を持たせる。
        ("s_deadlock", "s_deadlock"),
        ("s_double_owned", "s_double_owned"),
        ("s_handoff_livelock", "s_handoff_livelock"),
        ("s_generation_lost", "s_generation_lost"),
    ]
    transitions.extend(
        (f"s_handoff_count_{count}", f"s_handoff_count_{count + 1}")
        for count in range(1, max_handoffs)
    )

    if not guards:
        # 非循環依存制約を外すと、A が B を待つ間に B も A を待って循環する。
        transitions.extend(
            [
                ("s_matching_receiver_running", "s_deadlock"),
                ("s_matching_sender_running", "s_deadlock"),
                # 原子的な所有者 revoke を外すと、同じ転送値を両者が所有する。
                ("s_sender_blocked", "s_double_owned"),
                # 上限時の yield を外すと、上限判定位置で連鎖が閉じる。
                ("s_limit_handoff", "s_handoff_livelock"),
                ("s_round_snapshot", "s_generation_lost"),
            ]
        )

    labels = {
        "s_main_loop": {"main_loop"},
        "s_sender_running": {"running", "sender_owns"},
        "s_sender_blocked": {"blocked_sender", "sender_owns"},
        "s_matching_receiver_running": {"running", "sender_owns"},
        "s_sender_resumed": {"sender_resumed", "receiver_owns"},
        "s_receiver_running": {"running"},
        "s_receiver_blocked": {"blocked_receiver"},
        "s_matching_sender_running": {"running", "sender_owns"},
        "s_receiver_resumed": {"receiver_resumed", "receiver_owns"},
        "s_limit_handoff": {"at_max_limit", "receiver_owns"},
        "s_forced_yield": {"yielding", "counter_reset", "target_ready_tail"},
        "s_other_ready_dispatched": {"other_ready_dispatched"},
        "s_generation_pending": {"reschedule_pending"},
        "s_round_snapshot": {"reschedule_pending", "round_snapshotted"},
        "s_generation_observing": {"reschedule_pending", "task_observed"},
        "s_generation_complete": {"generation_complete"},
        "s_deadlock": {"deadlock", "blocked_sender", "blocked_receiver"},
        "s_double_owned": {"sender_owns", "receiver_owns"},
        "s_handoff_livelock": {"at_max_limit", "handoff_livelock"},
        "s_generation_lost": {"reschedule_pending", "generation_lost"},
    }
    labels.update({state: {"in_handoff_chain", "receiver_owns"} for state in handoff_states})
    return Kripke(S=states, S0={"s_main_loop"}, R=transitions, L=labels)


def properties() -> list[FormalProperty]:
    bad_ownership = And(AtomicProposition("sender_owns"), AtomicProposition("receiver_owns"))
    blocked_sender_violation = And(
        AtomicProposition("blocked_sender"),
        Not(AF(AtomicProposition("sender_resumed"))),
    )
    blocked_receiver_violation = And(
        AtomicProposition("blocked_receiver"),
        Not(AF(AtomicProposition("receiver_resumed"))),
    )
    handoff_return_violation = And(
        AtomicProposition("at_max_limit"),
        Not(AF(AtomicProposition("main_loop"))),
    )
    forced_yield_effects = And(
        AtomicProposition("counter_reset"), AtomicProposition("target_ready_tail")
    )
    forced_yield_violation = And(AtomicProposition("at_max_limit"), Not(AF(forced_yield_effects)))
    no_peer_dispatch_violation = And(
        AtomicProposition("at_max_limit"),
        Not(AF(AtomicProposition("other_ready_dispatched"))),
    )
    generation_completion_violation = And(
        AtomicProposition("reschedule_pending"),
        Not(AF(AtomicProposition("generation_complete"))),
    )
    return [
        {
            "name": "deadlock_freedom_under_acyclic_topology",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("deadlock"))),
            "violation": AtomicProposition("deadlock"),
            "expect": True,
        },
        {
            "name": "single_ownership_during_rendezvous",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_ownership)),
            "violation": bad_ownership,
            "expect": True,
        },
        {
            "name": "blocked_sender_resumes_after_matching_receive",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("blocked_sender"),
                    AF(AtomicProposition("sender_resumed")),
                )
            ),
            "violation": blocked_sender_violation,
            "expect": True,
        },
        {
            "name": "blocked_receiver_resumes_after_matching_send",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("blocked_receiver"),
                    AF(AtomicProposition("receiver_resumed")),
                )
            ),
            "violation": blocked_receiver_violation,
            "expect": True,
        },
        {
            "name": "handoff_limit_forces_scheduler_return",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("at_max_limit"),
                    AF(AtomicProposition("main_loop")),
                )
            ),
            "violation": handoff_return_violation,
            "expect": True,
        },
        {
            "name": "handoff_limit_resets_counter_and_queues_target_at_tail",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("at_max_limit"),
                    AF(forced_yield_effects),
                )
            ),
            "violation": forced_yield_violation,
            "expect": True,
        },
        {
            "name": "handoff_limit_dispatches_preexisting_ready_peer",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("at_max_limit"),
                    AF(AtomicProposition("other_ready_dispatched")),
                )
            ),
            "violation": no_peer_dispatch_violation,
            "expect": True,
        },
        {
            "name": "interrupt_generation_completes_after_target_observation",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("reschedule_pending"),
                    AF(AtomicProposition("generation_complete")),
                )
            ),
            "violation": generation_completion_violation,
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    # 1. ガード有効時: 全特性が満たされることの証明
    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        assert passed == prop["expect"], f"Proof failed for {prop['name']}"
        print(f"[{'PASS' if passed else 'FAIL'}] {prop['name']}")

    # 2. ガード無効時（変異検査）: 反証可能性（違反状態の検出）の確認
    km_mut = build_model(guards=False)
    for prop in properties():
        res = modelcheck(km_mut, prop["formula"])
        passed = km_mut.S0.issubset(res)
        assert not passed, (
            f"Mutation check failed: {prop['name']} was not refuted under guards=False!"
        )
        print(f"[PASS (Mutated Refuted)] {prop['name']}")
