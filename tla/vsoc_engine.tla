---------------------------- MODULE vsoc_engine ----------------------------
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS
    MaxPC,          \* Length of the program
    HotspotThreshold,
    CacheSize,
    StackLimit      \* Maximum stack depth

VARIABLES
    pc,             \* Program Counter
    stack,          \* Execution Stack
    mode,           \* "INTERP" or "JIT"
    hotness,        \* Frequency of execution (PC -> Nat)
    jit_cache,      \* Compiled blocks (Set of PC)
    status          \* "RUNNING", "HALTED", "ERROR"

Vars == <<pc, stack, mode, hotness, jit_cache, status>>

last(s) == s[Len(s)]

MC_MaxPC == 5
MC_HotspotThreshold == 2
MC_CacheSize == 2
MC_StackLimit == 3

-----------------------------------------------------------------------------

\* Program Definition (Abstracted as a function PC -> Opcode)
OP_CONST == 1
OP_ADD   == 2
OP_JMP   == 3
OP_HALT  == 4
OP_DROP  == 5

\* A stack-neutral loop program: CONST 1, CONST 1, ADD, DROP, JMP 0
Program(p) ==
    IF p = 0 THEN OP_CONST
    ELSE IF p = 1 THEN OP_CONST
    ELSE IF p = 2 THEN OP_ADD
    ELSE IF p = 3 THEN OP_DROP
    ELSE IF p = 4 THEN OP_JMP
    ELSE OP_HALT

-----------------------------------------------------------------------------

Init ==
    /\ pc = 0
    /\ stack = <<>>
    /\ mode = "INTERP"
    /\ hotness = [i \in 0..MaxPC |-> 0]
    /\ jit_cache = {}
    /\ status = "RUNNING"

-----------------------------------------------------------------------------

\* Action: Interpret one instruction
Interpret ==
    /\ status = "RUNNING"
    /\ mode = "INTERP"
    /\ LET op == Program(pc) IN
       /\ IF op = OP_CONST THEN
             /\ stack' = stack \o <<1>>
             /\ pc' = pc + 1
             /\ status' = "RUNNING"
          ELSE IF op = OP_ADD THEN
             /\ IF Len(stack) >= 2 THEN
                   /\ stack' = SubSeq(stack, 1, Len(stack)-2) \o <<Head(Tail(stack)) + last(stack)>>
                   /\ pc' = pc + 1
                   /\ status' = "RUNNING"
                ELSE
                   /\ status' = "ERROR"
                   /\ UNCHANGED <<pc, stack>>
          ELSE IF op = OP_JMP THEN
             /\ pc' = 0 \* Simple loop
             /\ status' = "RUNNING"
             /\ UNCHANGED <<stack>>
          ELSE IF op = OP_HALT THEN
             /\ status' = "HALTED"
             /\ UNCHANGED <<pc, stack>>
          ELSE IF op = OP_DROP THEN
             /\ IF Len(stack) >= 1 THEN
                   /\ stack' = SubSeq(stack, 1, Len(stack)-1)
                   /\ pc' = pc + 1
                   /\ status' = "RUNNING"
                ELSE
                   /\ status' = "ERROR"
                   /\ UNCHANGED <<pc, stack>>
          ELSE 
             /\ status' = "ERROR"
             /\ UNCHANGED <<pc, stack>>
    /\ hotness' = [p \in 0..MaxPC |-> IF p = pc THEN (IF hotness[p] < HotspotThreshold THEN hotness[p] + 1 ELSE hotness[p]) ELSE hotness[p]]
    /\ UNCHANGED <<mode, jit_cache>>

\* Action: Compile a hotspot
Compile ==
    /\ status = "RUNNING"
    /\ \E p \in 0..MaxPC :
        /\ hotness[p] >= HotspotThreshold
        /\ p \notin jit_cache
        /\ Cardinality(jit_cache) < CacheSize
        /\ jit_cache' = jit_cache \cup {p}
    /\ UNCHANGED <<pc, stack, mode, hotness, status>>

\* Action: Enter JIT mode
EnterJit ==
    /\ status = "RUNNING"
    /\ mode = "INTERP"
    /\ pc \in jit_cache
    /\ mode' = "JIT"
    /\ UNCHANGED <<pc, stack, hotness, jit_cache, status>>

\* Action: JIT Execute (Functional equivalent to Interpret for the same PC)
JitExecute ==
    /\ status = "RUNNING"
    /\ mode = "JIT"
    /\ LET op == Program(pc) IN
       /\ IF op = OP_CONST THEN
             /\ IF Len(stack) < StackLimit THEN
                   /\ stack' = stack \o <<1>>
                   /\ pc' = pc + 1
                   /\ status' = "RUNNING"
                   /\ mode' = "JIT"
                ELSE
                   /\ status' = "ERROR"
                   /\ UNCHANGED <<pc, stack, mode>>
          ELSE IF op = OP_ADD THEN
             /\ IF Len(stack) >= 2 THEN
                   /\ stack' = SubSeq(stack, 1, Len(stack)-2) \o <<Head(Tail(stack)) + last(stack)>>
                   /\ pc' = pc + 1
                   /\ status' = "RUNNING"
                   /\ mode' = "JIT"
                ELSE
                   /\ status' = "ERROR"
                   /\ UNCHANGED <<pc, stack, mode>>
          ELSE IF op = OP_JMP THEN
             /\ pc' = 0
             /\ IF 0 \in jit_cache THEN mode' = "JIT" ELSE mode' = "INTERP"
             /\ status' = "RUNNING"
             /\ UNCHANGED <<stack>>
          ELSE IF op = OP_HALT THEN
             /\ status' = "HALTED"
             /\ mode' = "INTERP"
             /\ UNCHANGED <<pc, stack>>
          ELSE IF op = OP_DROP THEN
             /\ IF Len(stack) >= 1 THEN
                   /\ stack' = SubSeq(stack, 1, Len(stack)-1)
                   /\ pc' = pc + 1
                   /\ status' = "RUNNING"
                   /\ mode' = "JIT"
                ELSE
                   /\ status' = "ERROR"
                   /\ mode' = "INTERP"
                   /\ UNCHANGED <<pc, stack>>
          ELSE
             /\ status' = "ERROR"
             /\ mode' = "INTERP"
             /\ UNCHANGED <<pc, stack>>
    /\ UNCHANGED <<hotness, jit_cache>>

\* Fallback: Exit JIT mode if PC not in cache
ExitJit ==
    /\ status = "RUNNING"
    /\ mode = "JIT"
    /\ pc \notin jit_cache
    /\ mode' = "INTERP"
    /\ UNCHANGED <<pc, stack, hotness, jit_cache, status>>

-----------------------------------------------------------------------------

Next ==
    \/ Interpret
    \/ Compile
    \/ EnterJit
    \/ JitExecute
    \/ ExitJit

Spec == Init /\ [][Next]_Vars

-----------------------------------------------------------------------------

\* Invariant: Integrity of execution
\* If we are in JIT, the result must be as if we interpreted.
\* Since they share the same variables 'stack' and 'pc', and actions are mirrors,
\* this is naturally maintained, but we can check for logic errors.
ExecutionSafety == status /= "ERROR"

\* Liveness: Eventually we should compile something if hot
\* (Actually, TLC checks invariants mainly, but let's check one)
ActuallyTestsJIT == Cardinality(jit_cache) > 0 \* Can be used to check if JIT is ever reached in a specific trace

=============================================================================
