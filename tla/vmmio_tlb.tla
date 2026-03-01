-------------------------- MODULE vmmio_tlb --------------------------
EXTENDS Naturals, FiniteSets, Sequences

CONSTANTS
    Pages,          \* Set of guest pages
    Tiers           \* {TIER1, TIER2, TIER3}

MC_Pages == {1, 2, 3}
MC_Tiers == {1, 2, 3}

VARIABLES
    tlb_cache,      \* Function: Page -> [tier: Tiers, valid: BOOLEAN]
    perm_table,     \* Function: Page -> Tiers (Static permission table)
    current_tier,   \* Current execution tier
    access_status   \* {"OK", "SECURITY_FAULT", "IDLE"}

TIER1 == 1
TIER2 == 2
TIER3 == 3

vars == <<tlb_cache, perm_table, current_tier, access_status>>

-----------------------------------------------------------------------------

TypeInvariant ==
    /\ tlb_cache \in [Pages -> [tier: {TIER1, TIER2, TIER3}, valid: BOOLEAN]]
    /\ perm_table \in [Pages -> {TIER1, TIER2, TIER3}]
    /\ current_tier \in {TIER1, TIER2, TIER3}
    /\ access_status \in {"OK", "SECURITY_FAULT", "IDLE"}

Init ==
    /\ tlb_cache = [p \in Pages |-> [tier |-> TIER3, valid |-> FALSE]]
    /\ perm_table \in [Pages -> {TIER1, TIER2, TIER3}]
    /\ current_tier = TIER3
    /\ access_status = "IDLE"

-----------------------------------------------------------------------------

\* Action: Guest attempts to access a page
AccessPage(p) ==
    LET permitted == (current_tier <= perm_table[p]) IN
    /\ IF tlb_cache[p].valid THEN
          \* TLB Hit
          /\ access_status' = IF permitted THEN "OK" ELSE "SECURITY_FAULT"
          /\ UNCHANGED <<tlb_cache, perm_table, current_tier>>
       ELSE
          \* TLB Miss -> Refill from perm_table
          /\ tlb_cache' = [tlb_cache EXCEPT ![p] = [tier |-> perm_table[p], valid |-> TRUE]]
          /\ access_status' = IF permitted THEN "OK" ELSE "SECURITY_FAULT"
          /\ UNCHANGED <<perm_table, current_tier>>

\* Action: Tier transition (e.g., trap to host)
EnterTier(t) ==
    /\ t \in {TIER1, TIER2}
    /\ current_tier' = t
    /\ access_status' = "IDLE"
    /\ UNCHANGED <<tlb_cache, perm_table>>

ExitTier ==
    /\ current_tier < TIER3
    /\ current_tier' = TIER3
    /\ access_status' = "IDLE"
    /\ UNCHANGED <<tlb_cache, perm_table>>

-----------------------------------------------------------------------------

Next ==
    \/ \E p \in Pages : AccessPage(p)
    \/ \E t \in {TIER1, TIER2} : EnterTier(t)
    \/ ExitTier

Spec == Init /\ [][Next]_vars

-----------------------------------------------------------------------------

\* Safety: Access is only granted if tier is sufficient
SecurityInvariant ==
    (access_status = "OK") => \E p \in Pages : (current_tier <= perm_table[p])

\* Consistency: TLB always reflects perm_table if valid
TLBConsistency ==
    \A p \in Pages : tlb_cache[p].valid => (tlb_cache[p].tier = perm_table[p])

=============================================================================
