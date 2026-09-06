# Fireball アクティブバックログ

Fireball Hypervisor の現行作業および次期フェーズのタスク一覧。
全体の開発ロードマップは [`roadmap_phase.md`](docs/plans/roadmap_phase.md) を参照。
品質課題および検証結果は検証パイプライン実行時に生成される `reports/doc_report.md` を参照。

---

## Phase 0: Quality Gate & Early Validation 【進行中 / ACTIVE】
<!-- traceability: {META_SpecificationFirst} {META_Risk_Tiering} {Resource_Estimation_Model} -->

盆栽デザイン（Bonsai Design）に基づき、仕様策定（Step 0）、早期検証・形式検証（Step 1）を完了。
現在は **Step 2（シミュレータコード品質向上、実装の勘所・Gotchas抽出、テスト設計・コードへの還元）** を集中的に推進中。

### 1. 完了実績 (DONE)
- [x] **Step 0: 仕様策定・動的図解・ルール体系刷新 (DONE)**:
  - 全 13 コンポーネント設計書における静的・動的ペアリングの徹底
  - 複雑な動的アルゴリズムに対するシーケンス図（責務重視）およびアクティビティ図（手順重視）の体系的配備
  - `.agents/rules/` の 4 コア体系（docs, cpp, python, dev/antipatterns）への再編および Claude Code 互換 YAML frontmatter 付与
- [x] **Step 1: コンセプトコード・初期テスト仕様・形式検証 (DONE)**:
  - 全 16 コンセプトコードの実装および最新仕様同期（`typing.Any` 完全排除、具体型・代数的データ型徹底）
  - 全 10 形式検証モデル（`pyModelChecking`）の CTL 論理式証明および `guards=False` 変異検査による反証性担保（28/28 変異検出）
  - 全 22 ユニットテストスイート（[`test_gotchas.py`](experiments/pysim/tests/cross_cutting/test_gotchas.py) 含む 22/22 PASS）

---

### 2. 現在進行中のタスク (ACTIVE: Step 2 推進中)
- [ ] **Step 2.1: pysim シミュレータコードの品質向上 & リファクタリング**:
  - `experiments/pysim/` 配下の各モジュール（Loader, Interpreter, JIT, COOS, vMMIO, HAL, GDB）のコード品質向上
  - 可読性・保守性・モジュール分離の洗練、不要・重複コードの排除、最新設計思想に沿った自然言語コメントの徹底
  - エラーハンドリング・境界検査の堅牢化
- [ ] **Step 2.2: 実装の勘所（Gotchas・不変条件）の網羅的抽出とテスト設計への還元**:
  - シミュレータの実行・リファクタリングから得られる新たな実装の勘所（Gotchas）やシステム不変条件（Invariants）の継続的抽出
  - コンポーネント別テスト仕様書（`tests/*_test_spec.md`）への Gotchas 固有識別子および設計理由の追記・拡充
  - 仕様書（自然言語記述）とテスト仕様書の完全同期
- [ ] **Step 2.3: ユニットテストコードの網羅性・品質強化**:
  - エッジケース・異常系・直交表組み合わせテストの拡充
  - テストランナー（[`run_all.py`](experiments/pysim/tests/run_all.py)）による全 22+ スイートの高速・高信頼実行の維持
- [ ] **Step 2.4: 物理リソース予算（RAM 32KB / ROM 128KB）の厳密な再見積もり**:
  - 詳細正本: [`resource_budget_estimation.md`](docs/architecture/resource_budget_estimation.md)
  - **RAM (32KB)**: 統合物理メモリプール 21.5KB + OSスタック/静的変数 ~3.5KB $\to$ 静的合計 **~25.0 KB** (安全余白 ~7.7 KB / 24%) の実機適合確認 `{Resource_Estimation_Model}`
  - **ROM (128KB)**: 不変ルックアップテーブル/辞書 ~8.2KB + 機械語コード ~45〜55KB $\to$ 静的合計 **~53〜63 KB** (空き余白 ~65〜75 KB / 約50%) の確認
- [ ] **Step 2.5: オーナー（人間）による最終品質レビュー & Phase 1 GO 判定**:
  - 仕様・シミュレータコード・テスト設計・バジェットを Freeze し、C++23 実装フェーズ（Phase 1）への移行を最終承認 `{META_SpecificationFirst}`

---

## Phase 1: vSoC First 実装（約3ヶ月） 【待機中 / オーナー GO 判定後に着手】
<!-- traceability: {META_AI_Native_Dev} {PositionIndependentCode} {JIT_CopyAndPatch} {ROMParsing} -->

スタンドアロン vSoC コア（Loader, Interpreter, JIT）を C++23 で実装し、ホストハーネス上で WAMR 比較ベンチマークを実施する。
**前提コンパイラ: Clang 17+ 必須（`[[clang::musttail]]` 前提、GCC/MSVC 非サポート）**。

### Phase 1.0: Core Utilities (`inc/common/`)
- [ ] **固定 SBO 多相関数ラッパー (`inc/common/economic_function.hxx`)**:
  - 16〜32B インラインバッファ内包、動的ヒープ確保排除、超過時コンパイル/アサート停止
- [ ] **バンプアロケータ (`inc/common/bump_allocator.hxx`)**:
  - 一括確保・スコープ終了時一括解放（Reset）による断片化ゼロアロケータ
- [ ] **エラー伝播 & ビュー (`inc/common/result.hxx`, `inc/common/binary_view.hxx`)**:
  - `result<T, E>`（例外フリー戻り値伝播）および `void*` を排除した `std::span` 型付きビュー

### Phase 1.1: WASM 32-bit Binary Loader (`runtime_loader`)
- [ ] **`BinaryStream` / LEB128 デコーダ (`inc/runtime/loader.hxx`, `src/runtime/loader.cxx`)**:
  - ROM バイト列に対するゼロコピー uleb128 / sleb128 デコーダ・文字列リーダー `{ROMParsing}`
- [ ] **`module_view` 索引生成**:
  - ROM 上のセクション直接参照構造体の構築 `{META_AccessDictionary}`
- [ ] **WASM バリデータ (V1〜V6) & ロールバック**:
  - マジックナンバー、バージョン、セクション順序、型シグネチャの検証 `{LightweightVerifier}`
  - 検証失敗時のバンプポインタ完全ロールバック (`LOAD-GOTCHA-02`)
- [ ] **Loader 単体テストスイート (`tests/test_loader.cxx`)**:
  - 正常系 WASM バイナリおよび各種不正バイナリの拒絶テスト

### Phase 1.2: WASM Stackless Fast Interpreter (`runtime_interpreter`)
- [ ] **`execution_context` & 独立3バッファスタック (`inc/runtime/interpreter.hxx`)**:
  - `OperandStack`/`LocalStack`/`control_frame` 専用領域の独立管理・ローカル変数基底 R2 渡し `{ContextPointerRegister}`
- [ ] **コア命令ハンドラ群 (`src/runtime/opcode_handlers.cxx`)**:
  - `__fastcall` 継続渡し（CPS）4引数シグネチャ（R0=IP, R1=stack_bot, R2=local_base, R3=tos） `{ThreadedInterpreter}`
  - 算術・比較・変換・制御・メモリ操作ハンドラと `MemoryBoundaryCheck` トラップ `{MemoryBoundaryCheck}`
  - 分岐脱出時のフレームプルーニングと TOS 復元 (`INTR-GOTCHA-02`)
- [ ] **スレッド化ディスパッチャ (`src/runtime/dispatch.cxx`)**:
  - `[[clang::musttail]]` によるダイレクトスレッド実行と JIT レジスタ整合 `{ThreadedInterpreter}`
- [ ] **Interpreter 単体テストスイート (`tests/test_interpreter.cxx`)**:
  - WebAssembly 公式 Core テストスイート（Spec Tests）サブセットのパス確認

### Phase 1.3: Copy-and-Patch JIT Compiler & Runtime (`jit_compiler`, `jit_runtime`)
- [ ] **ARM Thumb-2 / x86_64 ネイティブパッチステンシル (`inc/jit/stencils.hxx`)**:
  - `__fastcall` CPS 4引数レジスタ規約準拠の事前コンパイル済みネイティブバイト列（RO-Data）とリロケーションテーブル `{JIT_CopyAndPatch}` `{ADR_TosCacheAsymmetry}`
- [ ] **トリプルバッファ キャッシュマネージャ (`src/jit/cache_manager.cxx`)**:
  - 2KB × 3面 の代謝（Oldest 破棄・昇格）制御 `{JIT_MultiBuffer_Cache}` `{JIT_OldestOnly_Promote}`
  - MPU W^X バッチトランザクション管理（書き込み時 RW+XN / 実行時 RO+X）
  - 3段高速検索パイプライン（カードマーキング $	o$ 基数テーブル $	o$ 二分探索）
- [ ] **Safepoint 協調 & 透過的インタープリタ切り替え (`src/jit/safepoint.cxx`)**:
  - JIT $\leftrightarrow$ インタープリタ間の Low-Overhead フォールバックおよびホットスポット検出 `{JIT_LazyChaining}` `{Interpreter_LazyJITSwitch}` `{JIT_RuntimeAPI_Fallback}`
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

- [ ] **COOS カーネル (`inc/core/os_coos.hxx`)**: スタックレス C++20 コルーチンスケジューラ、対称ハンドオフ (`COOS-GOTCHA-01`〜`03`)
- [ ] **IPC ルータ (`inc/interface/ipc_router.hxx`)**: 3段階ルーティング、ゼロコピー CSP チャネル & RAII 所有権移譲 (`IPCR-GOTCHA-01`〜`03`)
- [ ] **vMMIO コントローラ (`inc/runtime/vmmio.hxx`)**: 多段ダイレクトデコードページテーブル & ソフトウェア TLB (`VMMIO-GOTCHA-01`〜`03`)
- [ ] **HAL & WASI ドライバ (`inc/platform/hal.hxx`, `inc/platform/wasi.hxx`)**: GPIO / I2C / SPI / Timer / WASI Preview 1、`ShmBufferPool` (`HAL-GOTCHA-01`〜`03`)
- [ ] **GDB Server (`inc/runtime/debugger.hxx`)**: GDB リモートシリアルプロトコル（RSP）サーバー、メモリ書き換え時 JIT キャッシュフラッシュ (`DBG-GOTCHA-01`〜`03`)

---

## Phase 3: PoC（ターゲットボード移植 / 将来予定）

- [ ] **Cortex-M33 実機移植**: BBC micro:bit v2 / nRF5340 / STM32U5 / Zephyr OS 環境への移植
- [ ] **実機性能・リアルタイム性評価**: sub-µs GPIO 割り込み応答および 64KB RAM 適合検証

---

## Phase 4: OSS & Production（継続）

- [ ] **OSS リリース準備**: ビルド手順、ドキュメント公開、サンプルプログラム
