--------------------------------------------------------------------------------
-- IPC デッドロック・パニック回避の形式検証
-- 所有権移譲ロジック、Rollback、Drop Handler の安全性を検証
-- {OwnershipTransfer} {IPC_ZeroCopy} {Challenge_CspHandoffStarvation} {IPC_DropHandler}
--------------------------------------------------------------------------------

---- MODULE IPCDeadlockVerification ----
EXTENDS Integers, Sequences, FiniteSets

-- 定数
CONSTANT MAX_QUEUE_SIZE, NUM_TASKS, NUM_SERVICES, NUM_MESSAGES
ASSUME MAX_QUEUE_SIZE = 4
ASSUME NUM_TASKS = 4
ASSUME NUM_SERVICES = 2
ASSUME NUM_MESSAGES = 3

-- タスクと所有権の状態
CONSTANT IDLE, SENDING, REVOKING, ENQUEUING, ENQUEUED, ROLLING_BACK, GRANTED, KILLED
CONSTANT VALID, IN_FLIGHT, REVOKED

-- 変数
VARIABLES
    tasks,           -- Task状態 [task_id] -> {state, owned_msg}
    messages,        -- Message管理 [msg_id] -> {owner, state, recipient}
    queues,          -- 受信キュー [service_id] -> <<msg, msg, ...>>
    ownership,       -- 所有権テーブル [msg_id] -> {state, holder}
    registry,        -- サービスレジストリ [service_id] -> {state, channel}
    dropped,         -- Drop されたメッセージ
    panicked         -- パニック状態フラグ

vars == <<tasks, messages, ownership, queues, registry, dropped, panicked>>

-- 初期状態
Init ==
    /\ tasks = [t \in 0..NUM_TASKS-1 |-> [state |-> IDLE, owned_msg |-> 0]]
    /\ messages = [m \in 0..NUM_MESSAGES-1 |-> [owner |-> 0, state |-> VALID, recipient |-> 0]]
    /\ ownership = [m \in 0..NUM_MESSAGES-1 |-> [state |-> VALID, holder |-> 0]]
    /\ queues = [s \in 0..NUM_SERVICES-1 |-> <<>>]
    /\ registry = [s \in 0..NUM_SERVICES-1 |-> [state |-> IDLE, channel |-> 0]]
    /\ dropped = {}
    /\ panicked = FALSE

-- ========== Phase 1: Revoke（送信側の権限無効化）==========
Revoke(sender, msg, recipient) ==
    LET msg_id == msg
        owner_before == ownership[msg_id].holder
    IN /\ sender = owner_before  -- 送信側が所有者であることを確認
       /\ ownership[msg_id].state = VALID  -- 所有権が有効
       /\ ownership' = [ownership EXCEPT ![msg_id].state = IN_FLIGHT]  -- In-flight に遷移
       /\ tasks' = [tasks EXCEPT ![sender].state = REVOKING, ![sender].owned_msg = 0]
       /\ UNCHANGED <<messages, queues, registry, dropped, panicked>>

-- ========== Phase 2a: Enqueue（キューイング）==========
Enqueue(sender, msg, service) ==
    LET msg_id == msg
        queue == queues[service]
    IN /\ Len(queue) < MAX_QUEUE_SIZE  -- キューに空きあり
       /\ ownership[msg_id].state = IN_FLIGHT  -- In-flight 状態
       /\ Len(queue) < MAX_QUEUE_SIZE
       /\ queues' = [queues EXCEPT ![service] = Append(queue, msg)]
       /\ messages' = [messages EXCEPT ![msg_id].state = ENQUEUED]
       /\ tasks' = [tasks EXCEPT ![sender].state = ENQUEUING]
       /\ UNCHANGED <<ownership, registry, dropped, panicked>>

-- ========== Phase 2b: Rollback（失敗時の巻き戻し）==========
-- キュー満杯時に所有権を返却
Rollback(sender, msg, service) ==
    LET msg_id == msg
        queue == queues[service]
    IN /\ Len(queue) >= MAX_QUEUE_SIZE  -- キューが満杯
       /\ ownership[msg_id].state = IN_FLIGHT
       /\ ownership' = [ownership EXCEPT ![msg_id].state = VALID, ![msg_id].holder = sender]
       /\ messages' = [messages EXCEPT ![msg_id].state = VALID, ![msg_id].owner = sender]
       /\ tasks' = [tasks EXCEPT ![sender].state = ROLLING_BACK]
       /\ UNCHANGED <<queues, registry, dropped, panicked>>

-- ========== Phase 3: Grant（受信側への権限付与）==========
Grant(service, receiver) ==
    LET queue == queues[service]
    IN /\ Len(queue) > 0  -- キューにメッセージあり
       /\ LET msg_id == Head(queue)
          IN /\ ownership[msg_id].state = IN_FLIGHT
             /\ ownership' = [ownership EXCEPT ![msg_id].state = VALID, ![msg_id].holder = receiver]
             /\ messages' = [messages EXCEPT ![msg_id].owner = receiver]
             /\ queues' = [queues EXCEPT ![service] = Tail(queue)]
             /\ tasks' = [tasks EXCEPT ![receiver].state = GRANTED]
       /\ UNCHANGED <<registry, dropped, panicked>>

-- ========== Drop Handler（リソース回収）==========
-- 送信先がKillされた場合、キュー内のメッセージをDropハンドラで回収
DropHandler(service) ==
    LET queue == queues[service]
    IN /\ Len(queue) > 0
       /\ \E msg_id \in DOMAIN queue:
            LET msg == queue[msg_id]
            IN /\ ownership[msg].state = IN_FLIGHT  -- In-flight 中に Kill
               /\ dropped' = dropped \cup {msg}  -- Drop記録
               /\ ownership' = [ownership EXCEPT ![msg].state = REVOKED]  -- 回収完了
               /\ queues' = [queues EXCEPT ![service] = <<>>]  -- キュークリア
       /\ UNCHANGED <<tasks, messages, registry, panicked>>

-- ========== Task Kill（異常終了）==========
TaskKill(task_id) ==
    /\ tasks[task_id].state # IDLE
    /\ tasks' = [tasks EXCEPT ![task_id].state = KILLED]
    /\ UNCHANGED <<messages, ownership, queues, registry, dropped, panicked>>

-- ========== Transition ==========
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

-- ========== 不変条件 ==========

-- 不変条件1: 所有権の一貫性 {OwnershipTransfer}
OwnershipConsistency ==
    \A msg_id \in 0..NUM_MESSAGES-1:
        (messages[msg_id].owner = ownership[msg_id].holder) \/
        (ownership[msg_id].state = IN_FLIGHT)

-- 不変条件2: In-flight メッセージの安全性
InFlightSafety ==
    \A msg_id \in 0..NUM_MESSAGES-1:
        (ownership[msg_id].state = IN_FLIGHT) =>
            (messages[msg_id].state = ENQUEUED) \/ (messages[msg_id].state = REVOKED)

-- 不変条件3: キュー内メッセージの所有権一貫性
QueueOwnershipConsistency ==
    \A service \in 0..NUM_SERVICES-1:
        \A i \in 1..Len(queues[service]):
            LET msg_id == queues[service][i]
            IN ownership[msg_id].state = IN_FLIGHT

-- 不変条件4: Drop Handler によるメモリリーク防止
NoMemoryLeak ==
    \A msg_id \in 0..NUM_MESSAGES-1:
        \/ ownership[msg_id].state = VALID
        \/ ownership[msg_id].state = IN_FLIGHT
        \/ ownership[msg_id].state = REVOKED
        \/ (msg_id \in dropped)

-- 不変条件5: Revoke 後は Grant までの間、送信側は権限なし
RevokeGuard ==
    \A msg_id \in 0..NUM_MESSAGES-1:
        (ownership[msg_id].state = IN_FLIGHT) =>
            ~(messages[msg_id].owner = 0)  -- 送信側（task 0 と仮定）は権限なし

-- 不変条件6: デッドロック不在 {Challenge_CspHandoffStarvation}
-- キューが満杯でも Rollback で確実に返却されること
NoDeadlock ==
    \A service \in 0..NUM_SERVICES-1:
        (Len(queues[service]) >= MAX_QUEUE_SIZE) =>
            (\E msg_id \in DOMAIN messages:
                (messages[msg_id].state = VALID /\ ownership[msg_id].state = VALID))

-- 不変条件7: Drop Handler の有効性 {IPC_DropHandler}
-- Kill されたメッセージは Dropped に追加される
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

-- ========== LTL 特性 ==========

-- Liveness: Enqueue されたメッセージは最終的に Grant される
EventuallyGranted ==
    \A msg_id \in 0..NUM_MESSAGES-1:
        (messages[msg_id].state = ENQUEUED) ~> (ownership[msg_id].holder # 0)

-- Safety: パニック状態に到達しない
NoUnrecoverableError ==
    ~(panicked = TRUE)

================================================================================
