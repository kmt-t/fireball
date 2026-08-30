"""
docs/components/tier2_runtime/formal/loader_verification_model.py
pyModelChecking による WASM ローダの
(1) 検証（V1-V6 軽量検証）に合格していないモジュールは決して実行されないこと
(2) パースされたモジュールは、検証がスタックしたまま放置されず必ず合否いずれかへ収束すること
の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AF, Imply, Not, AtomicProposition

BACKS = ["components/tier2_runtime/runtime_loader.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    WASM ローダの変異検査対応保護証明モデル
    - s_rom_unparsed: ROM上のバイナリが未パース
    - s_parsing: ROMを直接参照するModuleView（索引構造）を構築中
    - s_parsed_unverified: ModuleView構築済み、検証未実施 (pending)
    - s_verifying: 軽量検証（V1-V6）実施中 (pending)
    - s_verified_ok: 検証合格 (settled)
    - s_verified_bad: 検証不合格 (settled)
    - s_executable: 検証合格モジュールが実行可能状態へ遷移 (settled)
    - s_rejected: 検証不合格モジュールを拒否（実行させない）(settled)
    - s_executing_unverified: 違反状態（検証を経ずに、または不合格のまま実行された）
    - s_stuck_verifying: 違反状態（検証が合否いずれにも収束せず放置される）
    """
    S = [
        "s_rom_unparsed",
        "s_parsing",
        "s_parsed_unverified",
        "s_verifying",
        "s_verified_ok",
        "s_verified_bad",
        "s_executable",
        "s_rejected",
        "s_executing_unverified",
        "s_stuck_verifying",
    ]
    S0 = {"s_rom_unparsed"}
    R = [
        ("s_rom_unparsed", "s_parsing"),
        ("s_parsing", "s_parsed_unverified"),
        ("s_parsed_unverified", "s_verifying"),
        ("s_verifying", "s_verified_ok"),
        ("s_verifying", "s_verified_bad"),
        ("s_verified_ok", "s_executable"),
        ("s_verified_bad", "s_rejected"),
        ("s_executable", "s_executable"),
        ("s_rejected", "s_rejected"),
        # 違反状態の自己ループ（Kripke 構造は全域的でなければならない）
        ("s_executing_unverified", "s_executing_unverified"),
        ("s_stuck_verifying", "s_stuck_verifying"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. 検証ステップを飛ばして直接実行してしまう経路
        R = R + [("s_parsed_unverified", "s_executing_unverified")]
        # 2. 検証不合格にもかかわらず拒否されず実行されてしまう経路
        R = R + [("s_verified_bad", "s_executing_unverified")]
        # 3. V1-V6 の境界（有限個の固定チェック）を外すと、検証が合否に収束しないままになりうる
        R = R + [("s_verifying", "s_stuck_verifying")]

    L = {
        "s_rom_unparsed": {"unparsed"},
        "s_parsing": {"parsing"},
        "s_parsed_unverified": {"pending"},
        "s_verifying": {"pending"},
        "s_verified_ok": {"settled"},
        "s_verified_bad": {"settled"},
        "s_executable": {"settled", "executable"},
        "s_rejected": {"settled"},
        "s_executing_unverified": {"executing_unverified"},  # 違反状態
        "s_stuck_verifying": {"stuck"},  # 違反状態
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_exec = AtomicProposition("executing_unverified")
    bad_stuck = AtomicProposition("stuck")
    pending = AtomicProposition("pending")
    settled = AtomicProposition("settled")
    return [
        {
            "name": "execution_requires_verification",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_exec)),
            "violation": bad_exec,
            "expect": True,  # 検証を経ない、または不合格のままの実行状態は到達不能
        },
        {
            "name": "verification_always_converges",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(pending, AF(settled))),
            "violation": bad_stuck,
            "expect": True,  # V1-V6 は有限個の固定チェックであり、検証は必ず合否いずれかへ収束する (AF)
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
