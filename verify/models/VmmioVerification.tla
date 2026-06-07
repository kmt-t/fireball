--------------------------------------------------------------------------------
-- vSoC vMMIO セキュリティゲート・TLB整合性の形式検証
-- 3層セキュリティ (Tier 1/2/3)、L1/L2ダイレクトインデックス、
-- ダイレクトマップTLBの一貫性を検証する。
-- {UnifiedAccessModel} {RoleBasedAccessControl} {FastAddressCheck}
--------------------------------------------------------------------------------

---- MODULE VmmioVerification ----
EXTENDS Integers, Sequences, FiniteSets

CONSTANT FC_COUNT, L2_SIZE, TLB_SIZE
ASSUME FC_COUNT = 16
ASSUME L2_SIZE = 16
ASSUME TLB_SIZE = 16

-- アドレス空間定義 {UnifiedAccessModel}
CONSTANT TIER1_BASE, TIER1_LIMIT   -- Bit 31 = 0, [0x0000_0000 - 0x7FFF_FFFF]
CONSTANT TIER2_BASE, TIER2_LIMIT   -- FC=12, [0xC000_0000 - 0xCFFF_FFFF]
CONSTANT TIER3_SHM_BASE, TIER3_SHM_LIMIT    -- FC=14, [0xE000_0000 - 0xEFFF_FFFF]
CONSTANT TIER3_PASS_BASE, TIER3_PASS_LIMIT -- FC=15, [0xF000_0000 - 0xFFFF_FFFF]

ASSUME TIER1_BASE = 0x00000000 /\ TIER1_LIMIT = 0x7FFFFFFF
ASSUME TIER2_BASE = 0xC0000000 /\ TIER2_LIMIT = 0xCFFFFFFF
ASSUME TIER3_SHM_BASE = 0xE0000000 /\ TIER3_SHM_LIMIT = 0xEFFFFFFF
ASSUME TIER3_PASS_BASE = 0xF0000000 /\ TIER3_PASS_LIMIT = 0xFFFFFFFF

-- PTE フィールド定義 {RoleBasedAccessControl}
-- PTE = {present, read, write, execute, tier}
NULL == [present |-> FALSE, read |-> FALSE, write |-> FALSE,
         execute |-> FALSE, tier |-> 0]

VARIABLES
    L1Dir,             -- L1 Page Directory [0..15] -> (NULL or L2 table id)
    L2Tables,          -- L2 Page Tables [FC][L2Idx] -> PTE
    TLBCache,          -- Software TLB Cache [tlb_idx] -> {vpn, pte}
    MemoryMap,         -- Mapped regions for verification
    AccessLog          -- Log of accesses for audit trail

vars == <<L1Dir, L2Tables, TLBCache, MemoryMap, AccessLog>>

-- アドレスフィールド抽出 {FastAddressCheck}
GetMSB(raw) == raw \div 2147483648  -- Bit 31
GetFC(raw) == (raw \div 268435456) % 16  -- Bits [31:28]
GetL2Idx(raw) == (raw \div 4096) % 16    -- Bits [15:12]
GetVPN(raw) == raw \div 4096
GetOffset(raw) == raw % 4096

-- Tier判定
GetTier(raw) ==
    IF GetMSB(raw) = 0 THEN 1
    ELSE IF GetFC(raw) = 12 THEN 2
    ELSE IF GetFC(raw) \in {14, 15} THEN 3
    ELSE 0  -- Invalid

-- TLBダイレクトマップインデックス計算 {FastAddressCheck}
GetTLBIdx(raw) == GetVPN(raw) % TLB_SIZE

-- 初期状態
Init ==
    /\ L1Dir = [i \in 0..FC_COUNT-1 |-> 0]  -- 0 = NULL
    /\ L2Tables = [fc \in 0..FC_COUNT-1 |-> [idx \in 0..L2_SIZE-1 |-> NULL]]
    /\ TLBCache = [i \in 0..TLB_SIZE-1 |-> [vpn |-> 0, pte |-> NULL]]
    /\ MemoryMap = {}
    /\ AccessLog = <<>>

-- Tier 1 (ゲストRAM)：高速バイパス {FastAddressCheck}
Tier1Access(raw) ==
    IF GetMSB(raw) = 0 /\ raw >= TIER1_BASE /\ raw <= TIER1_LIMIT
    THEN TRUE
    ELSE FALSE

-- Tier 2 (静的デバイス)：L1/L2テーブルウォーク {FastAddressCheck}
Tier2Access(raw) ==
    IF GetFC(raw) = 12 /\ raw >= TIER2_BASE /\ raw <= TIER2_LIMIT
    THEN L1Dir[12] # 0  -- L1[12]がマップされていること
    ELSE FALSE

-- Tier 3 (動的vMMIO)：実行時権限チェック {RoleBasedAccessControl}
Tier3Access(raw, permission) ==
    LET fc == GetFC(raw)
        l2_idx == GetL2Idx(raw)
        pte == IF L1Dir[fc] # 0 THEN L2Tables[fc][l2_idx] ELSE NULL
    IN IF fc \in {14, 15} /\ pte.present = TRUE
       THEN CASE permission = "read" -> pte.read
              [] permission = "write" -> pte.write
              [] permission = "execute" -> pte.execute
       ELSE FALSE

-- テーブルウォーク：L1->L2 {FastAddressCheck}
TableWalk(raw) ==
    LET fc == GetFC(raw)
        l2_idx == GetL2Idx(raw)
    IN IF L1Dir[fc] # 0
       THEN L2Tables[fc][l2_idx]
       ELSE NULL

-- TLB ルックアップ（O(1)ダイレクトマップ）
LookupTLB(raw) ==
    LET tlb_idx == GetTLBIdx(raw)
    IN IF TLBCache[tlb_idx].vpn = GetVPN(raw)
       THEN TLBCache[tlb_idx].pte
       ELSE TableWalk(raw)

-- TLBRefill：ミス時にテーブルウォーク結果を詰め替え
LookupRefill ==
    \E raw \in 0..131071:  -- 32KB アドレス空間でテスト
        LET vpn == GetVPN(raw)
            tlb_idx == GetTLBIdx(raw)
            pte == TableWalk(raw)
        IN IF pte.present = TRUE /\ TLBCache[tlb_idx].vpn # vpn
           THEN /\ TLBCache' = [TLBCache EXCEPT ![tlb_idx] = [vpn |-> vpn, pte |-> pte]]
                /\ UNCHANGED <<L1Dir, L2Tables, MemoryMap, AccessLog>>
           ELSE UNCHANGED vars

-- TLB フラッシュ（キャッシュ一貫性）
FlushTLB ==
    /\ TLBCache' = [i \in 0..TLB_SIZE-1 |-> [vpn |-> 0, pte |-> NULL]]
    /\ UNCHANGED <<L1Dir, L2Tables, MemoryMap, AccessLog>>

-- L2 ページテーブル更新（動的マッピング用）
MapPage(fc, l2_idx, pte) ==
    IF fc \in {14, 15} /\ L1Dir[fc] # 0
    THEN /\ L2Tables' = [L2Tables EXCEPT ![fc][l2_idx] = pte]
         /\ TLBCache' = [i \in 0..TLB_SIZE-1 |-> [vpn |-> 0, pte |-> NULL]]  -- Flush
         /\ UNCHANGED <<L1Dir, MemoryMap, AccessLog>>
    ELSE UNCHANGED vars

-- 初期化：L1[12] を設定（静的デバイス領域の有効化）
InitTier2 ==
    /\ L1Dir' = [L1Dir EXCEPT ![12] = 1]  -- L1[12] = pointer
    /\ UNCHANGED <<L2Tables, TLBCache, MemoryMap, AccessLog>>

Next ==
    \/ LookupRefill
    \/ FlushTLB
    \/ MapPage(14, 0, [present |-> TRUE, read |-> TRUE, write |-> TRUE, execute |-> FALSE, tier |-> 3])
    \/ InitTier2

-- 不変条件 1：TLB 整合性 {RoleBasedAccessControl}
TLBConsistency ==
    \A i \in 0..TLB_SIZE-1:
        TLBCache[i].pte.present = TRUE =>
            (LET raw == TLBCache[i].vpn * 4096
             IN TableWalk(raw) = TLBCache[i].pte)

-- 不変条件 2：Tier1 アクセスは高速バイパス（テーブルウォーク不要）
Tier1NoTableWalk ==
    \A raw \in 0..TIER1_LIMIT:
        Tier1Access(raw) => GetTier(raw) = 1

-- 不変条件 3：Tier2 アクセスは L1[12] が設定されていること
Tier2RequiresL1 ==
    \A raw \in TIER2_BASE..TIER2_LIMIT:
        (GetTier(raw) = 2) => (L1Dir[12] # 0)

-- 不変条件 4：Tier3 アクセスは権限チェック後のみ許可
Tier3PermissionCheck ==
    \A raw \in TIER3_SHM_BASE..TIER3_PASS_LIMIT:
        (GetTier(raw) = 3) =>
            (LET fc == GetFC(raw)
                 l2_idx == GetL2Idx(raw)
                 pte == L2Tables[fc][l2_idx]
             IN pte.present = TRUE => (pte.read \/ pte.write \/ pte.execute))

-- 不変条件 5：TLB キャッシュサイズは固定 16
TLBSizeFixed == Cardinality(DOMAIN TLBCache) = TLB_SIZE

-- 不変条件 6：L2 テーブルサイズは固定 16
L2SizeFixed == \A fc \in 0..FC_COUNT-1: Cardinality(DOMAIN L2Tables[fc]) = L2_SIZE

Invariants ==
    /\ TLBConsistency
    /\ Tier1NoTableWalk
    /\ Tier2RequiresL1
    /\ Tier3PermissionCheck
    /\ TLBSizeFixed
    /\ L2SizeFixed

Spec == Init /\ [][Next]_vars

-- LTL 特性：いずれかの権限チェック不合格時、アクセス拒否
EventuallyAccessDenied ==
    \A raw \in 0..131071:
        (GetTier(raw) = 3 /\ TableWalk(raw).present = FALSE) ~> FALSE

================================================================================
