# 協調型OS COOS スケジューラ設計書

## 1. コンセプト
COOSスケジューラは、C++20コルーチンを活用したスタックレスな協調型マルチタスクの核となるコンポーネントである。タスクの実行、一時停止(yield)、および割り込みによる再開を管理し、極小リソース環境での決定論的な実行を提供する。 `{CooperativeMultitasking}` `{UseCpp20Coroutine}` `{COOS_Deterministic}` `{CSPCommunication}`

## 2. アーキテクチャ分類
本コンポーネントは **Tier 3 (実装ドメイン)** に属する。コルーチンハンドルの管理とタスク実行順序の制御に特化した単一責務のモジュールとして設計する。 `{3TierSeparation}`

## 3. 静的モデル

### 3.1 データ構造
- **`Scheduler`**: タスクのREADYキュー管理、実行順序制御、およびコルーチン実行をカプセル化した主要クラス。
- **`task_context`**: 各タスクの実行状態、スタック/ヒープ境界、コルーチンハンドルを集約したデータ構造。
- **`scheduler_config`**: 最大タスク数やタイムアウト閾値などの不変の設定。 `{COOS_Transparent}`

### 3.2 内部ブロック図
```mermaid
graph TD
    subgraph Scheduler_Layer
        Engine[Scheduler Engine]
        TCB[task_context]
    end

    subgraph Dependency_Injection
        I_IF[interrupt_controller]
        T_IF[timer_driver]
    end

    Engine -- method injection --> Dependency_Injection
    Engine -- manages --> TCB
```

### 3.3 主要なデータ定義

#### `Scheduler` クラス
依存関係（割り込み制御等）とタスクキューをカプセル化する。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 割り込み制御機 | 物理ハードウェア（NVIC等）の制御用 | 構造体への参照 | `interrupt_controller` |
| 実行可能列 | 次に実行すべきタスクの優先度付きキュー（侵入型リスト） | リスト構造 | `task_context` のリスト |
| 待機リスト | イベントや時間待ちを行っているタスクのリスト（侵入型） | リスト構造 | `task_context` のリスト |
| 現在のタスク | 現在CPUコアを占有しているタスク | 構造体への参照 | `task_context` (NULL許容) |

## 4. 動的モデル

### 4.1 アルゴリズム
- **スケジューリング**: ラウンドロビン方式。
    - スケジューラ・コンテキスト内の「実行可能タスク列」を侵入型リストで管理し、定数時間 O(1) でのタスク切り替えを実現する。
- **アイドル状態の検知**: 全ての管理タスクが「待機状態（BLOCKED）」となった場合にアイドル・ハンドラ（Periodic Task等）を実行する。 `{IdleDetection}` `{PeriodicTask}`
- **割り込み処理**: HALからの割り込み通知（`notify_interrupt`）を受信し、対象タスクを優先的に再開する。 `{InterruptWakeup}`

### 4.2 状態遷移図
```mermaid
stateDiagram-v2
    state "READY" as ready
    state "RUNNING" as running
    state "BLOCKED" as blocked
    state "INTERRUPTED" as interrupted
    
    [*] --> ready: spawn / spawn_task
    ready --> running: schedule
    running --> ready: yield
    running --> blocked: wait / send / recv
    blocked --> ready: timeout
    blocked --> interrupted: notify_interrupt
    ready --> interrupted: notify_interrupt
    interrupted --> running: schedule (priority)
    running --> [*]: exit / error (cleanup)
```

## 5. インターフェイス設計

### 5.1 公開API
外部から利用可能なオブジェクト指向APIを定義する。依存関係は `initialize` メソッドで注入する。

#### `initialize`
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | スケジューラを動作させるための依存コンポーネントを注入する。 |
| シグネチャ | `initialize(memory: address) -> operation-result` |
| 引数 | `memory`: メモリ管理ユニットのアドレス |
| 戻り値 | 操作結果 |

#### `spawn`
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 新しいWASMタスクを生成し、READY キューに追加する。 |
| シグネチャ | `spawn(name: string, entry: address, priority: u8) -> result<task_id, recovery_strategy>` |
| 引数 | `name`: タスク名称<br>`entry`: WASMエントリポイント<br>`priority`: 実行優先度 |
| 戻り値 | 結果型 (成功時は `task_id`) |

#### `spawn_task`
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 既存のコルーチンオブジェクトからネイティブタスクを生成し、READY キューに追加する。 |
| シグネチャ | `spawn_task(task: task&&) -> 結果型` |
| 引数 | `task`: 移動セマンティクスによるコルーチンタスク |
| 戻り値 | 結果型 (成功時は `task_id`) |

#### `yield`
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 現在のタスクの実行を中断し、スケジュールの再評価を行う。 |
| シグネチャ | `yield() -> void` |

#### `run`
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | メインスケジューリングループを開始する。 |
| シグネチャ | `run() -> void` |

#### `set_idle_handler`
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | READYキューが空になった際に呼び出されるアイドル時処理を登録する。 |
| シグネチャ | `set_idle_handler(handler: idle_handler) -> void` |
| 引数 | `handler`: 関数ポインタ (`void(*)()`) |

#### `notify_interrupt`
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | ハードウェア割り込みの発生を通知し、待機中タスクを READY へ移行させる。 |
| シグネチャ | `notify_interrupt(task: task_id) -> void` |
| 引数 | `task`: 再開対象のタスクID |

#### `terminate`
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 指定したタスクを終了し、リソースを解放する。 |
| シグネチャ | `terminate(id: task_id) -> void` |
| 引数 | `id`: 終了対象のタスクID |

## 6. 設計判断 (ADR)

### ADR-SCHED-001: 侵入型リストによる管理
- **決定事項**: TCBの連結には `std::list` 等を避け、TCB自体に `next` ポインタを持たせる侵入型リストを採用する。
- **理由**: 動的メモリ確保を排除し、RAM 64KB環境での生存を確実にするため. `{Policy_Memory}`

## 7. 設計完了チェックリスト
- [x] Tier 3 (Implementation Domain) に基づき設計となっているか
- [x] スケジューラの責務が明確に定義されているか
- [x] **構造化データ（インターフェイス、ハーネス等）が表形式で記述されているか**
- [x] 命名規則（プリフィックス/ポストフィックスなし、PODメンバの末尾アンダースコアなし）が遵守されているか
- [x] 禁止コンテナ (`std::list`, `std::vector`) を回避しているか
- [x] 上位の要求 `{Keyword}` とのトレーサビリティが確保されているか
