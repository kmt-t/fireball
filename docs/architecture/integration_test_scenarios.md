# コンポーネント間 結合テスト仕様書 (Integration Test Specification)

## 1. 目的と対象範囲

本書は、Fireball ハイパーバイザの全 Tier（Tier 1 Core、Tier 2 Runtime、Tier 3 JIT）における各コンポーネント間の結合動作を、独立したリアル WASM バイトコード（WAT より生成されたバイナリ）を用いて包括的に検証する**結合テストシナリオ（End-to-End Component Integration Test Scenarios）**の仕様を定義する。

- **対象 Tier**: Tier 1 Core (`os_coos`, `os_scheduler`, `system_config`, `system_containers`, `system_logging`, `system_syscall`), Tier 1 Interface (`interface_wit`, `ipc_router`, `system_service`), Tier 2 Runtime (`runtime_vsoc`, `runtime_loader`, `runtime_interpreter`, `runtime_vmmio`, `debug_manager`), Tier 3 Platform & JIT (`platform_hal`, `platform_memory`, `jit_compiler`, `jit_runtime`)
- **テストランナー**: `experiments/pysim/run_integration_tests.py`
- **テストスクリプト群**: `experiments/pysim/scenario1_loader_and_memory.py` 〜 `experiments/pysim/scenario10_vmmio_virtual_devices.py`

### 1.1 コンポーネント × 結合テストシナリオ カバレッジマトリクス (Coverage Matrix)

| 分類 / Tier | コンポーネント設計書 | 主な検証責務 | カバーシナリオ |
| :--- | :--- | :--- | :--- |
| **Tier 1 Core** | [`os_coos.md`](../components/tier1_core/os_coos.md) | 協調型マルチタスク、コルーチン実行制御 | Scenario 6, 9 |
| | [`os_scheduler.md`](../components/tier1_core/os_scheduler.md) | Fuel / `yield_every` 境界中断、DIRECT_SWITCH | Scenario 6, 9 |
| | [`system_config.md`](../components/tier1_core/system_config.md) | システム静的定数、スタック・RAM容量制約 | Scenario 1, 10 |
| | [`system_containers.md`](../components/tier1_core/system_containers.md) | `RadixBinaryTreeView` (bswap32), `FlatMapView`, `RingBuffer` | Scenario 1, 4, 5, 9 |
| | [`system_logging.md`](../components/tier1_core/system_logging.md) | 構造化ロギング、LogDictionary、UART 出力 | Scenario 9 |
| | [`system_syscall.md`](../components/tier1_core/system_syscall.md) | `fd_write` 分散ギャザー、`proc_exit`、`fireball_call` 代理 | Scenario 2, 10 |
| **Tier 1 Interface** | [`interface_wit.md`](../components/tier1_interface/interface_wit.md) | WASI Preview 1 ABI、型シグネチャ整合 | Scenario 2 |
| | [`ipc_router.md`](../components/tier1_interface/ipc_router.md) | 3段階ルーティング、RBAC、Zero-Copy 所有権移譲 | Scenario 9 |
| | [`system_service.md`](../components/tier1_interface/system_service.md) | システムサービス呼び出し、WASI トランスポート | Scenario 2 |
| **Tier 2 Runtime** | [`runtime_vsoc.md`](../components/tier2_runtime/runtime_vsoc.md) | 統合 ExecEnv、モジュールリンク、共有メモリ | Scenario 1, 4, 6, 8 |
| | [`runtime_loader.md`](../components/tier2_runtime/runtime_loader.md) | WASM バイナリパース、Active Data/Elem セグメント | Scenario 1, 8 |
| | [`runtime_interpreter.md`](../components/tier2_runtime/runtime_interpreter.md) | CPS 4引数ディスパッチ、全幅メモリ、深い再帰、制御フレーム | Scenario 1〜10 |
| | [`runtime_vmmio.md`](../components/tier2_runtime/runtime_vmmio.md) | Bit 31 RAM Bypass、FlatMap PTE、TLB[16]、仮想デバイス | Scenario 10 |
| | [`debug_manager.md`](../components/tier2_runtime/debug_manager.md) | GDB RSP TCP ソケット接続、ブレークポイント、レジスタ/メモリ改変 | Scenario 7, 8 |
| **Tier 3 Platform** | [`platform_hal.md`](../components/tier3_platform/platform_hal.md) | UartTransport ソケットペア、タイマー | Scenario 2, 7, 9 |
| | [`platform_memory.md`](../components/tier3_platform/platform_memory.md) | リニアメモリページ拡張（`memory.grow`）、MPU 領域保護 | Scenario 1, 4, 8, 10 |
| **Tier 3 JIT** | [`jit_compiler.md`](../components/tier3_jit/jit_compiler.md) | Copy-and-Patch JIT 生成、PIC トレース、差分検証 | Scenario 4, 5, 8 |
| | [`jit_runtime.md`](../components/tier3_jit/jit_runtime.md) | 3面キャッシュ代謝、2-bit Card Marking、UnifiedPC + bswap32 | Scenario 4, 5 |

---

## 2. 結合テストシナリオ一覧

### シナリオ 1: Tier 1 Core + Tier 2 Loader & Linear Memory
- **スクリプト**: [`experiments/pysim/scenario1_loader_and_memory.py`](file:///x:/hotspot/workspace/mysrc/fireball/experiments/pysim/scenario1_loader_and_memory.py)
- **対象コンポーネント**: `runtime_loader`, `runtime_interpreter`, `system_containers` (RadixBinaryTreeView, FlatMapView)
- **WAT シナリオ**:
  - アクティブデータセグメント（Active Data Segments）による ROM 文字列・バイナリ配列の初期配置
  - ゲスト関数からのリニアメモリアクセス（`i32.load` / `i32.store`）
  - 動的メモリ拡張（`memory.grow` / `memory.size`）と拡張ページ（Page 2: offset 131,072）への境界超過アクセス
  - グローバル変数（`global.get` / `global.set`）の変更と状態保持

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-01 | データセグメント初期展開 | WASMロード完了 | メモリ特定番地を参照 | `256` 番地に文字列、`1024` 番地にバイト列が正確に配置される | `runtime_loader.md`, `runtime_interpreter.md` |
| INT-02 | 動的メモリ拡張とページ境界アクセス | 1ページ（64KB）で起動 | `test_grow(2)` を実行し Page 2 へストア | メモリが3ページ（192KB）に拡張され、新領域への書き込み・読み出しが成功する | `runtime_interpreter.md` §3.1 |
| INT-03 | グローバル変数ミューテーション | 初期値 100 | `inc_global(25)`, `inc_global(-50)` | `125`, `75` が返却され、モジュール内グローバル状態が保持される | `runtime_interpreter.md` |

---

### シナリオ 2: Tier 2 Runtime + System Call & WASI I/O
- **スクリプト**: [`experiments/pysim/scenario2_wasi_syscall_io.py`](file:///x:/hotspot/workspace/mysrc/fireball/experiments/pysim/scenario2_wasi_syscall_io.py)
- **対象コンポーネント**: `runtime_interpreter`, `system_syscall`, `wasi`, `system`
- **WAT シナリオ**:
  - WASI 標準 ABI（`wasi_snapshot_preview1`）による `fd_write` および `proc_exit` のインポート解決
  - 複数 iovec 構造体（分散ギャザー I/O: Header + Payload）の stdout フラッシュ
  - `proc_exit` システムコールによるゲストタスク停止および終了コード伝播

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-10 | 分散ギャザー `fd_write` | iovec 配列2要素を構成 | `fd_write(fd=1, iovs, 2)` を実行 | 合計 23 バイトが書き込まれ、ホストトランスポートから `"HELLO-WASI [SYSTEM_OK]\n"` が得られる | `system_syscall.md`, `wasi_preview1_abi.md` |
| INT-11 | ゲスト `proc_exit` 停止 | 実行中 | `proc_exit(42)` を実行 | システムが `halted=True` に遷移し、`exit_code=42` が正確に記録される | `system_syscall.md` §5.7 |

---

### シナリオ 3: Tier 2 Interpreter + Recursion & Indirect Table Dispatch
- **スクリプト**: [`experiments/pysim/scenario3_recursion_and_tables.py`](file:///x:/hotspot/workspace/mysrc/fireball/experiments/pysim/scenario3_recursion_and_tables.py)
- **対象コンポーネント**: `runtime_interpreter` (UnifiedStack, CallFrame)
- **WAT シナリオ**:
  - 再帰フィボナッチ関数（`fib(12)`）による深いコールスタック構築と巻き戻し
  - WASM テーブル（`table` / `elem`）と `call_indirect` による動的関数ポインタディスパッチ（加算・減算・乗算・XOR）
  - `br_table` による多分岐ジャンプテーブル処理

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-20 | 深い再帰呼び出しとフレーム巻き戻し | 統合スタック初期化 | `fib(12)` を実行 | スタックオーバーフローやフレーム破壊を起こさず、正確に `144` を返す | `runtime_interpreter.md` §3.3 |
| INT-21 | テーブル動的ディスパッチ (`call_indirect`) | 関数テーブル登録済み | `dispatch_calc(op_id, a, b)` | 指定した演算関数（add/sub/mul/xor）が型安全にディスパッチされて正しい値を返す | `runtime_interpreter.md` §4.1 |
| INT-22 | 多段ジャンプスイッチ (`br_table`) | ブロックネスト | `test_br_table(selector)` | セレクタ値（0/1/2/default）に応じて対応するブロック外へ正確にジャンプする | `runtime_interpreter.md` §4.1 |

---

### シナリオ 4: Tier 2 Runtime + Tier 3 JIT Hybrid Compilation
- **スクリプト**: [`experiments/pysim/scenario4_hybrid_jit_loop.py`](file:///x:/hotspot/workspace/mysrc/fireball/experiments/pysim/scenario4_hybrid_jit_loop.py)
- **対象コンポーネント**: `runtime_interpreter`, `runtime_engine` (CardMarking, HistoryRing), `jit_compiler`, `jit_runtime`
- **WAT シナリオ**:
  - エラトステネスの篩（素数計算: 1000 未満の素数探索）
  - ホットループ実行時の 2-bit Card Marking による HOT 検出
  - COOS `idle_hook` での JIT トレース自動コンパイルと Active キャッシュバンク格納
  - Tier 2 インタープリタ単独実行と Tier 3 ハイブリッド実行の計算結果完全一致（Differential Testing）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-30 | ホットスポット検出と JIT 自動コンパイル | ループ実行 | `idle_hook` を呼び出す | ループ内の BasicBlock が HOT 昇格し、JIT キャッシュバンクに登録される | `jit_runtime.md`, `runtime_vsoc.md` |
| INT-31 | JIT / インタープリタ差分検証 | 同一ワークロード | Tier 2 と Tier 3 の結果を比較 | 双方が正確に `168`（1000未満の素数の個数）を返し、値が 100% 一致する | `jit_compiler.md`, `runtime_interpreter.md` |

---

### シナリオ 5: Multi-Function UnifiedPC & bswap32 Radix Tree
- **スクリプト**: [`experiments/pysim/scenario5_multimodule_unified_pc.py`](file:///x:/hotspot/workspace/mysrc/fireball/experiments/pysim/scenario5_multimodule_unified_pc.py)
- **対象コンポーネント**: `jit_runtime`, `jit_compiler`, `system_containers` (RadixBinaryTreeView)
- **WAT シナリオ**:
  - 複数関数（3D 内積 `dot3`、マンハッタン距離 `manhattan3`、バッチ処理 `batch_metrics`）の相互呼び出し
  - `UnifiedPC = (func_index << 16) | bytecode_offset` による関数間 PC 衝突防止
  - `bswap32` キー投影による Radix テーブルの完全一様分散と $O(1)$ 高速検索

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-40 | 複数関数にまたがる UnifiedPC JIT トレース | 複数関数がホット化 | `cache.active.traces` を検査 | 異なる `func_index`（上位16bit）を持つ複数の JIT トレースが正常に共存・実行される | `jit_runtime.md` §3.1, §4.1 |
| INT-41 | `RadixBinaryTreeView` による UnifiedPC 検索 | トレース登録済み | `radix_tree.find(unified_pc)` | 全 UnifiedPC に対し $O(1)$ 粗索引＋有界二分探索で正しく JIT トレースが取得できる | `system_containers.md`, `jit_runtime.md` |

---

### シナリオ 6: COOS Cooperative Multitasking & Coroutines
- **スクリプト**: [`experiments/pysim/scenario6_coos_multitask_yield.py`](file:///x:/hotspot/workspace/mysrc/fireball/experiments/pysim/scenario6_coos_multitask_yield.py)
- **対象コンポーネント**: `os_scheduler`, `os_coos`, `runtime_interpreter`
- **WAT シナリオ**:
  - プロデューサ・タスク（メモリへ 100 件のデータ書き込み）
  - コンシューマ・タスク（メモリから 100 件のデータを読み込み合計 50,500 を算出）
  - Fuel 制限（`yield_every=16`）による協調的中断（`yield`）と再開（`resume`）の繰り返し

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-50 | コルーチン協調中断と状態保持 | `yield_every=16` 設定 | `next(coro)` を反復実行 | 途中で複数回中断しながらも、ローカル変数やスタック状態を保持して完走する | `runtime_interpreter.md`, `os_scheduler.md` |
| INT-51 | 共有メモリを介したタスク間データ受け渡し | 同一 ExecEnv 共有 | プロデューサ完走後にコンシューマ実行 | プロデューサが書き込んだデータが正しく読み取られ、合計値 `50500` が得られる | `runtime_vsoc.md`, `runtime_interpreter.md` |

---

### シナリオ 7: GDB Remote Serial Protocol (RSP) Socket Debugger
- **スクリプト**: [`experiments/pysim/scenario7_gdb_socket_debugger.py`](file:///x:/hotspot/workspace/mysrc/fireball/experiments/pysim/scenario7_gdb_socket_debugger.py)
- **対象コンポーネント**: `debug_manager`, `gdb_rsp_protocol`, `runtime_engine` (JIT Cache Flush), `runtime_interpreter`
- **通信シナリオ**:
  - GDB サーバー（`GDBServer`）が実 TCP ソケットでリッスン
  - GDB クライアントからの接続、パケット送受信（`?`, `g`, `G`, `m`, `M`, `Z0`, `z0`, `s`, `c`）
  - 仮想レジスタ（PC, SP, FP, TOS, Locals）の読み出し・動的書き換え
  - ブレークポイント設定とヒット時の `$S05`（SIGTRAP）停止
  - メモリ書き換え時の JIT キャッシュ自動 Flush（`{Debugger_Jit_Flush}`）
  - 単歩ステップ実行（`s`）と正常終了（`$W00`）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-60 | TCP ソケット接続と停止理由クエリ | GDBServer 稼働中 | `?` パケット送信 | クライアント接続が受理され、`$S05#b8`（SIGTRAP）が返却される | `debug_manager.md`, `gdb_rsp_protocol.md` |
| INT-61 | 20 仮想レジスタ読み出し・書き換え | 停止中 | `g` および `G` パケット送信 | 160文字 HEX 列で全仮想レジスタが正しく取得・変更される | `debug_manager.md` §3.3 |
| INT-62 | メモリ検査・書き換えと JIT Flush | 停止中 | `m` および `M` パケット送信 | 指定オフセットのバイト列が読み書きされ、JIT キャッシュ全バンクが無効化される | `debug_manager.md`, `{Debugger_Jit_Flush}` |
| INT-63 | ブレークポイント停止とステップ実行 | 実行中 | `Z0` でブレークポイント設定後 `c` / `s` | 指定 PC で正確にトラップ停止し、単歩ステップ実行で 1 命令進む | `debug_manager.md`, `gdb_rsp_protocol.md` |
| INT-64 | プログラム正常完走とデタッチ | ブレークポイント解除済み | `c` パケット送信 | プログラムが最後まで完走し、`$W00#b7`（終了）が返る | `debug_manager.md` |

---

### シナリオ 8: Storage Coverage (Globals / Locals / Memory Full-Width) & GDB Debugger
- **スクリプト**: [`experiments/pysim/scenario8_comprehensive_storage_coverage.py`](file:///x:/hotspot/workspace/mysrc/fireball/experiments/pysim/scenario8_comprehensive_storage_coverage.py)
- **対象コンポーネント**: `runtime_interpreter`, `debug_manager`, `gdb_rsp_protocol`, `runtime_loader`
- **WAT & デバッグシナリオ**:
  - 全幅メモリアクセス: `i32.store8`/`load8_u`/`load8_s`, `i32.store16`/`load16_u`/`load16_s`, `i32.store`/`load`
  - 可変グローバル変数（`global.get`, `global.set`）と呼び出し間状態永続性
  - ローカル変数パイプライン演算（`local.get`, `local.set`, パラメータ保持）
  - リアルタイム GDB RSP ソケット経由でのブレークポイント捕捉、ローカル変数改変、リニアメモリ書き換えと JIT キャッシュ無効化

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-70 | 全幅メモリ読み書きと符号/ゼロ拡張 | モジュールロード完了 | `test_memory_widths()` 実行 | 8/16/32-bit の符号/ゼロ拡張が正しく反映され期待値 `65757` を返す | `runtime_interpreter.md` §3.4 |
| INT-71 | グローバル変数パイプライン演算 | 初期値 100 | `pipeline_process(5, 200)` | メモリ配列との乗算累積が正確に実行され、グローバル値が `550` $\to$ `1000` へ更新保持される | `runtime_interpreter.md` |
| INT-72 | デバッガからのストレージ動的改変 | ブレークポイント停止中 | `G` でローカル変数変更、`M` でメモリパッチ | 実行コンテキストとリニアメモリが即座に更新され、後続ステップに正確に反映される | `debug_manager.md`, `gdb_rsp_protocol.md` |
| INT-73 | ストレージ改変後の単歩ステップと完走 | 改変完了後 | `s` でステップ実行後 `c` で完走 | 改変後のローカル変数とメモリに基づき正確に完走（結果 `150`）し正常終了する | `debug_manager.md` |

---

### シナリオ 9: Tier 1 Interface IPC Router & Structured Logging
- **スクリプト**: [`experiments/pysim/scenario9_ipc_router_and_logging.py`](file:///x:/hotspot/workspace/mysrc/fireball/experiments/pysim/scenario9_ipc_router_and_logging.py)
- **対象コンポーネント**: `ipc_router`, `system_logging`, `system_containers`, `platform_hal`
- **検証シナリオ**:
  - 3段階ルーティングパイプライン: FlatMapView URI 検索、RBAC ロール権限判定、Zero-Copy 所有権移譲
  - キュー溢れ時の Rollback 復元とターゲットフォールト時の Drop Handler リソース回収
  - `LogDictionary` によるポインタ書式（`%s`）の静的拒絶と、COOS アイドルフラッシュによる UART 出力

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-80 | IPC 3段階ルーティングと所有権移譲 | 送信元 `CLIENT_APP` | `route_message` 実行後 `receive_message` | 所有権が `SENDER_OWNS` $\to$ `IN_FLIGHT` $\to$ `RECEIVER_OWNS` へ遷移する | `ipc_router.md` §3.3 |
| INT-81 | RBAC 権限拒絶とキュー溢れ Rollback | 未許可ロール / キュー満杯 | メッセージ送信 | `ERR_PERMISSION_DENIED` / `ERR_QUEUE_FULL` で安全に拒絶され送信元へロールバック | `ipc_router.md` §4.1 |
| INT-82 | 構造化ロギングと安全書式検証 | LogDictionary 登録 | `log_event` 後 `flush()` | 不正書式 `%s` が拒絶され、ログレベルフィルタを経て UART へ正常出力される | `system_logging.md` |

---

### シナリオ 10: Tier 2 Runtime vMMIO Virtual Devices & Address Translation
- **スクリプト**: [`experiments/pysim/scenario10_vmmio_virtual_devices.py`](file:///x:/hotspot/workspace/mysrc/fireball/experiments/pysim/scenario10_vmmio_virtual_devices.py)
- **対象コンポーネント**: `runtime_vmmio`, `system_syscall`, `platform_memory`, `system_config`
- **検証シナリオ**:
  - Bit 31 RAM Bypass フラグ: ゲストリニア RAM（Bit 31 == 0）の $O(1)$ 高速パス
  - 仮想デバイス（FC=0xC）、共有メモリ（FC=0xE）、物理パススルー（FC=0xF）の PTE マッピング
  - 4-bit Folding XOR Hash による Direct-Mapped Software TLB[16] ヒット/ミス遷移
  - タスク間共有メモリの所有権分離と `TRAP_OWNER_MISMATCH` 検知

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INT-90 | Bit 31 RAM Bypass 高速パス | リニア RAM アドレス | `access()` 実行 | ページテーブルを介さず `OK_GUEST_RAM` で即時バイパスされる | `runtime_vmmio.md` §3.3 |
| INT-91 | 仮想デバイス書き込みとハンドラディスパッチ | デバイスページ登録済み | `access()` で書き込み | `OK_SYSCALL` が返り登録ハンドラが呼び出される | `runtime_vmmio.md` §4.1 |
| INT-92 | 16エントリ Direct-Mapped TLB キャッシュ | 同一ページ反復アクセス | 連続 `access()` | 2回目以降が TLB ヒットとなり `tlb_hits` が増加する | `runtime_vmmio.md` §4.1 |
| INT-93 | タスク間共有メモリ所有権分離 | Task 1 が Task 2 SHM アクセス | `access()` 実行 | `TRAP_OWNER_MISMATCH` で安全にトラップ遮断される | `runtime_vmmio.md` §4.6 |

---

## 3. 実行方法と検証結果

```bash
# 全結合テストシナリオの一括実行
uv run --system-certs --with wasmtime python experiments/pysim/run_integration_tests.py
```

### 検証実績
- **全 10 シナリオ**: **10/10 PASSED** (約 6.5 秒)
- **全 18 コンポーネント 100% カバレッジ**: Tier 1 Core、Tier 1 Interface、Tier 2 Runtime、Tier 3 Platform & JIT の全コンポーネントを実動検証。
- **完全差分検証**: 全シナリオにおいて、純粋インタープリタ実行と JIT 実行の出力がバイト単位・値単位で 100% 一致。
- **GDB リモートデバッグ & vMMIO & IPC**: 実ソケット GDB 対話、仮想 MMIO 変換、Zero-Copy IPC ルーティングが完全動作。
