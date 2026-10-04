# Fireball アクティブバックログ

Fireball Hypervisor の現行作業および次期フェーズのタスク一覧。
全体の開発ロードマップは [`roadmap_phase.md`](docs/plans/roadmap_phase.md) を参照。
完了済み項目は [`backlog_archive.md`](docs/plans/backlog_archive.md) に移管する。
品質課題および検証結果は検証パイプライン実行時に生成される `reports/doc_report.md` を参照。

現状基準日: **2026-10-05**。同期対象のソース基準は `1328ab03` である。

現行リポジトリの判定は次のとおりである。

- Phase 0 は Step 2 を継続中である。Phase 1 の開始はオーナーのGO判定待ちである。
- pysim はC++ Interpreterとx64 JITを含む参照実装である。製品側の `inc/`・`src/` は `main`、allocator、backtrace の基盤に留まる。
- 旧JITテストハーネスの移行とカードエイジングは完了済みである。完了範囲は [`backlog_archive.md`](docs/plans/backlog_archive.md) を参照する。
- 検証の実行基盤は整っている。COOSの世代保留中CSP経路、Loaderの目標契約、組み込み資源適合には未検証範囲が残る。
- ARMv8-Mの対象ボード、物理ABI、メモリ保護、配置内訳はTBDである。要求上の最小容量はRAM 32KB / ROM 96KBであり、容量条件自体は未決ではない。

### 同期時点の検証状況
<!-- traceability: {Pairwise_Combinatorial_Testing} {ThreadedInterpreter} {JIT_CopyAndPatch} {ROMParsing} -->

全件実行、局所実行、登録検査を区別する。過去の合格記録を現行ソース全体の合格へ読み替えない。

| 対象 | 日付・確認範囲 | 結果と残る範囲 |
| :--- | :--- | :--- |
| 検証マトリクス | 2026-10-05、[`check_verification_matrix.py`](tools/check_verification_matrix.py) | PASS。26仕様、14概念コード、20コンポーネント形式モデル、25テスト仕様、12シナリオ、結合3スイート、WASMワークロード3スイート。登録とペアワイズ被覆を確認した。 |
| 局所回帰テスト | 2026-10-05、COOS、パーサー、JIT差分、Core Spec選択セット、ペアワイズ結合の5ファイル | 95件PASS。実行入口は下記を参照する。 |
| 形式検証 | 2026-10-05、LoaderとJIT cacheの2モデル | 正常検査と `guards=False` 変異検査がPASS。JITエイジング単独の変異検査もPASS。全モデルの再実行ではない。 |
| 型検査 | 2026-10-05、[`pyrightconfig.json`](pyrightconfig.json) の7ファイル | エラー・警告0件。製品Tier全体の型検査ではない。 |
| ランタイムメモリ | 2026-10-05、4入力×2実行方式×3回 | アリーナ使用量、出力一致、ワークスペース再利用を確認した。使用量とアリーナ外の既知領域は [`resource_budget_estimation.md`](docs/architecture/resource_budget_estimation.md) を参照する。ARMv8-Mの全体配置の判定は残る。 |
| 単体・統合シナリオ | 登録済みの30単体スイートと12シナリオ | 同期時には全件を再実行していない。旧バックログの単体30/30は2026-09-21の記録である。統合12/12の旧記録には実行日・環境・revisionがない。 |
| 公式Core Spec選択セット | 2026-10-04の保存済み記録 | 固定37ファイル、内部期待値assert 15,230件、skip 0件。公式スイート全体の適合ではない。正本は [`wasm_core_mvp_test_results.md`](docs/qa/wasm_core_mvp_test_results.md)。 |
| WASMワークロード | 2026-10-04の保存済み記録 | SDK/libcゲスト52件、HALゲスト11件、Core Spec37件の3/3スイート成功。同期時には全3スイートを再実行していない。 |
| 文書ゲート | 2026-10-02の保存済み監査 | 全PASSの記録がある。現行ソース全体の再検査結果ではない。正本は [`audit_summary.md`](reports/tev1-clean-20261002-194529-40b400/audit_summary.md)。 |
| 変更文書の局所検査 | 2026-10-05、資源予算、バックログ、アーカイブ | 書式・リンク・文章の局所検査と、正本88文書を含むキーワード参照検査で変更文書の問題0件。3文書の保存済み監査が古いため、監査義務ゲートは未通過である。LLM再監査は実行していない。 |

2026-10-05の局所回帰テストは、プロジェクトルートで次のコマンドを実行した。

```bash
PYTHONPATH=.:experiments/pysim .venv/bin/python -m pytest -q \
  experiments/pysim/qa/tier1_core/test_coos.py \
  experiments/pysim/qa/tier2_runtime/test_wasm_reader.py \
  experiments/pysim/qa/tier3_plugins/jit/test_jit_differential.py \
  experiments/pysim/qa/workloads/test_wasm_core_spec.py \
  experiments/pysim/qa/integration/test_pairwise_combinations.py
```

---

## Phase 0: Quality Gate & Early Validation 【進行中 / ACTIVE】
<!-- traceability: {META_SpecificationFirst} {META_Risk_Tiering} {Resource_Estimation_Model} -->

盆栽デザイン（Bonsai Design）に基づき、仕様策定（Step 0）、早期検証・形式検証（Step 1）を完了。
現在は **Step 2（シミュレータコード品質向上、実装の勘所・Gotchas抽出、テスト設計・コードへの還元）** を集中的に推進中。

### 1. 現在進行中のタスク (ACTIVE: Step 2 推進中)
<!-- traceability: {JIT_CardAgingSweep} {META_SpecificationFirst} {Size_20KSLOC} {LowLatencyJIT} {ADR_InterruptRescheduleGeneration} {ADR_TaskIdLifetime} {ADR_RendezvousChannel} {ADR_JitCompileScheduling} {ADR_RuntimeEventRetention} {Challenge_JITCacheEfficiency} {ROMParsing} -->
- [ ] **Step 2.1: pysim シミュレータコードの品質向上 & リファクタリング**:
  - Tier 2 Interpreter・Runtime契約とTier 3 JIT・観測プラグインの分離後の品質向上を継続する。
  - 制御命令だけの区間のtrace検索と履歴記録を抑止した。通常命令を実行したブロックの開始PCを保持し、履歴転送時の全件Loader索引検索を削除した。prefix命令の実行境界もLoaderへ揃えた。
  - 可読性・保守性・モジュール分離の洗練、不要・重複コードの排除、最新設計思想に沿った自然言語コメントの徹底
  - エラーハンドリング・境界検査の堅牢化
- [ ] **Step 2.2: 実装の勘所（Gotchas・不変条件）の網羅的抽出とテスト設計への還元**:
  - シミュレータの実行・リファクタリングから得られる新たな実装の勘所（Gotchas）やシステム不変条件（Invariants）の継続的抽出
  - コンポーネント別テスト仕様書（`docs/qa/tier*/`）への Gotchas 固有識別子および設計理由の追記・拡充
  - GOTCHAの正本をコンポーネント側へ集約する文書整備には保存済み監査がある。追加・変更した不変条件の文書と実行テストを継続して同期する。
- [ ] **アーキテクチャ監査課題の設計整合・ADR策定**:
  - 採用したADRの実装追従を行う。タスクIDは終了時の登録・待機・資源解放後に再利用する。IPCの応答は相手の受信待機まで応答側をサスペンドする。Runtimeイベントリングは最古を上書きして最新履歴を保持する。
  - JITの通常idle hookは成功数ではなく、失敗・スキップを含む候補処理数で予算を消費する。満杯時は通常予算の例外として固定キュー容量ぶんをその場で全件処理する。境界値と副作用を実行テスト・形式モデルへ反映する。
  - スラッシング防止方策は未決である。trace実行回数と破棄時記録を分析し、未使用evictionだけを対象にするか、費用回収の少ないtraceも含めるかを決める。抑止条件・適用範囲・再許可の回復条件を確定する。現在の`UNEXECUTED`へ戻す動作は暫定方策とする。
  - Loaderの命令利得表を現行compilerの対応範囲と照合して校正する。Logging概念コードの出力境界をTier 1 `printk`へ揃える。Debugger概念コードを現行のレジスタ配置とRSP検証へ揃える。これらの追従前に概念コードを現行契約の検証済み証拠としない。
  - 現行の概要書収集では26コンポーネント、6 WITファイルを確認した。リンク切れ・未掲載・未知のコンポーネントリンクは検出されなかった。責務・契約・実装の意味整合は個別の正本と照合する。
  - JIT chainとInterpreter handlerの実行境界、共有状態、yield/fallbackを現行のネイティブ実装と照合する。古い参照モデルとの差を現行実装の欠陥と断定しない。
  - ARMv8-Mの物理ABI、命令生成、trace layout、MPU/W^X、メモリ配置、ROM/RAM予算、実機検証条件は、x64のJIT契約検証後に新規設計する。現時点ではすべてTBD。
  - Interpreter概念コードの型、独立したスタック領域、呼出し・分岐の状態を現行設計と照合する。旧記録の型・再帰の指摘は再確認してから残課題として扱う。
  - `README.md`の旧Tier説明を現行アーキテクチャへ同期する。DebuggerはTier 3 Plugins、ゲスト公開WITはTier 3 Platformに属する。
  - 変更文書の監査義務を更新する。局所ゲートは3文書の `OBLIG-ASSESSMENT-STALE` を検出した。定義文書を含めずに局所実行した結果と、正本を含めた確認結果を区別する。LLM監査はユーザーの明示指示時に実行する。
- [ ] **Step 2.3: ユニットテストコードの網羅性・品質強化**:
  - エッジケース・異常系・直交表組み合わせテストの拡充
  - [`os_coos_test_spec.md`](docs/qa/tier1_core/os_coos_test_spec.md) のTEST-COOS-16を直接検証する。成立に必要な直接遷移、遷移先の世代観測、追加連鎖停止を順に観測する。現行の局所テスト成功から同経路の検証完了を推定しない。
  - [`runtime_loader_test_spec.md`](docs/qa/tier2_runtime/runtime_loader_test_spec.md) の目標契約と現行pysim QAを区別する。QA専用リンクハーネスを製品の複数モジュール登録・リンク・Radix索引の実装証拠にしない。
  - Loaderの未検証条件は同テスト仕様の正本に従って補う。Data位置逆引き、多数インポート、未登録名のハッシュ衝突を対象とする。
  - 単体30スイート、結合3スイートと12シナリオ、WASMワークロード3スイートの実行登録を維持する。変更に直接関係する試験を実行し、日付・環境・revision・skipを含む結果を記録する。
  - Pyrightは7ファイルの試行範囲である。拡張時は対象と検出する契約を明示し、試行範囲の合格をpysim全体の型検証完了としない。
- [ ] **参照実装の性能評価とJIT性能差の解消**:
  - 正本は [`BENCHMARK_REPORT.md`](experiments/pysim/benchmarks/BENCHMARK_REPORT.md) とする。2026-10-03のホスト測定ではHybrid JITがC++ Interpreterより遅い。
  - 算術ループとAO-Benchの性能差を実行経路ごとに測定する。入力、出力一致、ビルド条件、測定revisionを揃えて改善効果を判定する。
  - カードエイジングの既定値とキャッシュ圧迫条件を測定し、物理予算への影響はStep 2.4で確認する。
  - ホスト値をARMv8-Mの速度・資源量へ外挿しない。WAMR比較による要求達成の判定はPhase 1.4で行う。
- [ ] **Step 2.4: 資源予算の再見積もりとARMv8-M物理配置の確定**:
  - 詳細正本: [`resource_budget_estimation.md`](docs/architecture/resource_budget_estimation.md)
  - ランタイムアリーナの再計測は2026-10-05に完了した。4入力を2方式で各3回実行した。使用量、アラインメント、ワークスペース再利用、アリーナ外の線形メモリとJITコード領域を正本へ記録した。
  - JIT RAM削減後のx64参照計上値では、算術ループのHybrid JITの既知部分は15,624バイト、間接呼出しは16,816バイトとなる。候補PC配列を削除し、物理ヘッダを16バイトにした。実行回数カウンタの参照を含む現行dispatchワークスペースはモジュールごとに384〜6,272バイトとなる。記述枠上限32件/バンクはRAM予算の設定であり、実測平均サイズの保証ではない。未計上領域とJITコード領域8,192バイトを含め、対象ABIで全体適合を評価する。
  - 対象CPU・ボード、割込みstack、JIT領域、メモリ保護方式、リンカ配置を確定する。最小構成の静的合計21KB / 物理RAM 32KB / ROM 96KBへの適合を内訳と実測で確認する。
  - JIT管理情報・コンパイラ作業領域、システム共有領域の内訳を補う。アリーナ管理表1,344バイトは計測済みである。既定アリーナ上限131,072バイトを実使用量へ読み替えない。
  - **コード規模 (20 KSLOC)**: コメントとテストを除く製品ソースコードの上限は20,000 SLOCである。現行pysimはPython製品80ファイル、25,083物理行である。旧係数によるC++参考推定は取り下げた。現行構成をC++実測と同じSLOC条件で再見積もりする。
- [ ] **Step 2.5: オーナー（人間）による最終品質レビュー & Phase 1 GO 判定**:
  - 仕様・シミュレータコード・テスト設計・バジェットを Freeze し、C++23 実装フェーズ（Phase 1）への移行を最終承認

---

## Phase 1: vSoC First 実装（約3ヶ月） 【待機中 / オーナー GO 判定後に着手】
<!-- traceability: {META_AI_Native_Dev} {PositionIndependentCode} {JIT_CopyAndPatch} {ROMParsing} -->

スタンドアロン vSoC コア（Loader, Interpreter, JIT）を C++23 で実装し、ホストハーネス上で WAMR 比較ベンチマークを実施する。
**前提コンパイラ: Clang 17+ 必須（`[[clang::musttail]]` 前提、GCC/MSVC 非サポート）**。

### Phase 1.0: Core Utilities (`inc/common/`)
製品側のC++実装は [`main.cxx`](src/main.cxx)、`src/allocator/`、[`backtrace.cxx`](src/utils/backtrace.cxx) の基盤のみである。pysim配下のC++ Interpreterとx64 JITは参照実装として存在する。製品側の `inc/common/` とLoader以降のPhase 1完了とは区別する。現行のバンプアロケータは [`bump_allocator.hxx`](inc/allocator/bump_allocator.hxx) に存在する。

- [ ] **固定 SBO 多相関数ラッパー (`inc/common/economic_function.hxx`)**:
  - 16〜32B インラインバッファ内包、動的ヒープ確保排除、超過時コンパイル/アサート停止
- [ ] **バンプアロケータ (`inc/common/bump_allocator.hxx`; 現行基盤 [`bump_allocator.hxx`](inc/allocator/bump_allocator.hxx))**:
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

### Phase 1.2: WASM Stackless Fast Interpreter (`interpreter`)
- [ ] **`execution_context` & 独立3バッファスタック (`inc/runtime/interpreter.hxx`)**:
  - オペランド領域・ローカル値領域・制御ブロック復帰情報領域を独立管理する。ローカル変数基底を論理引数で渡し、物理配置は対象ABIで決める `{ContextPointerRegister}`
- [ ] **コア命令ハンドラ群 (`src/runtime/opcode_handlers.cxx`)**:
  - 継続渡し4論理引数シグネチャ（`ctx`, `sp`, `local_base`, `tos`）。物理配置は対象ABIで決める `{CPS_4Args}`
  - 算術・比較・変換・制御・メモリ操作ハンドラと `MemoryBoundaryCheck` トラップ `{MemoryBoundaryCheck}`
  - 分岐脱出時のフレームプルーニングと TOS 復元 (`GOTCHA-INTP-02`)
- [ ] **スレッド化ディスパッチャ (`src/runtime/dispatch.cxx`)**:
  - `[[clang::musttail]]` によるダイレクトスレッド実行と JIT レジスタ整合 `{ThreadedInterpreter}`
- [ ] **Interpreter 単体テストスイート (`tests/test_interpreter.cxx`)**:
  - WebAssembly 公式 Core テストスイート（Spec Tests）サブセットのパス確認

### Phase 1.3: Copy-and-Patch JIT Compiler & Runtime (`jit_compiler`, `jit_runtime`)
<!-- traceability: {Interpreter_LazyJITSwitch} -->
- [ ] **ARMv8-M実装仕様策定（TBD）**: 物理ABI、命令列、trace layout、dispatcher、メモリ保護、資源予算、実機検証条件をすべて未確定として扱う。
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
- [ ] **GDB Server（Tier 3 Plugins）**: [`debugger.md`](docs/components/tier3_plugins/debugger.md) に従うRSPサーバー。`Interpreter + Debugger`を静的構成し、JITとの同時構成を拒否する (`GOTCHA-DBG-01`〜`03`)

---

## Phase 3: PoC（ターゲットボード移植 / 将来予定）

- [ ] **ARMv8-M実機移植**: 対象ボード、OS、実機仕様はTBD。
- [ ] **ARMv8-M実機性能・応答性評価**: 最小構成のRAM 32KB / ROM 96KBへの適合を実測する。測定手順と協調境界での応答性条件はTBD。

---

## Phase 4: OSS & Production（継続）

- [ ] **OSS リリース準備**: ビルド手順、ドキュメント公開、サンプルプログラム
