-------------------------- MODULE ipc_handoff --------------------------
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS
    Tasks,          \* Set of Task IDs
    Messages        \* Set of Message IDs

VARIABLES
    owner,          \* Function: Message -> Task \cup {System, Channel}
    channel_state,  \* Record: [status: {EMPTY, FULL}, data: Message \cup {None}]
    sender_status,  \* Function: Task -> {RUNNING, BLOCKED_SEND}
    receiver_status \* Function: Task -> {RUNNING, BLOCKED_RECV}

SYSTEM == "SYSTEM"
CHANNEL == "CHANNEL"
vars == <<owner, channel_state, sender_status, receiver_status>>

-----------------------------------------------------------------------------

TypeInvariant ==
    /\ owner \in [Messages -> Tasks \cup {SYSTEM, CHANNEL}]
    /\ channel_state.status \in {"EMPTY", "FULL"}
    /\ channel_state.data \in Messages \cup {"None"}
    /\ sender_status \in [Tasks -> {"RUNNING", "BLOCKED_SEND"}]
    /\ receiver_status \in [Tasks -> {"RUNNING", "BLOCKED_RECV"}]

\* Initial State: Give all messages to arbitrary task so it can send
Init ==
    /\ owner = [m \in Messages |-> CHOOSE t \in Tasks : TRUE]
    /\ channel_state = [status |-> "EMPTY", data |-> "None"]
    /\ sender_status = [t \in Tasks |-> "RUNNING"]
    /\ receiver_status = [t \in Tasks |-> "RUNNING"]

-----------------------------------------------------------------------------

\* Action: Task tries to send 'msg'.
Send(t, msg) ==
    /\ owner[msg] = t
    /\ sender_status[t] = "RUNNING"
    /\ IF channel_state.status = "EMPTY" THEN
           /\ owner' = [owner EXCEPT ![msg] = CHANNEL]
           /\ channel_state' = [status |-> "FULL", data |-> msg]
           /\ sender_status' = [sender_status EXCEPT ![t] = "BLOCKED_SEND"]
           /\ UNCHANGED <<receiver_status>>
       ELSE
           \* Block if full? Or retry?
           \* Simplification: Unchanged (Retry loop)
           UNCHANGED vars

\* Action: Task tries to receive (or wakes up from block)
Recv(t) ==
    IF channel_state.status = "FULL" THEN
       \* Channel has data: Receive it!
       /\ receiver_status[t] \in {"RUNNING", "BLOCKED_RECV"}
       /\ LET msg == channel_state.data IN
          /\ owner' = [owner EXCEPT ![msg] = t]
          /\ channel_state' = [status |-> "EMPTY", data |-> "None"]
          /\ sender_status' = [s \in Tasks |-> IF sender_status[s] = "BLOCKED_SEND" THEN "RUNNING" ELSE sender_status[s]]
          /\ receiver_status' = [receiver_status EXCEPT ![t] = "RUNNING"]
    ELSE
       \* Channel Empty: Block if running
       /\ receiver_status[t] = "RUNNING"
       /\ receiver_status' = [receiver_status EXCEPT ![t] = "BLOCKED_RECV"]
       /\ UNCHANGED <<owner, channel_state, sender_status>>

-----------------------------------------------------------------------------

Next ==
    \/ (\E t \in Tasks, m \in Messages : Send(t, m))
    \/ (\E t \in Tasks : Recv(t))

-----------------------------------------------------------------------------

\* Properties
DataConsistency ==
    (channel_state.status = "FULL") => (owner[channel_state.data] = CHANNEL)

\* Single Owner: Implied by Function Type of 'owner'

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

=============================================================================
