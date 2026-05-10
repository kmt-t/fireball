-----------------------------------------------------------------------------
-- EventDrivenCOOS_Revised.tla
-----------------------------------------------------------------------------
---- MODULE EventDrivenCOOS_Revised ----
EXTENDS Naturals, Sequences, FiniteSets, TLC

CONSTANTS TaskA, TaskB, IPC_REQUEST, IPC_REPLY, READY, RUNNING, BLOCKED_CALL

VARIABLES task_state, message_owner, queue

vars == <<task_state, message_owner, queue>>

Tasks == {TaskA, TaskB}
Messages == {1}

Init ==
    /\ task_state = [t \in Tasks |-> READY]
    /\ message_owner = [m \in Messages |-> TaskA]
    /\ queue = << >>

Spawn(t) ==
    /\ task_state[t] = READY
    /\ task_state' = [task_state EXCEPT ![t] = RUNNING]
    /\ UNCHANGED <<message_owner, queue>>

Call(caller, callee, msg) ==
    /\ message_owner[msg] = caller
    /\ task_state[caller] = RUNNING
    /\ task_state' = [task_state EXCEPT ![caller] = BLOCKED_CALL]
    /\ message_owner' = [message_owner EXCEPT ![msg] = callee]
    /\ queue' = Append(queue, <<IPC_REQUEST, callee, caller, msg>>)

Reply(callee, caller, msg) ==
    /\ message_owner[msg] = callee
    /\ task_state[caller] = BLOCKED_CALL
    /\ task_state' = [task_state EXCEPT ![caller] = READY]
    /\ message_owner' = [message_owner EXCEPT ![msg] = caller]
    /\ queue' = Append(queue, <<IPC_REPLY, caller, callee, msg>>)

Dispatch ==
    /\ queue /= << >>
    /\ LET ev == Head(queue)
       IN  /\ queue' = Tail(queue)
           /\ task_state' = [task_state EXCEPT ![ev[2]] = RUNNING]
           /\ UNCHANGED <<message_owner>>

Next ==
    \/ \E t \in Tasks : Spawn(t)
    \/ \E m \in Messages : Call(TaskA, TaskB, m)
    \/ \E m \in Messages : Reply(TaskB, TaskA, m)
    \/ Dispatch

Spec == Init /\ [][Next]_vars

OwnershipInvariant ==
    \A m \in Messages :
        \E t1 \in Tasks : (message_owner[m] = t1) /\ \A t2 \in Tasks : (message_owner[m] = t2) => t1 = t2

StateConsistency ==
    \A t \in Tasks : task_state[t] \in {READY, RUNNING, BLOCKED_CALL}

=============================================================================
