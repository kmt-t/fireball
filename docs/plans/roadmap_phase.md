# 開発ロードマップ

全体の開発フェーズ・工期・目的を定義する。各フェーズの具体的なタスクは [`backlog_list.md`](docs/plans/backlog_list.md) を参照。
品質課題および検証結果は検証パイプライン実行時に生成される `reports/doc_report.md` を参照。

## フェーズ概要
<!-- traceability: {Size_20KSLOC} -->

| フェーズ | 工期 | 目的 | 状態 |
|---|---|---|:---:|
| **Phase 0: Quality Gate & Early Validation** | 約6ヶ月 | 仕様品質検証・動的図解・形式検証・シミュレータコード品質向上・Gotchasテスト還元 | **進行中 (ACTIVE: Step 2 推進中)** |
| **Phase 1: vSoC First (C++23 実装)** | 約3ヶ月 | 基礎ユーティリティ・スタンドアロンvSoCコア実装（Loader/Interpreter/JIT） | **待機中 (PENDING: オーナーGO待ち)** |
| **Phase 2: Integration (周辺統合)** | 約4ヶ月 | 周辺サブシステム実装・統合（COOS/IPC/vMMIO/HAL/GDB） | 未着手 |
| **Phase 3: PoC (実機移植・評価)** | 約2ヶ月 | ARMv8-M候補ボードの仕様確定後に移植・性能評価（対象はTBD） | 未着手 |
| **Phase 4: OSS & Production** | 継続 | OSSリリース整備・エコシステム対応・ドキュメント公開 | 未着手 |

**Phase 0 を品質ゲート・早期検証・シミュレータ改善に集中させる理由:**
- **盆栽デザイン（Bonsai Design）の徹底**:
  - Step 0（仕様・図解）および Step 1（コンセプトコード・テスト設計・形式検証）に続き、現在は **Step 2（シミュレータコード品質向上、実装の勘所・Gotchasの抽出、テスト設計・コードへの還元）** を集中的に実施中。
  - シミュレータのコード品質を十分に高め、エッジケースや不変条件（Invariants）を洗い出し尽くすことで、C++ 本実装での手戻りを原理的に防止する。
- **C++ 実装着手（Phase 1）前の必須要件**:
  1. シミュレータ（`experiments/pysim`）のコード品質向上、堅牢化、および全ユニットテストスイートの高信頼化
  2. 実装の勘所（Gotchas）の網羅的抽出とテスト仕様書（`docs/qa/tier*/`）へのフィードバック完了
  3. ARMv8-Mの対象CPU・ボード確定後に物理リソース予算を作成し、構成との整合性を検証する。現時点では対象と予算をTBDとする `{Resource_Estimation_Model}`
  4. 製品コード規模20 KSLOC以内の予算計画と、コメント・テストを除く同一SLOC定義による実測手順の確定
  5. C++23 ヘッダ（`inc/**/*.hxx`）における構造体メモリレイアウト、アライメント、constexpr 設計、POD ハーネス設計の確定
  6. 人間（オーナー/アーキテクト）による最終レビューおよびフェーズ移行の GO 判定

---

## Phase 0: Quality Gate & Early Validation（進行中 / ACTIVE）
<!-- traceability: {META_Risk_Tiering} {META_SpecificationFirst} -->

仕様策定・形式検証・シミュレータ品質向上・Gotchasテスト還元。

| サブフェーズ / ステップ | 目的 | 状態 |
|---|---|:---:|
| **Step 0: Bonsai Design & Documentation** | 静的・動的設計ペアリング、自然言語仕様徹底、Mermaid動的図解（シーケンス図／アクティビティ図）、ルール体系刷新 | **DONE** |
| **Step 1: Early Validation** | 全16コンセプトコード（`Any`完全排除）、テスト仕様書、pyModelChecking形式検証（CTL論理式＋`guards=False`変異検査） | **DONE** |
| **Step 2: Reference Simulation & Gotchas Feedback** | `experiments/pysim` シミュレータコードの品質向上・リファクタリング、未検証エッジケース・Gotchasの抽出、テスト仕様書およびユニットテストスイートへの還元 | **進行中 (ACTIVE)** |
| **Step 2.4: Resource Budget & Header Review** | ARMv8-Mの物理予算（TBD）と製品コード20 KSLOCの再見積もり（[`resource_budget_estimation.md`](docs/architecture/resource_budget_estimation.md)）・C++23ヘッダレイアウト確認 | **待機中** |
| **Step 2.5: Gate Review & Go Decision** | オーナー（人間）による最終品質レビュー・Freeze・Phase 1 GO判定 | **待機中** |

---

## Phase 1: vSoC First（C++23 実装 / 約3ヶ月）【待機中 / オーナー GO 待ち】
<!-- traceability: {Interpreter_LazyJITSwitch} {LightweightVerifier} {META_AI_Native_Dev} -->

スタンドアロン vSoC コア（Loader, Interpreter, JIT）を C++23 で実装し、ホストハーネス上で WAMR 比較ベンチマークを実施する。プロジェクトの言語標準は C23/C++23 とする。
**コンパイラ要件: Clang 17+ 必須（`[[clang::musttail]]` 前提、GCC/MSVC 非サポート）**。

- **Phase 1.0: Core Utilities (`inc/common/`)**
  - 固定 SBO 多相関数ラッパー `economic_function<Sig>`（ヒープ確保排除、超過時コンパイル/アサート停止）
  - 高速一括確保アロケータ `bump_allocator`
  - 例外フリーエラー伝播 `result<T, E>`
  - 型安全非所有メモリビュー `binary_view` / `mutable_binary_view`
- **Phase 1.1: WASM 32-bit Binary Loader (`runtime_loader`)**
  - ROM バイト列ゼロコピー LEB128 デコーダ・セクションインデックス構築 `{ROMParsing}` `{META_AccessDictionary}`
  - バリデータ (V1〜V6)
  - 不正バイナリ検証失敗時のバンプポインタ完全ロールバック
- **Phase 1.2: WASM Stackless Fast Interpreter (`interpreter`)**
  - `execution_context` と独立した3本の領域（オペランド領域、ローカル値領域、制御ブロック復帰情報領域） `{ContextPointerRegister}`
  - 継続渡し4論理引数（`[[clang::musttail]]`）スレッド化ディスパッチャ `{ThreadedInterpreter}`
  - 全コア命令ハンドラ（算術・制御・メモリ境界トラップ） `{MemoryBoundaryCheck}`
  - 分岐脱出時のフレームプルーニングと TOS レジスタ復元
- **Phase 1.3: Copy-and-Patch JIT Compiler & Runtime (`jit_compiler`, `jit_runtime`)**
  - x64 JIT契約を検証する。ARMv8-MのABIと物理命令生成はTBDとする。
  - x64で検証したJIT領域と3面キャッシュ契約を管理する。ARMv8-Mの物理配置と保護方式はTBD。
  - 3段検索（カードマーキング → Folding XOR高速キャッシュ → 少数のソート済みJITエントリの二分探索。Radix索引なし）
  - C++ native dispatchによるInterpreter/JIT継続とLOOP後方分岐回数yieldを実装・検証する `{JIT_BackedgeYield}` `{JIT_RuntimeAPI_Fallback}`
- **Phase 1.4: Standalone vSoC Harness & WAMR Benchmark (`runtime_vsoc`)**
  - ホスト (x86_64 / Linux / macOS / Windows) 実行ハーネス
  - WAMR (Fast Interpreter) 比較ベンチマーク (CoreMark-PRO, aobench)

---

## Phase 2: Integration（周辺サブシステム統合 / 約4ヶ月）
<!-- traceability: {GLOBAL_UseCpp20Coroutine} {FastAddressCheck} -->

周辺コンポーネントの実装と C++23 統合。

- **COOS カーネル**: C++23 で実装するスタックレスコルーチンスケジューラ（C++20 コルーチン機能を使用）、対称ハンドオフ (`os_scheduler.hxx`, `os_coos.hxx`)
- **IPC ルータ**: 3段階ルーティング、ゼロコピー CSP チャネル & RAII 所有権移譲 (`ipc_router.hxx`) `{CSP_Handoff}`
- **vMMIO コントローラ**: 多段ダイレクトデコードページテーブル & ソフトウェア TLB (`runtime_vmmio.hxx`) `FastAddressCheck`
- **HAL & WASI ドライバ**: GPIO / I2C / SPI / Timer / WASI Preview 1、`HalBufferPool` (`hal_dispatch.hxx`, `platform_driver.hxx`, `platform_wasi.hxx`)
- **GDB Server**: GDB リモートシリアルプロトコル（RSP）デバッガ、メモリ書き換え時 JIT キャッシュフラッシュ (`runtime_debugger.hxx`)

---

## Phase 3: PoC（実機移植・評価 / 約2ヶ月）

実機ターゲットボード移植と最終検証。

- **ターゲットボード移植**: ARMv8-Mの対象CPU、ボード、OSはTBD
- **実機性能・リアルタイム性評価**: 測定基準、RAM/ROM上限、割込み応答条件はTBD

---

## Phase 4: OSS & Production（継続）

OSS リリースに向けた整備とエコシステム展開。
