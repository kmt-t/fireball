# EventDrivenCOOS_ThreeState 検証レポート

**作成日**: 2026-05-11  
**対象**: COOSスケジューラの3状態モデル（READY, RUNNING, BLOCKED）  
**標準**: TLA+ 仕様言語 + TLC モデルチェッカー

---

## 1. 概要

本レポートは、COOSスケジューラのタスク状態を標準的なOS定石に基づいて3状態（Running, Ready, Blocked）に統一した設計仕様を、形式検証によって検証するものである。

### 1.1 対象ドキュメント

- `docs/components/core/os_scheduler.md` (修正版)
- `docs/components/core/os_coos.md` (修正版)
- `docs/components/os_event_driven.md` (仕様参照)

### 1.2 検証ツール

| ツール | 目的 |
|---|---|
| **TLA+** | 仕様言語。状態遷移ロジックをモデル化 |
| **TLC** | モデルチェッカー。不変条件と活性特性を検証 |

---

## 2. モデル設計

### 2.1 状態定義（3状態モデル）

```
状態集合: {READY, RUNNING, BLOCKED}

遷移ルール：
  READY → RUNNING    : Schedule（スケジューラが実行を開始）
  RUNNING → READY    : Yield（タスクが実行を中断）
  RUNNING → BLOCKED  : wait/send/recv（イベント待機）
  BLOCKED → READY    : Dispatch（INT/IPC_REPLY イベント処理）
```

### 2.2 イベント駆動型割り込み

**旧設計（INTERRUPTED状態）**:
```
ISR → 直接 task_state を INTERRUPTED へ → スケジューラで処理
```

**新設計（イベント化）**:
```
ISR → INT イベントをキューに投入 → Dispatch が INT イベントを処理 → BLOCKED → READY
```

---

## 3. 不変条件（Invariants）

| ID | 不変条件 | 説明 |
|---|---|---|
| **INV-1** | StateConsistency | すべてのタスク状態が {READY, RUNNING, BLOCKED} に属する |
| **INV-2** | QueueBounded | キューサイズが QUEUE_MAX_SIZE を超えない |
| **INV-3** | EventValidity | キュー内のすべてのイベントが有効型 |
| **INV-4** | SingleRunning | RUNNING 状態は最大1個（単一スレッド） |
| **INV-5** | OwnershipInvariant | メッセージの所有権が常に一意 |

---

## 4. 活性特性（Liveness Properties）

| ID | 活性特性 | 説明 |
|---|---|---|
| **LIVE-1** | EventualDispatch | キューが空でない限り、最終的に Dispatch が実行 |
| **LIVE-2** | CallReplyPairing | Call 後、最終的に Reply が完結 |
| **LIVE-3** | IdleRecovery | すべてのタスクが BLOCKED の場合、最終的に復帰 |

---

## 5. 検証実行

### 5.1 実行方法

```bash
cd verify
./run_eventdriven_coos.sh
```

### 5.2 検証パラメータ

| パラメータ | 値 |
|---|---|
| QUEUE_MAX_SIZE | 4 |
| Tasks | {TaskA, TaskB} |
| Messages | {msg1} |

---

## 6. 設計の正当性

### 6.1 3状態モデルの妥当性

**標準OS設計との比較**:

| OS | States | ISR処理 |
|---|---|---|
| Linux | RUNNING, INTERRUPTIBLE, UNINTERRUPTIBLE, ... | ISR → scheduler |
| FreeRTOS | Running, Ready, Blocked | ISR → task ready |
| seL4 | Active, Inactive | IPC reply |
| Fireball (新) | RUNNING, READY, BLOCKED | ISR → Event |

---

## 7. 既知の制限と対応

### 7.1 ISR ドロップによる割り込み喪失

**対応**:
1. アイドル時に割り込みステータスをポーリング（再検出）
2. TODO: ISR 専用の高優先度キュー導入の実装検討
3. `{NotRTOS}` 制約により完全リアルタイム性は要求しない

---

## 8. トレーサビリティ

| 要件キーワード | 対応ドキュメント |
|---|---|
| `{CooperativeMultitasking}` | os_scheduler.md, os_coos.md |
| `{COOS_Deterministic}` | models/EventDrivenCOOS_ThreeState.tla |
| `{GLOBAL_Policy_Memory}` | EventQueue サイズ固定 |

---

**検証完了日**: 2026-05-11  
**ステータス**: ✓ 正式検証対象
