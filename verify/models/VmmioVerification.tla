
---- MODULE VmmioVerification ----
EXTENDS Integers, Sequences, FiniteSets

CONSTANT FC_COUNT, L2_SIZE, TLB_SIZE
ASSUME FC_COUNT = 16
ASSUME L2_SIZE = 16
ASSUME TLB_SIZE = 16


CONSTANT TIER1_BASE, TIER1_LIMIT   
CONSTANT TIER2_BASE, TIER2_LIMIT   
CONSTANT TIER3_SHM_BASE, TIER3_SHM_LIMIT    
CONSTANT TIER3_PASS_BASE, TIER3_PASS_LIMIT 

ASSUME TIER1_BASE = 0x00000000 /\ TIER1_LIMIT = 0x7FFFFFFF
ASSUME TIER2_BASE = 0xC0000000 /\ TIER2_LIMIT = 0xCFFFFFFF
ASSUME TIER3_SHM_BASE = 0xE0000000 /\ TIER3_SHM_LIMIT = 0xEFFFFFFF
ASSUME TIER3_PASS_BASE = 0xF0000000 /\ TIER3_PASS_LIMIT = 0xFFFFFFFF



NULL == [present |-> FALSE, read |-> FALSE, write |-> FALSE,
         execute |-> FALSE, tier |-> 0]

VARIABLES
    L1Dir,             
    L2Tables,          
    TLBCache,          
    MemoryMap,         
    AccessLog          

vars == <<L1Dir, L2Tables, TLBCache, MemoryMap, AccessLog>>


GetMSB(raw) == raw \div 2147483648  
GetFC(raw) == (raw \div 268435456) % 16  
GetL2Idx(raw) == (raw \div 4096) % 16    
GetVPN(raw) == raw \div 4096
GetOffset(raw) == raw % 4096


GetTier(raw) ==
    IF GetMSB(raw) = 0 THEN 1
    ELSE IF GetFC(raw) = 12 THEN 2
    ELSE IF GetFC(raw) \in {14, 15} THEN 3
    ELSE 0  


GetTLBIdx(raw) == GetVPN(raw) % TLB_SIZE


Init ==
    /\ L1Dir = [i \in 0..FC_COUNT-1 |-> 0]  
    /\ L2Tables = [fc \in 0..FC_COUNT-1 |-> [idx \in 0..L2_SIZE-1 |-> NULL]]
    /\ TLBCache = [i \in 0..TLB_SIZE-1 |-> [vpn |-> 0, pte |-> NULL]]
    /\ MemoryMap = {}
    /\ AccessLog = <<>>


Tier1Access(raw) ==
    IF GetMSB(raw) = 0 /\ raw >= TIER1_BASE /\ raw <= TIER1_LIMIT
    THEN TRUE
    ELSE FALSE


Tier2Access(raw) ==
    IF GetFC(raw) = 12 /\ raw >= TIER2_BASE /\ raw <= TIER2_LIMIT
    THEN L1Dir[12] # 0  
    ELSE FALSE


Tier3Access(raw, permission) ==
    LET fc == GetFC(raw)
        l2_idx == GetL2Idx(raw)
        pte == IF L1Dir[fc] # 0 THEN L2Tables[fc][l2_idx] ELSE NULL
    IN IF fc \in {14, 15} /\ pte.present = TRUE
       THEN CASE permission = "read" -> pte.read
              [] permission = "write" -> pte.write
              [] permission = "execute" -> pte.execute
       ELSE FALSE


TableWalk(raw) ==
    LET fc == GetFC(raw)
        l2_idx == GetL2Idx(raw)
    IN IF L1Dir[fc] # 0
       THEN L2Tables[fc][l2_idx]
       ELSE NULL


LookupTLB(raw) ==
    LET tlb_idx == GetTLBIdx(raw)
    IN IF TLBCache[tlb_idx].vpn = GetVPN(raw)
       THEN TLBCache[tlb_idx].pte
       ELSE TableWalk(raw)


LookupRefill ==
    \E raw \in 0..131071:  
        LET vpn == GetVPN(raw)
            tlb_idx == GetTLBIdx(raw)
            pte == TableWalk(raw)
        IN IF pte.present = TRUE /\ TLBCache[tlb_idx].vpn # vpn
           THEN /\ TLBCache' = [TLBCache EXCEPT ![tlb_idx] = [vpn |-> vpn, pte |-> pte]]
                /\ UNCHANGED <<L1Dir, L2Tables, MemoryMap, AccessLog>>
           ELSE UNCHANGED vars


FlushTLB ==
    /\ TLBCache' = [i \in 0..TLB_SIZE-1 |-> [vpn |-> 0, pte |-> NULL]]
    /\ UNCHANGED <<L1Dir, L2Tables, MemoryMap, AccessLog>>


MapPage(fc, l2_idx, pte) ==
    IF fc \in {14, 15} /\ L1Dir[fc] # 0
    THEN /\ L2Tables' = [L2Tables EXCEPT ![fc][l2_idx] = pte]
         /\ TLBCache' = [i \in 0..TLB_SIZE-1 |-> [vpn |-> 0, pte |-> NULL]]  
         /\ UNCHANGED <<L1Dir, MemoryMap, AccessLog>>
    ELSE UNCHANGED vars


InitTier2 ==
    /\ L1Dir' = [L1Dir EXCEPT ![12] = 1]  
    /\ UNCHANGED <<L2Tables, TLBCache, MemoryMap, AccessLog>>

Next ==
    \/ LookupRefill
    \/ FlushTLB
    \/ MapPage(14, 0, [present |-> TRUE, read |-> TRUE, write |-> TRUE, execute |-> FALSE, tier |-> 3])
    \/ InitTier2


TLBConsistency ==
    \A i \in 0..TLB_SIZE-1:
        TLBCache[i].pte.present = TRUE =>
            (LET raw == TLBCache[i].vpn * 4096
             IN TableWalk(raw) = TLBCache[i].pte)


Tier1NoTableWalk ==
    \A raw \in 0..TIER1_LIMIT:
        Tier1Access(raw) => GetTier(raw) = 1


Tier2RequiresL1 ==
    \A raw \in TIER2_BASE..TIER2_LIMIT:
        (GetTier(raw) = 2) => (L1Dir[12] # 0)


Tier3PermissionCheck ==
    \A raw \in TIER3_SHM_BASE..TIER3_PASS_LIMIT:
        (GetTier(raw) = 3) =>
            (LET fc == GetFC(raw)
                 l2_idx == GetL2Idx(raw)
                 pte == L2Tables[fc][l2_idx]
             IN pte.present = TRUE => (pte.read \/ pte.write \/ pte.execute))


TLBSizeFixed == Cardinality(DOMAIN TLBCache) = TLB_SIZE


L2SizeFixed == \A fc \in 0..FC_COUNT-1: Cardinality(DOMAIN L2Tables[fc]) = L2_SIZE

Invariants ==
    /\ TLBConsistency
    /\ Tier1NoTableWalk
    /\ Tier2RequiresL1
    /\ Tier3PermissionCheck
    /\ TLBSizeFixed
    /\ L2SizeFixed

Spec == Init /\ [][Next]_vars


EventuallyAccessDenied ==
    \A raw \in 0..131071:
        (GetTier(raw) = 3 /\ TableWalk(raw).present = FALSE) ~> FALSE

====
