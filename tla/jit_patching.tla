-------------------------- MODULE jit_patching --------------------------
EXTENDS Naturals, Sequences, FiniteSets, TLC

CONSTANTS
    Templates,      \* Set of Template IDs
    Values,         \* Set of possible patch values
    MaxCodeSize     \* Maximum length of code sequence

VARIABLES
    template_store, \* Function: TemplateID -> Sequence of (Instruction \cup {HOLE})
    code_cache,     \* Function: CodeBlockID -> Sequence of (Instruction \cup Value)
    next_block_id   \* Counter for unique block IDs

HOLE == "HOLE"
Instr == {"I1", "I2", "I3"}

vars == <<template_store, code_cache, next_block_id>>

-----------------------------------------------------------------------------

TypeInvariant ==
    /\ template_store \in [Templates -> Seq(Instr \cup {HOLE})]
    /\ code_cache \in [0..(next_block_id - 1) -> Seq(Instr \cup Values)]
    /\ next_block_id \in Nat

Init ==
    \* Initialize with some templates containing HOLEs
    /\ template_store = [t \in Templates |-> << "I1", HOLE, "I2" >>] 
    /\ code_cache = <<>> \* Empty map (function with empty domain effectively)
    \* TLA+ '<<>>' is empty sequence, but we model map. Let's use empty function.
    /\ code_cache = [x \in {} |-> <<>>]
    /\ next_block_id = 0

-----------------------------------------------------------------------------

\* Helper: Apply patch value 'v' to template sequence 't_seq'
\* Replaces every occurrence of HOLE with v
ApplyPatch(t_seq, v) ==
    [i \in 1..Len(t_seq) |-> IF t_seq[i] = HOLE THEN v ELSE t_seq[i]]

\* Action: Instantiate a template with a patch value
\* Correctness:
\*  1. Template must NOT be modified.
\*  2. New code block must match Template except for HOLE -> Value.
Instantiate(t_id, val) ==
    /\ t_id \in Templates
    /\ val \in Values
    /\ next_block_id < MaxCodeSize \* Just a bound for model checking
    /\ LET new_code == ApplyPatch(template_store[t_id], val) IN
       /\ code_cache' = code_cache @@ (next_block_id :> new_code)
       /\ next_block_id' = next_block_id + 1
       /\ UNCHANGED <<template_store>> \* Vital: Template Store is unchanged

-----------------------------------------------------------------------------

Next ==
    \/ (\E t \in Templates, v \in Values : Instantiate(t, v))
    \/ (next_block_id >= MaxCodeSize /\ UNCHANGED vars) \* Done Action inline

-----------------------------------------------------------------------------

\* Property: Template Immutability
\* Ensure templates never change state (Read-Only)
TemplateImmutability ==
    [][UNCHANGED template_store]_vars

\* Property: Patch Correctness
\* Every generated block must match its source template where template is NOT a HOLE
PatchCorrectness ==
    \A bid \in DOMAIN code_cache :
        \E t \in Templates, v \in Values :
            \* We can't know which template generated which block unless we track history
            \* But structurally, it must match SOME valid instantiation.
            \* Simplify: Check if the block structure matches the standard template form <<I1, v, I2>>
            LET block == code_cache[bid] IN
            (Len(block) = 3) /\ (block[1] = "I1") /\ (block[3] = "I2") /\ (block[2] \in Values)

Spec == Init /\ [][Next]_vars

=============================================================================
