-------------------------- MODULE ipc_handoff --------------------------
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS
    Tasks,          \* Set of Task IDs
    Messages        \* Set of Message IDs

VARIABLES
    owner,          \* Function: Message -> Task \cup {System, Channel}
    channel_state,  \* Record: [status: {EMPTY, FULL}, data: Message \cup {None}]
    task_status     \* Function: Task -> {RUNNING, BLOCKED_SEND, BLOCKED_RECV}

SYSTEM == "SYSTEM"
CHANNEL == "CHANNEL"
vars == <<owner, channel_state, task_status>>

-----------------------------------------------------------------------------

TypeInvariant ==
    /\ owner \in [Messages -> Tasks \cup {SYSTEM, CHANNEL}]
    /\ channel_state.status \in {"EMPTY", "FULL"}
    /\ channel_state.data \in Messages \cup {"None"}
    /\ task_status \in [Tasks -> {"RUNNING", "BLOCKED_SEND", "BLOCKED_RECV"}]

Init ==
    /\ owner = [m \in Messages |-> CHOOSE t \in Tasks : TRUE]
    /\ channel_state = [status |-> "EMPTY", data |-> "None"]
    /\ task_status = [t \in Tasks |-> "RUNNING"]

-----------------------------------------------------------------------------

\* Action: Task tries to send 'msg'.
Send(t, msg) ==
    /\ owner[msg] = t
    /\ task_status[t] = "RUNNING"
    /\ IF channel_state.status = "EMPTY" THEN
           /\ IF \E r \in Tasks : task_status[r] = "BLOCKED_RECV" THEN
                 \* Case 3: Handoff (Direct to ready)
                 /\ LET r == CHOOSE r \in Tasks : task_status[r] = "BLOCKED_RECV" IN
                    /\ owner' = [owner EXCEPT ![msg] = r]
                    /\ task_status' = [task_status EXCEPT ![t] = "RUNNING", ![r] = "RUNNING"]
                    /\ UNCHANGED <<channel_state>>
              ELSE
                 \* Case 1: Send to Empty (Block)
                 /\ owner' = [owner EXCEPT ![msg] = CHANNEL]
                 /\ channel_state' = [status |-> "FULL", data |-> msg]
                 /\ task_status' = [task_status EXCEPT ![t] = "BLOCKED_SEND"]
       ELSE
           \* Case 2: Send to Full (Wait/Retry loop - modeled as blocking here)
           /\ task_status' = [task_status EXCEPT ![t] = "BLOCKED_SEND"]
           /\ UNCHANGED <<owner, channel_state>>

\* Action: Task tries to receive
Recv(t) ==
    /\ task_status[t] = "RUNNING"
    /\ IF channel_state.status = "FULL" THEN
           \* Case 4: Recv from Full
           /\ LET msg == channel_state.data IN
              /\ owner' = [owner EXCEPT ![msg] = t]
              /\ channel_state' = [status |-> "EMPTY", data |-> "None"]
              /\ IF \E s \in Tasks : task_status[s] = "BLOCKED_SEND" THEN
                    \* Wake up one pending sender (Simplification)
                    /\ LET s == CHOOSE s \in Tasks : task_status[s] = "BLOCKED_SEND" IN
                       /\ task_status' = [task_status EXCEPT ![t] = "RUNNING", ![s] = "RUNNING"]
                 ELSE
                    /\ task_status' = [task_status EXCEPT ![t] = "RUNNING"]
       ELSE
           \* Case 5: Recv from Empty (Block)
           /\ IF \E s \in Tasks : task_status[s] = "BLOCKED_SEND" THEN
                 \* Case 6: Handoff (Direct from sender)
                 /\ LET s == CHOOSE s \in Tasks : task_status[s] = "BLOCKED_SEND" IN
                    /\ \E msg \in Messages : 
                       /\ owner[msg] = s
                       /\ owner' = [owner EXCEPT ![msg] = t]
                       /\ task_status' = [task_status EXCEPT ![t] = "RUNNING", ![s] = "RUNNING"]
                       /\ UNCHANGED <<channel_state>>
              ELSE
                 /\ task_status' = [task_status EXCEPT ![t] = "BLOCKED_RECV"]
                 /\ UNCHANGED <<owner, channel_state>>

-----------------------------------------------------------------------------

Next ==
    \/ (\E t \in Tasks, m \in Messages : Send(t, m))
    \/ (\E t \in Tasks : Recv(t))

-----------------------------------------------------------------------------

\* Properties
DataConsistency ==
    (channel_state.status = "FULL") => (owner[channel_state.data] = CHANNEL)

\* Safety: Only one owner per message
SingleOwner == \A m \in Messages : owner[m] \in Tasks \cup {SYSTEM, CHANNEL}

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

=============================================================================
