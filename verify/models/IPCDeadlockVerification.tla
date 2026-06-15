---- MODULE IPCDeadlockVerification ----
EXTENDS Integers, Sequences, FiniteSets


CONSTANT MAX_QUEUE_SIZE, NUM_TASKS, NUM_SERVICES, NUM_MESSAGES
ASSUME MAX_QUEUE_SIZE = 4
ASSUME NUM_TASKS = 4
ASSUME NUM_SERVICES = 2
ASSUME NUM_MESSAGES = 3


CONSTANT IDLE, SENDING, REVOKING, ENQUEUING, ENQUEUED, ROLLING_BACK, GRANTED, KILLED
CONSTANT VALID, IN_FLIGHT, REVOKED


VARIABLES
    tasks,           
    messages,        
    queues,          
    ownership,       
    registry,        
    dropped,         
    panicked         

vars == <<tasks, messages, ownership, queues, registry, dropped, panicked>>


Init ==
    /\ tasks = [t \in 0..NUM_TASKS-1 |-> [state |-> IDLE, owned_msg |-> 0]]
    /\ messages = [m \in 0..NUM_MESSAGES-1 |-> [owner |-> 0, state |-> VALID, recipient |-> 0]]
    /\ ownership = [m \in 0..NUM_MESSAGES-1 |-> [state |-> VALID, holder |-> 0]]
    /\ queues = [s \in 0..NUM_SERVICES-1 |-> <<>>]
    /\ registry = [s \in 0..NUM_SERVICES-1 |-> [state |-> IDLE, channel |-> 0]]
    /\ dropped = {}
    /\ panicked = FALSE


Revoke(sender, msg, recipient) ==
    LET msg_id == msg
        owner_before == ownership[msg_id].holder
    IN /\ sender = owner_before  
       /\ ownership[msg_id].state = VALID  
       /\ ownership' = [ownership EXCEPT ![msg_id].state = IN_FLIGHT]  
       /\ tasks' = [tasks EXCEPT ![sender].state = REVOKING, ![sender].owned_msg = 0]
       /\ UNCHANGED <<messages, queues, registry, dropped, panicked>>


Enqueue(sender, msg, service) ==
    LET msg_id == msg
        queue == queues[service]
    IN /\ Len(queue) < MAX_QUEUE_SIZE  
       /\ ownership[msg_id].state = IN_FLIGHT  
       /\ Len(queue) < MAX_QUEUE_SIZE
       /\ queues' = [queues EXCEPT ![service] = Append(queue, msg)]
       /\ messages' = [messages EXCEPT ![msg_id].state = ENQUEUED]
       /\ tasks' = [tasks EXCEPT ![sender].state = ENQUEUING]
       /\ UNCHANGED <<ownership, registry, dropped, panicked>>



Rollback(sender, msg, service) ==
    LET msg_id == msg
        queue == queues[service]
    IN /\ Len(queue) >= MAX_QUEUE_SIZE  
       /\ ownership[msg_id].state = IN_FLIGHT
       /\ ownership' = [ownership EXCEPT ![msg_id].state = VALID, ![msg_id].holder = sender]
       /\ messages' = [messages EXCEPT ![msg_id].state = VALID, ![msg_id].owner = sender]
       /\ tasks' = [tasks EXCEPT ![sender].state = ROLLING_BACK]
       /\ UNCHANGED <<queues, registry, dropped, panicked>>


Grant(service, receiver) ==
    LET queue == queues[service]
    IN /\ Len(queue) > 0  
       /\ LET msg_id == Head(queue)
          IN /\ ownership[msg_id].state = IN_FLIGHT
             /\ ownership' = [ownership EXCEPT ![msg_id].state = VALID, ![msg_id].holder = receiver]
             /\ messages' = [messages EXCEPT ![msg_id].owner = receiver]
             /\ queues' = [queues EXCEPT ![service] = Tail(queue)]
             /\ tasks' = [tasks EXCEPT ![receiver].state = GRANTED]
       /\ UNCHANGED <<registry, dropped, panicked>>



DropHandler(service) ==
    LET queue == queues[service]
    IN /\ Len(queue) > 0
       /\ \E msg_id \in DOMAIN queue:
            LET msg == queue[msg_id]
            IN /\ ownership[msg].state = IN_FLIGHT  
               /\ dropped' = dropped \cup {msg}  
               /\ ownership' = [ownership EXCEPT ![msg].state = REVOKED]  
               /\ queues' = [queues EXCEPT ![service] = <<>>]  
       /\ UNCHANGED <<tasks, messages, registry, panicked>>


TaskKill(task_id) ==
    /\ tasks[task_id].state # IDLE
    /\ tasks' = [tasks EXCEPT ![task_id].state = KILLED]
    /\ UNCHANGED <<messages, ownership, queues, registry, dropped, panicked>>


Next ==
    \/ \E sender \in 0..NUM_TASKS-1:
        \E msg \in 0..NUM_MESSAGES-1:
        \E recipient \in 0..NUM_SERVICES-1:
            Revoke(sender, msg, recipient)
    \/ \E sender \in 0..NUM_TASKS-1:
        \E msg \in 0..NUM_MESSAGES-1:
        \E service \in 0..NUM_SERVICES-1:
            Enqueue(sender, msg, service)
    \/ \E sender \in 0..NUM_TASKS-1:
        \E msg \in 0..NUM_MESSAGES-1:
        \E service \in 0..NUM_SERVICES-1:
            Rollback(sender, msg, service)
    \/ \E service \in 0..NUM_SERVICES-1:
        \E receiver \in 0..NUM_TASKS-1:
            Grant(service, receiver)
    \/ \E service \in 0..NUM_SERVICES-1:
        DropHandler(service)
    \/ \E task_id \in 0..NUM_TASKS-1:
        TaskKill(task_id)




OwnershipConsistency ==
    \A msg_id \in 0..NUM_MESSAGES-1:
        (messages[msg_id].owner = ownership[msg_id].holder) \/
        (ownership[msg_id].state = IN_FLIGHT)


InFlightSafety ==
    \A msg_id \in 0..NUM_MESSAGES-1:
        (ownership[msg_id].state = IN_FLIGHT) =>
            (messages[msg_id].state = ENQUEUED) \/ (messages[msg_id].state = REVOKED)


QueueOwnershipConsistency ==
    \A service \in 0..NUM_SERVICES-1:
        \A i \in 1..Len(queues[service]):
            LET msg_id == queues[service][i]
            IN ownership[msg_id].state = IN_FLIGHT


NoMemoryLeak ==
    \A msg_id \in 0..NUM_MESSAGES-1:
        \/ ownership[msg_id].state = VALID
        \/ ownership[msg_id].state = IN_FLIGHT
        \/ ownership[msg_id].state = REVOKED
        \/ (msg_id \in dropped)


RevokeGuard ==
    \A msg_id \in 0..NUM_MESSAGES-1:
        (ownership[msg_id].state = IN_FLIGHT) =>
            ~(messages[msg_id].owner = 0)  



NoDeadlock ==
    \A service \in 0..NUM_SERVICES-1:
        (Len(queues[service]) >= MAX_QUEUE_SIZE) =>
            (\E msg_id \in DOMAIN messages:
                (messages[msg_id].state = VALID /\ ownership[msg_id].state = VALID))



DropHandlerEffectiveness ==
    \A msg_id \in dropped:
        ownership[msg_id].state = REVOKED

Invariants ==
    /\ OwnershipConsistency
    /\ InFlightSafety
    /\ QueueOwnershipConsistency
    /\ NoMemoryLeak
    /\ RevokeGuard
    /\ NoDeadlock
    /\ DropHandlerEffectiveness

Spec == Init /\ [][Next]_vars




EventuallyGranted ==
    \A msg_id \in 0..NUM_MESSAGES-1:
        (messages[msg_id].state = ENQUEUED) ~> (ownership[msg_id].holder # 0)


NoUnrecoverableError ==
    ~(panicked = TRUE)

====
