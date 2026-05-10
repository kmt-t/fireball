-----------------------------------------------------------------------------
-- EventDrivenCOOS_System.tla
-----------------------------------------------------------------------------
---- MODULE EventDrivenCOOS_System ----
EXTENDS Naturals, Sequences, FiniteSets, TLC

CONSTANTS TaskA, TaskB, IPC_REQUEST, IPC_REPLY, INT, IDLE, READY, RUNNING, BLOCKED_CALL

VARIABLES task_state, message_owner, queue

vars == <<task_state, message_owner, queue>>

Tasks == {TaskA, TaskB}
Messages == {1}

Init ==
    /\ task_state = [t \in Tasks |-> READY]
    /\ message_owner = [m \in Messages |-> TaskA]
    /\ queue = << >>

Interrupt(t) ==
    /\ queue' = Append(queue, <<INT, t, 0, 0>>)
    /\ UNCHANGED <<task_state, message_owner>>

IdleAction ==
    /\ queue = << >>
    /\ queue' = Append(queue, <<IDLE, TaskA, 0, 0>>)
    /\ UNCHANGED <<task_state, message_owner>>

Dispatch ==
    /\ queue /= << >>
    /\ LET ev == Head(queue)
       IN  /\ queue' = Tail(queue)
           /\ task_state' = [task_state EXCEPT ![ev[2]] = RUNNING]
           /\ UNCHANGED <<message_owner>>

Next ==
    \/ \E t \in Tasks : Interrupt(t)
    \/ IdleAction
    \/ Dispatch

Spec == Init /\ [][Next]_vars

OwnershipInvariant ==
    \A m \in Messages :
        \E t1 \in Tasks : (message_owner[m] = t1) /\ \A t2 \in Tasks : (message_owner[m] = t2) => t1 = t2

StateConsistency ==
    \A t \in Tasks : task_state[t] \in {READY, RUNNING, BLOCKED_CALL}

=============================================================================
