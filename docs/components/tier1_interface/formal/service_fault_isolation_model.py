"""
docs/components/tier1_interface/formal/service_fault_isolation_model.py
pyModelChecking による Service コンポーネントの
(1) あるサービスの異常終了が他サービスへ伝播・破壊しないこと（障害隔離）
(2) 異常終了したサービスはイベント通知契機の自己再起動により必ず復旧すること
の形式検証（証明・変異検査対応）モデル

サービス A・B が対称に異常終了しうる構造とすることで、単一経路モデル
（インターリーブが存在せず、隔離の証明が自明になってしまう）を避ける。
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, AtomicProposition, Imply, Not

BACKS = ["components/tier1_interface/system_service.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    Service コンポーネントの変異検査対応保護証明モデル（A・B 対称）
    - s_all_running: サービス A・B ともに正常稼働中 (up)
    - s_{a,b}_crashed: 当該サービスが異常終了、他方は無傷 (down)
    - s_{a,b}_isolated: IPC ルータ経由のタスク分離により異常が境界内に隔離される (down)
    - s_{a,b}_rebooting: 障害イベント通知契機の自己再起動処理中 (down)
    - s_{a,b}_recovered: 復旧し正常稼働へ復帰 (up)
    - s_corrupted: 違反状態（障害隔離が破れ、異常終了が他方を破壊/道連れにした）
    - s_stuck: 違反状態（自己再起動が発火せず、異常終了状態のまま放置される）
    """
    S = [
        "s_all_running",
        "s_a_crashed",
        "s_a_isolated",
        "s_a_rebooting",
        "s_a_recovered",
        "s_b_crashed",
        "s_b_isolated",
        "s_b_rebooting",
        "s_b_recovered",
        "s_corrupted",
        "s_stuck",
    ]
    S0 = {"s_all_running"}
    R = [
        ("s_all_running", "s_a_crashed"),
        ("s_a_crashed", "s_a_isolated"),
        ("s_a_isolated", "s_a_rebooting"),
        ("s_a_rebooting", "s_a_recovered"),
        ("s_a_recovered", "s_all_running"),
        ("s_all_running", "s_b_crashed"),
        ("s_b_crashed", "s_b_isolated"),
        ("s_b_isolated", "s_b_rebooting"),
        ("s_b_rebooting", "s_b_recovered"),
        ("s_b_recovered", "s_all_running"),
        # 違反状態の自己ループ（Kripke 構造は全域的でなければならない）
        ("s_corrupted", "s_corrupted"),
        ("s_stuck", "s_stuck"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. 障害隔離（IPC ルータ経由のタスク分離）を外すと、異常終了が他方を直接破壊する
        R = R + [("s_a_crashed", "s_corrupted"), ("s_b_crashed", "s_corrupted")]
        # 2. イベント通知契機の自己再起動を外すと、隔離済みのまま復旧しない経路が生じる
        R = R + [("s_a_isolated", "s_stuck"), ("s_b_isolated", "s_stuck")]

    L = {
        "s_all_running": {"up"},
        "s_a_crashed": {"down"},
        "s_a_isolated": {"down"},
        "s_a_rebooting": {"down"},
        "s_a_recovered": {"up"},
        "s_b_crashed": {"down"},
        "s_b_isolated": {"down"},
        "s_b_rebooting": {"down"},
        "s_b_recovered": {"up"},
        "s_corrupted": {"corrupted"},  # 違反状態
        "s_stuck": {"stuck"},  # 違反状態
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_corrupted = AtomicProposition("corrupted")
    bad_stuck = AtomicProposition("stuck")
    down = AtomicProposition("down")
    up = AtomicProposition("up")
    return [
        {
            "name": "crash_does_not_propagate",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_corrupted)),
            "violation": bad_corrupted,
            "expect": True,  # 障害隔離により、他サービスの破壊状態は到達不能
        },
        {
            "name": "crashed_service_always_recovers",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(down, AF(up))),
            "violation": bad_stuck,
            "expect": True,  # イベント通知契機の自己再起動により、異常終了は必ず復旧する (AF)
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
