# Fireball アクティブバックログ

Fireball Hypervisor の現行作業および次期フェーズのタスク一覧。
全体の開発ロードマップは `docs/plans/roadmap_phase.md` を参照。
品質ゲートおよび形式検証の最新結果は `reports/doc_report.md` を参照。

---

## Phase 0: 仕様確定・形式検証・実機シミュレータ実証 【DONE】
<!-- traceability: {META_SpecificationFirst} {META_Risk_Tiering} {Resource_Estimation_Model} -->

機械的な品質ゲート（静的解析・pyModelChecking 13モデル形式検証・WIT契約・LLM意味監査）はすべて合格（0 Errors, 0 Warnings）。
さらに、Python 完全実行可能シミュレータ（`experiments/pysim`）により全 11 統合シナリオの実証を完了。

### Phase 0 達成実績
- [x] **1. アーキテクチャ設計・リソース予算固定**: 最小構成（RAM 32KB / ROM 96KB）に対し、静的合計 21.0KB / 84KB とする予算設計完了 `{Resource_Estimation_Model}`
- [x] **2. WASM 64KB ページング & 8KB 部分ページ境界モデル確立**: `FastAddressCheck` 境界トラップ仕様の確定 `{FastAddressCheck}`
- [x] **3. pyModelChecking 13形式検証モデル合格**: CSPデッドロックフリー、W^X分離、PTE状態遷移等の完全証明 `{GLOBAL_UseCpp20Coroutine}` `{CSP_Handoff}` `{JIT_MultiBuffer_Cache}` `{Debugger_Jit_Flush}`
- [x] **4. WIT インターフェース契約 & リカバリー戦略確定**: 4つのリカバリー戦略（`ignore`, `retry`, `restart`, `panic`）パターン確定 `{META_RecoveryStrategy}`
- [x] **5. Python 実機シミュレータ (`experiments/pysim`) 実証**: Loader, Interpreter, Copy-and-Patch JIT, COOS, IPC, vMMIO, GDB デバッガの全 11 シナリオ完走
- [x] **6. オーナー最終 GO 判定**: C++23 実装フェーズ（Phase 1）への移行承認 `{META_SpecificationFirst}`

---

## Phase 1: vSoC First 実装（約3ヶ月） 【進行中 / ACTIVE】
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
