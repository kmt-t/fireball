#!/usr/bin/env python3
"""
vMMIO シナリオテスト

TLBコリジョン、権限昇格攻撃防止、キャッシュ一貫性などの
複雑なシナリオを検証する。

Keywords: {RoleBasedAccessControl} {UnifiedAccessModel}
"""

from verify_vmmio import (
    VMMIOController, VMMIOVerifier, PTE,
    get_vpn, get_tlb_idx, PAGE_SIZE,
    TIER1_BASE, TIER2_BASE, TIER3_SHM_BASE
)

class ScenarioTester:
    def __init__(self):
        self.results: list = []

    def test_tlb_collision(self):
        """シナリオ1：TLBコリジョン（複数のVPNが同じTLBスロットに）"""
        ctrl = VMMIOController()
        ctrl.init_tier2()

        # Tier3 領域で複数のVPNが同じTLBスロットを指すシナリオ
        fc = 14
        ctrl.l1_dir[14] = 1

        # 同じ L2 Index を使って複数のアドレスを作成
        l2_idx = 3

        # VPN 0（Tier3_SHM_BASE）
        raw0 = TIER3_SHM_BASE | (fc << 28) | (l2_idx << 12) | 0x000
        # VPN 16（16 << 12 = 0x10000）
        raw16 = TIER3_SHM_BASE | (fc << 28) | (l2_idx << 12) | 0x10000

        # 両者が同じ TLB スロットを指すか確認
        tlb_idx0 = get_tlb_idx(raw0)
        tlb_idx16 = get_tlb_idx(raw16)

        if tlb_idx0 != tlb_idx16:
            return False, f"TLB indices should match: {tlb_idx0} != {tlb_idx16}"

        # L2 テーブルをセットアップ
        ctrl.l2_tables[14][l2_idx] = PTE(present=True, read=True)

        # アドレス0でアクセス
        ctrl.tlb_refill(raw0)
        vpn0_result = get_vpn(raw0)

        if ctrl.tlb_cache[tlb_idx0]["vpn"] != vpn0_result:
            return False, f"TLB refill failed for raw0"

        # アドレス16でアクセス（同じスロットに詰め替え）
        ctrl.tlb_refill(raw16)
        vpn16_result = get_vpn(raw16)

        if ctrl.tlb_cache[tlb_idx16]["vpn"] != vpn16_result:
            return False, f"TLB eviction failed: VPN {ctrl.tlb_cache[tlb_idx16]['vpn']} != {vpn16_result}"

        return True, "TLB collision handling: OK"

    def test_privilege_escalation_prevention(self):
        """シナリオ2：権限昇格攻撃の防止"""
        ctrl = VMMIOController()
        ctrl.init_tier2()

        fc = 14
        l2_idx = 5
        raw = TIER3_SHM_BASE | (fc << 28) | (l2_idx << 12)

        # L1[14] をマップ
        ctrl.l1_dir[14] = 1

        # 初期状態：読取権限なし
        ctrl.l2_tables[14][l2_idx] = PTE(present=True, read=False, write=False)

        # アクセス試行 → 拒否されるはず
        if ctrl.tier3_access(raw, "read"):
            return False, "Privilege escalation: read permitted without permission"

        if ctrl.tier3_access(raw, "write"):
            return False, "Privilege escalation: write permitted without permission"

        # 権限付与
        ctrl.l2_tables[14][l2_idx] = PTE(present=True, read=True, write=True)

        # アクセス再試行 → 許可されるはず
        if not ctrl.tier3_access(raw, "read"):
            return False, "Privilege escalation: read denied after permission grant"

        if not ctrl.tier3_access(raw, "write"):
            return False, "Privilege escalation: write denied after permission grant"

        return True, "Privilege escalation prevention: OK"

    def test_cache_consistency_after_flush(self):
        """シナリオ3：TLBフラッシュ後のキャッシュ一貫性"""
        ctrl = VMMIOController()
        ctrl.init_tier2()

        # L2 テーブルをセットアップ
        fc = 14
        l2_idx = 2
        ctrl.l1_dir[14] = 1
        ctrl.l2_tables[14][l2_idx] = PTE(present=True, read=True)

        raw = TIER3_SHM_BASE | (fc << 28) | (l2_idx << 12)

        # TLB 詰め替え
        ctrl.tlb_refill(raw)
        vpn = get_vpn(raw)
        tlb_idx = get_tlb_idx(raw)

        if ctrl.tlb_cache[tlb_idx]["vpn"] != vpn:
            return False, f"TLB refill failed: VPN mismatch"

        # TLB フラッシュ
        ctrl.flush_tlb()

        if ctrl.tlb_cache[tlb_idx]["vpn"] != 0:
            return False, f"TLB flush failed: entry still contains VPN {ctrl.tlb_cache[tlb_idx]['vpn']}"

        # テーブルウォークで再取得
        pte = ctrl.table_walk(raw)
        if not pte.present:
            return False, "Table walk after flush failed"

        return True, "Cache consistency after flush: OK"

    def test_tier_separation_enforcement(self):
        """シナリオ4：Tier分離の強制"""
        ctrl = VMMIOController()
        ctrl.init_tier2()

        # Tier1: ゲストRAM（0x0000_0000 - 0x7FFF_FFFF）
        tier1_addr = 0x40000000
        if not ctrl.tier1_access(tier1_addr):
            return False, "Tier1 access check failed for valid guest RAM address"

        # Tier2: 静的デバイス（FC=12, 0xC000_0000）
        tier2_addr = TIER2_BASE
        if not ctrl.tier2_access(tier2_addr):
            return False, "Tier2 access check failed after L1[12] init"

        # Tier3: 動的vMMIO（FC=14/15, 0xE/F000_0000）
        fc = 14
        l2_idx = 0
        tier3_addr = TIER3_SHM_BASE | (fc << 28) | (l2_idx << 12)

        ctrl.l1_dir[14] = 1
        ctrl.l2_tables[14][l2_idx] = PTE(present=True, read=True)

        if not ctrl.tier3_access(tier3_addr, "read"):
            return False, "Tier3 access check failed for valid address with permission"

        # 権限 없이 접근 → 거부되어야 함
        ctrl.l2_tables[14][l2_idx] = PTE(present=True, read=False)
        if ctrl.tier3_access(tier3_addr, "read"):
            return False, "Tier3 access check failed: read permitted without permission"

        return True, "Tier separation enforcement: OK"

    def test_page_boundary_alignment(self):
        """シナリオ5：4KBページ境界アラインメント"""
        ctrl = VMMIOController()

        # 1ページ内のオフセット
        page_offsets = [0, 0x100, 0x800, 0xFFF]

        for offset in page_offsets:
            raw = TIER1_BASE + offset
            vpn = get_vpn(raw)

            if vpn != 0:
                return False, f"VPN extraction failed for offset {hex(offset)}"

        # ページ境界を超える
        raw_page1 = TIER1_BASE + 0x1000  # Next page
        vpn_page1 = get_vpn(raw_page1)

        if vpn_page1 != 1:
            return False, f"VPN should be 1 for address {hex(raw_page1)}, got {vpn_page1}"

        return True, "Page boundary alignment: OK"

    def test_l2_table_saturation(self):
        """シナリオ6：L2テーブルサイズ制約（16エントリ）"""
        ctrl = VMMIOController()

        # L2テーブルの最大インデックスは 15（16エントリ）
        max_l2_idx = 15
        fc = 14

        ctrl.l1_dir[14] = 1

        # すべてのL2スロットをマップ
        for l2_idx in range(16):
            ctrl.l2_tables[14][l2_idx] = PTE(present=True, read=True)

        # 16エントリすべてがアクセス可能か確認
        for l2_idx in range(16):
            raw = TIER3_SHM_BASE | (fc << 28) | (l2_idx << 12)
            if not ctrl.tier3_access(raw, "read"):
                return False, f"L2[{l2_idx}] access failed"

        # L2_idx = 16 はループバック（16 & 0xF = 0）
        raw_overflow = TIER3_SHM_BASE | (fc << 28) | (16 << 12)
        l2_idx_actual = get_vpn(raw_overflow) & 0xF

        if l2_idx_actual != 0:
            return False, f"L2 index wrapping failed: {l2_idx_actual} != 0"

        return True, "L2 table saturation: OK"

    def run_all(self):
        """全シナリオテストを実行"""
        tests = [
            ("TLB Collision", self.test_tlb_collision),
            ("Privilege Escalation Prevention", self.test_privilege_escalation_prevention),
            ("Cache Consistency After Flush", self.test_cache_consistency_after_flush),
            ("Tier Separation Enforcement", self.test_tier_separation_enforcement),
            ("Page Boundary Alignment", self.test_page_boundary_alignment),
            ("L2 Table Saturation", self.test_l2_table_saturation),
        ]

        print("=" * 80)
        print("vMMIO Scenario Tests")
        print("Keywords: {RoleBasedAccessControl} {UnifiedAccessModel}")
        print("=" * 80)
        print()

        all_passed = True
        for name, test_fn in tests:
            try:
                passed, message = test_fn()
                status = "✓ PASS" if passed else "✗ FAIL"
                print(f"{status}: {name}")
                if message:
                    print(f"       {message}")
                if not passed:
                    all_passed = False
            except Exception as e:
                print(f"✗ ERROR: {name} - {e}")
                all_passed = False

        print()
        print("=" * 80)
        if all_passed:
            print("✓ All scenario tests passed")
        else:
            print("✗ Some scenario tests failed")
        print("=" * 80)

        return 0 if all_passed else 1

if __name__ == "__main__":
    tester = ScenarioTester()
    exit(tester.run_all())
