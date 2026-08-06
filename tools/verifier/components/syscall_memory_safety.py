"""
tools/verifier/components/syscall_memory_safety.py
SyscallMemorySafetyVerifier: WASM ゲストメモリの vMMIO ゲート検証部品
キーワード: {Challenge_SyscallMemorySafety}
"""

from tools.verifier.fireball_verifier import FireballModel, VerificationResult


def verify_syscall_memory_safety() -> VerificationResult:
    """
    ゲストポインタの境界チェックおよび vMMIO 許可テーブルの安全性を検証するモデル
    """
    model = FireballModel(
        name="SyscallMemorySafetyModel",
        keywords=["{Challenge_SyscallMemorySafety}", "{COMP_SYSCALL_001}"]
    )

    # 状態: guest_ptr_offset, is_vmmio_permitted, access_granted
    model.set_init_state(ptr_offset=0, is_permitted_region=True, access_granted=False)

    # 正常範囲アクセス
    model.rule(
        name="Access_Valid_Region",
        when=lambda s: s["ptr_offset"] < 1024 and s["is_permitted_region"],
        action=lambda s: {**s, "access_granted": True}
    )

    # 範囲外アクセス試行
    model.rule(
        name="Attempt_Out_Of_Bounds",
        when=lambda s: s["ptr_offset"] >= 1024,
        action=lambda s: {**s, "is_permitted_region": False, "access_granted": False}
    )

    # 不変式: 境界外領域 (is_permitted_region == False) に対して access_granted が True にならないこと
    model.invariant(
        name="GateBoundaryEnforcement",
        condition=lambda s: not (not s["is_permitted_region"] and s["access_granted"]),
        description="許可テーブルの範囲外領域アクセスが絶対に拒否されること"
    )

    results = model.verify()
    return results[0]


if __name__ == "__main__":
    res = verify_syscall_memory_safety()
    print(f"[{'PASS' if res.is_valid else 'FAIL'}] {res.property_name} (キーワード: {res.keywords})")
