--------------------------- MODULE COOS_EventDriven ---------------------------
EXTENDS Sequences, Integers, FiniteSets

(***************************************************************************)
(* Constants                                                               *)
(***************************************************************************)
CONSTANTS
    \* @type: Set(Str);
    Tasks,          \* Set of tasks
    \* @type: Int;
    MaxQueueSize,   \* Max size of event queue
    \* @type: Set(Str);
    Messages        \* Set of message IDs

VARIABLES
    \* @type: Str -> Str;
    taskState,      \* State of each task: "IDLE", "READY", "BLOCKED"
    \* @type: Seq([type: Str, target: Str, msg: Str, sender: Str]);
    eventQueue,     \* Sequence of events
    \* @type: Str -> Str;
    ownership,      \* Message owner: TaskID or "IN_FLIGHT" or "NONE"
    \* @type: Str -> Str;
    waitingFor      \* Message the task is waiting for

Vars == <<taskState, eventQueue, ownership, waitingFor>>

(***************************************************************************)
(* Type Definitions and Initial State                                      *)
(***************************************************************************)
TypeOK ==
    /\ taskState \in [Tasks -> {"IDLE", "READY", "BLOCKED"}]
    /\ ownership \in [Messages -> Tasks \cup {"IN_FLIGHT", "NONE"}]
    /\ waitingFor \in [Tasks -> Messages \cup {"NONE"}]

Init ==
    /\ taskState = [t \in Tasks |-> "IDLE"]
    /\ eventQueue = <<>>
    /\ ownership = [m \in Messages |-> "t1"]
    /\ waitingFor = [t \in Tasks |-> "NONE"]

(***************************************************************************)
(* Actions                                                                 *)
(***************************************************************************)

\* Task sends a message (IPC_SEND)
Send(t, target, m) ==
    /\ taskState[t] \in {"IDLE", "READY"}
    /\ ownership[m] = t
    /\ Len(eventQueue) < MaxQueueSize
    /\ eventQueue' = Append(eventQueue, [type |-> "IPC_SEND", target |-> target, msg |-> m, sender |-> t])
    /\ taskState' = [taskState EXCEPT ![t] = "BLOCKED"]
    /\ ownership' = [ownership EXCEPT ![m] = "IN_FLIGHT"]
    /\ waitingFor' = [waitingFor EXCEPT ![t] = m]

\* Event loop dispatches an event
Dispatch ==
    /\ eventQueue /= <<>>
    /\ LET ev == Head(eventQueue)
       IN /\ eventQueue' = Tail(eventQueue)
          /\ CASE ev.type = "IPC_SEND" ->
                  /\ taskState' = [taskState EXCEPT ![ev.target] = "READY"]
                  /\ ownership' = [ownership EXCEPT ![ev.msg] = ev.target]
                  /\ UNCHANGED waitingFor
               [] ev.type = "IPC_REPLY" ->
                  /\ taskState' = [taskState EXCEPT ![ev.target] = "READY"]
                  /\ ownership' = [ownership EXCEPT ![ev.msg] = ev.target]
                  /\ waitingFor' = [waitingFor EXCEPT ![ev.target] = "NONE"]
               [] ev.type = "INT" ->
                  /\ taskState' = [taskState EXCEPT ![ev.target] = "READY"]
                  /\ UNCHANGED <<ownership, waitingFor>>

\* Task returns a reply (IPC_REPLY)
Reply(t, m) ==
    /\ taskState[t] = "READY"
    /\ ownership[m] = t
    /\ \E sender \in Tasks :
        /\ waitingFor[sender] = m
        /\ Len(eventQueue) < MaxQueueSize
        /\ eventQueue' = Append(eventQueue, [type |-> "IPC_REPLY", target |-> sender, msg |-> m, sender |-> t])
        /\ ownership' = [ownership EXCEPT ![m] = "IN_FLIGHT"]
        /\ UNCHANGED <<taskState, waitingFor>>

\* Interrupt occurs
Interrupt(t) ==
    /\ Len(eventQueue) < MaxQueueSize
    /\ eventQueue' = Append(eventQueue, [type |-> "INT", target |-> t, msg |-> "NONE", sender |-> "NONE"])
    /\ UNCHANGED <<taskState, ownership, waitingFor>>

Next ==
    \/ \E t, target \in Tasks, m \in Messages : Send(t, target, m)
    \/ Dispatch
    \/ \E t \in Tasks, m \in Messages : Reply(t, m)
    \/ \E t \in Tasks : Interrupt(t)

Spec == Init /\ [][Next]_Vars

(***************************************************************************)
(* Invariants                                                              *)
(***************************************************************************)

\* Guarantee that a message always has a unique owner
Safety_SingleOwner ==
    \forall m \in Messages :
        ownership[m] /= "NONE" =>
            \/ \E t \in Tasks : ownership[m] = t
            \/ ownership[m] = "IN_FLIGHT"

=============================================================================
