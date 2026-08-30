# 開発ロードマップ

全体の開発フェーズ・工期・目的を定義する。各フェーズの具体的なタスクは `docs/plans/backlog_list.md` を参照。
品質課題および検証結果は `reports/doc_report.md` を正本とする。

## フェーズ概要

| フェーズ | 工期 | 目的 | 状態 |
|---|---|---|:---:|
| **Phase 0: Quality Gate & Foundation** | 約6ヶ月 | 仕様品質検証・13形式検証・バジェット再見積・クラス設計 | **進行中 (レビュー・設計中)** |
| **Phase 1: vSoC First (C++23 実装)** | 約3ヶ月 | スタンドアロンvSoCコア実装（Loader/Interpreter/JIT） | **待機中 (オーナーGO待ち)** |
| **Phase 2: Integration (周辺統合)** | 約4ヶ月 | 周辺サブシステム実装・統合（COOS/IPC/vMMIO/HAL/GDB） | 未着手 |
| **Phase 3: PoC (実機移植・評価)** | 約2ヶ月 | ターゲットボード移植（Cortex-M33/Zephyr）・性能評価 | 未着手 |
| **Phase 4: OSS & Production** | 継続 | OSSリリース整備・エコシステム対応・ドキュメント公開 | 未着手 |

**Phase 0 を品質ゲート・設計確定に集中させる理由:**
- **ゲート判定は人間（オーナー/アーキテクト）が実施する**: 機械的ゲート合格は前提条件に過ぎず、リソース制約・クラス構造・トレードオフの最終承認はアーキテクトが行う。
- **C++ 実装着手前の必須要件**:
  1. 物理リソース（RAM 32KB / ROM 96KB）のバイト単位の再見積もりと整合性検証 `{Resource_Estimation_Model}`
  2. C++23 ヘッダ（`inc/**/*.hxx`）における具体的な構造体メモリレイアウト、アライメント、constexpr 設計、POD ハーネス設計の確定
  3. Python 実機シミュレータ（`pysim` 11シナリオ）による動的振る舞いの先行検証の確認
- 仕様・設計・バジェットが完全に固まる前に C++ 実装へ進むことによる手戻りを完全防止する。

---

## Phase 0: Quality Gate & Prototype（DONE）

設計ドキュメント・WIT契約・ビルド基盤・形式検証・実機シミュレータを完成。`{META_SpecificationFirst}` `{META_Risk_Tiering}`

| サブフェーズ | 目的 | 状態 |
|---|---|:---:|
| Phase 0.7: Static DI & Build System | Harnessパターン・静的DI・WIT→C++自動生成・CMakeビルド | **DONE** |
| Phase 0.75: Constexpr Verification | コード生成のconstexpr対応・コンパイル時計算の実証 | **DONE** |
| Phase 0.76: SysML Alignment | 既存設計図のSysML準拠化・パラメトリック図導入 | **DONE** |
| Phase 0.8: Spec Quality Gate & Formal Models | 13形式検証モデル・WIT契約・LLM意味監査合格・全ゲート0 Error | **DONE** |
| Phase 0.85: Executable Python Simulator (`pysim`) | 全11シナリオ（Loader/Interpreter/JIT/COOS/vMMIO/GDB）完走実証 | **DONE** |

**形式検証モデル一覧（pyModelChecking 13モデル）** — `docs/components/*/formal/*.py`:

| モデル | 対象 | 主要プロパティ |
| :--- | :--- | :--- |
| `components/tier1_core/formal/coos_channel_model.py` | COOS CSP ランデブーとハンドオフ有界性 | デッドロック不在 / 二重所有不在 / メインループ復帰保証 |
| `components/tier1_core/formal/logging_flush_model.py` | 構造化ログのバッファリングとアイドル時フラッシュ | ログ欠損不在 / バッファ溢れ防止 / アイドルフラッシュ保証 |
| `components/tier1_core/formal/syscall_trap_model.py` | システムコールトラップ遷移とコンテキスト保護 | 特権昇格分離 / 状態復帰不変条件 |
| `components/tier1_interface/formal/csp_handoff_model.py` | IPC 所有権移譲と Drop ハンドラ | 二重所有不在 / in-flight 解決保証 |
| `components/tier1_interface/formal/service_fault_isolation_model.py` | サービスフォルト隔離とチャネルリカバリー | フォールト波及防止 / チャネル再初期化 |
| `components/tier1_interface/formal/wit_resource_lifecycle_model.py` | WIT リソースライフサイクルと Drop 契約 | リソースリーク不在 / 二重解放不在 |
| `components/tier2_runtime/formal/loader_verification_model.py` | WASM バイナリローダの検証状態遷移 | 不正バイナリ完全拒絶 / 整合状態遷移 |
| `components/tier2_runtime/formal/vsoc_state_model.py` | vSoC 実行状態と Safepoint 応答性 | IRQ/JIT レース不在 / Safepoint 到達保証 |
| `components/tier2_runtime/formal/vsoc_cache_coherency_model.py` | JIT キャッシュ世代とデバッガ介入 | 旧コード実行不在 / 世代単調性 / バンク回収 / flush 完了 |
| `components/tier3_jit/formal/jit_cache_model.py` | JIT 3面代謝・MPU W^X・遅延チェイニング | W^X 分離 / ダングリングチェイン不在 / 2-bit FSM 健全性 |

---

## Phase 1: vSoC First（C++23 実装 / 約3ヶ月）

スタンドアロン vSoC コア（Loader, Interpreter, JIT）を C++23 で実装し、ホストハーネス上で WAMR 比較ベンチマークを実施する。`{META_AI_Native_Dev}`

- **Phase 1.1: WASM 32-bit Binary Loader (`runtime_loader`)**
  - ROM バイト列ゼロコピー LEB128 デコーダ・セクションインデックス構築 `{ROMParsing}` `{META_AccessDictionary}`
  - バリデータ (V1〜V6) `{LightweightVerifier}`
- **Phase 1.2: WASM Stackless Fast Interpreter (`runtime_interpreter`)**
  - `execution_context` & 統合スタック（ARTスタイル） `{ContextPointerRegister}`
  - `__fastcall` CPS 4引数（`[[clang::musttail]]`）スレッド化ディスパッチャ `{ThreadedInterpreter}`
  - 全コア命令ハンドラ（算術・制御・メモリ境界トラップ） `{MemoryBoundaryCheck}`
- **Phase 1.3: Copy-and-Patch JIT Compiler (`jit_compiler`)**
  - ARM Thumb-2 ネイティブパッチステンシル & 事前コンパイルテンプレート `{JIT_CopyAndPatch}` `{ADR_TosCacheAsymmetry}`
  - 2KB×3面 トリプルバッファ MPU W^X 代謝マネージャ `{JIT_MultiBuffer_Cache}` `{JIT_OldestOnly_Promote}`
  - Safepoint 協調 & JIT/インタープリタ透過切り替え `{JIT_LazyChaining}` `{Interpreter_LazyJITSwitch}` `{JIT_RuntimeAPI_Fallback}`
- **Phase 1.4: Standalone vSoC Harness & WAMR Benchmark (`runtime_vsoc`)**
  - ホスト (x86_64 / Linux / macOS) 実行ハーネス
  - WAMR (Fast Interpreter) 比較ベンチマーク (CoreMark-PRO, aobench)

---

## Phase 2: Integration（約4ヶ月）

周辺コンポーネントの実装と C++23 統合。

- **COOS カーネル**: スタックレス C++20 コルーチンスケジューラ (`os_scheduler.hxx`, `os_coos.hxx`) `{GLOBAL_UseCpp20Coroutine}`
- **IPC ルータ**: ゼロコピー CSP チャネル & RAII 所有権移譲 (`ipc_router.hxx`) `{CSP_Handoff}`
- **vMMIO コントローラ**: 2段階ダイレクトデコードページテーブル & ソフトウェア TLB (`runtime_vmmio.hxx`) `{FastAddressCheck}`
- **HAL & WASI ドライバ**: GPIO / I2C / SPI / Timer / WASI Preview 1 (`platform_hal.hxx`, `platform_wasi.hxx`)
- **GDB Server**: GDB リモートシリアルプロトコル（RSP）デバッガ (`runtime_debugger.hxx`)

---

## Phase 3: PoC（約2ヶ月）

実機ターゲットボード移植と最終検証。

- **ターゲットボード移植**: ARM Cortex-M33 (nRF5340 / STM32U5 / micro:bit v2 / Zephyr OS)
- **実機性能・リアルタイム性評価**: sub-µs 割り込み応答、64KB RAM 適合、CoreMark 測定

---

## Phase 4: OSS & Production（継続）

OSS リリースに向けた整備とエコシステム展開。

- ドキュメンテーション・公開 Web サイト
- CMake / Meson ビルド設定ジェネレータ
- 標準開発環境（コンテナ・リンカスクリプト・デバッグ環境）
- コミュニティ対応・RFC プロセス運用
