# Fireball アクティブバックログ

Fireball Hypervisor の現行作業および次期フェーズのタスク一覧。
全体の開発ロードマップは `docs/plans/roadmap_phase.md` を参照。
品質ゲートおよび形式検証の最新結果は `reports/doc_report.md` を参照。

---

## Phase 0.8: 仕様レビュー・バジェット再見積・クラス設計 【進行中 / ACTIVE】
<!-- traceability: {META_SpecificationFirst} {META_Risk_Tiering} {Resource_Estimation_Model} -->

機械的な品質ゲート（静的解析・pyModelChecking 13モデル形式検証・WIT契約・LLM意味監査）はすべて合格（0 Errors, 0 Warnings）。
さらに、Python 完全実行可能シミュレータ（`experiments/pysim`）により全 11 統合シナリオの実証を完了。
現在、**オーナー（アーキテクト）による最終仕様精読、RAM/ROM バジェットの厳密な再見積もり、および C++23 クラス・構造体メモリレイアウト設計の確定** を実施中。

### Phase 0 達成実績 & 現在進行中のタスク
- [x] **1. 形式検証・WIT契約・Python シミュレータ実証 (DONE)**:
  - pyModelChecking 13モデル形式検証合格（CSPデッドロックフリー、W^X分離、PTE状態遷移等） `{GLOBAL_UseCpp20Coroutine}` `{CSP_Handoff}` `{JIT_MultiBuffer_Cache}` `{Debugger_Jit_Flush}`
  - WIT インターフェース契約 & 4つのリカバリー戦略（`ignore`, `retry`, `restart`, `panic`）確定 `{META_RecoveryStrategy}`
  - Python 実機シミュレータ (`experiments/pysim`) 全11シナリオ完走実証
- [ ] **2. 物理リソース予算（RAM 32KB / ROM 96KB）の厳密な再見積もり (進行中)**:
  - **RAM (32KB)**: JITキャッシュ 6.63KB, 統合スタック 2.02KB, WASMリニアメモリ 4.0KB, vSoCメタデータ 1.13KB, COOS 1.31KB, IPC/vMMIO 1.38KB, サブシステム 1.75KB, C++ Core/MSPスタック 3.0KB $\to$ 静的合計 **21.20 KB** (安全余白 10.80 KB / 33.7%) の実機適合確認 `{Resource_Estimation_Model}`
  - **ROM (96KB)**: JITステンシル 8.0KB, Interpreter 14.0KB, Loader 6.0KB, COOS/IPC 10.0KB, vMMIO 5.5KB, HAL/WASI 12.0KB, GDB 4.5KB, 内蔵WASM 20.0KB, スタートアップ 4.0KB $\to$ 静的合計 **84.0 KB** (安全余白 12.0 KB / 12.5%) の確認
- [ ] **3. C++23 クラス・構造体メモリレイアウト & constexpr 設計 (進行中)**:
  - `inc/core/`: `TaskControlBlock`, `CoosChannel`, `SchedulerContext`
  - `inc/interface/`: `IpcRouter`, `SharedBlockView`, `WitResourceTable`
  - `inc/runtime/`: `ExecutionContext` (16B), `ModuleView` (ゼロコピー索引), `OpcodeHandlerTable` (`[[clang::musttail]]`), `VmmioPageTable`
  - `inc/jit/`: `JitCacheManager` (2KB×3面), `Thumb2StencilTable`, `SafepointController`
  - `inc/platform/`: `ConsolidatedMemoryManager`, `PMSAv8MPUController`, `HalDriverHarness`
  - POD ハーネスと C++20/23 Concepts によるゼロオーバーヘッド静的 DI のヘッダ定義 `{META_StaticDI}` `{GLOBAL_ComponentHarness}`
- [ ] **4. オーナー（人間）による最終仕様精読 & GO 判定**:
  - 仕様・バジェット・クラス設計を Freeze し、C++23 実装フェーズ（Phase 1）への移行を最終承認 `{META_SpecificationFirst}`

---

## Phase 1: vSoC First 実装（約3ヶ月） 【オーナー GO 判定後に着手】
<!-- traceability: {META_AI_Native_Dev} {PositionIndependentCode} {JIT_CopyAndPatch} {ROMParsing} -->

スタンドアロン vSoC コア（Loader, Interpreter, JIT）を C++23 で実装し、ホストハーネス上で WAMR 比較ベンチマークを実施する。

### Phase 1.1: WASM 32-bit Binary Loader (`runtime_loader`)
- [ ] **`BinaryStream` / LEB128 デコーダ (`inc/runtime/loader.hxx`, `src/runtime/loader.cxx`)**:
  - ROM バイト列に対するゼロコピー uleb128 / sleb128 デコーダ・文字列リーダー `{ROMParsing}`
- [ ] **`module_view` 索引生成**:
  - ROM 上のセクション（Type, Import, Function, Table, Memory, Global, Export, Element, Code, Data）直接参照構造体の構築 `{META_AccessDictionary}`
- [ ] **WASM バリデータ (V1〜V6)**:
  - マジックナンバー、バージョン、セクション順序、型シグネチャ、Memory Section（64KB ページ / 8KB 部分ページ）の検証 `{LightweightVerifier}`
- [ ] **Loader 単体テストスイート (`tests/test_loader.cxx`)**:
  - 正常系 WASM バイナリおよび各種不正バイナリの拒絶テスト

### Phase 1.2: WASM Stackless Fast Interpreter (`runtime_interpreter`)
- [ ] **`execution_context` & 統合スタック (`inc/runtime/interpreter.hxx`)**:
  - スタックボトム配置と単一スタック上への CallFrame/ControlFrame インライン統合（Android ART スタイル）・ローカル変数基底 R3 渡し `{ContextPointerRegister}`
- [ ] **コア命令ハンドラ群 (`src/runtime/opcode_handlers.cxx`)**:
  - `__fastcall` 継続渡し（CPS）4引数シグネチャ（R0=IP, R1=stack_bot, R2=ENV, R3=local_base） `{ThreadedInterpreter}`
  - 算術演算 (i32/i64 add, sub, mul, clz, ctz, popcnt, rotl, rotr 等)
  - 比較・変換演算 (i32/i64 eq, ne, lt_s, lt_u, extend, wrap, reinterpret 等)
  - 制御フロー (block, loop, br, br_if, br_table, return, call, call_indirect)
  - メモリ操作 (i32/i64 load/store 8/16/32/64) と `MemoryBoundaryCheck` トラップ `{MemoryBoundaryCheck}`
- [ ] **スレッド化ディスパッチャ (`src/runtime/dispatch.cxx`)**:
  - `[[clang::musttail]]` によるダイレクトスレッド実行と JIT レジスタ整合 `{ThreadedInterpreter}`
- [ ] **Interpreter 単体テストスイート (`tests/test_interpreter.cxx`)**:
  - WebAssembly 公式 Core テストスイート（Spec Tests）サブセットのパス確認

### Phase 1.3: Copy-and-Patch JIT Compiler (`jit_compiler`)
- [ ] **ARM Thumb-2 / x86_64 ネイティブパッチステンシル (`inc/jit/stencils.hxx`)**:
  - `__fastcall` CPS 4引数レジスタ規約（R0=IP, R1=stack_bot, R2=ENV, R3=local_base）準拠の事前コンパイル済みネイティブバイト列（RO-Data）とリロケーションテーブル。R4=TOS / R5=NOS はトレース内部に閉じたキャッシュとし、入口で `LDR`×2、脱出時に `STR`×2 で統合スタックと同期する `{JIT_CopyAndPatch}` `{ADR_TosCacheAsymmetry}`
- [ ] **トリプルバッファ キャッシュマネージャ (`src/jit/cache_manager.cxx`)**:
  - 2KB × 3面 の代謝（`JIT_OldestOnly_Promote` / 最古破棄）制御 `{JIT_MultiBuffer_Cache}` `{JIT_OldestOnly_Promote}`
  - MPU W^X トランザクション管理（書き込み時 RW / 実行時 RX）
- [ ] **Safepoint 協調 & 透過的インタープリタ切り替え (`src/jit/safepoint.cxx`)**:
  - JIT $\leftrightarrow$ インタープリタ間の Low-Overhead フォールバック（コンテキスト再構築ゼロ、有界極小コスト）およびホットスポット検知 `{JIT_LazyChaining}` `{Interpreter_LazyJITSwitch}` `{JIT_RuntimeAPI_Fallback}`
- [ ] **JIT 単体テストスイート (`tests/test_jit.cxx`)**:
  - ホットスポットループの JIT トレース生成・実行・フォールバック検証

### Phase 1.4: Standalone vSoC Harness & WAMR Benchmark (`runtime_vsoc`)
- [ ] **ホスト実行ハーネス (`tools/harness/vsoc_host_runner.cxx`)**:
  - WASM バイナリのロードから実行完了までの単体テストスイート (x86_64 / Linux / macOS / Windows)
- [ ] **WAMR (Fast Interpreter) 比較ベンチマーク (`benchmarks/wamr_comparison.cxx`)**:
  - CoreMark-PRO / aobench / Wasm-Bench による実行速度、RAM 消費量、起動レイテンシの測定・比較評価

---

## Phase 2: Integration（周辺サブシステム統合 / 次期予定）
<!-- traceability: {META_3TierSeparation} {GLOBAL_UseCpp20Coroutine} {UnifiedAccessModel} -->

- [ ] **COOS カーネル (`inc/core/os_coos.hxx`)**: スタックレス C++20 コルーチンスケジューラ (`os_scheduler.md`, `os_coos.md`)
- [ ] **IPC ルータ (`inc/interface/ipc_router.hxx`)**: ゼロコピー CSP チャネル & RAII 所有権移譲 (`ipc_router.md`)
- [ ] **vMMIO コントローラ (`inc/runtime/vmmio.hxx`)**: 2段階ダイレクトデコードページテーブル & ソフトウェア TLB (`runtime_vmmio.md`)
- [ ] **HAL & WASI ドライバ (`inc/platform/hal.hxx`, `inc/platform/wasi.hxx`)**: GPIO / I2C / SPI / Timer / WASI Preview 1 (`platform_hal.md`)
- [ ] **GDB Server (`inc/runtime/debugger.hxx`)**: GDB リモートシリアルプロトコル（RSP）サーバー (`debug_manager.md`)

---

## Phase 3: PoC（ターゲットボード移植 / 将来予定）

- [ ] **Cortex-M33 実機移植**: BBC micro:bit v2 / nRF5340 / STM32U5 / Zephyr OS 環境への移植
- [ ] **実機性能・リアルタイム性評価**: sub-µs GPIO 割り込み応答および 64KB RAM 適合検証

---

## Phase 4: OSS & Production（継続予定）

- [ ] **OSS リリース整備**: MIT ライセンス、ドキュメンテーション Web サイト、チュートリアル
- [ ] **CMake / Meson ビルド設定ジェネレータ**: ターゲットボード別ワンコマンドビルド設定
- [ ] **エコシステム対応**: WIT ツールチェーン統合、WASI libc 適合、デバッグ環境整備
