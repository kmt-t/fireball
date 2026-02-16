-------------------------------- MODULE coos --------------------------------
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS
    MaxTasks,       \* Maximum number of supported tasks
    TaskIds         \* Set of possible Task IDs (e.g., 1..MaxTasks)

VARIABLES
    active_tasks,   \* Set of currently active task IDs
    ready_queue,    \* Sequence of task IDs waiting to run
    current_task,   \* ID of the currently running task (or 0 if idle)
    task_status     \* Function mapping TaskId -> status

\* Task States
STATUS_FREE == "free"
STATUS_READY == "ready"
STATUS_RUNNING == "running"
STATUS_BLOCKED == "blocked"
STATUS_TERMINATED == "terminated"

vars == <<active_tasks, ready_queue, current_task, task_status>>

-----------------------------------------------------------------------------

\* Type Invariant
TypeInvariant ==
    /\ active_tasks \subseteq TaskIds
    /\ current_task \in (active_tasks \cup {0})
    /\ task_status \in [TaskIds -> {STATUS_FREE, STATUS_READY, STATUS_RUNNING, STATUS_BLOCKED, STATUS_TERMINATED}]
    /\ Len(ready_queue) <= MaxTasks

\* Coherence Invariant
Coherence ==
    /\ \A t \in active_tasks : task_status[t] \in {STATUS_READY, STATUS_RUNNING, STATUS_BLOCKED}
    /\ current_task /= 0 => task_status[current_task] = STATUS_RUNNING
    /\ \A t \in TaskIds \ active_tasks : task_status[t] \in {STATUS_FREE, STATUS_TERMINATED}

-----------------------------------------------------------------------------

Init ==
    /\ active_tasks = {}
    /\ ready_queue = <<>>
    /\ current_task = 0
    /\ task_status = [t \in TaskIds |-> STATUS_FREE]

-----------------------------------------------------------------------------

\* Action: Spawn a new task
Spawn(t) ==
    /\ t \notin active_tasks
    /\ task_status[t] \in {STATUS_FREE, STATUS_TERMINATED}
    /\ Cardinality(active_tasks) < MaxTasks
    /\ active_tasks' = active_tasks \cup {t}
    /\ task_status' = [task_status EXCEPT ![t] = STATUS_READY]
    /\ ready_queue' = ready_queue \o <<t>>
    /\ UNCHANGED <<current_task>>

\* Action: Scheduler picks a task from ready queue (Run)
Schedule ==
    /\ current_task = 0
    /\ ready_queue /= <<>>
    /\ LET next_task == Head(ready_queue) IN
       /\ current_task' = next_task
       /\ ready_queue' = Tail(ready_queue)
       /\ task_status' = [task_status EXCEPT ![next_task] = STATUS_RUNNING]
       /\ UNCHANGED <<active_tasks>>

\* Action: Current task Yields
Yield ==
    /\ current_task /= 0
    /\ task_status' = [task_status EXCEPT ![current_task] = STATUS_READY]
    /\ ready_queue' = ready_queue \o <<current_task>>
    /\ current_task' = 0
    /\ UNCHANGED <<active_tasks>>

\* Action: Terminate current task
Terminate ==
    /\ current_task /= 0
    /\ active_tasks' = active_tasks \ {current_task}
    /\ task_status' = [task_status EXCEPT ![current_task] = STATUS_TERMINATED]
    /\ current_task' = 0
    /\ UNCHANGED <<ready_queue>>

-----------------------------------------------------------------------------

Next ==
    \/ (\E t \in TaskIds : Spawn(t))
    \/ Schedule
    \/ Yield
    \/ Terminate

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

-----------------------------------------------------------------------------

\* Properties to verify
Liveness == \A t \in TaskIds : (t \in active_tasks /\ task_status[t] = STATUS_READY) ~> (task_status[t] = STATUS_RUNNING)

=============================================================================
