# イベント駆動型 COOS 形式検証レポート

## 検証対象モデル

- **EventDrivenCOOS_FiniteQueue.tla**: 有限キュー + ISR ドロップ戦略

## 検証仕様

### 1. 不変条件（Invariant）

| ID | 仕様 | 状態 | 備考 |
|---|---|---|---|
| **INV-1** | OwnershipInvariant: メッセージ所有者が常に一意 | ✓ 検証対象 | ドロップされても成立するか？ |
| **INV-2** | StateConsistency: タスク状態は有効値のみ | ✓ 検証対象 | - |
| **INV-3** | QueueBounded: キューサイズ ≤ QUEUE_MAX_SIZE | ✓ 検証対象 | ISR ドロップで満たされる |
| **INV-4** | EventValidity: キュー内イベントが型安全 | ✓ 検証対象 | - |

### 2. 活性特性（Liveness）

| ID | 仕様 | 状態 | 備考 |
|---|---|---|---|
| **LIVE-1** | EventualDispatch: キューが空でない限り最終的に消費される | 検証予定 | キューが満杯でイベントドロップ時の挙動確認 |
| **LIVE-2** | CallReplyPairing: Call 後、最終的に Reply が完結 | 検証予定 | Reply enqueue が失敗した場合の動作確認 |

---

## 検出された矛盾と疑問

### [矛盾-1] ISR ドロップと Reply 完結の保証

**問題**: Reply イベントもキュー満杯でドロップされる可能性がある。

```
Scenario:
1. TaskA が TaskB へ Call
   → EventQueue: [IPC_REQUEST, B, A, msg]
2. ISR が連続発火 → キュー満杯
   → Interrupt イベント次々ドロップ
3. TaskB の Reply をキューイングしようとする
   → キュー満杯 → Reply もドロップ？
4. TaskA は BLOCKED_CALL のまま永遠に待機（デッドロック）
```

**現在のモデル**: Reply（Normal IPC）がキュー満杯でも enqueue を試みるが、モデルでは Call/Reply は **常に enqueue 成功** と仮定している。

**修正必要**: Call/Reply もキュー満杯をチェックし、失敗時の挙動を定義する。

---

### [矛盾-2] ISR ドロップによる割り込み喪失

**問題**: ドロップされた Interrupt イベントに対応するタスクが永遠に起動しない。

```
GPIO 割り込み → Interrupt イベント投入
         ↓
キュー満杯 → ドロップ
         ↓
タスク side では割り込みが起きたことを知らない
         ↓
GPIO のピン状態が変わったが、タスク未検知 → 状態不一致
```

**影響**: `{InterruptWakeup}` 要件を満たさない場合がある。

**選択肢**:
1. **ISR 再検出メカニズム**: アイドル時に割り込みステータスをポーリング
2. **ISR 優先キュー**: ISR イベント専用の小さいキューを用意（複雑性 ↑）
3. **アプリケーション責任**: アプリが ISR 喪失を許容設計

---

### [矛盾-3] ドロップ戦略の不完全性

**現在**: ISR イベントのみドロップ。

**未定義**:
- Call/Reply がキュー満杯時の挙動
- Normal タスク間通信でどの優先度を付けるか
- ドロップ時の復帰メカニズム

**TLA+ では Call/Reply はドロップしない前提** だが、実装では IPC も失敗する可能性あり。

---

## 検証対象の詳細（TLC設定）

```
CONSTANT QUEUE_MAX_SIZE = 8  \* 極小環境での典型値
```

### 検証手順

```bash
cd docs/formal_verification
tlc EventDrivenCOOS_FiniteQueue.tla -config EventDrivenCOOS_FiniteQueue.cfg
```

### 期待される TLC 結果

| 不変条件 | 期待結果 | 現在 |
|---|---|---|
| OwnershipInvariant | ✓ Pass | TBD |
| StateConsistency | ✓ Pass | TBD |
| QueueBounded | ✓ Pass | TBD |
| CallReplyPairing | ⚠️ Fail（反例: ISR ドロップ） | TBD |

---

## 推奨される設計修正

### 修正案-1: Call/Reply にも enqueue 失敗処理を追加

```tla
Call(caller, callee, msg) ==
    IF Len(queue) < QUEUE_MAX_SIZE
    THEN
        /\ message_owner' = [message_owner EXCEPT ![msg] = callee]
        /\ task_state' = [task_state EXCEPT ![caller] = BLOCKED_CALL]
        /\ queue' = Append(queue, <<IPC_REQUEST, callee, caller, msg>>)
    ELSE
        \* Enqueue 失敗時の挙動（caller が state をどう遷移させるか定義必要）
        /\ task_state' = [task_state EXCEPT ![caller] = BLOCKED_CALL]
        /\ queue' = queue
        /\ UNCHANGED message_owner
```

### 修正案-2: Interrupt 再検出ポーリング

アイドル時に ISR 状態をポーリング：

```tla
IdleAction_WithInterruptPoll ==
    /\ queue = << >>
    /\ \* ISR 状態を確認、未処理割り込みがあれば投入
    /\ queue' = Append(queue, <<IDLE, TaskA, 0, 0>>)
```

---

## 次のステップ

1. **TLC で検証実行** → 反例の収集
2. **Call/Reply enqueue 失敗時の挙動を定義**
3. **ISR 再検出メカニズムを追加**
4. **ドラフト（os_event_driven.md）に反映**
