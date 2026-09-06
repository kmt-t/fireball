# コンポーネント間 結合テスト仕様書 (Integration Test Specification)

## 1. 目的と対象範囲

本書は、Fireball ハイパーバイザの全 Tier（Tier 1 Core、Tier 2 Runtime、Tier 3 Platform & JIT）における各コンポーネント間の結合動作を、独立したリアル WASM バイトコード（WAT より生成されたバイナリ）を用いて包括的に検証する**システム結合テストシナリオ（End-to-End Component Integration Test Scenarios）**の仕様を定義する。

### 1.1 本番実装と参照実装の位置づけ

本書に定めるテストシナリオ群は、Fireball ハイパーバイザのアーキテクチャ受入基準（Acceptance Criteria）であり、特定の実装言語や実行環境に従属するものではない。

- **本番実装（Production Implementation / C++ Hypervisor）**:
  - 本書で定義されるシナリオ仕様（WAT ゲストバイナリ、入出力シーケンス、アーキテクチャ不変条件）は、Fireball 本番ハイパーバイザ（C++23 実装）が満たすべき受入テストスイート（Acceptance Test Suite）の正本仕様となる。
  - 各コンポーネントの C++ 実装に対し、ホスト結合ハーネス等を介して本シナリオ群を検証する。
- **参照実装（Reference Implementation / Python Simulator）**:
  - アーキテクチャの早期妥当性確認、状態遷移の探索、および Gotchas（実装上の勘所・不変条件）の抽出を目的とした Python 製の参照シミュレータ（`experiments/pysim`）。
  - 各シナリオには、この参照実装上で動作する実行可能なリファレンススクリプト（`experiments/pysim/scenarios/`）が提供されており、仕様が実行可能（Executable Specification）であることを実証している。

- **対象 Tier**: Tier 1 Core (`os_coos`, `os_scheduler`, `system_config`, `system_containers`, `system_logging`, `system_syscall`), Tier 1 Interface (`interface_wit`, `ipc_router`, `system_service`), Tier 2 Runtime (`runtime_vsoc`, `runtime_loader`, `runtime_interpreter`, `runtime_vmmio`, `debug_manager`), Tier 3 Platform & JIT (`platform_hal`, `platform_memory`, `jit_compiler`, `jit_runtime`)
- **参照実装テストスイート**: `experiments/pysim/scenarios/`
- **参照テストランナー**: [`run_all.py`](experiments/pysim/scenarios/run_all.py)

### 1.2 コンポーネント × 結合テストシナリオ カバレッジマトリクス (Coverage Matrix)

| 分類 / Tier | コンポーネント設計書 | 主な検証責務 | カバーシナリオ |
| :--- | :--- | :--- | :--- |
| **Tier 1 Core** | [`os_coos.md`](docs/components/tier1_core/os_coos.md) | 協調型マルチタスク、コルーチン実行制御 | Scenario 6, 9 |
| **Tier 1 Core** | [`os_scheduler.md`](docs/components/tier1_core/os_scheduler.md) | Fuel / `yield_every` 境界中断、DIRECT_SWITCH | Scenario 6, 9 |
| **Tier 1 Core** | [`system_config.md`](docs/components/tier1_core/system_config.md) | システム静的定数、スタック・RAM容量制約 | Scenario 1, 10 |
| **Tier 1 Core** | [`system_containers.md`](docs/components/tier1_core/system_containers.md) | `RadixBinaryTreeView` (bswap32), `FlatMapView`, `RingBuffer` | Scenario 1, 4, 5, 9 |
| | [`system_logging.md`](docs/components/tier1_core/system_logging.md) | 構造化ロギング、LogDictionary、UART 出力 | Scenario 9 |
| | [`system_syscall.md`](docs/components/tier1_core/system_syscall.md) | `fd_write` 分散ギャザー、`proc_exit`、`fireball_call` 代理 | Scenario 2, 10, 11 |
| **Tier 1 Interface** | [`interface_wit.md`](docs/components/tier1_interface/interface_wit.md) | WASI Preview 1 ABI、型シグネチャ整合 | Scenario 2, 11 |
| | [`ipc_router.md`](docs/components/tier1_interface/ipc_router.md) | 3段階ルーティング、RBAC、Zero-Copy 所有権移譲 | Scenario 9 |
| | [`system_service.md`](docs/components/tier1_interface/system_service.md) | システムサービス呼び出し、WASI トランスポート | Scenario 2, 11 |
| **Tier 2 Runtime** | [`runtime_vsoc.md`](docs/components/tier2_runtime/runtime_vsoc.md) | 統合 ExecEnv、モジュールリンク、共有メモリ | Scenario 1, 4, 6, 8 |
| | [`runtime_loader.md`](docs/components/tier2_runtime/runtime_loader.md) | WASM バイナリパース、Active Data/Elem セグメント | Scenario 1, 8 |
| | [`runtime_interpreter.md`](docs/components/tier2_runtime/runtime_interpreter.md) | CPS 4引数ディスパッチ、全幅メモリ、深い再帰、制御フレーム | Scenario 1〜11 |
| | [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md) | Bit 31 RAM Bypass、FlatMap PTE、TLB[16]、仮想デバイス | Scenario 10 |
| | [`debug_manager.md`](docs/components/tier2_runtime/debug_manager.md) | GDB RSP TCP ソケット接続、ブレークポイント、レジスタ/メモリ改変 | Scenario 7, 8 |
| **Tier 3 Platform** | [`platform_hal.md`](docs/components/tier3_platform/platform_hal.md) | GPIO, I2C, SPI, Timer, UartTransport | Scenario 2, 7, 9, 11 |
| | [`platform_memory.md`](docs/components/tier3_platform/platform_memory.md) | リニアメモリページ拡張（`memory.grow`）、MPU 領域保護 | Scenario 1, 4, 8, 10 |
| **Tier 3 JIT** | [`jit_compiler.md`](docs/components/tier3_jit/jit_compiler.md) | Copy-and-Patch JIT 生成、PIC トレース、差分検証 | Scenario 4, 5, 8 |
| | [`jit_runtime.md`](docs/components/tier3_jit/jit_runtime.md) | 3面キャッシュ代謝、2-bit Card Marking、UnifiedPC + bswap32 | Scenario 4, 5 |

### 1.3 仕様キーワード・不変条件カバレッジ追跡表 (Requirements Traceability Matrix: RTM)

各コンポーネント設計書に定義されている仕様キーワード、アーキテクチャ不変条件（Invariants）、およびエッジケース要件に対する結合テスト（Scenario 1〜11）の実動網羅状況：

| 仕様キーワード / 不変条件 | 定義元設計書 | 仕様上の定義・要件 | カバーテスト ID | 実装実証 |
| :--- | :--- | :--- | :--- | :---: |
| `RadixBinaryTreeView_bswap32` | `system_containers.md`, `jit_runtime.md` | UnifiedPC（`func_idx << 20 \| pc`）の bswap32 によるリトルエンディアン上位集約インデックス検索 | `INT-40`, `INT-41` | ✅ PASS |
| `FlatMapView_BinarySearch` | `system_containers.md`, `ipc_router.md` | 静的ソート配列に対する $O(\log N)$ バイナリサーチ（動的割当なし） | `INT-01`, `INT-80` | ✅ PASS |
| `RingBuffer_Overwrite` | `system_containers.md`, `system_logging.md` | 静的容量リングバッファ、満杯時の最古エントリ自動上書き | `INT-82` | ✅ PASS |
| `BitView_CardMarking` | `system_containers.md`, `jit_runtime.md` | 関数ごと 8バイト/カード 2-bit カードマーキング（UNEXEC $\to$ EXEC $\to$ HOT $\to$ COMPILED） | `INT-30`, `INT-31` | ✅ PASS |
| `DirectSwitch` | `os_coos.md`, `os_scheduler.md` | コンテキストスイッチスタック退避なしの CPS 関数呼び出し継続 | `INT-50`, `INT-51` | ✅ PASS |
| `FuelExhaustion_Yield` | `os_scheduler.md`, `os_coos.md` | Fuel 枯渇（トレース境界での `quantum` 判定）での決定論的な中断と再開——判定・発行は駆動する側の責務 | `INT-50` | ✅ PASS |
| `DictionaryBasedIPC` | `system_logging.md` | 静的 LogDictionary、危険書式（`%s` / `%p`）の登録時静的拒絶 | `INT-82` | ✅ PASS |
| `BufferedLogging` | `system_logging.md` | 実行時リングバッファ蓄積 $\to$ COOS `idle_hook` での一括 UART フラッシュ | `INT-82` | ✅ PASS |
| `WASI_ScatteredIO` | `system_syscall.md`, `interface_wit.md` | 分散ギャザー `fd_write` / スキャッター `fd_read` による多要素 iovec 転送 | `INT-10`, `INT-104` | ✅ PASS |
| `Syscall_ProcExit` | `system_syscall.md` | `proc_exit` システムコールによるゲストタスク停止および終了コード伝播 | `INT-11` | ✅ PASS |
| `ThreeStageRouting` | `ipc_router.md` | Stage 1 URI検索 $\to$ Stage 2 RBAC判定 $\to$ Stage 3 Zero-Copy CSP Rendezvous 所有権移譲 | `INT-80`, `INT-81` | ✅ PASS |
| `PreflightRejection` | `ipc_router.md` | Revoke前の静的チェック（RBAC拒否・メッセージサイズ超過）失敗時、所有権は送信側から一度も動かない | `INT-81` | ✅ PASS |
| `RAM_Bypass_Bit31` | `runtime_vmmio.md` | Bit 31 == 0 アドレスに対するページテーブル不使用 $O(1)$ 高速バイパス | `INT-90` | ✅ PASS |
| `DirectMappedTLB16` | `runtime_vmmio.md` | 20-bit VPN の 4-bit Folding XOR Hash による Direct-Mapped TLB キャッシュ | `INT-92` | ✅ PASS |
| `OwnerMismatchTrap` | `runtime_vmmio.md` | タスク間共有メモリ（FC=0xE）の所有権移動に伴うアンマップによる未登録ページフォルト（`TRAP_UNREGISTERED_PAGE`）遮断 | `INT-93` | ✅ PASS |
| `ActiveDataSegments` | `runtime_loader.md` | モジュールロード時のアクティブデータセグメント自動リニアメモリ展開 | `INT-01` | ✅ PASS |
| `CPS_4Args` | `runtime_interpreter.md` | `ip, stack_bot, local_base, tos` 4引数による CPS 関数ポインタディスパッチ | `INT-01`〜`INT-105` | ✅ PASS |
| `SignZeroExtension` | `runtime_interpreter.md` | 8/16/32-bit メモリ読み書きにおける符号付き・符号なしゼロ/符号拡張の完全性 | `INT-70` | ✅ PASS |
| `ControlFrameCleanup` | `runtime_interpreter.md` | `br_table` / `block` / `loop` / `if` 偽分岐時のスタックフレーム不変性・リーク防止 | `INT-20`, `INT-22` | ✅ PASS |
| `RSPMinimalSet` | `debug_manager.md`, `gdb_rsp_protocol.md` | GDB RSP 最小コマンドセット（`?`, `g/G`, `m/M`, `Z0/z0`, `s`, `c`）の実ソケット対話 | `INT-60`〜`INT-64` | ✅ PASS |
| `Debugger_Jit_Flush` | `debug_manager.md`, `jit_runtime.md` | デバッガからのメモリ書き込み（`M` パケット）時の JIT キャッシュ全バンク即時無効化 | `INT-62`, `INT-72` | ✅ PASS |
| `HAL_PeripheralDrivers` | `platform_hal.md` | GPIO（入出力・エッジIRQ）、I2C（LM75）、SPI（EEPROM）、Timer | `INT-100`〜`INT-102` | ✅ PASS |
| `WASI_InMemVFS` | `interface_wit.md`, `system_syscall.md` | WASI In-Memory VFS（`fd_seek`, `fd_read`, `fd_write`, `random_get`, `clock_time_get`） | `INT-103`〜`INT-105` | ✅ PASS |
| `CopyAndPatch_JIT` | `jit_compiler.md` | ステンシル展開による高速 Copy-and-Patch JIT コード生成 | `INT-30`, `INT-40` | ✅ PASS |
| `TraceBoundaryInvariant` | `jit_compiler.md` | トレース境界でのスタック自己完結性、メモリ同期、およびフォールバック | `INT-31`, `INT-41` | ✅ PASS |
| `ThreeBankCacheEviction` | `jit_runtime.md` | Active / Warm / Oldest 3面バンク代謝と Oldest ヒット時の Active 昇格 | `INT-31`, `INT-41` | ✅ PASS |

---

## 2. 結合テストシナリオ一覧

### シナリオ 1: Tier 1 Core + Tier 2 Loader & Linear Memory
- **対象コンポーネント**: `runtime_loader`, `runtime_interpreter`, `system_containers` (RadixBinaryTreeView, FlatMapView)
- **参照実装スクリプト (Reference Script)**: [`scenario1_loader_and_memory.py`](experiments/pysim/scenarios/scenario1_loader_and_memory.py)
- **WAT シナリオ**:
  - アクティブデータセグメント（Active Data Segments）による ROM 文字列・バイナリ配列の初期配置
  - ゲスト関数からのリニアメモリアクセス（`i32.load` / `i32.store`）
  - 動的メモリ拡張（`memory.grow` / `memory.size`）と拡張ページ（Page 2: offset 131,072）への境界超過アクセス
  - グローバル変数（`global.get` / `global.set`）の変更と状態保持

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-01 | データセグメント初期展開 | WASMロード完了 | メモリ特定番地を参照 | `256` 番地に文字列、`1024` 番地にバイト列が正確に配置される | `ActiveDataSegments`, `ThreadedInterpreter` |
| INT-02 | 動的メモリ拡張とページ境界アクセス | 1ページ（64KB）で起動 | `test_grow(2)` を実行し Page 2 へストア | メモリが3ページ（192KB）に拡張され、新領域への書き込み・読み出しが成功する | `WasmPageAlignment`, `MemoryBoundaryCheck` |
| INT-03 | グローバル変数ミューテーション | 初期値 100 | `inc_global(25)`, `inc_global(-50)` | `125`, `75` が返却され、モジュール内グローバル状態が保持される | `ThreadedInterpreter` |

---

### シナリオ 2: Tier 2 Runtime + System Call & WASI I/O
- **対象コンポーネント**: `runtime_interpreter`, `system_syscall`, `wasi`, `system`
- **参照実装スクリプト (Reference Script)**: [`scenario2_wasi_syscall_io.py`](experiments/pysim/scenarios/scenario2_wasi_syscall_io.py)
- **WAT シナリオ**:
  - WASI 標準 ABI（`wasi_snapshot_preview1`）による `fd_write` および `proc_exit` のインポート解決
  - 複数 iovec 構造体（分散ギャザー I/O: Header + Payload）の stdout フラッシュ
  - `proc_exit` システムコールによるゲストタスク停止および終了コード伝播

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-10 | 分散ギャザー `fd_write` | iovec 配列2要素を構成 | `fd_write(fd=1, iovs, 2)` を実行 | 合計 23 バイトが書き込まれ、ホストトランスポートから `"HELLO-WASI [SYSTEM_OK]\n"` が得られる | `WASI_ScatteredIO` |
| INT-11 | ゲスト `proc_exit` 停止 | 実行中 | `proc_exit(42)` を実行 | システムが `halted=True` に遷移し、`exit_code=42` が正確に記録される | `Syscall_ProcExit` |

---

### シナリオ 3: Tier 2 Interpreter + Recursion & Indirect Table Dispatch
- **対象コンポーネント**: `runtime_interpreter` (UnifiedStack, CallFrame)
- **参照実装スクリプト (Reference Script)**: [`scenario3_recursion_and_tables.py`](experiments/pysim/scenarios/scenario3_recursion_and_tables.py)
- **WAT シナリオ**:
  - 再帰フィボナッチ関数（`fib(12)`）による深いコールスタック構築と巻き戻し
  - WASM テーブル（`table` / `elem`）と `call_indirect` による動的関数ポインタディスパッチ（加算・減算・乗算・XOR）
  - `br_table` による多分岐ジャンプテーブル処理

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-20 | 深い再帰呼び出しとフレーム巻き戻し | 統合スタック初期化 | `fib(12)` を実行 | スタックオーバーフローやフレーム破壊を起こさず、正確に `144` を返す | `ThreadedInterpreter`, `ControlFrameCleanup` |
| INT-21 | テーブル動的ディスパッチ (`call_indirect`) | 関数テーブル登録済み | `dispatch_calc(op_id, a, b)` | 指定した演算関数（add/sub/mul/xor）が型安全にディスパッチされて正しい値を返す | `ThreadedInterpreter`, `CPS_4Args` |
| INT-22 | 多段ジャンプスイッチ (`br_table`) | ブロックネスト | `test_br_table(selector)` | セレクタ値（0/1/2/default）に応じて対応するブロック外へ正確にジャンプする | `ControlFrameCleanup` |

---

### シナリオ 4: Tier 2 Runtime + Tier 3 JIT Hybrid Compilation
- **対象コンポーネント**: `runtime_interpreter`, `runtime_engine` (CardMarking, HistoryRing), `jit_compiler`, `jit_runtime`
- **参照実装スクリプト (Reference Script)**: [`scenario4_hybrid_jit_loop.py`](experiments/pysim/scenarios/scenario4_hybrid_jit_loop.py)
- **WAT シナリオ**:
  - エラトステネスの篩（素数計算: 1000 未満の素数探索）
  - ホットループ実行時の 2-bit Card Marking による HOT 検出
  - COOS `idle_hook` での JIT トレース自動コンパイルと Active キャッシュバンク格納
  - Tier 2 インタープリタ単独実行と Tier 3 ハイブリッド実行の計算結果完全一致（Differential Testing）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-30 | ホットスポット検出と JIT 自動コンパイル | ループ実行 | `idle_hook` を呼び出す | ループ内の BasicBlock が HOT 昇格し、JIT キャッシュバンクに登録される | `BitView_CardMarking`, `JIT_MultiBuffer_Cache` |
| INT-31 | JIT / インタープリタ差分検証 | 同一ワークロード | Tier 2 と Tier 3 の結果を比較 | 双方が正確に `168`（1000未満の素数の個数）を返し、値が 100% 一致する | `JIT_CopyAndPatch`, `TraceBoundaryInvariant` |

---

### シナリオ 5: Multi-Function UnifiedPC & bswap32 Radix Tree
- **対象コンポーネント**: `jit_runtime`, `jit_compiler`, `system_containers` (RadixBinaryTreeView)
- **参照実装スクリプト (Reference Script)**: [`scenario5_multimodule_unified_pc.py`](experiments/pysim/scenarios/scenario5_multimodule_unified_pc.py)
- **WAT シナリオ**:
  - 複数関数（3D 内積 `dot3`、マンハッタン距離 `manhattan3`、バッチ処理 `batch_metrics`）の相互呼び出し
  - `UnifiedPC = (func_index << 16) | bytecode_offset` による関数間 PC 衝突防止
  - `bswap32` キー投影による Radix テーブルの完全一様分散と $O(1)$ 高速検索

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-40 | 複数関数にまたがる UnifiedPC JIT トレース | 複数関数がホット化 | `cache.active.traces` を検査 | 異なる `func_index`（上位16bit）を持つ複数の JIT トレースが正常に共存・実行される | `RadixBinaryTreeView_bswap32` |
| INT-41 | `RadixBinaryTreeView` による UnifiedPC 検索 | トレース登録済み | `radix_tree.find(unified_pc)` | 全 UnifiedPC に対し $O(1)$ 粗索引＋有界二分探索で正しく JIT トレースが取得できる | `RadixBinaryTreeView_bswap32`, `ThreeBankCacheEviction` |

---

### シナリオ 6: COOS Cooperative Multitasking & Fuel-Limited Quantum Stepping
- **対象コンポーネント**: `os_scheduler`, `os_coos`, `runtime_interpreter`
- **参照実装スクリプト (Reference Script)**: [`scenario6_coos_multitask_yield.py`](experiments/pysim/scenarios/scenario6_coos_multitask_yield.py)
- **WAT シナリオ**:
  - プロデューサ・タスク（メモリへ 100 件のデータ書き込み）
  - コンシューマ・タスク（メモリから 100 件のデータを読み込み合計 50,500 を算出）
  - Fuel 制限（`quantum=16`）による決定論的な中断の繰り返し。中断・再開の意思決定はランタイム（vSoC / COOS）側の責務であり、Fireball インタープリタ（`Interpreter`）自身はコルーチンではない——`step()` は境界に達するたびに値を返却し、そのつど中断（`co_yield`）するかどうかを決定するのは呼び出し側（ランタイム）である

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-50 | Fuel 境界での決定論的中断と状態保持 | `quantum=16` 設定 | `Interpreter.step()` を `finished` になるまで反復実行 | 途中で複数回中断しながらも、ローカル変数やスタック状態を保持して完走する | `CooperativeMultitasking`, `FuelExhaustion_Yield` |
| INT-51 | 共有メモリを介したタスク間データ受け渡し | 同一 ExecEnv 共有 | プロデューサ完走後にコンシューマ実行 | プロデューサが書き込んだデータが正しく読み取られ、合計値 `50500` が得られる | `CooperativeMultitasking`, `DirectContextSwitch` |

---

### シナリオ 7: GDB Remote Serial Protocol (RSP) Socket Debugger
- **対象コンポーネント**: `debug_manager`, `gdb_rsp_protocol`, `runtime_engine` (JIT Cache Flush), `runtime_interpreter`
- **参照実装スクリプト (Reference Script)**: [`scenario7_gdb_socket_debugger.py`](experiments/pysim/scenarios/scenario7_gdb_socket_debugger.py)
- **通信シナリオ**:
  - GDB サーバー（`GDBServer`）が実 TCP ソケットでリッスン
  - GDB クライアントからの接続、パケット送受信（`?`, `g`, `G`, `m`, `M`, `Z0`, `z0`, `s`, `c`）
  - 仮想レジスタ（PC, SP, FP, TOS, Locals）の読み出し・動的書き換え
  - ブレークポイント設定とヒット時の `$S05`（SIGTRAP）停止
  - メモリ書き換え時の JIT キャッシュ自動 Flush（`{Debugger_Jit_Flush}`）
  - 単歩ステップ実行（`s`）と正常終了（`$W00`）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-60 | TCP ソケット接続と停止理由クエリ | GDBServer 稼働中 | `?` パケット送信 | クライアント接続が受理され、`$S05#b8`（SIGTRAP）が返却される | `RSPMinimalSet` |
| INT-61 | 20 仮想レジスタ読み出し・書き換え | 停止中 | `g` および `G` パケット送信 | 160文字 HEX 列で全仮想レジスタが正しく取得・変更される | `RSPMinimalSet` |
| INT-62 | メモリ検査・書き換えと JIT Flush | 停止中 | `m` および `M` パケット送信 | 指定オフセットのバイト列が読み書きされ、JIT キャッシュ全バンクが無効化される | `Debugger_Jit_Flush` |
| INT-63 | ブレークポイント停止とステップ実行 | 実行中 | `Z0` でブレークポイント設定後 `c` / `s` | 指定 PC で正確にトラップ停止し、単歩ステップ実行で 1 命令進む | `RSPMinimalSet` |
| INT-64 | プログラム正常完走とデタッチ | ブレークポイント解除済み | `c` パケット送信 | プログラムが最後まで完走し、`$W00#b7`（終了）が返る | `RSPMinimalSet` |

---

### シナリオ 8: Storage Coverage (Globals / Locals / Memory Full-Width) & GDB Debugger
- **対象コンポーネント**: `runtime_interpreter`, `debug_manager`, `gdb_rsp_protocol`, `runtime_loader`
- **参照実装スクリプト (Reference Script)**: [`scenario8_comprehensive_storage_coverage.py`](experiments/pysim/scenarios/scenario8_comprehensive_storage_coverage.py)
- **WAT & デバッグシナリオ**:
  - 全幅メモリアクセス: `i32.store8`/`load8_u`/`load8_s`, `i32.store16`/`load16_u`/`load16_s`, `i32.store`/`load`
  - 可変グローバル変数（`global.get`, `global.set`）と呼び出し間状態永続性
  - ローカル変数パイプライン演算（`local.get`, `local.set`, パラメータ保持）
  - リアルタイム GDB RSP ソケット経由でのブレークポイント捕捉、ローカル変数改変、リニアメモリ書き換えと JIT キャッシュ無効化

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-70 | 全幅メモリ読み書きと符号/ゼロ拡張 | モジュールロード完了 | `test_memory_widths()` 実行 | 8/16/32-bit の符号/ゼロ拡張が正しく反映され期待値 `65757` を返す | `SignZeroExtension` |
| INT-71 | グローバル変数パイプライン演算 | 初期値 100 | `pipeline_process(5, 200)` | メモリ配列との乗算累積が正確に実行され、グローバル値が `550` $\to$ `1000` へ更新保持される | `ThreadedInterpreter` |
| INT-72 | デバッガからのストレージ動的改変 | ブレークポイント停止中 | `G` でローカル変数変更、`M` でメモリパッチ | 実行コンテキストとリニアメモリが即座に更新され、後続ステップに正確に反映される | `RSPMinimalSet`, `Debugger_Jit_Flush` |
| INT-73 | ストレージ改変後の単歩ステップと完走 | 改変完了後 | `s` でステップ実行後 `c` で完走 | 改変後のローカル変数とメモリに基づき正確に完走（結果 `150`）し正常終了する | `RSPMinimalSet` |

---

### シナリオ 9: Tier 1 Interface IPC Router & Structured Logging
- **対象コンポーネント**: `ipc_router`, `system_logging`, `system_containers`, `platform_hal`
- **参照実装スクリプト (Reference Script)**: [`scenario9_ipc_router_and_logging.py`](experiments/pysim/scenarios/scenario9_ipc_router_and_logging.py)
- **検証シナリオ**:
  - 3段階ルーティングパイプライン: FlatMapView URI 検索、RBAC ロール権限判定、Zero-Copy 所有権移譲
  - キュー溢れ時の Rollback 復元とターゲットフォールト時の Drop Handler リソース回収
  - `LogDictionary` によるポインタ書式（`%s`）の静的拒絶と、COOS アイドルフラッシュによる UART 出力

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-80 | IPC 3段階ルーティングと所有権移譲 | 送信元 `RUNTIME` | `send` 実行後 `receive` | 所有権が `SENDER_OWNS` $\to$ `IN_FLIGHT` $\to$ `RECEIVER_OWNS` へ遷移する | `ThreeStageRouting` |
| INT-81 | RBAC 権限拒絶とメッセージサイズ超過 | 未許可ロール / kv_pair数が8個を超過 | メッセージ送信 | `ERR_PERMISSION_DENIED` / `ERR_MSG_TOO_LARGE` で安全に拒絶され、所有権は送信側のまま維持される | `PreflightRejection` |
| INT-82 | 構造化ロギングと安全書式検証 | LogDictionary 登録 | `log_event` 後 `flush()` | 不正書式 `%s` が拒絶され、ログレベルフィルタを経て UART へ正常出力される | `DictionaryBasedIPC`, `BufferedLogging` |

---

### シナリオ 10: Tier 2 Runtime vMMIO Virtual Devices & Address Translation
- **対象コンポーネント**: `runtime_vmmio`, `system_syscall`, `platform_memory`, `system_config`
- **参照実装スクリプト (Reference Script)**: [`scenario10_vmmio_virtual_devices.py`](experiments/pysim/scenarios/scenario10_vmmio_virtual_devices.py)
- **検証シナリオ**:
  - Bit 31 RAM Bypass フラグ: ゲストリニア RAM（Bit 31 == 0）の $O(1)$ 高速パス
  - 仮想デバイス（FC=0xC）、共有メモリ（FC=0xE）、物理パススルー（FC=0xF）の PTE マッピング
  - 4-bit Folding XOR Hash による Direct-Mapped Software TLB[16] ヒット/ミス遷移
  - タスク間共有メモリの所有権分離とアンマップによる未登録ページ遮断

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-90 | Bit 31 RAM Bypass 高速パス | リニア RAM アドレス | `access()` 実行 | ページテーブルを介さず `OK_GUEST_RAM` で即時バイパスされる | `RAM_Bypass_Bit31` |
| INT-91 | 仮想デバイス書き込みとハンドラディスパッチ | デバイスページ登録済み | `access()` で書き込み | `OK_SYSCALL` が返り登録ハンドラが呼び出される | `vMMIO_TrapAndEmulate` |
| INT-92 | 16エントリ Direct-Mapped TLB キャッシュ | 同一ページ反復アクセス | 連続 `access()` | 2回目以降が TLB ヒットとなり `tlb_hits` が増加する | `DirectMappedTLB16` |
| INT-93 | タスク間共有メモリ所有権分離 | 非所有（未マッピング）タスクのSHMアクセス | `access()` 実行 | `TRAP_UNREGISTERED_PAGE` で安全にトラップ遮断される | `OwnerMismatchTrap` |

---

### シナリオ 11: HAL Peripheral Drivers & WASI Preview 1 Full Dummy Stack
- **対象コンポーネント**: `platform_hal`, `interface_wit`, `system_service`, `system_syscall`, `runtime_interpreter`
- **参照実装スクリプト (Reference Script)**: [`scenario11_hal_and_wasi_drivers.py`](experiments/pysim/scenarios/scenario11_hal_and_wasi_drivers.py)
- **検証シナリオ**:
  - **HAL 周辺機器ダミードライバ**:
    - GPIO コントローラ（16ピン）: 入出力モード設定、ピン読み出し/書き込み、エッジ割り込み IRQ コールバック
    - I2C バス・温度センサ（LM75 `0x48`）: 16-bit 温度レジスタ読み出し（`25.5℃` $\to$ `0x1980`）および設定レジスタ書き換え
    - SPI バス・4KB EEPROM（25LC040）: WREN(0x06), WRITE(0x02), READ(0x03) による全二重トランザクション
    - タイマードライバ: 単調増加ナノ秒クロック（`monotonic_ns`）およびハードウェア Tick 進行
  - **WASI Preview 1 インメモリスタック**:
    - 仮想ファイルディスクリプタ（`fd_read`, `fd_write`, `fd_seek`: SET/CUR/END）
    - 標準入出力（stdin バッファ入力、stdout/stderr キャプチャ）
    - ユーティリティ（`random_get` 乱数エントロピ充填、`clock_time_get` 高精度タイムスタンプ）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-100 | HAL GPIO 入出力とエッジ IRQ | GPIO ドライバ初期化 | ピン出力設定後値トグル | ピン状態が正しく反転し、登録された IRQ コールバックがトリガされる | `HAL_PeripheralDrivers` |
| INT-101 | HAL I2C 仮想温度センサ読み書き | I2C バス初期化 | 0x48 のレジスタ R/W | 温度値 `0x1980` が読み出され、設定レジスタが正常に更新される | `HAL_PeripheralDrivers` |
| INT-102 | HAL SPI 4KB EEPROM 書き込み・読み出し | SPI ドライバ初期化 | WREN $\to$ Write $\to$ Read | 指定アドレスに書き込んだバイト列が 100% 一致して読み出される | `HAL_PeripheralDrivers` |
| INT-103 | WASI In-Memory VFS シークと読み書き | 仮想 FD 3 (config.ini) | `fd_seek` 後 `fd_read`/`fd_write` | ファイルポインタが移動し、指定位置から正確に読み書きできる | `WASI_InMemVFS` |
| INT-104 | WASI 標準ストリームバッファリング | stdin にデータ充填 | `fd_read(fd=0)` 実行 | ストリームバッファから指定バイト数が正しく読み込まれる | `WASI_ScatteredIO` |
| INT-105 | WASI 乱数取得 & 高精度クロック | ゲストリニアメモリ指定 | `random_get`, `clock_time_get` | 乱数バッファが充填され、単調増加ナノ秒タイムスタンプが得られる | `WASI_InMemVFS` |

---

## 3. 実装検証環境と実証実績

本書に定義された結合テストシナリオは、Fireball ハイパーバイザの本番実装および参照実装に対する共通の受入基準（Acceptance Criteria）として機能する。

### 3.1 参照実装（pysim）による検証実績

Python 製の参照シミュレータ環境（`experiments/pysim`）を用いて全 11 シナリオの実動検証が完了している。

- **全 11 シナリオ**: **11/11 PASSED** (約 6.3 秒)
- **全 18 コンポーネント 100% カバレッジ**: Tier 1 Core、Tier 1 Interface、Tier 2 Runtime、Tier 3 Platform & JIT の全コンポーネントを実動検証。
- **完全差分検証**: 全シナリオにおいて、純粋インタープリタ実行と JIT 実行の出力がバイト単位・値単位で 100% 一致。
- **HAL & WASI 完全スタック**: GPIO / I2C / SPI / Timer ダミードライバおよび WASI In-Memory VFS / Random / Clock が完全実動。

#### 参照テストランナーの実行方法

```bash
# 参照実装による全結合テストシナリオの一括実行
uv run --system-certs --with wasmtime python experiments/pysim/scenarios/run_all.py
```

### 3.2 本番実装（C++ Hypervisor）への適用方針

本番ハイパーバイザ（C++23 実装）の開発においては、本書の各シナリオで定義された WAT ゲストモジュール、前提条件、入力、および期待結果・不変条件をそのまま受入テストケースとして適用する。ホストテストハーネス上で同一の WAT バイナリを実行し、参照実装と同等の入出力整合性および状態遷移不変条件を満たすことを検証する。
