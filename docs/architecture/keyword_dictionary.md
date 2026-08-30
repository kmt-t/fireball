# Fireball リンク用キーワード台帳 (Keyword Dictionary & Link Registry)

この文書は、Fireball プロジェクトにおける**ドキュメント間・仕様間・テスト間のリンク用メタキーワード（Link Anchors）の正本台帳**である。

章節項番号（`§3.3` 等）や見出し文字列、ファイルパスによる直接参照は、仕様改訂やリファクタリングに伴う見出し変更・章番号ズレによって容易に陳腐化・リンク切れを起こす。これを防ぐため、Fireball では中括弧で囲まれた一意なキーワード（`{...}`）をアンカーとして定義し、すべての設計書・テスト仕様書・結合テスト・形式検証モデルを相互リンクする。

---

## 1. リンクキーワードの運用ルール

1. **章節項番号依存の禁止**: ドキュメント間の参照において「§3.3を参照」「第4章を参照」といった章番号依存の記述を禁止し、キーワードアンカーを用いて紐付ける。
2. **一意性と定義元の明確化**: すべてのローカルキーワードは `requirement_list.md` またはコンポーネント設計書を正本とし、台帳でその定義元とリンク先を管理する。
3. **トレーサビリティの機械検証**: `spec-integrator` パイプラインが `DocGraph` を構築し、すべてのキーワード参照エッジ（Reference Edges）を自動検証する。

---

## 2. カテゴリ別 リンクキーワード台帳

### 2.1 WASM実行 & ランタイム (vSoC / Interpreter / Loader / vMMIO)

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 結合テスト / テストID |
| :--- | :--- | :--- | :--- | :--- |
| `{ThreadedInterpreter}` | `requirement_list.md` | `runtime_interpreter.md` | CPS 4引数ディスパッチ、UnifiedStack、レジスタ保持による高速命令実行 | Scenario 1〜11 |
| `{CPS_4Args}` | `runtime_interpreter.md` | `runtime_interpreter.md` | `ip, stack_bot, env, local_base` による 4 引数 CPS ディスパッチ規約 | Scenario 1〜11 |
| `{MemoryBoundaryCheck}` | `requirement_list.md` | `runtime_interpreter.md` | ゲストリニアメモリ境界外アクセスのトラップ遮断 | Scenario 1, 8, 10 |
| `{SignZeroExtension}` | `runtime_interpreter.md` | `runtime_interpreter.md` | 8/16/32-bit メモリ読み書きの符号付き・符号なしゼロ/符号拡張 | Scenario 8 (`INT-70`) |
| `{ControlFrameCleanup}` | `runtime_interpreter.md` | `runtime_interpreter.md` | `br_table` / `block` / `loop` / `if` 偽分岐時のスタックフレーム自動復元 | Scenario 3 (`INT-20`, `INT-22`) |
| `{ROMParsing}` | `requirement_list.md` | `runtime_loader.md` | WASM バイナリの Zero-Copy ロード・直接解析 | Scenario 1 |
| `{ActiveDataSegments}` | `runtime_loader.md` | `runtime_loader.md` | ロード時のアクティブデータセグメント自動リニアメモリ展開 | Scenario 1 (`INT-01`) |
| `{RAM_Bypass_Bit31}` | `runtime_vmmio.md` | `runtime_vmmio.md` | Bit 31 == 0 アドレスに対するページテーブル不使用 $O(1)$ 高速バイパス | Scenario 10 (`INT-90`) |
| `{DirectMappedTLB16}` | `runtime_vmmio.md` | `runtime_vmmio.md` | 20-bit VPN の 4-bit Folding XOR Hash による Direct-Mapped TLB | Scenario 10 (`INT-92`) |
| `{OwnerMismatchTrap}` | `runtime_vmmio.md` | `runtime_vmmio.md` | タスク間共有メモリ（FC=0xE）の所有権不一致時 `TRAP_OWNER_MISMATCH` 遮断 | Scenario 10 (`INT-93`) |
| `{vMMIO_TrapAndEmulate}` | `requirement_list.md` | `runtime_vmmio.md` | 仮想デバイスアクセス時のトラップ・ホストフック代理ディスパッチ | Scenario 10 (`INT-91`) |
| `{DynamicMmap}` | `requirement_list.md` | `runtime_vmmio.md` | 共有メモリID指定による外部バッファの動的 vMMIO マッピング | Scenario 10 |
| `{ExecutionContext_Layout}` | `architecture_overview.md` | `runtime_interpreter.md` | `execution_context` 16バイト物理フィールド配置 | Scenario 1〜11 |
| `{CallFrame_Layout}` | `architecture_overview.md` | `runtime_interpreter.md` | `call_frame` 20バイト統合スタックインライン物理配置 | Scenario 3, 8 |
| `{ControlFrame_Layout}` | `architecture_overview.md` | `runtime_interpreter.md` | `control_frame` 16バイト統合スタックインライン物理配置 | Scenario 3 |
| `{AAPCS_FastCall}` | `architecture_overview.md` | `runtime_interpreter.md` | CPS 4引数 AAPCS レジスタマッピング規約 (`R0`〜`R3`) | Scenario 1〜11 |
| `{VsocRuntime_Layout}` | `architecture_overview.md` | `runtime_vsoc.md` | `vsoc_runtime` 12バイト物理実行環境配置 | Scenario 1〜11 |
| `{ADR_TraceBoundaryYield}` | `runtime_interpreter.md` | `runtime_interpreter.md` | インタープリタ/JIT の協調的 Yield をトレース境界（切れ目）に限定する設計判断 | Scenario 6 (`INT-50`) |

---

### 2.2 タスク管理・スケジューリング・通信 (COOS / Scheduler / IPC)

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 結合テスト / テストID |
| :--- | :--- | :--- | :--- | :--- |
| `{CooperativeMultitasking}` | `requirement_list.md` | `os_coos.md` | コルーチン協調型マルチタスク実行エンジン | Scenario 6 (`INT-50`) |
| `{DirectContextSwitch}` | `requirement_list.md` | `os_scheduler.md` | READYキューを経由しないコルーチン直接ジャンプ超低レイテンシ遷移 | Scenario 6, 9 (`INT-50`, `INT-80`) |
| `{FuelExhaustion_Yield}` | `os_scheduler.md` | `os_scheduler.md` | Fuel 枯渇（`yield_every` 境界）での決定論的コルーチン協調中断と再開 | Scenario 6 (`INT-50`) |
| `{MainLoopReturnGuarantee}` | `os_coos.md` | `os_coos.md` | 連続ハンドオフ上限到達時のメインループ強制復帰形式保証 | Scenario 6 |
| `{CSPCommunication}` | `requirement_list.md` | `ipc_router.md` | ホーアCSPに基づく所有権移譲ゼロコピーメッセージパッシング | Scenario 9 (`INT-80`) |
| `{ThreeStageRouting}` | `ipc_router.md` | `ipc_router.md` | Stage 1 URI検索 $\to$ Stage 2 RBAC判定 $\to$ Stage 3 Zero-Copy 所有権移譲 | Scenario 9 (`INT-80`, `INT-81`) |
| `{QueueFullRollback}` | `ipc_router.md` | `ipc_router.md` | キュー満杯時の送信元ロールバック（所有権保持） | Scenario 9 (`INT-81`) |
| `{TargetFaultDropHandler}` | `ipc_router.md` | `ipc_router.md` | 宛先サービス死亡・フォールト時の `RECLAIMED_BY_DROP` 安全回収 | Scenario 9 (`INT-81`) |

---

### 2.3 JIT コンパイラ & ランタイム (JIT Compiler / Runtime)

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 結合テスト / テストID |
| :--- | :--- | :--- | :--- | :--- |
| `{JIT_CopyAndPatch}` | `requirement_list.md` | `jit_compiler.md` | ステンシル展開とバイナリパッチによる Copy-and-Patch 高速コード生成 | Scenario 4, 5, 8 (`INT-30`, `INT-40`) |
| `{TraceBoundaryInvariant}` | `jit_compiler.md` | `jit_compiler.md` | トレース境界でのスタック自己完結性、メモリ同期、およびフォールバック | Scenario 4, 5 (`INT-31`, `INT-41`) |
| `{JitBranchChainingHandler}` | `jit_compiler.md` | `jit_compiler.md` | JIT 専用チェイニングハンドラと純粋インタープリタ分岐ハンドラの分離 | Scenario 4, 5 |
| `{JIT_MultiBuffer_Cache}` | `requirement_list.md` | `jit_runtime.md` | Active / Warm / Oldest 3面バンク循環キャッシュ管理 | Scenario 4, 5 (`INT-31`) |
| `{ThreeBankCacheEviction}` | `jit_runtime.md` | `jit_runtime.md` | 3面バンク代謝と Oldest ヒット時の Active 昇格・局所アンリンク | Scenario 4, 5 (`INT-31`, `INT-41`) |
| `{RadixBinaryTreeView_bswap32}` | `system_containers.md` | `jit_runtime.md` | UnifiedPC（`func_idx << 20 \| pc`）の bswap32 による Radix 検索 | Scenario 5 (`INT-40`, `INT-41`) |
| `{BitView_CardMarking}` | `system_containers.md` | `jit_runtime.md` | 関数ごと 8バイト/カード 2-bit カードマーキング Hotspot 検出（UNEXEC $\to$ EXEC $\to$ HOT $\to$ COMPILED） | Scenario 4 (`INT-30`) |

---

### 2.4 デバッガ & システムコア & プラットフォーム (Debug / Syscall / Logging / HAL)

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 結合テスト / テストID |
| :--- | :--- | :--- | :--- | :--- |
| `{RSPMinimalSet}` | `debug_manager.md` | `debug_manager.md` | GDB RSP 最小コマンドセット（`?`, `g/G`, `m/M`, `Z0/z0`, `s`, `c`）の実ソケット対話 | Scenario 7, 8 (`INT-60`〜`INT-64`) |
| `{Debugger_Jit_Flush}` | `debug_manager.md` | `debug_manager.md` | デバッガからのメモリ書き込み（`M` パケット）時の JIT キャッシュ全バンク即時無効化 | Scenario 7, 8 (`INT-62`, `INT-72`) |
| `{DebuggerLabelTableSwitch}` | `debug_manager.md` | `debug_manager.md` | デバッガアタッチ時のインタープリタハンドラテーブル動的切り替え | Scenario 7 |
| `{WASI_ScatteredIO}` | `system_syscall.md` | `system_syscall.md` | 分散ギャザー `fd_write` / スキャッター `fd_read` による多要素 iovec 転送 | Scenario 2, 11 (`INT-10`, `INT-104`) |
| `{Syscall_ProcExit}` | `system_syscall.md` | `system_syscall.md` | `proc_exit` システムコールによるゲストタスク停止および終了コード伝播 | Scenario 2 (`INT-11`) |
| `{DictionaryBasedIPC}` | `system_logging.md` | `system_logging.md` | 静的 LogDictionary、危険書式（`%s` / `%p`）の登録時静的拒絶 | Scenario 9 (`INT-82`) |
| `{BufferedLogging}` | `system_logging.md` | `system_logging.md` | 実行時リングバッファ蓄積 $\to$ COOS `idle_hook` での一括 UART フラッシュ | Scenario 9 (`INT-82`) |
| `{HAL_PeripheralDrivers}` | `platform_hal.md` | `platform_hal.md` | GPIO（入出力・エッジIRQ）、I2C（LM75）、SPI（EEPROM）、Timer ダミードライバ | Scenario 11 (`INT-100`〜`INT-102`) |
| `{WASI_InMemVFS}` | `interface_wit.md` | `system_service.md` | WASI In-Memory VFS（`fd_seek`, `fd_read`, `fd_write`, `random_get`, `clock_time_get`） | Scenario 11 (`INT-103`〜`INT-105`) |
| `{FlatMapView_BinarySearch}` | `system_containers.md` | `system_containers.md` | 静的ソート配列に対する $O(\log N)$ バイナリサーチ（動的割当なし） | Scenario 1, 9 (`INT-01`, `INT-80`) |
| `{RingBuffer_Overwrite}` | `system_containers.md` | `system_containers.md` | 静的容量リングバッファ、満杯時の最古エントリ自動上書き | Scenario 9 (`INT-82`) |
| `{Pairwise_Combinatorial_Testing}` | `combinatorial_test_spec.md` | `combinatorial_test_spec.md` | 7因子288組の全2因子間ペアを100%網羅する All-Pairs 組み合わせテスト | `PAIR-01`〜`PAIR-26` |

---

## 3. システム横断 メタキーワード (Meta & Global Keywords)

| キーワード | 分類 | 説明 |
| :--- | :--- | :--- |
| `{META_3TierSeparation}` | メタ | 設計複雑度に応じた3階層のデコンポジション（分解）とカプセル化された依存関係管理。 |
| `{META_ConfigurableSystem}` | メタ | ヘッダマクロ定義および `constexpr` 定数により、システムパラメータをコンパイル時に静的確定する。 |
| `{META_FaultIsolation}` | メタ | メモリパーティションにより、コンポーネント間の障害伝播を防止する。 |
| `{META_RecoveryStrategy}` | メタ | エラーコードの代わりに推奨されるリカバリー動作（Retry/Panic等）を返し、自己修復を促進する。 |
| `{META_RestrictedPhysicalAccess}` | メタ | 物理リソースへのアクセスを許可テーブルで厳格に制限する。 |
| `{META_ZeroOverhead}` | メタ | ゼロコスト抽象化。高性能組み込み向けC++デザイン。 |
| `{GLOBAL_UseCpp20Coroutine}` | グローバル | C++20/23 コルーチンを活用し、標準的な言語機能によるコンテキストスイッチを実現する。 |
| `{GLOBAL_UseCpp23Library}` | グローバル | C++23 の型・アルゴリズム語彙（`std::span` 等）を活用し静的コンテナ語彙と両立する。 |
| `{GLOBAL_InterruptWakeup}` | グローバル | 割り込み発生時、関連タスクの割り込みハンドラをウェイクアップする。 |
| `{GLOBAL_PeriodicTask}` | グローバル | システムティックまたはアイドルループを利用した定期実行タスクをサポートする。 |
| `{GLOBAL_IdleDetection}` | グローバル | システムのアイドル状態を検知し、バックグラウンド処理（GC/ログ出力）を実行する。 |
