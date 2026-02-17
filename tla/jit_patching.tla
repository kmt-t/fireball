-------------------------- MODULE jit_patching --------------------------
EXTENDS Naturals, Sequences, FiniteSets, TLC

CONSTANTS
    Templates,      \* Set of Template IDs
    Values,         \* Set of possible patch values
    MaxCodeSize     \* Maximum length of code sequence

VARIABLES
    template_store, \* Function: TemplateID -> Sequence of (Instruction \cup {HOLE})
    code_cache,     \* Function: CodeBlockID -> Sequence of (Instruction \cup Value)
    next_block_id,  \* Counter for unique block IDs
    patching_state  \* State of the patching process: IDLE or PATCHING

HOLE == "HOLE"
Instr == {"I1", "I2", "I3"}

vars == <<template_store, code_cache, next_block_id, patching_state>>

-----------------------------------------------------------------------------

TypeInvariant ==
    /\ template_store \in [Templates -> Seq(Instr \cup {HOLE})]
    /\ code_cache \in [0..(next_block_id) -> Seq(Instr \cup Values \cup {"None"})]
    /\ next_block_id \in 0..MaxCodeSize
    /\ patching_state \in {"IDLE", "PATCHING"}

Init ==
    /\ template_store = [t \in Templates |-> << "I1", HOLE, "I2" >>] 
    /\ code_cache = [x \in 0..MaxCodeSize |-> <<>>]
    /\ next_block_id = 0
    /\ patching_state = "IDLE"

-----------------------------------------------------------------------------

\* Helper: Apply patch value 'v' to template sequence 't_seq'
ApplyPatch(t_seq, v) ==
    [i \in 1..Len(t_seq) |-> IF t_seq[i] = HOLE THEN v ELSE t_seq[i]]

\* Action: Atomic Instantiate (The target behavior)
InstantiateAtomic(t_id, val) ==
    /\ patching_state = "IDLE"
    /\ t_id \in Templates
    /\ val \in Values
    /\ next_block_id < MaxCodeSize
    /\ LET new_code == ApplyPatch(template_store[t_id], val) IN
       /\ code_cache' = [code_cache EXCEPT ![next_block_id] = new_code]
       /\ next_block_id' = next_block_id + 1
       /\ UNCHANGED <<template_store, patching_state>>

\* Action: Start Non-Atomic Patch (Modeling potential race/inconsistency)
StartPatch(t_id) ==
    /\ patching_state = "IDLE"
    /\ next_block_id < MaxCodeSize
    /\ code_cache' = [code_cache EXCEPT ![next_block_id] = template_store[t_id]]
    /\ patching_state' = "PATCHING"
    /\ UNCHANGED <<template_store, next_block_id>>

\* Action: Complete Patch
CompletePatch(val) ==
    /\ patching_state = "PATCHING"
    /\ val \in Values
    /\ code_cache' = [code_cache EXCEPT ![next_block_id] = ApplyPatch(code_cache[next_block_id], val)]
    /\ next_block_id' = next_block_id + 1
    /\ patching_state' = "IDLE"
    /\ UNCHANGED <<template_store>>

-----------------------------------------------------------------------------

Next ==
    \/ (\E t \in Templates, v \in Values : InstantiateAtomic(t, v))
    \/ (\E t \in Templates : StartPatch(t))
    \/ (\E v \in Values : CompletePatch(v))

Spec == Init /\ [][Next]_vars

-----------------------------------------------------------------------------

\* Property: Patch Correctness
\* A completed block must NOT contain any HOLEs.
NoHolesInCache ==
    \A bid \in 0..(next_block_id - 1) :
        \A i \in 1..Len(code_cache[bid]) : code_cache[bid][i] /= HOLE

\* Safety: Template Immutability
TemplateImmutability == [][UNCHANGED template_store]_vars

=============================================================================
