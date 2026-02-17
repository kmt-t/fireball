---------------------------- MODULE interpreter ----------------------------
EXTENDS Naturals, Sequences

CONSTANTS
    StackSize,      \* Maximum stack size
    CodeSize,       \* Code size (abstract)
    Instructions    \* Set of valid opcodes

VARIABLES
    pc,             \* Program Counter
    sp,             \* Stack Pointer
    stack,          \* Abstract stack (sequence)
    state,          \* Execution State: Ready, Running, Trapped
    trap_code       \* Trap reason if trapped

Vars == <<pc, sp, stack, state, trap_code>>

-----------------------------------------------------------------------------

\* States
Ready   == "Ready"
Running == "Running"
Trapped == "Trapped"

\* Opcodes (Abstract)
OP_NOP   == 0
OP_CONST == 1
OP_ADD   == 2
OP_SUB   == 3
OP_CALL  == 16 \* fireball_call
OP_END   == 255

-----------------------------------------------------------------------------

TypeOK ==
    /\ pc \in 0..CodeSize
    /\ sp \in 0..StackSize
    /\ stack \in Seq(Nat)
    /\ Len(stack) = sp
    /\ state \in {Ready, Running, Trapped}

Init ==
    /\ pc = 0
    /\ sp = 0
    /\ stack = <<>>
    /\ state = Ready
    /\ trap_code = 0

-----------------------------------------------------------------------------

\* Actions

Start ==
    /\ state = Ready
    /\ state' = Running
    /\ UNCHANGED <<pc, sp, stack, trap_code>>

FetchDecode ==
    /\ state = Running
    /\ pc < CodeSize
    \* Abstract fetch: non-deterministically choose an instruction
    /\ \E op \in Instructions :
        \/ /\ op = OP_CONST
           /\ sp + 1 <= StackSize
           /\ stack' = Append(stack, 42) \* Abstract val
           /\ sp' = sp + 1
           /\ pc' = pc + 1
           /\ UNCHANGED <<state, trap_code>>
        \/ /\ op = OP_ADD
           /\ sp >= 2
           /\ stack' = SubSeq(stack, 1, sp-2) \o << 99 >> \* Abstract result
           /\ sp' = sp - 1
           /\ pc' = pc + 1
           /\ UNCHANGED <<state, trap_code>>
        \/ /\ op = OP_CALL \* fireball_call simulation
           /\ state' = Trapped
           /\ trap_code' = 1 \* SYSCALL/VMMIO
           /\ UNCHANGED <<pc, sp, stack>>
        \* Error Cases (Traps)
        \/ /\ op = OP_CONST /\ sp + 1 > StackSize \* Overflow
           /\ state' = Trapped
           /\ trap_code' = 2 \* STACK_OVERFLOW
           /\ UNCHANGED <<pc, sp, stack>>
        \/ /\ op = OP_ADD /\ sp < 2 \* Underflow
           /\ state' = Trapped
           /\ trap_code' = 2 \* STACK_UNDERFLOW OR INVALID_STACK
           /\ UNCHANGED <<pc, sp, stack>>

Next ==
    \/ Start
    \/ FetchDecode

-----------------------------------------------------------------------------

\* Invariants

StackSafety ==
    /\ Len(stack) <= StackSize
    /\ Len(stack) >= 0

ExecutionTermination ==
    state = Trapped => trap_code /= 0

Spec == Init /\ [][Next]_Vars

=============================================================================
