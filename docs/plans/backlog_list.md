# Fireball アクティブバックログ

Fireball Hypervisor の現行作業および次期フェーズのタスク一覧。
全体の開発ロードマップは `docs/plans/roadmap_phase.md` を参照。
品質ゲートおよび形式検証の最新結果は `reports/doc_report.md` を参照。

---

## Phase 0.8: 仕様最終レビュー & GO 判定 【進行中 / レビュー中】
<!-- traceability: {META_SpecificationFirst} {META_Risk_Tiering} {Resource_Estimation_Model} -->

機械的な品質ゲート（静的解析・pyModelChecking 形式検証・WIT契約・LLM意味監査）はすべて合格（0 Errors, 0 Warnings）。
現在、**オーナー（アーキテクト）による最終仕様精読および GO 判定** を実施中。

### オーナーレビュー観点 & チェックリスト

- [ ] **1. アーキテクチャ構成 & リソース予算**:
  - RAM < 64KB 制約に対し、静的メモリ合計 24.0KB（残余 40KB）のメモリ予算設計の妥当性確認 (`architecture_overview.md`, `resource_budget.md`) `{Resource_Estimation_Model}`
- [ ] **2. WASM 64KB ページング & 8KB 部分ページ境界モデル**:
  - WebAssembly 標準 64KB ページングと、極小環境向け 8KB/16KB 部分ページの `FastAddressCheck` 境界トラップ仕様の確認 (`runtime_vmmio.md`, `system_config_details.md`) `{FastAddressCheck}`
- [ ] **3. 形式検証（pyModelChecking: 4モデル）の検証**:
  - Mutex 相互排他性、CSP デッドロックフリー、JIT 3面キャッシュ代謝、vSoC Safepoint 応答性のモデル検査確認 (`docs/components/*/formal/*.py`) `{GLOBAL_UseCpp20Coroutine}` `{CSP_Handoff}` `{JIT_MultiBuffer_Cache}`
- [ ] **4. WIT インターフェース契約 & リカバリー戦略**:
  - エラーコード廃止と 4 つのリカバリー戦略（`ignore`, `retry`, `restart`, `panic`）パターンの確認 (`interface_wit.md`) `{META_RecoveryStrategy}`
- [ ] **5. Phase 1 (vSoC First) への最終 GO / NO-GO 判定**:
  - 仕様を固定（Freeze）し、C++23 実装フェーズへの移行を承認 `{META_SpecificationFirst}`

---

## Phase 1: vSoC First 実装（約3ヶ月） 【GO 判定後に着手】
<!-- traceability: {META_AI_Native_Dev} {PositionIndependentCode} {JIT_CopyAndPatch} {ROMParsing} -->

スタンドアロン vSoC コア（Loader, Interpreter, JIT）を C++23 で実装し、ホストハーネス上で WAMR 比較ベンチマークを実施する。

### Phase 1.1: WASM 32-bit Binary Loader (`runtime_loader`)
- [ ] **`BinaryStream` 実装**:
  - ROM バイト列に対するゼロコピー LEB128 デコーダ・文字列リーダー `{ROMParsing}`
- [ ] **`module_view` 索引生成**:
  - ROM 上のセクション（Type, Import, Memory, Export, Code）直接参照構造体の構築 `{META_AccessDictionary}`
- [ ] **WASM バリデータ (V1〜V6)**:
  - マジックナンバー、バージョン、セクション順、Memory Section（64KB ページ / 8KB 部分ページ）の検証 `{LightweightVerifier}`

### Phase 1.2: WASM Stackless Fast Interpreter (`runtime_interpreter`)
- [ ] **`execution_context` 実装**:
  - 仮想 CPU レジスタ群（PC, SP, FP, Linear Memory Pointer, Memory Size） `{ContextPointerRegister}`
- [ ] **コア命令ハンドラ群 (`opcode_handler`)**:
  - 算術演算 (i32/i64 add, sub, mul, clz, ctz 等)
  - 制御フロー (block, loop, br, br_if, br_table, return, call)
  - メモリ操作 (i32.load, i32.store 等) と `MemoryBoundaryCheck` トラップ `{MemoryBoundaryCheck}`
- [ ] **スレッド化ディスパッチャ**:
  - ダイレクトスレッド実行による分岐オーバーヘッドの極小化 `{ThreadedInterpreter}`

### Phase 1.3: Copy-and-Patch JIT Compiler (`jit_compiler`)
- [ ] **ARM Thumb-2 ネイティブパッチテンプレート**:
  - 事前コンパイル済みネイティブバイト列（RO-Data）とリロケーションテーブル `{JIT_CopyAndPatch}`
- [ ] **トリプルバッファ キャッシュマネージャ**:
  - 2KB × 3面 の代謝（`JIT_OldestOnly_Promote` / 最古破棄）制御 `{JIT_DoubleBuffer_Cache}` `{JIT_MultiBuffer_Cache}`
- [ ] **Safepoint 協調 & 透過的インタープリタ切り替え**:
  - ホットスポット検知カウンタとデバッグ/割り込み時の Safepoint イールド `{JIT_LazyChaining}` `{Interpreter_LazyJITSwitch}`

### Phase 1.4: Standalone vSoC Harness & WAMR Benchmark (`runtime_vsoc`)
- [ ] **ホスト実行ハーネス (x86_64 / Linux / macOS)**:
  - WASM バイナリのロードから実行完了までの単体テストスイート
- [ ] **WAMR (Fast Interpreter) 比較ベンチマーク**:
  - CoreMark-PRO / Wasm-Bench による実行速度、RAM 消費量、起動レイテンシの測定・比較評価

---

## Phase 2: Integration（周辺コンポーネント統合 / 将来予定）
<!-- traceability: {META_3TierSeparation} {GLOBAL_UseCpp20Coroutine} {UnifiedAccessModel} -->

- [ ] **COOS カーネル**: スタックレス C++20 コルーチンスケジューラ (`os_scheduler.md`, `os_coos.md`)
- [ ] **IPC ルータ**: ゼロコピー CSP チャネル & 所有権移譲 (`ipc_router.md`)
- [ ] **vMMIO コントローラ**: 2段階ダイレクトデコードページテーブル & ソフトウェア TLB (`runtime_vmmio.md`)
- [ ] **HAL & システムサービス**: タイマー、割り込みコントローラ、仮想 UART/GPIO (`platform_hal.md`, `system_service.md`)

---

## Phase 3: PoC（ターゲットボード移植 / 将来予定）

- [ ] **Cortex-M33 実機移植**: BBC micro:bit v2 / nRF52840 / Zephyr OS 環境への移植
- [ ] **実機性能・リアルタイム性評価**: sub-µs GPIO 応答および 64KB RAM 適合検証
