# Fireball アクティブバックログ

Fireball Hypervisor の現行作業および次期フェーズのタスク一覧。
全体の開発ロードマップは [`roadmap_phase.md`](docs/plans/roadmap_phase.md) を参照。
完了済み項目は [`backlog_archive.md`](docs/plans/backlog_archive.md) に移管する。
品質課題および検証結果は検証パイプライン実行時に生成される `reports/doc_report.md` を参照。

現状基準日: **2026-09-18**

現行リポジトリの判定は次のとおりである。

- Phase 0 は Step 2 を継続中である。pysim のユニットテストは **25/25**、統合シナリオは **12/12** が通過している。
- テストの実行基盤は整っているが、旧 JIT テストハーネス参照の除去、Gotchas の文書同期、網羅性向上は未完了である。
- C++23 本実装は Phase 1 着手前であり、現状は `main`、allocator、backtrace の基盤に留まる。Loader、Interpreter、JIT、vSoC の実装および C++ 単体テストは未着手である。
- アーキテクチャの整合性は、要求・設計・形式モデル・テスト・実装の現行証跡に基づいて確認する。

---

## Phase 0: Quality Gate & Early Validation 【進行中 / ACTIVE】
<!-- traceability: {META_SpecificationFirst} {META_Risk_Tiering} {Resource_Estimation_Model} -->

盆栽デザイン（Bonsai Design）に基づき、仕様策定（Step 0）、早期検証・形式検証（Step 1）を完了。
現在は **Step 2（シミュレータコード品質向上、実装の勘所・Gotchas抽出、テスト設計・コードへの還元）** を集中的に推進中。

### 1. 現在進行中のタスク (ACTIVE: Step 2 推進中)
- [ ] **Step 2.1: pysim シミュレータコードの品質向上 & リファクタリング**:
  - `experiments/pysim/` 配下の各モジュール（Loader, Interpreter, JIT, COOS, vMMIO, HAL, GDB）のコード品質向上
  - 可読性・保守性・モジュール分離の洗練、不要・重複コードの排除、最新設計思想に沿った自然言語コメントの徹底
  - エラーハンドリング・境界検査の堅牢化
- [ ] **Step 2.2: 実装の勘所（Gotchas・不変条件）の網羅的抽出とテスト設計への還元**:
  - シミュレータの実行・リファクタリングから得られる新たな実装の勘所（Gotchas）やシステム不変条件（Invariants）の継続的抽出
  - コンポーネント別テスト仕様書（`docs/qa/tier*/`）への Gotchas 固有識別子および設計理由の追記・拡充
  - 仕様書（自然言語記述）とテスト仕様書の完全同期
- [ ] **旧JITテストハーネスの移行と重複dispatchの除去**:
  - QA内の`IntegratedHybridEngine`／`WASMContext`／`run_step`を参照するJITテスト（少なくとも `experiments/pysim/qa/tier3_jit/test_x64_jit.py`）を、現行`Interpreter`／`RuntimeEngine`の共有実行コンテキストへ移行する
  - 現行経路で同等の検証が成立したテストから旧ハーネスと重複opcode dispatchを除去する。単にテストを削除せず、既存の検証対象が維持されることを確認する
- [ ] **アーキテクチャ監査課題の設計整合・ADR策定**:
  - **現行証跡の再監査**: 要求・仕様・形式モデル・テスト・実装を基準に監査記録を整備する。対象はJITチェイン終端、AAPCS SP整列、ハンドラABI、JIT状態遷移・計算量、CSP保証、WIT／vMMIO契約、設計根拠である
  - JITのネイティブトレース間chainと参照シミュレータの再検索との差は、ターゲット固有最適化と参照モデルの抽象度差を切り分けて再レビューする
  - **JITトレースヘッダ更新と MPU W^X 保護（RO+X）のハードウェア整合化**: Cortex-M33 PMSAv8 において RO 領域（Region 4）への書き込みが MemManage Fault となる制約の解消。パッチトランザクション相乗りモデル（`begin_jit_patch` 内一括更新）またはヘッダ・データスロットの RAM 領域（Region 3）分離配置モデルの策定
  - **インタープリタ概念コードの移植性是正**: 残存する広すぎる型注釈を具体化し、ホスト再帰呼び出しを組み込み実装方針に適合させる
- [ ] **Step 2.3: ユニットテストコードの網羅性・品質強化**:
  - エッジケース・異常系・直交表組み合わせテストの拡充
  - テストランナー（[`run_all.py`](experiments/pysim/qa/run_all.py)）に登録された **25 スイート**の高速・高信頼実行を維持する。2026-09-18 の実行結果は **25/25 PASSED** である
  - 統合シナリオランナーに登録された **12 シナリオ**の実行結果も **12/12 PASSED** である。`docs/qa/verification_factor_matrix.md` の「24 suite」表記は実登録数と同期させる
- [ ] **Step 2.4: 物理リソース予算（最小構成 RAM 32KB / ROM 96KB）の厳密な再見積もり**:
  - 詳細正本: [`resource_budget_estimation.md`](docs/architecture/resource_budget_estimation.md)
  - **RAM (32KB)**: 統合物理メモリプール 23.55KB + OSスタック/静的変数 ~3.5KB $\to$ 静的合計 **~27.05 KB** (余裕 ~5.72 KB / 17.4%) の実機適合確認 `{Resource_Estimation_Model}`
  - **ROM (96KB)**: 不変ルックアップテーブル/辞書 ~8.2KB + 機械語コード ~45〜55KB $\to$ 静的合計 **~53〜63 KB** (空き余白 ~33〜43 KB / 約34〜45%) の確認
  - **コード規模 (20 KSLOC)**: コメントとテストを除く製品ソースコードの上限を20,000 SLOCとする `{Size_20KSLOC}`。最新pysimの18,701物理行からの参考推定は約21.5〜23.4 KSLOCであり、計測定義が異なるため、C++実測と同じSLOC条件で再見積もりする
- [ ] **Step 2.5: オーナー（人間）による最終品質レビュー & Phase 1 GO 判定**:
  - 仕様・シミュレータコード・テスト設計・バジェットを Freeze し、C++23 実装フェーズ（Phase 1）への移行を最終承認 `{META_SpecificationFirst}`

---

## Phase 1: vSoC First 実装（約3ヶ月） 【待機中 / オーナー GO 判定後に着手】
<!-- traceability: {META_AI_Native_Dev} {PositionIndependentCode} {JIT_CopyAndPatch} {ROMParsing} -->

スタンドアロン vSoC コア（Loader, Interpreter, JIT）を C++23 で実装し、ホストハーネス上で WAMR 比較ベンチマークを実施する。
**前提コンパイラ: Clang 17+ 必須（`[[clang::musttail]]` 前提、GCC/MSVC 非サポート）**。

### Phase 1.0: Core Utilities (`inc/common/`)
現状の C++ 実装は `src/main.cxx`、`src/allocator/`、`src/utils/backtrace.cxx` の基盤のみであり、`inc/common/` および Loader 以降の Phase 1 実装は未着手である。現行のバンプアロケータは `inc/allocator/bump_allocator.hxx` に存在するが、Phase 1 完了とは判定しない。

- [ ] **固定 SBO 多相関数ラッパー (`inc/common/economic_function.hxx`)**:
  - 16〜32B インラインバッファ内包、動的ヒープ確保排除、超過時コンパイル/アサート停止
- [ ] **バンプアロケータ (`inc/common/bump_allocator.hxx`; 現行基盤 `inc/allocator/bump_allocator.hxx`)**:
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
  - 検証失敗時のバンプポインタ完全ロールバック (`GOTCHA-LOAD-02`)
- [ ] **Loader 単体テストスイート (`tests/test_loader.cxx`)**:
  - 正常系 WASM バイナリおよび各種不正バイナリの拒絶テスト

### Phase 1.2: WASM Stackless Fast Interpreter (`runtime_interpreter`)
- [ ] **`execution_context` & 独立3バッファスタック (`inc/runtime/interpreter.hxx`)**:
  - オペランド領域・ローカル値領域・制御ブロック復帰情報領域の独立管理・ローカル変数基底 R2 渡し `{ContextPointerRegister}`
- [ ] **コア命令ハンドラ群 (`src/runtime/opcode_handlers.cxx`)**:
  - 継続渡し4論理引数シグネチャ（R0=ctx, R1=sp, R2=local_base, R3=tos） `{ThreadedInterpreter}`
  - 算術・比較・変換・制御・メモリ操作ハンドラと `MemoryBoundaryCheck` トラップ `{MemoryBoundaryCheck}`
  - 分岐脱出時のフレームプルーニングと TOS 復元 (`GOTCHA-INTR-02`)
- [ ] **スレッド化ディスパッチャ (`src/runtime/dispatch.cxx`)**:
  - `[[clang::musttail]]` によるダイレクトスレッド実行と JIT レジスタ整合 `{ThreadedInterpreter}`
- [ ] **Interpreter 単体テストスイート (`tests/test_interpreter.cxx`)**:
  - WebAssembly 公式 Core テストスイート（Spec Tests）サブセットのパス確認

### Phase 1.3: Copy-and-Patch JIT Compiler & Runtime (`jit_compiler`, `jit_runtime`)
- [ ] **ARM Thumb-2 / x86_64 ネイティブパッチステンシル (`inc/jit/stencils.hxx`)**:
  - 継続渡し4論理引数レジスタ規約準拠の事前コンパイル済みネイティブバイト列（RO-Data）とリロケーションテーブル `{JIT_CopyAndPatch}` `{ADR_TosCacheAsymmetry}`
- [ ] **トリプルバッファ キャッシュマネージャ (`src/jit/cache_manager.cxx`)**:
  - 連続8KB領域のうち可変バンクは2KB × 3面（Oldest 破棄・昇格）。先頭2KBの共通コード領域は非エビクション `{JIT_MultiBuffer_Cache}` `{JIT_OldestOnly_Promote}`
  - MPU W^X バッチトランザクション管理（書き込み時 RW+XN / 実行時 RO+X）
  - 3段検索（カードマーキング → Folding XOR高速キャッシュ → 少数のソート済みJITエントリの二分探索。Radix索引なし）
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

- [ ] **COOS カーネル (`inc/core/os_coos.hxx`)**: スタックレス C++20 コルーチンスケジューラ、対称ハンドオフ (`GOTCHA-COOS-01`〜`03`)
- [ ] **IPC ルータ (`inc/interface/ipc_router.hxx`)**: 3段階ルーティング、ゼロコピー CSP チャネル & RAII 所有権移譲 (`GOTCHA-IPCR-01`〜`03`)
- [ ] **vMMIO コントローラ (`inc/runtime/vmmio.hxx`)**: 多段ダイレクトデコードページテーブル & ソフトウェア TLB (`GOTCHA-VMMIO-01`〜`03`)
- [ ] **HAL & WASI ドライバ (`inc/runtime/hal_dispatch.hxx`, `inc/platform/driver.hxx`, `inc/platform/wasi.hxx`)**: GPIO / I2C / SPI / Timer / WASI Preview 1、`HalBufferPool` (`GOTCHA-HAL-01`〜`03`)
- [ ] **GDB Server (`inc/runtime/debugger.hxx`)**: GDB リモートシリアルプロトコル（RSP）サーバー、メモリ書き換え時 JIT キャッシュフラッシュ (`GOTCHA-DBG-01`〜`03`)

---

## Phase 3: PoC（ターゲットボード移植 / 将来予定）

- [ ] **Cortex-M33 実機移植**: BBC micro:bit v2 / nRF5340 / STM32U5 / Zephyr OS 環境への移植
- [ ] **実機性能・リアルタイム性評価**: sub-µs GPIO 割り込み応答および想定構成 64KB RAM 適合検証

---

## Phase 4: OSS & Production（継続）

- [ ] **OSS リリース準備**: ビルド手順、ドキュメント公開、サンプルプログラム
