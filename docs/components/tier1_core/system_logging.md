# ロギング コンポーネント設計書 {VERIFY_LLM} {VERIFY_FORMAL}
<!-- evidence:
     concept: concepts/logging_concept.py
     formal: formal/logging_flush_model.py
     test: tests/system_logging_test_spec.md
-->

## 1. コンセプト
<!-- traceability: {IPCRouter} {DictionaryBasedIPC} {BufferedLogging} {GLOBAL_IdleDetection} -->
ロギングコンポーネントは、ハイパーバイザ内部の状態を記録し、外部（UART/ITM等）へ出力する。システムコールはすべてIPCルータを経由して行われ、ログデータの転送もIPCルータを通過する。メモリ消費と通信負荷を抑えるため、辞書参照IPCと内部リングバッファによる遅延出力を採用する。また、COOSの **Idle Hook** を利用してシステム負荷が低い時に集中的に出力を行うことで、実行性能への影響を抑える。自己完結した参照実装は [`concepts/logging_concept.py`](concepts/logging_concept.py) を参照。 `{IPCRouter}` `{DictionaryBasedIPC}` `{BufferedLogging}` `{GLOBAL_IdleDetection}`

**適用範囲外**: 本コンポーネントが扱うのはビルド時に辞書登録された固定フォーマットの内部状態ログのみである。ゲストの `wasi:cli/stdout`/`stderr`（`print`/`eprint` による実行時生成の任意長文字列）はここでは表現できず、`console-output`（`{WASI_ConsoleRawOutput}`）という別経路で扱う。

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} -->
本コンポーネントは **Tier 1 (主要システムコンポーネント: Primary Component)** に属し、システム共通のリングバッファロギングおよびアイドル検出フックに基づく遅延出力を担当する。 `{META_3TierSeparation}`

## 3. 静的モデル

### 3.1 データ構造
- **`Logger`**: ログの収集、バッファリング、および物理出力への転送を一括して管理する主要クラス。
- **`logging_config`**: バッファサイズやデフォルトログレベルなどの不変な構成情報。
- **`log_entry`**: リングバッファに格納される単一のログデータ構造（辞書オフセット + 引数）。

### 3.2 内部ブロック図
```mermaid
graph TD
    subgraph Logging_Layer
        Engine[Logger Engine]
        RB[Internal Ring Buffer]
    end

    subgraph Dependency
        HAL[HAL_Transport]
    end

    Engine -- holds reference --> HAL
    Engine -- manages --> RB
```

### 3.3 主要なクラス・構造体・配列・定数

#### ロガー（Logger）クラス
依存関係（HALトランスポート）とバッファ状態をカプセル化する。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 出力トランスポート | HAL_Transport で定義された物理デバイス（UART等）への参照 | 構造体への参照 | `hal_transport` (非所有) |
| 循環バッファ | ログデータを一時的に保持する領域 | リングバッファ | 固定長配列 |
| 書き込み/読み出し索引 | バッファの現在の状態を示すポインタ | アトミック値 | 32bit |
| 出力閾値 | 現在出力対象としている最小のログレベル | uint8_t | `log_level` |

#### ログ構成（logging_config）
<!-- traceability: {META_ConfigurableSystem} -->
ロギングシステムの動作パラメータを定義する。 `{META_ConfigurableSystem}`

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| バッファ総容量 | 循環バッファの大きさを定義する | バイト数 | 2のべき乗 |
#### ログ辞書（LogDictionary）
<!-- traceability: {DictionaryBasedIPC} {META_FlatMapIndexed} -->
ROM上に固定配置されたフォーマット文字列配列の非所有アクセスを担う。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 辞書ストレージ参照 | ROM上のソート済み固定長エントリ配列（`flat_map_storage`）への参照（所有権分離） | 非所有参照 | `flat_map_storage` |
| ペイロードビュー | $O(\log N)$ 二分探索を行う非所有スパン | 非所有ビュー | `fireball::flat_map_view` |

## 4. 動的モデル

### 4.1 アルゴリズム
<!-- traceability: {DictionaryBasedIPC} {BufferedLogging} -->
- **辞書参照ロギング (`LOG-GOTCHA-01`, `{DictionaryBasedIPC}`)**:
  送信側はメッセージ文字列ではなく、辞書内のオフセットと引数のみをIPCで送信する。
  **設計理由と不変条件**: ログ API は任意長文字列ポインタ（`%s`, `%p` 等）を直接受け付ける経路を一切排除している。実行時に動的構築した文字列ポインタをログエントリに格納することを許すと、ログを出力したタスクが終了・クラッシュした後にロガーが不正メモリを参照するダングリングポインタ（Use-After-Free）が発生する。全ログメッセージを静的辞書オフセットとスカラー引数（u32）のみに限定することで、メモリ安全性を根本から保証する。
- **遅延出力と割り込み即時応答性 (`LOG-GOTCHA-03`, `{BufferedLogging}`)**:
  IPC受信時はリングバッファへの格納のみを行い、実際の物理出力は `HAL_Transport` を介した抽象化された通信路によりバックグラウンドで行われる。具体的なトランスポート実装（UARTやITMなど）はシステム構成定義ファイル（`inc/fireball_config.hxx`）で指定される。
  **設計理由と不変条件**: ログフラッシュ（DMA/HAL 転送）のループはアトミックな不可分処理としてはならず、各エントリ送信完了ごとに `interrupt_pending()` を確認する。外部割り込みが発生した場合は直ちにフラッシュ処理を中断して残余エントリをバッファに残したままスケジューラへ制御を戻す。これにより、長大なログ出力中であっても外部割り込みの応答レイテンシが悪化することを防止する。
- **バッファフル・ポリシー (`LOG-GOTCHA-02`)**: **FINALIZED: Overwrite**。
  リングバッファが満杯の場合、古いログを破棄して新しいログを書き込む。システムの状態継続を優先。
  **設計理由と不変条件**: ログバッファが満杯になった際に呼び出し元タスクをブロック（一時停止）させたり動的メモリ再確保を行うと、高負荷時や異常フォールト発生時にログ出力処理自身が原因となってシステム全体がデッドロックやメモリ枯渇に陥る。そのため、満杯時は最も古いエントリを非ブロッキングで安全に上書きし（ドロップカウンタをインクリメント）、直近の診断情報を確実に残しつつシステムの稼働継続性を最優先する。

### 4.2 辞書構造
<!-- traceability: {DictionaryBasedIPC} -->
辞書はROM上に固定配置され、ホスト側ツールが `dict_offset + args` から可読テキストに展開する。 `{DictionaryBasedIPC}`

| 項目 | 値 |
| :--- | :--- |
| 配置場所 | ROM (実行時不変) |
| エントリフォーマット | `{ id: u32, format: null-terminated UTF-8 }` (AoS) |
| 最大エントリ数 | `FB_CONF_LOG_DICT_MAX_ENTRIES` (コンパイル時固定) |
| 所有権モデル | ストレージ所有権分離。ROM上のソート済みAoS固定長配列（`flat_map_storage`）に実体を配置し、`LogDictionary` および `Logger` は非所有スパン（`flat_map_view`）を介して二分探索を行う。ロガー本体が辞書配列を複製・所有することはない |
| フォーマット文字列 | printf形式。最大4個の `u32` 引数を参照可能。不許可指定子（`%s`, `%p`, `%c` 等のポインタ間接参照）はビルド時に静的拒絶 |
| 引数スライス規則 | フォーマット文字列に含まれる指定子数 $n$（$0 \le n \le 4$）に対し、渡された4引数タプルの先頭 $n$ 個（`args[0..n]`）のみが展開時に参照され、未使用スロットは安全に無視される |
| 登録時期 | ビルド時 (実行時の追加は不可) |

### 4.2.1 COOS / IPC 診断ログイベント仕様
<!-- traceability: {DictionaryBasedIPC} {BufferedLogging} -->
COOS および IPC において、デバッグ時に重大な不整合・境界超過・通信遮断を検知するための診断ログイベントを定義する。ログのオーバーヘッドを最小化するため、常時ログは出力せず、異常系・境界値到達時のみに厳選して発行する。 `{DictionaryBasedIPC}` `{BufferedLogging}`

| イベントID | 分類 | レベル | フォーマット文字列 | 引数構成 (args[0..3]) | 発生条件 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x0101` | COOS | `WARN` | `COOS: handoff limit reached (task=%d, count=%d)` | `task_id`, `handoff_count`, 0, 0 | 連続ハンドオフ上限（4回）に到達し強制 YIELD |
| `0x0102` | COOS | `ERROR` | `COOS: task capacity exceeded (max=%d, attempted=%d)` | `max_tasks`, `attempted_count`, 0, 0 | タスク上限（16）超過によるタスク spawn 拒否 |
| `0x0103` | COOS | `ERROR` | `COOS: duplicate task id rejected (task=%d)` | `task_id`, 0, 0, 0 | 既存タスクと同一 ID の spawn 試行の拒絶 |
| `0x0104` | COOS | `WARN` | `COOS: irq queue overflow dropped (irq=%d, dropped_total=%d)` | `irq_id`, `dropped_count`, 0, 0 | 割込通知キュー（16）溢れによるイベント破棄 |
| `0x0201` | IPC | `WARN` | `IPC: rbac denied (sender_role=%d, target_role=%d)` | `sender_role`, `target_role`, 0, 0 | RBAC 権限マトリクス違反によるメッセージ遮断 |
| `0x0202` | IPC | `WARN` | `IPC: unknown uri routing failed (uri_handle=%d)` | `uri_handle`, 0, 0, 0 | サービスレジストリ未登録の URI への送信試行 |
| `0x0203` | IPC | `ERROR` | `IPC: message too large (kv_count=%d, max=%d)` | `kv_count`, `max_kv_pairs`, 0, 0 | 許可された最大 KV ペア数（8）を超過したメッセージ |
| `0x0204` | IPC | `ERROR` | `IPC: invalid ownership state (current_state=%d, op=%d)` | `ownership_state`, `operation`, 0, 0 | 送信側が所有権を持たないメッセージの送信試行 |
| `0x0205` | IPC | `ERROR` | `IPC: channel waiter collision (channel=%d, dir=%d)` | `channel_idx`, `wait_dir`, 0, 0 | 1チャネル1待機タスクの不変条件に対する重複待機試行 |

### 4.3 COOS Idle Hook 連携 (Flush Protocol)
<!-- traceability: {GLOBAL_IdleDetection} -->
COOSスケジューラの `set_idle_hook` で `logger.flush()` を登録する。 `{GLOBAL_IdleDetection}`

1. COOSスケジューラがREADYタスクがないことを検出
2. `idle_hook()` を呼び出し → `logger.flush()` が実行
3. リングバッファの連続ブロックをバッチとして物理トランスポート（UART/DMA）へ転送開始
4. DMA転送完了割り込みで次のブロックを順次排出し、バッファが空になったら制御を返す

### 4.4 状態遷移図
<!-- traceability: {DictionaryBasedIPC} {BufferedLogging} {GLOBAL_IdleDetection} -->
```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Validating: log_received(dict_id, args)
    Validating --> Enqueuing: id_within_bounds / store raw entry (overwrite oldest on full)
    Enqueuing --> Idle: enqueued
    Idle --> Flushing: buffer_not_empty / idle_hook
    Flushing --> DrainingBatch: start_dma_batch
    DrainingBatch --> Flushing: dma_complete / buffer_not_empty
    DrainingBatch --> Idle: buffer_empty
```

### 4.5 内部シーケンス
<!-- traceability: {DictionaryBasedIPC} {BufferedLogging} {GLOBAL_IdleDetection} -->
#### ログ出力シーケンス
```mermaid
sequenceDiagram
    participant C as Client
    participant L as Logging Subsystem
    participant RB as Ring Buffer
    participant HW as UART/DMA
    
    C->>L: IPC(dict_id, args)
    L->>L: Validate dict_id bounds (no string formatting)
    L->>RB: push(raw_entry) / overwrite if full
    L-->>C: reply(OK)
    Note over L,HW: COOS Idle Flush (Batch DMA Transfer)
    L->>RB: get_contiguous_block()
    L->>HW: Start DMA Batch Transfer(raw_entries)
    HW-->>L: Transfer Complete Interrupt
    L->>RB: advance_read_ptr(transferred_count)
```

## 5. インターフェース定義

### 5.1 公開API
外部から利用可能なオブジェクト指向APIを定義する。

#### ログイベント記録 (`log_event`)

<!-- traceability: {BufferedLogging} {META_ZeroOverhead} -->

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 発生したイベントを、レベルと辞書オフセット形式で記録する。 |
| シグネチャ | `auto log_event(level: uint8_t, offset: uint32_t, args: std::span<const uint32_t, 4>) -> log_result_t` |
| 引数 | `level`: ログレベル重要度<br>`offset`: 辞書オフセット（API上は `uint32_t` で受け取るが、IPC送信時は下記 IPC 不変条件のとおり `kv_pair` の識別キー幅である24bitに収める）<br>`args`: ログパラメータとなる数値配列（最大4要素の `std::span`） |
| 戻り値 | `log_result_t` (常に `SUCCESS` を返し、バッファ満杯時は最古ログを自動上書きしてシステムの実行継続性を最優先する) |
| 期待する結果 | 正常：ログ情報がリングバッファにキューイングされる。 |

#### バッファリング出力 (`flush`)

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | リングバッファに蓄積されたログを物理トランスポートへ一括出力する。 |
| シグネチャ | `auto flush() -> log_result_t` |
| 戻り値 | `log_result_t` (成功時は `SUCCESS`、物理トランスポートがDMA転送中かつ出力バッファが空でないといったハードウェアビジー状態の失敗時には `ERR_TRANSPORTER_BUSY` を返す) |
| 補足 | COOS の `set_idle_hook` により、システムアイドル時に呼び出される。物理転送中に割り込み（INTイベント、例：WASIタイマー等）が発生した場合は、現在のDMA転送の完了待機を中止してバックグラウンドDMAに任せ、次のログバッファのフラッシュ処理をスキップして速やかに制御をスケジューラに戻す。 |

### 5.2 URI/IPCインターフェース
<!-- traceability: {DictionaryBasedIPC} -->
- **URI**: `fireball://logging/system/0`
- **メッセージ形式**: Key-Valueプロトコル。 `level`, `dict_offset`, `arg0`〜`arg3` を含む。 `{DictionaryBasedIPC}`
- **不変条件**: 辞書オフセットは `kv_pair`（`{DictionaryBasedIPC}`）の識別キー幅に合わせ 24bit、引数は各 32bit とする。24bit（最大16MB）は本プロジェクトの ROM 辞書サイズに対して十分な範囲である。

## 6. 制約達成の方策

### 6.1 性能制約と方策
<!-- traceability: {BufferedLogging} -->
- **目標**: ログ出力による呼び出し側のブロッキングを最小化する。
- **方策**: `{BufferedLogging}` 内部バッファリングと非同期出力により、IPCハンドラを即座に解放する。

### 6.2 メモリ制約と方策
<!-- traceability: {MemoryIsolation} {META_ConfigurableSystem} -->
- **目標**: ログ機能によるメモリ圧迫を防止する。
- **方策**: `{MemoryIsolation}` `{META_ConfigurableSystem}` 独立した静的メモリプールを使用し、バッファサイズをコンパイル時に固定する。動的メモリ確保（ヒープ）は一切使用しない。

### 6.3 安全性制約と方策
<!-- traceability: {BufferedLogging} {MemoryIsolation} {META_ConfigurableSystem} -->
- **目標**: ログ出力の失敗がシステム全体に波及しないようにする。
- **方策**: `{BufferedLogging}` `{MemoryIsolation}` `{META_ConfigurableSystem}` ログの蓄積はリングバッファでバッファリングを行い、メモリパーティションによってログ領域のクラッシュを他のコンポーネントから隔離する。また、バッファサイズ等の制限はコンパイル時マクロ定義で設定される。バッファフル時は古いログを破棄し、システムの継続実行を優先する。
