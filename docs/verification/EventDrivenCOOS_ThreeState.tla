---- MODULE EventDrivenCOOS_ThreeState ----

EXTENDS Naturals, Sequences, FiniteSets, TLC

CONSTANTS
    TaskA, TaskB,
    IPC_REQUEST, IPC_REPLY, INT,
    READY, RUNNING, BLOCKED,
    QUEUE_MAX_SIZE

VARIABLES
    task_state,
    message_owner,
    queue,
    dropped_count

vars == <<task_state, message_owner, queue, dropped_count>>

Tasks == {TaskA, TaskB}
Messages == {1}

Init ==
    /\ task_state = [t \in Tasks |-> READY]
    /\ message_owner = [m \in Messages |-> TaskA]
    /\ queue = << >>
    /\ dropped_count = 0

Call(caller, callee, msg) ==
    /\ message_owner[msg] = caller
    /\ task_state[caller] = RUNNING
    /\ Len(queue) < QUEUE_MAX_SIZE
    /\ task_state' = [task_state EXCEPT ![caller] = BLOCKED]
    /\ message_owner' = [message_owner EXCEPT ![msg] = callee]
    /\ queue' = Append(queue, <<IPC_REQUEST, callee, caller, msg>>)
    /\ UNCHANGED dropped_count

Reply(callee, caller, msg) ==
    /\ message_owner[msg] = callee
    /\ task_state[caller] = BLOCKED
    /\ Len(queue) < QUEUE_MAX_SIZE
    /\ task_state' = [task_state EXCEPT ![caller] = READY]
    /\ message_owner' = [message_owner EXCEPT ![msg] = caller]
    /\ queue' = Append(queue, <<IPC_REPLY, caller, callee, msg>>)
    /\ UNCHANGED dropped_count

Interrupt(t) ==
    /\ Len(queue) < QUEUE_MAX_SIZE
    /\ queue' = Append(queue, <<INT, t, 0, 0>>)
    /\ UNCHANGED <<task_state, message_owner, dropped_count>>

Dispatch ==
    /\ queue /= << >>
    /\ LET ev == Head(queue)
           target_task == ev[2]
       IN  /\ queue' = Tail(queue)
           /\ IF ev[1] = INT
              THEN
                  /\ IF task_state[target_task] = BLOCKED
                     THEN task_state' = [task_state EXCEPT ![target_task] = READY]
                     ELSE task_state' = task_state
              ELSE
                  /\ task_state' = task_state
           /\ UNCHANGED <<message_owner, dropped_count>>

Schedule ==
    /\ \A t \in Tasks : task_state[t] /= RUNNING
    /\ \E t \in Tasks :
           /\ task_state[t] = READY
           /\ task_state' = [task_state EXCEPT ![t] = RUNNING]
    /\ UNCHANGED <<message_owner, queue, dropped_count>>

Yield(t) ==
    /\ task_state[t] = RUNNING
    /\ task_state' = [task_state EXCEPT ![t] = READY]
    /\ UNCHANGED <<message_owner, queue, dropped_count>>

Next ==
    \/ \E m \in Messages : Call(TaskA, TaskB, m)
    \/ \E m \in Messages : Reply(TaskB, TaskA, m)
    \/ \E t \in Tasks : Interrupt(t)
    \/ Dispatch
    \/ Schedule
    \/ \E t \in Tasks : Yield(t)

Spec == Init /\ [][Next]_vars

StateConsistency ==
    \A t \in Tasks : task_state[t] \in {READY, RUNNING, BLOCKED}

QueueBounded ==
    Len(queue) <= QUEUE_MAX_SIZE

EventValidity ==
    \A i \in 1..Len(queue) :
        LET ev == queue[i]
        IN  ev[1] \in {IPC_REQUEST, IPC_REPLY, INT}

SingleRunning ==
    Cardinality({t \in Tasks : task_state[t] = RUNNING}) <= 1

AllInvariants ==
    /\ StateConsistency
    /\ QueueBounded
    /\ EventValidity
    /\ SingleRunning

EventualDispatch ==
    (queue /= << >>) ~> (queue = << >>)

CallReplyPairing ==
    (task_state[TaskA] = BLOCKED) ~> (task_state[TaskA] = READY)

THEOREM Spec => []AllInvariants

THEOREM Spec => EventualDispatch

THEOREM Spec => CallReplyPairing

====
