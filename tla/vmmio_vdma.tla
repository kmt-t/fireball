-------------------------- MODULE vmmio_vdma --------------------------
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS
    MemSize,        \* Total guest memory size (in pages or bytes)
    MaxTransfer     \* Maximum transfer count

VARIABLES
    memory,         \* Function: Address -> Value
    reg_src,        \* Source address register
    reg_dst,        \* Destination address register
    reg_count,      \* Count register
    reg_ctrl,       \* Control register (0: IDLE, 1: START, 2: BUSY, 3: DONE)
    irq_done        \* IRQ flag

vars == <<memory, reg_src, reg_dst, reg_count, reg_ctrl, irq_done>>

-----------------------------------------------------------------------------

\* Addresses are 0..MemSize-1
Address == 0..MemSize-1

TypeInvariant ==
    /\ memory \in [Address -> Naturals]
    /\ reg_src \in Address
    /\ reg_dst \in Address
    /\ reg_count \in 0..MaxTransfer
    /\ reg_ctrl \in {0, 1, 2, 3}
    /\ irq_done \in BOOLEAN

Init ==
    /\ memory \in [Address -> {0}] \* Initial values
    /\ reg_src = 0
    /\ reg_dst = 0
    /\ reg_count = 0
    /\ reg_ctrl = 0
    /\ irq_done = FALSE

-----------------------------------------------------------------------------

\* Action: Guest writes to registers
WriteReg(src, dst, count) ==
    /\ reg_ctrl = 0 \* Only when idle
    /\ reg_src' = src
    /\ reg_dst' = dst
    /\ reg_count' = count
    /\ UNCHANGED <<memory, reg_ctrl, irq_done>>

\* Action: Guest starts DMA
StartDMA ==
    /\ reg_ctrl = 0
    /\ reg_count > 0
    /\ reg_ctrl' = 1
    /\ UNCHANGED <<memory, reg_src, reg_dst, reg_count, irq_done>>

\* Action: Controller process DMA (Atomic or step-by-step)
ProcessDMA ==
    /\ reg_ctrl = 1
    /\ IF reg_src + reg_count <= MemSize /\ reg_dst + reg_count <= MemSize THEN
           \* Safe transfer
           /\ memory' = [a \in Address |-> 
                IF a >= reg_dst /\ a < reg_dst + reg_count
                THEN memory[reg_src + (a - reg_dst)]
                ELSE memory[a]]
           /\ reg_ctrl' = 3
           /\ irq_done' = TRUE
       ELSE
           \* Out of bounds - Abort
           /\ reg_ctrl' = 0
           /\ UNCHANGED <<memory, irq_done>>
    /\ UNCHANGED <<reg_src, reg_dst, reg_count>>

\* Action: Reset IRQ
ClearIRQ ==
    /\ irq_done = TRUE
    /\ irq_done' = FALSE
    /\ reg_ctrl = 3
    /\ reg_ctrl' = 0
    /\ UNCHANGED <<memory, reg_src, reg_dst, reg_count>>

-----------------------------------------------------------------------------

Next ==
    \/ (\E s, d \in Address, c \in 0..MaxTransfer : WriteReg(s, d, c))
    \/ StartDMA
    \/ ProcessDMA
    \/ ClearIRQ

Spec == Init /\ [][Next]_vars

-----------------------------------------------------------------------------

\* Safety: Boundaries
BoundarySafety ==
    reg_ctrl = 2 => (reg_src + reg_count <= MemSize /\ reg_dst + reg_count <= MemSize)

\* Safety: No data corruption outside target
NoSideEffect ==
    \A a \in Address :
        (reg_ctrl = 3 /\ ~(a >= reg_dst /\ a < reg_dst + reg_count)) => (memory[a] = 0)

=============================================================================
