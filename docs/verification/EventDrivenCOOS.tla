-----------------------------------------------------------------------------
-- EventDrivenCOOS.tla
-- Modeling the Event-Driven COOS specification
-----------------------------------------------------------------------------
---- MODULE EventDrivenCOOS ----
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS TaskA, TaskB, IPC_REQUEST, IPC_REPLY, READY, RUNNING, BLOCKED_CALL

VARIABLES task_state, message_owner, queue, channel_state

vars == <<task_state, message_owner, queue, channel_state>>

-- Tasks: {TaskA, TaskB}
-- States: [task -> State]
-- Queue: Sequence of (Type, TargetTask, Caller)

Init ==
    /\ task_state = [t \in {TaskA, TaskB} |-> READY]
    /\ message_owner = [m \in {1} |-> TaskA] -- Assume 1 message
    /\ queue = << >>
    /\ channel_state = "IDLE"

-- Actions
Call(caller, callee, msg) ==
    /\ task_state[caller] = RUNNING
    /\ task_state' = [task_state EXCEPT ![caller] = BLOCKED_CALL, ![callee] = RUNNING]
    /\ message_owner' = [message_owner EXCEPT ![msg] = callee]
    /\ queue' = Append(queue, <<IPC_REQUEST, callee, caller>>)
    /\ channel_state' = "REQUEST_PENDING"

Reply(callee, caller, msg) ==
    /\ task_state[callee] = RUNNING
    /\ task_state[caller] = BLOCKED_CALL
    /\ task_state' = [task_state EXCEPT ![caller] = READY, ![callee] = READY]
    /\ message_owner' = [message_owner EXCEPT ![msg] = caller]
    /\ queue' = Append(queue, <<IPC_REPLY, caller, callee>>)
    /\ channel_state' = "IDLE"

-- Simplified Dispatch (Kernel loop)
Dispatch ==
    /\ queue /= << >>
    /\ LET ev == Head(queue)
       IN  /\ queue' = Tail(queue)
           /\ task_state' = [task_state EXCEPT ![ev[2]] = RUNNING]
           /\ UNCHANGED <<message_owner, channel_state>>

Next ==
    \/ \E m \in {1} : Call(TaskA, TaskB, m)
    \/ \E m \in {1} : Reply(TaskB, TaskA, m)
    \/ Dispatch

Spec == Init /\ [][Next]_vars

-- Invariants
-- 1. Ownership: A message is owned by at most one task
-- 2. Deadlock: Caller is blocked until reply
OwnershipInvariant ==
    \A m \in {1} : \E! t \in {TaskA, TaskB} : message_owner[m] = t

=============================================================================
