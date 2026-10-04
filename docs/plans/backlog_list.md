# Fireball アクティブバックログ

未完了の作業を記載する。全体のフェーズは [`roadmap_phase.md`](docs/plans/roadmap_phase.md) を参照する。
予算と計測値の正本は [`resource_budget_estimation.md`](docs/architecture/resource_budget_estimation.md) とする。
検証の実行手順は [`README.md`](tools/README.md) に従う。

更新日: **2026-10-05**。現在はPhase 0のStep 2を進行中である。
Phase 1の開始はオーナーのGO判定を待つ。

## Phase 0: 参照実装と設計の残課題

<!-- traceability: {META_SpecificationFirst} {Resource_Estimation_Model} {Size_20KSLOC} {LowLatencyJIT} {ADR_TaskIdLifetime} {ADR_RendezvousChannel} {ADR_JitCompileScheduling} {ADR_RuntimeEventRetention} {Challenge_JITCacheEfficiency} {ADR_InterruptRescheduleGeneration} {JIT_CardAgingSweep} {Pairwise_Combinatorial_Testing} -->

- [ ] **採用ADRの実装追従**: タスクIDの解放順序、IPC応答時の待機、イベント履歴の上書きを実装・検証する。JITコンパイルキューの通常予算と満杯時処理も反映する。
- [ ] **JITスラッシング対策**: 実行回数と破棄記録から抑止対象を決める。適用範囲、抑止条件、再許可条件を確定する。
- [ ] **Loaderの利得表校正**: 命令利得と候補選定を現行compilerの対応範囲へ揃える。
- [ ] **概念コードの同期**: Interpreterの状態・ABI、Loggingのprintk境界、Debuggerのレジスタ配置とRSPを現行仕様へ揃える。JIT chainと制御handlerの境界も照合する。
- [ ] **未検証経路の補完**: COOSの世代保留中CSP経路、Loaderの登録・リンク・索引、Data位置逆引き、インポート衝突を直接検証する。追加した不変条件を仕様・形式モデル・テストへ対応付ける。
- [ ] **参照実装の性能評価**: 算術ループとAO-BenchのJIT・Interpreter経路を分解して測る。検索とカードエイジングの費用を同一条件で評価する。
- [ ] **コンパイル作業領域の削減設計**: 命令数、出力長、stack深度に対応する容量と再利用方式を決める。実行時RAMと同時に使う領域を含めて評価する。
- [ ] **ARMv8-Mの仕様と予算確定**: 対象ボード、物理ABI、命令生成、JIT配置、MPU/W^X、stackと共有領域を設計する。RAM 32KB・ROM 96KB、静的合計21KB、製品20 KSLOCへの適合を確認する。
- [ ] **文書と検証義務の同期**: READMEのTier説明を修正し、変更仕様の証跡と監査義務を更新する。LLM監査はユーザーの明示指示時に行う。
- [ ] **Phase 1移行判定**: 仕様、参照実装、検証、予算をレビューし、オーナーがGOを判定する。

## Phase 1: C++23によるvSoC実装

<!-- traceability: {META_AI_Native_Dev} {PositionIndependentCode} {ROMParsing} {META_AccessDictionary} {LightweightVerifier} {ContextPointerRegister} {CPS_4Args} {MemoryBoundaryCheck} {ThreadedInterpreter} {JIT_CopyAndPatch} {Interpreter_LazyJITSwitch} -->

- [ ] **共通基盤**: 固定容量の関数ラッパー、バンプアロケータ、エラー伝播、型付き非所有ビューを整備する。
- [ ] **Loader**: ROMの直接参照、LEB128、セクション索引、検証と失敗時のロールバックを実装する。
- [ ] **Interpreter**: 独立した3本の領域、4論理引数の継続、musttail dispatch、命令handlerとtrapを実装する。
- [ ] **JIT**: 確定した対象ABIに従ってコード生成、cache、継続、yield/fallbackを実装・検証する。
- [ ] **ホストハーネスとWAMR比較**: Core Spec選択セットと代表入力で正当性、速度、RAM/ROM、起動時間を評価する。

## Phase 2以降

<!-- traceability: {META_3TierSeparation} {GLOBAL_UseCpp20Coroutine} {UnifiedAccessModel} -->

- [ ] **Phase 2: 周辺統合**: COOS、IPC、vMMIO、HAL/WASI、GDBをC++実装へ統合する。
- [ ] **Phase 3: 実機評価**: ARMv8-Mへ移植し、容量、性能、協調境界の応答性を確認する。
- [ ] **Phase 4: 公開準備**: ビルド手順、公開文書、サンプルとリリースを整備する。
