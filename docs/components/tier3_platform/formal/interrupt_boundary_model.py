"""
docs/components/tier3_platform/formal/interrupt_boundary_model.py
pyModelChecking による物理割り込み境界・FIFO 投函・スケジューラ復帰の形式検証
（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, AtomicProposition, Imply, Not

BACKS = [
    "components/tier3_platform/platform_driver.md",
]


def build_model(*, guards: bool = True) -> Kripke:
    """
    ISR がタスク状態を直接変更せず、固定長 FIFO とスケジューラ境界を経由する
    割り込み通知経路の保護証明・変異検査対応モデル。
    - s_idle: 割り込み待機中
    - s_isr_capture: ISR が固定 5 ワードの interrupt-event を構成中
    - s_fifo_enqueued: interrupt-event を固定長 FIFO へ投函済み
    - s_scheduler_boundary: スケジューラが協調境界で FIFO をドレイン中
    - s_task_ready: FIFO のイベントによりタスクを READY として観測可能
    - s_isr_direct_ready: 違反状態（ISR がタスク状態を直接 READY に変更）
    - s_fifo_stalled: 違反状態（FIFO 投函後にスケジューラ境界へ復帰しない）
    """
    S = [
        "s_idle",
        "s_isr_capture",
        "s_fifo_enqueued",
        "s_scheduler_boundary",
        "s_task_ready",
        "s_isr_direct_ready",
        "s_fifo_stalled",
    ]
    S0 = {"s_idle"}
    R = [
        # 物理割り込み受付から固定長 FIFO 投函まで
        ("s_idle", "s_isr_capture"),
        ("s_isr_capture", "s_fifo_enqueued"),
        # スケジューラの協調境界で FIFO をドレインして READY へ反映
        ("s_fifo_enqueued", "s_scheduler_boundary"),
        ("s_scheduler_boundary", "s_task_ready"),
        ("s_task_ready", "s_idle"),
        # 違反状態の自己ループ
        ("s_isr_direct_ready", "s_isr_direct_ready"),
        ("s_fifo_stalled", "s_fifo_stalled"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. ISR からタスク状態を直接変更すると、FIFO を経由しない READY 遷移が発生
        R = [*R, ("s_isr_capture", "s_isr_direct_ready")]
        # 2. FIFO 投函後のスケジューラ境界復帰を怠ると、イベント処理が永久に停滞
        R = [*R, ("s_fifo_enqueued", "s_fifo_stalled")]

    L = {
        "s_idle": {"interrupt_idle"},
        "s_isr_capture": {"isr_running", "event_constructing"},
        "s_fifo_enqueued": {"event_queued", "interrupt_pending"},
        "s_scheduler_boundary": {"scheduler_boundary", "fifo_draining"},
        "s_task_ready": {"task_ready"},
        "s_isr_direct_ready": {"isr_direct_task_update"},
        "s_fifo_stalled": {"event_queued", "interrupt_pending", "fifo_stalled"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_direct_update = AtomicProposition("isr_direct_task_update")
    event_queued = AtomicProposition("event_queued")
    scheduler_boundary = AtomicProposition("scheduler_boundary")
    return [
        {
            "name": "isr_does_not_update_task_state_directly",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_direct_update)),
            "violation": bad_direct_update,
            "expect": True,  # ISR は固定長 FIFO への投函以外のタスク状態変更を行わない
        },
        {
            "name": "interrupt_event_reaches_scheduler_boundary",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(event_queued, AF(scheduler_boundary))),
            "violation": AtomicProposition("fifo_stalled"),
            "expect": True,  # 投函済みイベントは必ず協調境界でドレインされる
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    print("=== Formal Verification: Interrupt Boundary Model (guards=True) ===")
    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        assert passed == prop["expect"], f"Proof failed for {prop['name']}"
        print(f"[PASS] {prop['name']} (guards=True)")

    print("=== Mutation Testing: Interrupt Boundary Model (guards=False) ===")
    km_mut = build_model(guards=False)
    for prop in properties():
        res = modelcheck(km_mut, prop["formula"])
        passed = km_mut.S0.issubset(res)
        assert not passed, (
            f"Mutation check failed: {prop['name']} was not refuted under guards=False!"
        )
        print(f"[PASS] {prop['name']} mutation rejected (guards=False)")
