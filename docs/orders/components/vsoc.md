# vSoC コンポーネント設計書

## 1. コンセプト
vSoC (Virtual System-on-Chip) は、WASM実行環境の統合マネージャであり、各サブコンポーネントを統合する「ハーネス」としての役割を持つ。サブコンポーネントとして Loader、Execution Engine (Interpreter + JIT)、vMMIO、Debugger を統括して実行制御を行う。 `{LowLatencyJIT}` `{MemoryIsolation}` `{FaultIsolation}` `{EnvironmentPointer}` `{ComponentHarness}`

## 2. 静的モデル

### 2.1 データ構造
- **vsoc_harness**: vSoCを構成する実行ユニット（サブコンポーネント）の集合。
- **vsoc_runtime**: ハーネスのエイリアスであり、実行コンテキストから参照される周辺環境のインターフェイス。
- **JIT Code Cache**: コンパイル済みのネイティブコードを保持するダブルバッファ領域。 `{JIT_DoubleBuffer_Cache}`
- **vMMIO Map**: 仮想的なメモリマップドI/Oのフック情報および物理空間との対応を管理する。

### 2.2 内部ブロック図
```mermaid
graph TD
    subgraph vsoc_harness
        Loader[WasmLoader]
        Engine[Execution Engine]
        vMMIO[vMMIO]
        Debug[Debugger]
    end

    vSoC[vSoC Manager] -- uses --> vsoc_harness
    Engine -- contains --> Interp[Interpreter]
    Engine -- contains --> JIT[JIT Compiler]
    Engine -- accesses --> vMMIO
    Engine -- notifies --> Debug
    
    HAL[HAL RSP Parser] --> Queue[debug_command_queue_t]
    Queue --> Debug
```

### 2.3 主要なクラス・構造体・配列・定数

#### `vsoc_harness` / `vsoc_runtime` (実行ハーネス)
各サブコンポーネントへのインターフェイスを保持する。

| 構成項目 | 機能と役割 | 備考 |
| :--- | :--- | :--- |
| `loader` | `wasm_loader` へのポインタ。バイナリパースを担う。 | interface |
| `engine` | `executor` (Interpreter + JIT) へのポインタ。実行制御を担う。 | interface |
| `mmio` | `vmmio` へのポインタ。仮想ハードウェアアクセスを担う。 | interface |
| `dbg` | `debugger` へのポインタ。デバッグ支援を担う。 | interface |

#### `vsoc_config` (vSoC構成)
vSoCの動作パラメータを定義する。 `{ConfigurableSystem}`

| 構成項目 | 機能と役割 | 備考（制約、型など） |
| :--- | :--- | :--- |
| `jit_enabled` | JITコンパイル機能を有効化するかどうかの静的な設定。 | ブール値 |
| `code_cache_size` | JITコードキャッシュに割り当てるメモリサイズ。 | バイト数 |
| `ram_base` | ゲストRAMの仮想アドレス空間における開始位置。 | 通常 0x0 |
| `ram_size` | ゲストに割り当てるRAMの総量。 | バイト数 |
| `vmmio_base` | vMMIO領域の開始アドレス。 | 通常 0x4000_0000 |

## 3. 動的モデル

### 3.1 アルゴリズム
- **実行エンジン委譲 (exec_trace)**: vSoCは `step()` で現在のPCに対応する `exec_trace` を呼び出す。 `exec_trace` はインタープリタのディスパッチャまたはJITコードを指し、呼び出し側は実行エンジンを意識する必要がない。 `{ThreadedInterpreter}` `{CopyAndPatchJIT}`
- **概算Yield**: 監視対象の `yield_threshold` を基準に `co_yield` を発行する。 `{Challenge_ApproximateYield}`
- **デバッグ連携**: `step()` 前後で Debugger を呼び出し、HAL層からのコマンドを処理する。

### 3.2 状態遷移図
```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Loading: load
    Loading --> Ready: load_ok
    Ready --> Running: step
    Running --> Ready: yield
    Running --> Debugging: breakpoint
    Debugging --> Running: resume
    Running --> Error: trap
    Ready --> Idle: stop
```

### 3.3 内部シーケンス
#### WASM実行およびJIT遷移シーケンス
```mermaid
sequenceDiagram
    participant S as Scheduler
    participant V as vSoC
    participant E as Execution Engine
    participant C as JIT Code Cache
    
    S->>V: step()
    loop until yield
        V->>E: step(ctx)
        Note over E: Internally switches Interp/JIT
        E->>C: call native code
        C-->>E: return
        E-->>V: status (ok/yield/trap)
    end
    V-->>S: yield
    
    Note over V,J: co_yield processing (Hotspot Detection)
    V->>V: scan_history_buffer()
    V->>J: enqueue_compile_request(pc)
    
    Note over J,C: Background JIT Task (LIFO Order)
    J->>J: dequeue_request_reverse()
    J->>C: write_native_code

#### マルチモジュール動的リンクシーケンス
複数学のWASMモジュール間の依存関係を解決し、関数ポインタを接続する。 `{MultiModule_Support}`

```mermaid
sequenceDiagram
    participant V as vSoC Manager
    participant L as Wasm Loader
    participant R as Module Registry
    participant M as Target Module
    
    V->>L: load_module(binary)
    L->>L: parse_import_section()
    loop for each import
        L->>R: resolve_symbol(module_name, func_name)
        R->>M: get_exported_func(func_name)
        M-->>R: func_addr
        R-->>L: func_addr
        L->>L: patch_interp_table(func_addr)
    end
    L-->>V: load_complete
```
```

### 4.1 公開API (vsoc)
vSoC Manager の主要な操作。

#### 初期化 (init)
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 外部から提供されたサブコンポーネント（ハーネス）を用いてシステムを初期化する。 |
| 引数と役割 | `harness`: 設定済みのサブコンポーネント群。 |
| 期待する結果 | 正常：初期化完了。異常：コンポーネント不足等のエラー。 |
| 事前条件 | 各サブコンポーネントが生成済みであること。 |
| 事後条件 | vSoC が稼働準備状態（Idle）になる。 |

#### WASMモジュールのロード
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 指定されたWASMバイナリデータを読み込み、実行準備を完了させる。 |
| 引数と役割 | `data`: ロード対象のバイナリデータとそのサイズ。 |
| 期待する結果 | 正常：モジュールがロードされ、内部状態がReadyになる。異常：検証失敗時等のエラー。 |
| 事前条件 | システムが初期化済みであること。 |
| 事後条件 | 内部の `module_view` が構築され、実行可能状態になる。 |
| 不変条件 | 既存の実行コンテキストが破壊されないこと。 |
| エラー時の挙動 | 不正なバイナリの場合はロードを中断し、エラー値を返す。 |
| 補足 | ROM上のデータを直接参照するため、RAMへのコピーは発生しない。 |

#### 実行ステップ (step)
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 現在ロードされているモジュールの実行を `executor::step` に委譲して継続する。 |
| 補足 | インタープリタとJITの切り替えは `executor` の実装内で隠蔽される。 |

#### 仮想割り込み通知
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 物理割り込み等の外部イベントをゲストOS/アプリに通知するための仮想フラグを設定する。 |
| 引数と役割 | `irq_id`: 通知する仮想割り込みの識別子。 |
| 期待する結果 | 特定位のアドレス（SYSCTLレジスタ）にフラグが反映される。 |
| 事前条件 | なし。 |
| 事後条件 | 実行コンテキスト内の `interrupt_flags` が更新される。 |
| 不変条件 | 他の実行状態に副作用を及ぼさないこと。 |
| エラー時の挙動 | 無効なIDの場合は無視される。 |
| 補足 | ISRから呼び出されることを想定し、排他制御を考慮する。 |

#### vMMIO登録
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | ハーネス内の `vmmio` に対してハンドラを登録する。 |
| 補足 | 具体的なディスパッチロジックは `vmmio` インターフェイスに委譲される。 |

### 4.2 Native API エクスポート (Single Trap 方式)
WASMゲストからホストサービスを呼び出すための最小限のインターフェイスを提供する。 `{NativeAPI_Export}`

Fireballでは、ホスト側のコードサイズを極限まで削減するため、標準的なWASIの実装をホストから排除し、単一のトラップ命令とvMMIOレジスタによるサービス提供を行う。

- **トラップ命令**: `void fireball_call(uint32_t service_id)`
  - ゲストはこの関数をインポートし、サービスIDを指定して呼び出す。
  - 引数および戻り値の受け渡しは vMMIO レジスタ（`REG_SYSCALL_ARG0`等）を介して行う。
- **WASI互換性**: ゲスト側で `wasi-libc` と Fireball専用の Shim ライブラリをリンクすることで実現する。

### 4.3 マルチモジュール対応
複数のWASMモジュール間の依存関係を解決し、動的にリンクする。 `{MultiModule_Support}`

- **Module Registry**: ロード済みのモジュールを名前で管理する。
- **Dynamic Linking**: インポートセクションに基づき、他モジュールのエクスポートを解決する。

### 4.4 URI/IPCインターフェイス
- **URI**: `fireball://vsoc/control/<instance_id>`
- **メッセージ形式**: 実行制御、状態取得用のKey-Valueプロトコル。詳細定義は IPCルータの仕様に準ずる。

### 4.5 関連コンポーネントとの連携
| コンポーネント | 連携内容 | 参照データ構造 |
| :--- | :--- | :--- |
| **Interpreter** | インタープリタ実行の委譲とホットスポット履歴の取得 | `interpreter`, 履歴バッファ |
| **JIT Compiler** | トレース単位のコンパイル要求とJITコードキャッシュ管理 | `jit_compiler`, `JIT Code Cache` |
| **Wasm Loader** | モジュールロードと `module_view` の管理 | `loader`, `module_view` |
| **Debugger** | デバッグコマンドの処理と実行状態の同期 | `debugger`, `debug_command_queue` |

## 5. 制約達成の方策

### 5.1 性能制約と方策
- **目標**: WAMRインタープリタを上回る実行速度を実現する。
- **方策**: `{LowLatencyJIT}` `{ThreadedInterpreter}` コピーアンドパッチJITによるネイティブ実行と、スレッドインタープリタによる高速フォールバックを組み合わせる。

### 5.2 メモリ制約と方策
- **目標**: 64KB RAM環境で動作させる。
- **方策**: `{JIT_DoubleBuffer_Cache}` `{IndependentHeap}` ダブルバッファによる効率的なキャッシュ管理と、厳密なヒープ分離によりメモリ使用量を制御する。
- **高速アドレス判定**: ゲストRAMを `0x0` から配置し、単一の比較命令でRAMアクセスを判定することで、インタープリタおよびJITのオーバーヘッドを最小化する。

### 5.3 安全性制約と方策
- **目標**: ゲストアプリケーションの暴走を完全に隔離する。
- **方策**: `{MemoryBoundaryCheck}` `{RestrictedPhysicalAccess}` JITコードへの境界チェック埋め込みと、vMMIOによる物理アクセスの制限を行う。

## 6. 設計完了チェックリスト（網羅性確認）
- [x] コンポーネントの責務が明確に定義されているか
- [x] 内部設計（データ構造、ブロック図、クラス、アルゴリズム）が適切に定義されているか
- [x] 内部ブロック図（静的）とシーケンス/状態遷移図（動的）がセットで定義されているか
- [x] 公開APIのメソッド名が英語で記述され、事前/事後条件が明確か
- [x] 非機能制約（性能、メモリ、安全性）に対する具体的な方策が明示されているか
- [x] 設計の交差点（トレードオフ）が解消されているか
- [x] 上位の要求 `{Keyword}` とのトレーサビリティが確保されているか
