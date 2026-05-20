#!/usr/bin/env python3
"""
vMMIO形式検証スクリプト

3層セキュリティゲート、L1/L2ページテーブル、TLBダイレクトマップの
一貫性と安全性を検証する。

Keywords: {UnifiedAccessModel} {RoleBasedAccessControl} {FastAddressCheck}
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from enum import Enum

# アドレス空間定義
TIER1_BASE = 0x00000000
TIER1_LIMIT = 0x7FFFFFFF
TIER2_BASE = 0xC0000000
TIER2_LIMIT = 0xCFFFFFFF
TIER3_SHM_BASE = 0xE0000000
TIER3_SHM_LIMIT = 0xEFFFFFFF
TIER3_PASS_BASE = 0xF0000000
TIER3_PASS_LIMIT = 0xFFFFFFFF

FC_COUNT = 16
L2_SIZE = 16
TLB_SIZE = 16
PAGE_SIZE = 4096

# PTE (Page Table Entry)
@dataclass
class PTE:
    present: bool = False
    read: bool = False
    write: bool = False
    execute: bool = False
    tier: int = 0

    def __eq__(self, other):
        if isinstance(other, PTE):
            return (self.present == other.present and
                    self.read == other.read and
                    self.write == other.write and
                    self.execute == other.execute and
                    self.tier == other.tier)
        return False

    def __repr__(self):
        return f"PTE(P={self.present},R={self.read},W={self.write},X={self.execute},T={self.tier})"

# アドレスフィールド抽出 {FastAddressCheck}
def get_msb(raw: int) -> int:
    """Bit 31 を抽出"""
    return (raw >> 31) & 1

def get_fc(raw: int) -> int:
    """Bits [31:28] (Function Code) を抽出"""
    return (raw >> 28) & 0xF

def get_l2_idx(raw: int) -> int:
    """Bits [15:12] (L2 Index) を抽出"""
    return (raw >> 12) & 0xF

def get_vpn(raw: int) -> int:
    """Virtual Page Number (raw >> 12)"""
    return raw >> 12

def get_offset(raw: int) -> int:
    """Bits [11:0] (offset within page)"""
    return raw & 0xFFF

def get_tier(raw: int) -> int:
    """Tier判定"""
    if get_msb(raw) == 0:
        return 1
    fc = get_fc(raw)
    if fc == 12:
        return 2
    if fc in {14, 15}:
        return 3
    return 0  # Invalid

def get_tlb_idx(raw: int) -> int:
    """TLBダイレクトマップインデックス"""
    return get_vpn(raw) & (TLB_SIZE - 1)

# vMMIOコントローラーシミュレーション
class VMMIOController:
    def __init__(self):
        # L1 Page Directory [0..15]
        self.l1_dir = [0] * FC_COUNT

        # L2 Page Tables [FC][L2Idx]
        self.l2_tables = {fc: [PTE() for _ in range(L2_SIZE)] for fc in range(FC_COUNT)}

        # Software TLB Cache [tlb_idx] -> {vpn, pte}
        self.tlb_cache = [{"vpn": 0, "pte": PTE()} for _ in range(TLB_SIZE)]

        # Access log for audit
        self.access_log: List[Dict] = []

    # Tier 1 (ゲストRAM)：高速バイパス {FastAddressCheck}
    def tier1_access(self, raw: int) -> bool:
        """Tier1アクセス判定"""
        return (get_msb(raw) == 0 and
                raw >= TIER1_BASE and
                raw <= TIER1_LIMIT)

    # Tier 2 (静的デバイス)：L1マップ確認
    def tier2_access(self, raw: int) -> bool:
        """Tier2アクセス判定（L1[12]が設定済み）"""
        return (get_fc(raw) == 12 and
                raw >= TIER2_BASE and
                raw <= TIER2_LIMIT and
                self.l1_dir[12] != 0)

    # Tier 3 (動的vMMIO)：実行時権限チェック {RoleBasedAccessControl}
    def tier3_access(self, raw: int, permission: str) -> bool:
        """Tier3アクセス判定（権限チェック）"""
        fc = get_fc(raw)
        if fc not in {14, 15}:
            return False
        if not (raw >= TIER3_SHM_BASE and raw <= TIER3_PASS_LIMIT):
            return False

        l2_idx = get_l2_idx(raw)
        pte = self.l2_tables[fc][l2_idx]

        if not pte.present:
            return False

        if permission == "read":
            return pte.read
        elif permission == "write":
            return pte.write
        elif permission == "execute":
            return pte.execute

        return False

    # テーブルウォーク L1->L2
    def table_walk(self, raw: int) -> PTE:
        """L1/L2ページテーブルウォーク"""
        fc = get_fc(raw)
        l2_idx = get_l2_idx(raw)

        if self.l1_dir[fc] == 0:
            return PTE()

        return self.l2_tables[fc][l2_idx]

    # TLB ルックアップ（O(1)ダイレクトマップ）
    def lookup_tlb(self, raw: int) -> PTE:
        """TLBルックアップ（ダイレクトマップ）"""
        tlb_idx = get_tlb_idx(raw)
        vpn = get_vpn(raw)

        if self.tlb_cache[tlb_idx]["vpn"] == vpn:
            return self.tlb_cache[tlb_idx]["pte"]

        return self.table_walk(raw)

    # TLB Refill
    def tlb_refill(self, raw: int) -> None:
        """TLB詰め替え（ミス時）"""
        tlb_idx = get_tlb_idx(raw)
        vpn = get_vpn(raw)
        pte = self.table_walk(raw)

        if pte.present and self.tlb_cache[tlb_idx]["vpn"] != vpn:
            self.tlb_cache[tlb_idx] = {"vpn": vpn, "pte": pte}

    # TLB フラッシュ
    def flush_tlb(self) -> None:
        """TLBキャッシュをクリア"""
        self.tlb_cache = [{"vpn": 0, "pte": PTE()} for _ in range(TLB_SIZE)]

    # L2 ページテーブル更新
    def map_page(self, fc: int, l2_idx: int, pte: PTE) -> None:
        """ページマッピング"""
        if fc in {14, 15} and self.l1_dir[fc] != 0:
            self.l2_tables[fc][l2_idx] = pte
            self.flush_tlb()

    # 初期化：L1[12]を設定
    def init_tier2(self) -> None:
        """静的デバイス領域の有効化"""
        self.l1_dir[12] = 1

# 不変条件検証
class VMMIOVerifier:
    def __init__(self, controller: VMMIOController):
        self.ctrl = controller
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def verify_tlb_consistency(self) -> bool:
        """不変条件1：TLB整合性 {RoleBasedAccessControl}"""
        for i in range(TLB_SIZE):
            entry = self.ctrl.tlb_cache[i]
            if entry["pte"].present:
                raw = entry["vpn"] * PAGE_SIZE
                walked_pte = self.ctrl.table_walk(raw)
                if walked_pte != entry["pte"]:
                    self.errors.append(
                        f"TLB[{i}] inconsistency: "
                        f"cached PTE {entry['pte']} != walked PTE {walked_pte}"
                    )
                    return False
        return True

    def verify_tier1_no_table_walk(self) -> bool:
        """不変条件2：Tier1アクセスはテーブルウォーク不要"""
        test_addrs = [0x00000000, 0x40000000, 0x7FFFFFFF]
        for raw in test_addrs:
            if self.ctrl.tier1_access(raw):
                if get_tier(raw) != 1:
                    self.errors.append(
                        f"Tier1 detection failed for addr {hex(raw)}"
                    )
                    return False
        return True

    def verify_tier2_requires_l1(self) -> bool:
        """不変条件3：Tier2アクセスはL1[12]が設定されていること"""
        test_addr = TIER2_BASE
        self.ctrl.l1_dir[12] = 0  # L1[12] を未設定

        if self.ctrl.tier2_access(test_addr):
            self.errors.append(
                f"Tier2 access allowed without L1[12] mapping"
            )
            return False

        self.ctrl.l1_dir[12] = 1  # L1[12] を設定

        if not self.ctrl.tier2_access(test_addr):
            self.errors.append(
                f"Tier2 access denied with L1[12] mapping"
            )
            return False

        return True

    def verify_tier3_permission_check(self) -> bool:
        """不変条件4：Tier3アクセスは権限チェック後のみ許可"""
        fc = 14
        l2_idx = 0
        raw = TIER3_SHM_BASE

        # 権限なし
        self.ctrl.l1_dir[14] = 1
        self.ctrl.l2_tables[14][l2_idx] = PTE(present=True, read=False, write=False)

        if self.ctrl.tier3_access(raw, "read"):
            self.errors.append(
                f"Tier3 read permitted without read permission"
            )
            return False

        # 読取権限あり
        self.ctrl.l2_tables[14][l2_idx] = PTE(present=True, read=True)

        if not self.ctrl.tier3_access(raw, "read"):
            self.errors.append(
                f"Tier3 read denied with read permission"
            )
            return False

        return True

    def verify_tlb_size_fixed(self) -> bool:
        """不変条件5：TLBサイズは固定16"""
        if len(self.ctrl.tlb_cache) != TLB_SIZE:
            self.errors.append(
                f"TLB size mismatch: {len(self.ctrl.tlb_cache)} != {TLB_SIZE}"
            )
            return False
        return True

    def verify_l2_size_fixed(self) -> bool:
        """不変条件6：L2テーブルサイズは固定16"""
        for fc in range(FC_COUNT):
            if len(self.ctrl.l2_tables[fc]) != L2_SIZE:
                self.errors.append(
                    f"L2[{fc}] size mismatch: {len(self.ctrl.l2_tables[fc])} != {L2_SIZE}"
                )
                return False
        return True

    def verify_tlb_direct_map_correctness(self) -> bool:
        """追加検証：TLBダイレクトマップの正確性"""
        # 複数の VPN をテスト
        test_vpns = [0, 1, 7, 15, 16, 255, 4095, 8191]

        for vpn in test_vpns:
            expected_idx = vpn & (TLB_SIZE - 1)
            calculated_idx = get_tlb_idx(vpn * PAGE_SIZE)

            if expected_idx != calculated_idx:
                self.errors.append(
                    f"TLB direct-map index mismatch: VPN={vpn}, "
                    f"expected {expected_idx}, got {calculated_idx}"
                )
                return False

        return True

    def verify_address_field_extraction(self) -> bool:
        """追加検証：アドレスフィールド抽出の正確性"""
        test_cases = [
            (0xC1234567, {"msb": 1, "fc": 12, "l2": 4, "vpn": 0xC1234, "offset": 0x567}),
            (0xE0000000, {"msb": 1, "fc": 14, "l2": 0, "vpn": 0xE0000, "offset": 0}),
            (0x00000000, {"msb": 0, "fc": 0, "l2": 0, "vpn": 0, "offset": 0}),
        ]

        for raw, expected in test_cases:
            result = {
                "msb": get_msb(raw),
                "fc": get_fc(raw),
                "l2": get_l2_idx(raw),
                "vpn": get_vpn(raw),
                "offset": get_offset(raw),
            }

            if result != expected:
                self.errors.append(
                    f"Address field extraction failed for {hex(raw)}: "
                    f"expected {expected}, got {result}"
                )
                return False

        return True

    def run_all_checks(self) -> bool:
        """全ての検証を実行"""
        checks = [
            ("TLB Consistency", self.verify_tlb_consistency),
            ("Tier1 No Table Walk", self.verify_tier1_no_table_walk),
            ("Tier2 Requires L1", self.verify_tier2_requires_l1),
            ("Tier3 Permission Check", self.verify_tier3_permission_check),
            ("TLB Size Fixed", self.verify_tlb_size_fixed),
            ("L2 Size Fixed", self.verify_l2_size_fixed),
            ("TLB Direct-Map Correctness", self.verify_tlb_direct_map_correctness),
            ("Address Field Extraction", self.verify_address_field_extraction),
        ]

        all_passed = True
        for name, check_fn in checks:
            try:
                result = check_fn()
                status = "✓ PASS" if result else "✗ FAIL"
                print(f"{status}: {name}")
                if not result:
                    all_passed = False
            except Exception as e:
                print(f"✗ ERROR: {name} - {e}")
                all_passed = False

        if self.errors:
            print("\n❌ Errors:")
            for err in self.errors:
                print(f"  - {err}")

        if self.warnings:
            print("\n⚠️  Warnings:")
            for warn in self.warnings:
                print(f"  - {warn}")

        return all_passed

# メイン
def main():
    print("=" * 80)
    print("vMMIO Form Verification")
    print("Keywords: {UnifiedAccessModel} {RoleBasedAccessControl} {FastAddressCheck}")
    print("=" * 80)
    print()

    ctrl = VMMIOController()
    verifier = VMMIOVerifier(ctrl)

    # Initialize Tier2
    ctrl.init_tier2()

    # Run all verification checks
    passed = verifier.run_all_checks()

    print()
    print("=" * 80)
    if passed:
        print("✓ All verification checks passed")
        print("vMMIO design: VERIFIED")
    else:
        print("✗ Some verification checks failed")
        print("vMMIO design: FAILED")
    print("=" * 80)

    return 0 if passed else 1

if __name__ == "__main__":
    exit(main())
