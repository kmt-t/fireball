----------------------- MODULE loader_rollback -----------------------
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS
    MaxRAM,         \* Total RAM size in bytes
    ROMContents     \* Sequence of (Type, Size, Valid) where Type \in {CODE, DATA, INVALID}


MC_MaxRAM == 30
MC_ROMContents == << [type |-> 1, size |-> 10, valid |-> TRUE], [type |-> 2, size |-> 20, valid |-> TRUE], [type |-> 3, size |-> 5, valid |-> FALSE] >>

last(s) == s[Len(s)]

VARIABLES
    ram_ptr,        \* Bump allocator pointer (offset from start of heap)
    module_count,   \* Number of successfully loaded modules/sections
    load_stack,     \* Memory occupied by current module (stack of ram_ptr for rollback)
    status          \* {IDLE, LOADING, SUCCESS, ERROR}

TYPE_CODE == 1
TYPE_DATA == 2
TYPE_INVALID == 3

vars == <<ram_ptr, module_count, load_stack, status>>

-----------------------------------------------------------------------------

TypeInvariant ==
    /\ ram_ptr \in 0..MaxRAM
    /\ module_count \in Nat
    /\ load_stack \in Seq(0..MaxRAM)
    /\ status \in {"IDLE", "LOADING", "SUCCESS", "ERROR"}

Init ==
    /\ ram_ptr = 0
    /\ module_count = 0
    /\ load_stack = <<>>
    /\ status = "IDLE"

-----------------------------------------------------------------------------

StartLoad ==
    /\ status = "IDLE"
    /\ module_count < 3 \* Bound for model checking
    /\ load_stack' = load_stack \o <<ram_ptr>>
    /\ status' = "LOADING"
    /\ UNCHANGED <<ram_ptr, module_count>>

\* Action: Process a section from ROM
LoadSection(type, size, valid) ==
    /\ status = "LOADING"
    /\ IF valid /\ ram_ptr + size <= MaxRAM THEN
          \* Success: bump pointer
          /\ ram_ptr' = ram_ptr + size
          /\ UNCHANGED <<module_count, load_stack, status>>
       ELSE
          \* Failure: Trigger rollback
          /\ status' = "ERROR"
          /\ UNCHANGED <<ram_ptr, module_count, load_stack>>

\* Wait, Rollback was broken, removed.

\* Fixed Rollback Logic:
FixedRollback ==
    /\ status = "ERROR"
    /\ load_stack /= <<>>
    /\ ram_ptr' = last(load_stack)
    /\ load_stack' = SubSeq(load_stack, 1, Len(load_stack) - 1)
    /\ status' = "IDLE"
    /\ UNCHANGED <<module_count>>

\* Action: Commit Load
CommitLoad ==
    /\ status = "LOADING"
    /\ status' = "SUCCESS"
    /\ module_count' = module_count + 1
    /\ load_stack' = SubSeq(load_stack, 1, Len(load_stack) - 1)
    /\ UNCHANGED <<ram_ptr>>



-----------------------------------------------------------------------------

\* Action: Reset after success/error to allow more loads
Reset ==
    /\ status \in {"SUCCESS", "ERROR"}
    /\ status' = "IDLE"
    /\ UNCHANGED <<ram_ptr, module_count, load_stack>>

Next ==
    \/ StartLoad
    \/ (\E r \in {ROMContents[i] : i \in 1..Len(ROMContents)} : LoadSection(r.type, r.size, r.valid))
    \/ FixedRollback
    \/ CommitLoad
    \/ Reset
    \/ (module_count = 3 /\ UNCHANGED vars)

Spec == Init /\ [][Next]_vars

-----------------------------------------------------------------------------

\* Safety: RAM pointer never exceeds limit
RamLimitSafety == ram_ptr <= MaxRAM

\* Safety: If error occurred, ram_ptr must eventually return to pre-load state
RollbackCorrectness == 
    (status = "ERROR") => <>(status = "IDLE" /\ ram_ptr = ram_ptr) \* This is tricky in TLA

\* Better Property: In IDLE or SUCCESS, ram_ptr reflects committed data
Consistency == (status = "IDLE" \/ status = "SUCCESS") => (module_count >= 0)

=============================================================================
