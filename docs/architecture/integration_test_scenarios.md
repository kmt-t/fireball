# コンポーネント間 結合テスト仕様書 (Integration Test Specification)

## 1. 目的と対象範囲

本書は、Fireball ハイパーバイザの全 Tier（Tier 1 Core、Tier 2 Runtime、Tier 3 JIT）における各コンポーネント間の結合動作を、独立したリアル WASM バイトコード（WAT より生成されたバイナリ）を用いて包括的に検証する**結合テストシナリオ（End-to-End Component Integration Test Scenarios）**の仕様を定義する。

- **対象 Tier**: Tier 1 Core (`system_containers`, `system_syscall`, `os_scheduler`), Tier 2 Runtime (`runtime_loader`, `runtime_interpreter`, `runtime_vsoc`, `wasi`), Tier 3 JIT (`jit_compiler`, `jit_runtime`)
- **テストランナー**: `experiments/pysim/run_integration_tests.py`
- **テストスクリプト群**: `experiments/pysim/scenario1_loader_and_memory.py` 〜 `experiments/pysim/scenario6_coos_multitask_yield.py`

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

## 3. 実行方法と検証結果

```bash
# 全結合テストシナリオの一括実行
uv run --system-certs --with wasmtime python experiments/pysim/run_integration_tests.py
```

### 検証実績
- **全 7 シナリオ**: **7/7 PASSED** (約 4.8 秒)
- **完全差分検証**: 全シナリオにおいて、純粋インタープリタ実行と JIT 実行の出力がバイト単位・値単位で 100% 一致。
- **GDB リモートデバッグ**: 実 TCP ソケットを介した 10 ステップの GDB RSP リモート対話デバッグセッションが完全動作。
