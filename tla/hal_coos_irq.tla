------------------------ MODULE hal_coos_irq ------------------------
EXTENDS Naturals, Sequences, TLC

\* Model: Physical IRQ → HAL ISR → COOS Notify → Task Wakeup → WASM Handler Execution
\* 
\* Context transitions:
\* 1. Physical IRQ fires → HAL ISR (interrupt context)
\* 2. HAL ISR sets pending flag, returns  
\* 3. COOS idle loop checks pending → notifies waiting task
\* 4. Task transitions: BLOCKED → READY → RUNNING
\* 5. WASM handler executes (application context)

CONSTANTS
    TaskIds,        \* Set of task IDs
    IrqIds          \* Set of IRQ numbers

VARIABLES
    irq_pending,    \* Set of pending IRQ numbers
    irq_handler,    \* Function: IrqId → TaskId (which task handles which IRQ)
    task_state,     \* Function: TaskId → state
    current_task,   \* Currently running task (0 = idle)
    wasm_context    \* Current execution context: "idle" | "isr" | "task"

\* Task states
STATE_FREE == "free"
STATE_READY == "ready"
STATE_RUNNING == "running"
STATE_BLOCKED == "blocked"

vars == <<irq_pending, irq_handler, task_state, current_task, wasm_context>>

-----------------------------------------------------------------------------

TypeInvariant ==
    /\ irq_pending \subseteq IrqIds
    /\ irq_handler \in [IrqIds -> TaskIds]
    /\ task_state \in [TaskIds -> {STATE_FREE, STATE_READY, STATE_RUNNING, STATE_BLOCKED}]
    /\ current_task \in (TaskIds \cup {0})
    /\ wasm_context \in {"idle", "isr", "task"}

Coherence ==
    /\ current_task /= 0 => task_state[current_task] = STATE_RUNNING
    /\ wasm_context = "task" <=> current_task /= 0
    /\ wasm_context = "isr" => current_task = 0

-----------------------------------------------------------------------------

Init ==
    /\ irq_pending = {}
    /\ irq_handler \in [IrqIds -> TaskIds]  \* Configured at startup
    /\ task_state = [t \in TaskIds |-> STATE_FREE]
    /\ current_task = 0
    /\ wasm_context = "idle"

-----------------------------------------------------------------------------

\* Step 1: Physical IRQ fires → HAL ISR runs
PhysicalIRQ(irq) ==
    /\ irq \in IrqIds
    /\ wasm_context \in {"idle", "task"}  \* IRQ can preempt
    /\ irq_pending' = irq_pending \cup {irq}
    /\ wasm_context' = "isr"
    /\ UNCHANGED <<irq_handler, task_state, current_task>>

\* Step 2: HAL ISR completes → returns to previous context
ISRComplete ==
    /\ wasm_context = "isr"
    /\ wasm_context' = IF current_task = 0 THEN "idle" ELSE "task"
    /\ UNCHANGED <<irq_pending, irq_handler, task_state, current_task>>

\* Step 3: COOS idle loop checks pending IRQs → notifies handler task
COOSNotify ==
    /\ wasm_context = "idle"
    /\ irq_pending /= {}
    /\ LET irq == CHOOSE i \in irq_pending : TRUE
           handler_task == irq_handler[irq]
       IN
        /\ task_state[handler_task] = STATE_BLOCKED  \* Task was waiting for this IRQ
        /\ task_state' = [task_state EXCEPT ![handler_task] = STATE_READY]
        /\ irq_pending' = irq_pending \ {irq}
        /\ UNCHANGED <<irq_handler, current_task, wasm_context>>

\* Step 4: Scheduler picks ready task
Schedule ==
    /\ wasm_context = "idle"
    /\ current_task = 0
    /\ \E t \in TaskIds : task_state[t] = STATE_READY
    /\ LET next_task == CHOOSE t \in TaskIds : task_state[t] = STATE_READY
       IN
        /\ current_task' = next_task
        /\ task_state' = [task_state EXCEPT ![next_task] = STATE_RUNNING]
        /\ wasm_context' = "task"
        /\ UNCHANGED <<irq_pending, irq_handler>>

\* Step 5: WASM handler executes and completes → yields
TaskYield ==
    /\ current_task /= 0
    /\ wasm_context = "task"
    /\ task_state' = [task_state EXCEPT ![current_task] = STATE_READY]
    /\ current_task' = 0
    /\ wasm_context' = "idle"
    /\ UNCHANGED <<irq_pending, irq_handler>>

\* Task blocks waiting for IRQ
TaskWait ==
    /\ current_task /= 0
    /\ wasm_context = "task"
    /\ task_state' = [task_state EXCEPT ![current_task] = STATE_BLOCKED]
    /\ current_task' = 0
    /\ wasm_context' = "idle"
    /\ UNCHANGED <<irq_pending, irq_handler>>

-----------------------------------------------------------------------------

Next ==
    \/ (\E irq \in IrqIds : PhysicalIRQ(irq))
    \/ ISRComplete
    \/ COOSNotify
    \/ Schedule
    \/ TaskYield
    \/ TaskWait

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

-----------------------------------------------------------------------------

\* Safety: No task runs in ISR context
NoTaskInISR == wasm_context = "isr" => current_task = 0

\* Liveness: Pending IRQ eventually wakes handler task
IRQEventuallyHandled ==
    \A irq \in IrqIds :
        (irq \in irq_pending /\ task_state[irq_handler[irq]] = STATE_BLOCKED)
            ~> (task_state[irq_handler[irq]] = STATE_RUNNING)

\* Safety: At most one context active
ContextExclusive == 
    /\ (wasm_context = "isr" => current_task = 0)
    /\ (wasm_context = "task" <=> current_task /= 0)

=============================================================================
