# [REVISED] イベント駆動型OS COOS コンポーネント設計書

本ドキュメントは、非プリエンプティブなイベント駆動型COOSの設計を定義する。COOSはイベントループを用いてタスクをdispatchする。同期IPC（RPC-like procedure call）は、イベントループを介したサスペンド/レジュームとして実装される。 `{CooperativeMultitasking}` `{CSPCommunication}` `{COOS_Deterministic}`

## 1. コンセプト

- **Run-to-Completion**: 各dispatch単位は中断されることなく最後まで実行される。サスペンドが生じる場合、タスクは状態をTCBに保存し、制御をイベントループへ戻す。
- **明示的なイベントループ**: スケジューラは `EventQueue` から `Event` を取り出し、対象タスクを `Dispatch` するループとして機能する。
- **同期IPC / RPC-like call**: `call` / `reply` はイベントを介した非同期メッセージパッシングとして実装されるが、公開APIとしては同期RPCとして提供される。
- **所有権移譲**: `call` はrequest messageの所有権をcalleeへ、`reply` はresponse messageの所有権をcallerへ移譲する。 `{OwnershipTransfer}`
- **有限キュー制約**: EventQueue は固定長バッファで管理される。極小RAM環境（32KB～64KB）での動作保証が必須。 `{Policy_Memory}`

## 2. 静的モデル

### 2.1 主要データ構造
- **`Event`**: ディスパッチ単位。`type`, `target_task`, `caller_task`, `msg_handle` を含む。
- **`TaskControlBlock (TCB)`**: `state`, `context` (継続状態), `handler` を保持。
- **`MessagePool`**: 固定長プール。`message_handle` で管理。所有権不変条件の唯一の管理者。

## 3. 動的モデル

### 3.1 IPC シーケンス (Call/Reply)
各ステップがイベントを通じたアトミックな状態遷移として定義される。

#### 正常系（キューに余裕がある場合）

1. **Call(A -> B)**: 
   - Aの `call(uri, request)` 呼び出しにより、Requestの所有権がkernel経由でBへ移譲される。
   - Aのステートを `BLOCKED_CALL` へ移行し、Aの継続状態（コルーチンフレーム）を保存。
   - `(IPC_REQUEST, B, A, msg)` イベントをキューへ投入。
   - **前提条件**: `Len(EventQueue) < QUEUE_MAX_SIZE`
   
2. **Dispatch B**:
   - イベントループが上記Eventを取り出し、BへRequestの所有権を付与してBを `RUNNING` で呼び出す。
   - キューから `IPC_REQUEST` イベントが削除される。
   
3. **Reply(B -> A)**:
   - Bの `reply(response)` 呼び出しにより、Responseの所有権がkernel経由でAへ移譲される。
   - `(IPC_REPLY, A, B, msg)` イベントをキューへ投入。
   - **前提条件**: `Len(EventQueue) < QUEUE_MAX_SIZE`
   
4. **Resume A**:
   - イベントループが上記Eventを取り出し、Aの保存済み継続を再開。
   - Aのステートを `READY` へ戻す。
   - Aにとっては `call` が完了したように見える。

#### 異常系（キュー満杯時の動作）

**シナリオ**: Reply をキューイングしようとした時点でキューが満杯。

```
Call(A -> B) ─→ A: BLOCKED_CALL, Event enqueue成功
         ↓
Dispatch B ─→ B: RUNNING, event dequeue
         ↓
Reply(B -> A) ─→ B がreply() を呼び出す
         ↓
EventQueue 満杯 ─→ Reply イベント投入失敗
         ↓
[対応] B: BLOCKED_REPLY 状態に遷移（A同様に待機）
```

**処理フロー**:

```tla
Reply(callee, caller, msg) ==
    IF Len(queue) < QUEUE_MAX_SIZE
    THEN
        \* 正常: Reply イベント投入成功
        /\ message_owner' = [message_owner EXCEPT ![msg] = caller]
        /\ task_state' = [task_state EXCEPT ![caller] = READY]
        /\ queue' = Append(queue, <<IPC_REPLY, caller, callee, msg>>)
    ELSE
        \* 異常: キュー満杯 → callee も待機
        /\ task_state' = [task_state EXCEPT ![callee] = BLOCKED_REPLY]
        /\ queue' = queue
        /\ UNCHANGED message_owner
```

**この状況では、キューが消費されるまで caller/callee 両方が待機。** キューの消費率（Dispatch 頻度）が Reply 投入頻度を上回る必要がある。 `{LowLatencyJIT}` により Dispatch の実行時間を最小化して対応。

### 3.2 割り込みハンドラと ISR イベント投入

ISR がイベントをキューイングする際、キューが満杯なら **ドロップ + ログ出力** で対応する。

#### ISR イベント投入の流れ

```
ISR発火 ─→ Interrupt イベント生成 ─→ EventQueue に enqueue
                        ↓
                (キュー満杯？)
                   /    \
                 YES     NO
                  |       |
              [DROP]  [ENQUEUE]
                  |       |
              log++  →EventLoop
                  |       |
              ISR終了   (続行)
```

**実装コード（概念）**:

```cpp
void ISR_Handler() {
  Event event = {INT, target_task_id, 0, 0};
  if (!queue.enqueue(event)) {
    atomic_increment(&dropped_count);  // ログは別タスクで出力
    // ISR は正常にリターン（ブロックしない）
  }
}
```

**制約**: ISR はドロップ判定と counter increment のみを行う。ログ出力は ISR 外で実施。 `{COOS_Deterministic}`

### 3.3 ISR 再検出メカニズム（割り込み喪失対策）

ドロップされた割り込みイベントに対応するタスクが起動しないリスク対策。

#### アイドル時の割り込みステータス確認

```
EventQueue が空 ─→ IdleAction 実行
                ↓
        割り込みステータス確認（ハードウェアレジスタまたは pending フラグ）
                ↓
    未処理割り込みがあれば、再度 Interrupt イベント投入
                ↓
        キューに空きがあれば enqueue、満杯なら再度ドロップ
```

**TLA+ モデル**:

```tla
IdleAction_WithInterruptPoll ==
    /\ queue = << >>
    /\ pending_interrupt_status /= {}  \* 未処理割り込みあり
    /\ \E t \in pending_interrupt_status :
           IF Len(queue) < QUEUE_MAX_SIZE
           THEN queue' = Append(queue, <<INT, t, 0, 0>>)
           ELSE dropped_count' = dropped_count + 1
    /\ queue' = Append(queue, <<IDLE, TaskA, 0, 0>>)
```

**効果**: 
- ドロップされた割り込みが、アイドル時に検出・復帰される。
- ただし、タスク起動が遅延する（次アイドル時まで）。
- リアルタイム性が必要な場合は、アプリケーション側で ISR 喪失を許容設計。 `{NotRTOS}`

## 4. 検証 (Unified Logic)

### 4.1 不変条件

| ID | 仕様 | 状態 |
|---|---|---|
| **INV-1** | 所有権不変条件: Messageは単一のOwnerを持つ | ✓ TLA+ で検証中 |
| **INV-2** | 状態一貫性: タスク状態は有効値 {READY, RUNNING, BLOCKED_CALL, BLOCKED_REPLY} のみ | ✓ TLA+ で検証中 |
| **INV-3** | キュー有限性: `Len(EventQueue) <= QUEUE_MAX_SIZE` | ✓ ISR ドロップで保証 |
| **INV-4** | イベント型安全性: キュー内のすべてのイベントが有効な構造 | ✓ TLA+ で検証中 |

### 4.2 検出された設計矛盾と対応

#### [矛盾-1] Reply enqueue 失敗時のデッドロック

**問題**: Reply もキュー満杯でドロップされると、caller は BLOCKED_CALL のまま待機。

**対応**: Reply enqueue 失敗時、callee を BLOCKED_REPLY に遷移させ、待機させる。キュー消費により両者が復帰される。

**検証**: TLA+ モデル `EventDrivenCOOS_FiniteQueue.tla` で `CallReplyPairing` 活性特性を検証予定。

#### [矛盾-2] ISR ドロップによる割り込み喪失

**問題**: ドロップされた割り込みに対応するタスクが起動しない。

**対応**: 
1. **短期**: ISR 再検出メカニズム（アイドル時ポーリング）で復帰。
2. **中期**: ISR 専用の高優先度キュー（Phase 1 以降）。
3. **設計制約**: `{NotRTOS}` により、完全なリアルタイム性は要求しない。

**検証**: TLA+ で interrupt 喪失シナリオをモデル化し、アイドル時の復帰を確認。

#### [矛盾-3] キューサイズ決定の根拠

**問題**: QUEUE_MAX_SIZE をいくつに設定するか、根拠がない。

**対応**: 
- **設計値**: 初期値 `QUEUE_MAX_SIZE = 16` を採用。
- **根拠**: 
  - Normal IPC（Call/Reply）は 1 call につき最大 2 イベント（Request + Reply）。
  - ISR は複数タスクから発火可能。
  - 16 entry で、同時 8 call + ISR バースト に対応可能と推定。
- **検証**: フェーズ 1 の実装で、実測データを収集し、サイズを最適化。

### 4.3 TLA+ 検証の進捗

| モデル | 状態 | 検証内容 |
|---|---|---|
| `EventDrivenCOOS_Revised.tla` | ✓ 完了 | 基本的な Call/Reply 流れ |
| `EventDrivenCOOS_System.tla` | ✓ 完了 | Interrupt + Idle イベント化 |
| `EventDrivenCOOS_FiniteQueue.tla` | 🔄 進行中 | 有限キュー + ISR ドロップ + Reply 失敗処理 |

**次のステップ**: TLC で有限キューモデルを実行し、反例の収集と設計修正。

---

## 5. 実装上の注意点

### 5.1 コルーチンフレーム管理

- **フレームサイズ**: 静的（TCB に埋め込み）。スタック変数は固定サイズに制限。
- **メモリ配置**: スタックレスコルーチンのため、フレームは kernel が保持。 `{Policy_Memory}`

### 5.2 EventQueue の構成

```cpp
struct EventQueue {
    static constexpr size_t MAX_SIZE = 16;  // QUEUE_MAX_SIZE
    Event buffer[MAX_SIZE];
    size_t head = 0, tail = 0;
    
    bool enqueue(const Event& ev) {
        if (is_full()) return false;  // ドロップ
        buffer[tail] = ev;
        tail = (tail + 1) % MAX_SIZE;
        return true;
    }
    
    Event dequeue() {
        Event ev = buffer[head];
        head = (head + 1) % MAX_SIZE;
        return ev;
    }
    
    bool is_empty() const { return head == tail; }
    bool is_full() const { return (tail + 1) % MAX_SIZE == head; }
};
```

### 5.3 ISR ドロップログ

- **実装**: Atomic counter `dropped_interrupt_count` で記録。
- **出力**: Idle タスク内で logging システムに出力（ISR 外）。

---

## 6. 参考実装との比較

| 実装 | IPC スタイル | キュー管理 | ISR 対応 | 利点 / 課題 |
|---|---|---|---|---|
| **Zephyr** | messagequeue (ブロッキング) | 有限キュー | ISR → k_wakeup (直接) | 成熟度◎、複雑性高 |
| **FreeRTOS** | queue (ブロッキング) | 有限キュー | ISR → task ready | シンプル、プリエンプション |
| **seL4** | Capability IPC | なし（直接） | IPC reply (無条件) | 形式検証◎、複雑性◎ |
| **Fireball** | イベント駆動（同期API） | 有限キュー（ドロップ） | ISR → イベント化（ドロップ+再検出） | stackless 効率◎、検証◎、復帰遅延あり |

**Fireball の差異**:
- ✓ Stackless による RAM 効率（32-64KB 環境向け）
- ✓ TLA+ 形式検証で設計を論証
- ⚠️ ISR ドロップと再検出遅延（NotRTOS 前提）
- ⚠️ キューサイズ事前決定の必要性
