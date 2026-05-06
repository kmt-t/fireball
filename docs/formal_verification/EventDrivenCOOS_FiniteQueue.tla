-----------------------------------------------------------------------------
-- EventDrivenCOOS_FiniteQueue.tla
-- 有限キュー + ISR ドロップ戦略を含むモデル
-- ISR がキューに enqueue する際、満杯ならドロップ + ログ出力
-----------------------------------------------------------------------------
---- MODULE EventDrivenCOOS_FiniteQueue ----
EXTENDS Naturals, Sequences, FiniteSets, TLC

CONSTANTS TaskA, TaskB, IPC_REQUEST, IPC_REPLY, INT, IDLE, READY, RUNNING, BLOCKED_CALL
CONSTANTS QUEUE_MAX_SIZE \* キューの最大サイズ（極小環境では小さい値、e.g. 16）

VARIABLES task_state, message_owner, queue, dropped_count

vars == <<task_state, message_owner, queue, dropped_count>>

Tasks == {TaskA, TaskB}
Messages == {1}

Init ==
    /\ task_state = [t \in Tasks |-> READY]
    /\ message_owner = [m \in Messages |-> TaskA]
    /\ queue = << >>
    /\ dropped_count = 0

\* Normal タスク間 IPC: Call
Call(caller, callee, msg) ==
    /\ message_owner[msg] = caller
    /\ task_state[caller] = RUNNING
    /\ task_state' = [task_state EXCEPT ![caller] = BLOCKED_CALL]
    /\ message_owner' = [message_owner EXCEPT ![msg] = callee]
    /\ queue' = Append(queue, <<IPC_REQUEST, callee, caller, msg>>)
    /\ UNCHANGED dropped_count

\* Normal タスク間 IPC: Reply
Reply(callee, caller, msg) ==
    /\ message_owner[msg] = callee
    /\ task_state[caller] = BLOCKED_CALL
    /\ task_state' = [task_state EXCEPT ![caller] = READY]
    /\ message_owner' = [message_owner EXCEPT ![msg] = caller]
    /\ queue' = Append(queue, <<IPC_REPLY, caller, callee, msg>>)
    /\ UNCHANGED dropped_count

\* ISR: Interrupt イベント投入（キューに余裕があれば enqueue、なければドロップ）
Interrupt(t) ==
    IF Len(queue) < QUEUE_MAX_SIZE
    THEN
        \* キューに余裕がある → enqueue 成功
        /\ queue' = Append(queue, <<INT, t, 0, 0>>)
        /\ UNCHANGED <<task_state, message_owner, dropped_count>>
    ELSE
        \* キュー満杯 → ドロップ + ログ（ここでは dropped_count をインクリメント）
        /\ queue' = queue
        /\ dropped_count' = dropped_count + 1
        /\ UNCHANGED <<task_state, message_owner>>

\* Idle イベント（キューが空なら投入）
IdleAction ==
    /\ queue = << >>
    /\ queue' = Append(queue, <<IDLE, TaskA, 0, 0>>)
    /\ UNCHANGED <<task_state, message_owner, dropped_count>>

\* Dispatch: キューの先頭イベントを処理
Dispatch ==
    /\ queue /= << >>
    /\ LET ev == Head(queue)
       IN  /\ queue' = Tail(queue)
           /\ task_state' = [task_state EXCEPT ![ev[2]] = RUNNING]
           /\ UNCHANGED <<message_owner, dropped_count>>

Next ==
    \/ \E t \in Tasks : Interrupt(t)
    \/ IdleAction
    \/ \E m \in Messages : Call(TaskA, TaskB, m)
    \/ \E m \in Messages : Reply(TaskB, TaskA, m)
    \/ Dispatch

Spec == Init /\ [][Next]_vars

\* ========== 不変条件 ==========

\* 所有権不変条件: メッセージの所有者は常に一意
OwnershipInvariant ==
    \A m \in Messages :
        \E t1 \in Tasks : (message_owner[m] = t1) /\ \A t2 \in Tasks : (message_owner[m] = t2) => t1 = t2

\* 状態一貫性: タスク状態は有効な値のみ
StateConsistency ==
    \A t \in Tasks : task_state[t] \in {READY, RUNNING, BLOCKED_CALL}

\* キュー有限性: キューサイズが上限を超えない
QueueBounded ==
    Len(queue) <= QUEUE_MAX_SIZE

\* キューの型安全性: キュー内のすべてのイベントが有効な構造
EventValidity ==
    \A i \in 1..Len(queue) :
        LET ev == queue[i]
        IN  /\ ev[1] \in {IPC_REQUEST, IPC_REPLY, INT, IDLE}
            /\ ev[2] \in Tasks

\* ドロップ統計の単調性: dropped_count は増加し続ける（リセットしない）
DroppedCountMonotonic ==
    \A i, j : (i < j) => dropped_count <= dropped_count'

\* 仕様: すべての不変条件が成立
AllInvariants ==
    /\ OwnershipInvariant
    /\ StateConsistency
    /\ QueueBounded
    /\ EventValidity

\* ========== 活性特性（Liveness） ==========

\* デッドロック回避: キューが空でない限り、Dispatch は必ず発火する機会がある
EventualDispatch ==
    \A<<qs, ms, ts, dc>> \in Seq(<<queue, message_owner, task_state, dropped_count>>) :
        (qs /= << >>) ~> (\E ev \in 1..Len(qs) : TRUE)  \* イベントが消費される

\* Reply 完了: 任意の Call 後、最終的に Reply が発生する
\* （Call-Reply ペアが必ず完結する）
CallReplyPairing ==
    \A t1, t2 \in Tasks, m \in Messages :
        (message_owner[m] = t2) /\ (task_state[t1] = BLOCKED_CALL)
        ~> (message_owner[m] = t1) /\ (task_state[t1] = READY)

\* ========== 検証対象 ==========

\* TLC で検証すべき仕様
THEOREM Spec => AllInvariants

=============================================================================
