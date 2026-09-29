"""
docs/components/tier2_runtime/formal/vmmio_mapping_model.py
pyModelChecking による vMMIO アドレス空間変換・TLB・RAM境界保護・権限執行の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, And, AtomicProposition, Imply, Not

BACKS = [
    "components/tier2_runtime/runtime_vmmio.md",
]


def build_model(*, guards: bool = True) -> Kripke:
    """
    vMMIO メモリアクセスディスパッチ・境界検査・TLB・権限執行の保護証明・変異検査対応モデル
    - s_idle: メモリアクセス開始
    - s_guest_ram_eval: Bit 31 == 0 のリニア RAM バイパス境界判定 (FastAddressCheck)
    - s_guest_ram_ok: [addr, addr + access_size) が RAM 範囲内
    - s_trap_ram_oob: 全アクセス幅が RAM 範囲に収まらないトラップ
    - s_tlb_lookup: Bit 31 == 1 の 5-bit Folding XOR TLB 探索（32エントリ）
    - s_tlb_hit_check_perm: TLB ヒット時のインライン権限チェック
    - s_flatmap_walk: TLB ミス時の PTE 探索 (二分探索)
    - s_flatmap_check_perm: PTE 解決後の権限チェック & TLB リフィル
    - s_device_range_eval: アクセス幅が単一PTEとそのmapping_sizeに収まるか検査
    - s_device_range_ok: アクセス範囲が現在の仮想ページ・PTE境界内
    - s_owner_eval_shm / s_owner_eval_dynamic: 現行タスクとPTE所有者の照合
    - s_owner_ok / s_trap_owner: 所有者一致または非所有者トラップ
    - s_trap_device_range_oob: PTEまたはページ境界を越える範囲アクセスのトラップ
    - s_device_access_ok: 仮想デバイス / 共有メモリ物理アクセス成功
    - s_trap_unregistered: 未登録ページ / 未マッピング空間トラップ
    - s_trap_access_violation: 読み書き権限違反トラップ
    - s_unauthorized_ram: 違反状態（RAM 境界チェックを省いて境界外へアクセス成功）
    - s_unauthorized_perm: 違反状態（TLB ヒット時に権限チェックを省いてアクセス成功）
    - s_unauthorized_range: 違反状態（アクセス幅の範囲検査を省いてマッピング外へアクセス成功）
    - s_stale_access: 違反状態（Revoke 後に TLB フラッシュを怠り旧所有者がアクセス成功）
    - s_unauthorized_shm_owner / s_unauthorized_dynamic_owner: 違反状態（FC=14/13の非所有者アクセス成功）
    """
    S = [
        "s_idle",
        "s_guest_ram_eval",
        "s_guest_ram_ok",
        "s_trap_ram_oob",
        "s_tlb_lookup",
        "s_tlb_hit_check_perm",
        "s_flatmap_walk",
        "s_flatmap_check_perm",
        "s_device_range_eval",
        "s_device_range_ok",
        "s_owner_eval_shm",
        "s_owner_eval_dynamic",
        "s_owner_ok",
        "s_trap_owner",
        "s_trap_device_range_oob",
        "s_device_access_ok",
        "s_trap_unregistered",
        "s_trap_access_violation",
        "s_unauthorized_ram",
        "s_unauthorized_perm",
        "s_unauthorized_range",
        "s_stale_access",
        "s_unauthorized_shm_owner",
        "s_unauthorized_dynamic_owner",
    ]
    S0 = {"s_idle"}
    R = [
        # メモリアクセスディスパッチ
        ("s_idle", "s_guest_ram_eval"),
        ("s_idle", "s_tlb_lookup"),
        # リニア RAM 境界判定 (FastAddressCheck)
        ("s_guest_ram_eval", "s_guest_ram_ok"),
        ("s_guest_ram_eval", "s_trap_ram_oob"),
        ("s_guest_ram_ok", "s_idle"),
        ("s_trap_ram_oob", "s_idle"),
        # TLB 探索
        ("s_tlb_lookup", "s_tlb_hit_check_perm"),
        ("s_tlb_lookup", "s_flatmap_walk"),
        # TLB ヒット時のインライン権限チェック
        ("s_tlb_hit_check_perm", "s_device_range_eval"),
        ("s_tlb_hit_check_perm", "s_trap_access_violation"),
        # TLB ミス時の FlatMap 探索
        ("s_flatmap_walk", "s_flatmap_check_perm"),
        ("s_flatmap_walk", "s_trap_unregistered"),
        # PTE 解決後の権限チェック & TLB リフィル
        ("s_flatmap_check_perm", "s_device_range_eval"),
        ("s_flatmap_check_perm", "s_trap_access_violation"),
        # 全バイト範囲が1仮想ページとPTE mapping_size内にあることを確認
        ("s_device_range_eval", "s_device_range_ok"),
        ("s_device_range_eval", "s_trap_device_range_oob"),
        ("s_device_range_ok", "s_owner_eval_shm"),
        ("s_device_range_ok", "s_owner_eval_dynamic"),
        ("s_device_range_ok", "s_device_access_ok"),  # FC=15 passthrough
        ("s_owner_eval_shm", "s_owner_ok"),
        ("s_owner_eval_shm", "s_trap_owner"),
        ("s_owner_eval_dynamic", "s_owner_ok"),
        ("s_owner_eval_dynamic", "s_trap_owner"),
        ("s_owner_ok", "s_device_access_ok"),
        ("s_trap_owner", "s_idle"),
        # 完了後の復帰
        ("s_device_access_ok", "s_idle"),
        ("s_trap_unregistered", "s_idle"),
        ("s_trap_access_violation", "s_idle"),
        ("s_trap_device_range_oob", "s_idle"),
        # 違反状態の自己ループ
        ("s_unauthorized_ram", "s_unauthorized_ram"),
        ("s_unauthorized_perm", "s_unauthorized_perm"),
        ("s_unauthorized_range", "s_unauthorized_range"),
        ("s_stale_access", "s_stale_access"),
        ("s_unauthorized_shm_owner", "s_unauthorized_shm_owner"),
        ("s_unauthorized_dynamic_owner", "s_unauthorized_dynamic_owner"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. RAM 境界チェックを省くと境界外アクセスが直接成功状態へ漏洩
        R = [*R, ("s_guest_ram_eval", "s_unauthorized_ram")]
        # 2. TLB ヒット時に権限チェックをスキップすると無許可アクセスが直接成功状態へ漏洩
        R = [*R, ("s_tlb_hit_check_perm", "s_unauthorized_perm")]
        # 3. 共有メモリ Revoke 時に TLB 即時無効化を怠ると旧 TLB 残存で不正アクセスが成功
        R = [*R, ("s_tlb_lookup", "s_stale_access")]
        # 4. アクセス幅検査を省くと、PTE mapping_size の外へアクセスする
        R = [*R, ("s_device_range_eval", "s_unauthorized_range")]
        # 5. SHM owner mismatch guard removal allows another task to read/write.
        R = [*R, ("s_owner_eval_shm", "s_unauthorized_shm_owner")]
        # 6. DYNAMIC guest binding guard removal allows another task to access a HAL slot.
        R = [*R, ("s_owner_eval_dynamic", "s_unauthorized_dynamic_owner")]

    L = {
        "s_idle": {"idle"},
        "s_guest_ram_eval": {"evaluating_ram"},
        "s_guest_ram_ok": {"access_ok", "ram_access"},
        "s_trap_ram_oob": {"trapped", "trap_oob"},
        "s_tlb_lookup": {"looking_up_tlb"},
        "s_tlb_hit_check_perm": {"checking_perm", "tlb_hit"},
        "s_flatmap_walk": {"walking_flatmap", "tlb_miss"},
        "s_flatmap_check_perm": {"checking_perm", "tlb_miss"},
        "s_device_range_eval": {"checking_access_range"},
        "s_device_range_ok": {"range_checked"},
        "s_owner_eval_shm": {"checking_owner", "shm_owner_check"},
        "s_owner_eval_dynamic": {"checking_owner", "dynamic_owner_check"},
        "s_owner_ok": {"owner_checked"},
        "s_trap_owner": {"trapped", "trap_owner_mismatch"},
        "s_trap_device_range_oob": {"trapped", "trap_oob"},
        "s_device_access_ok": {"access_ok", "device_access"},
        "s_trap_unregistered": {"trapped", "trap_unregistered"},
        "s_trap_access_violation": {"trapped", "trap_access_violation"},
        # 違反状態
        # Mutation-only states intentionally have no `access_ok`/`trapped`
        # label: they represent a stuck unsafe path and must also refute the
        # liveness property, not merely the safety properties.
        "s_unauthorized_ram": {"unauthorized_ram"},
        "s_unauthorized_perm": {"unauthorized_perm"},
        "s_unauthorized_range": {"unauthorized_range"},
        "s_stale_access": {"stale_access"},
        "s_unauthorized_shm_owner": {"unauthorized_shm_owner"},
        "s_unauthorized_dynamic_owner": {"unauthorized_dynamic_owner"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_ram = AtomicProposition("unauthorized_ram")
    bad_perm = AtomicProposition("unauthorized_perm")
    bad_range = AtomicProposition("unauthorized_range")
    bad_stale = AtomicProposition("stale_access")
    bad_shm_owner = AtomicProposition("unauthorized_shm_owner")
    bad_dynamic_owner = AtomicProposition("unauthorized_dynamic_owner")
    return [
        {
            "name": "ram_boundary_enforcement",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_ram)),
            "violation": bad_ram,
            "expect": True,  # FastAddressCheck により境界外アクセスは到達不能
        },
        {
            "name": "permission_check_enforcement",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_perm)),
            "violation": bad_perm,
            "expect": True,  # TLB ヒット時も権限チェック必須
        },
        {
            "name": "access_width_mapping_boundary_enforcement",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_range)),
            "violation": bad_range,
            "expect": True,  # 開始アドレスだけでなく全アクセス幅が単一マッピング内に必要
        },
        {
            "name": "revoke_tlb_invalidation_safety",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_stale)),
            "violation": bad_stale,
            "expect": True,  # Revoke 時に TLB 即時破棄のため stale access は到達不能
        },
        {
            "name": "shm_owner_isolation",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_shm_owner)),
            "violation": bad_shm_owner,
            "expect": True,
        },
        {
            "name": "dynamic_guest_owner_isolation",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_dynamic_owner)),
            "violation": bad_dynamic_owner,
            "expect": True,
        },
        {
            "name": "access_definite_resolution",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("idle"),
                    AF(
                        Or_CTL(
                            AtomicProposition("access_ok"),
                            AtomicProposition("trapped"),
                        )
                    ),
                )
            ),
            "violation": None,
            "expect": True,  # 任意のアクセスは有限ステップでアクセス完了またはトラップへ到達
        },
    ]


def Or_CTL(p, q):
    """CTL OR convenience helper: p | q <=> Not(And(Not(p), Not(q)))"""
    return Not(And(Not(p), Not(q)))


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    # 1. 正常系証明 (guards=True)
    km_ok = build_model(guards=True)
    print("=== Formal Verification: vMMIO Memory Mapping Model (guards=True) ===")
    for prop in properties():
        res = modelcheck(km_ok, prop["formula"])
        passed = km_ok.S0.issubset(res)
        assert passed == prop["expect"], f"Property {prop['name']} verification failed!"
        print(f"  [{'PASS' if passed else 'FAIL'}] {prop['name']}")

    # 2. 変異検査反証 (guards=False: 全ての特性式が確実に False 検出されること)
    km_mut = build_model(guards=False)
    print("=== Mutation Testing: vMMIO Memory Mapping Model (guards=False) ===")
    for prop in properties():
        res_mut = modelcheck(km_mut, prop["formula"])
        violated = not km_mut.S0.issubset(res_mut)
        assert violated, f"Mutation for {prop['name']} was NOT detected!"
        print(f"  [PASS (Refuted as expected)] {prop['name']}")
