# [REVISED] イベント駆動型OS COOS コンポーネント設計書

本ドキュメントは、非プリエンプティブなイベント駆動型COOSの設計を定義する。COOSはイベントループを用いてタスクをdispatchする。同期IPC（RPC-like procedure call）は、イベントループを介したサスペンド/レジュームとして実装される。 `{CooperativeMultitasking}` `{CSPCommunication}`

## 1. コンセプト

- **Run-to-Completion**: 各dispatch単位は中断されることなく最後まで実行される。サスペンドが生じる場合、タスクは状態をTCBに保存し、制御をイベントループへ戻す。
- **明示的なイベントループ**: スケジューラは `EventQueue` から `Event` を取り出し、対象タスクを `Dispatch` するループとして機能する。
- **同期IPC / RPC-like call**: `call` / `reply` はイベントを介した非同期メッセージパッシングとして実装されるが、公開APIとしては同期RPCとして提供される。
- **所有権移譲**: `call` はrequest messageの所有権をcalleeへ、`reply` はresponse messageの所有権をcallerへ移譲する。 `{OwnershipTransfer}`

## 2. 静的モデル

### 2.1 主要データ構造
- **`Event`**: ディスパッチ単位。`type`, `target_task`, `caller_task`, `msg_handle` を含む。
- **`TaskControlBlock (TCB)`**: `state`, `context` (継続状態), `handler` を保持。
- **`MessagePool`**: 固定長プール。`message_handle` で管理。所有権不変条件の唯一の管理者。

## 3. 動的モデル

### 3.1 IPC シーケンス (Call/Reply)
各ステップがイベントを通じたアトミックな状態遷移として定義される。

1. **Call(A -> B)**: 
   - Aの `call(uri, request)` 呼び出しにより、Requestの所有権がkernel経由でBへ移譲される。
   - Aのステートを `BLOCKED_CALL` へ移行し、Aの継続状態を保存。
   - `(IPC_REQUEST, B, A, msg)` イベントをキューへ投入。
2. **Dispatch B**:
   - イベントループが上記Eventを取り出し、BへRequestの所有権を付与してBを `RUNNING` で呼び出す。
3. **Reply(B -> A)**:
   - Bの `reply(response)` 呼び出しにより、Responseの所有権がkernel経由でAへ移譲される。
   - Aのステートを `READY` へ戻す。
   - `(IPC_REPLY, A, B, msg)` イベントをキューへ投入。
4. **Resume A**:
   - イベントループが上記Eventを取り出し、Aの保存済み継続を再開。Aにとっては `call` が完了したように見える。

## 4. 検証 (Unified Logic)
- **所有権不変条件**: 任意のタイミングで、Messageは単一のOwnerを持つ。
- **デッドロック防止**: 循環呼び出しを静的解析（Call Graph）で禁止する。

---
TODO: TLA+モデルをこれに合わせる。
