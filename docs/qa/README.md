# 品質保証資料

`docs/qa/` は、複数コンポーネントにまたがる品質証跡と、その索引を管理する。

テスト仕様書は所有するTier別にこのディレクトリへ集約する。pysimの検証コードは、単体テスト、結合テスト、WASMバイナリワークロードテストに分類する。ベンチマークは性能測定のための独立した成果物とする。コンポーネント設計書、形式検証モデル、製品コードの正本は移動しない。

pysimの共有テスト支援は`experiments/pysim/qa/shared/`、スイート固有のテストダブルは`experiments/pysim/qa/private/`または各テストパッケージ内に置く。製品コードからQAモジュールをimportしない。

## pysimテストの分類

| 分類 | 対象と判定 | 実行入口 |
| :--- | :--- | :--- |
| 単体テスト | 一つのコンポーネントまたは単一契約を検証する。通常は他コンポーネント境界を実物の結線で通さない | [`run_all.py`](experiments/pysim/qa/run_all.py) |
| 結合テスト | コンポーネント境界を実際に結線し、状態・データ・制御の受渡しを検証する。HAL/IPC試験、ペアワイズ試験、12本の受入シナリオを含む | [`run_all.py`](experiments/pysim/qa/integration/run_all.py) |
| WASMバイナリワークロード | wasi-sdkでコンパイルしたwasi-libcゲスト、HAL用ゲスト、公式WebAssembly Core Spec Testsの固定セットを実行する | [`run_all.py`](experiments/pysim/qa/workloads/run_all.py) |
| ベンチマーク | 性能・資源量を計測する。機能適合テストの合格数へ加算しない | [`benchmarks`](experiments/pysim/benchmarks) |

単体テストは、試験対象の責務を単独で検証する。複数層のSDKゲスト試験や複数コンポーネントの結線を通るペアワイズ試験は、テスト関数が一つのファイルにあっても結合テストまたはWASMワークロードに分類する。`scenarios/`は既存パスを維持し、[`run_all.py`](experiments/pysim/qa/integration/run_all.py)から実行する。

`tier*/` と `specs/` の `*_test_spec.md` は [`FORMAT.md`](docs/qa/FORMAT.md) に従う。結合テストシナリオ、検証因子マトリクス、実行結果、バグ記録は本フォーマットの対象外とし、それぞれの資料の役割に応じた構成を持つ。

テスト実行結果、品質ゲートの実行結果、ベンチマーク結果もここに記録する。

テスト実行結果には、確認できる範囲で次の情報を記録する。

- 実行日、対象ソースとテストスイートの版
- 実行コマンド、実行環境
- 対象範囲と対象外の範囲
- 成功・失敗・skipの件数
- skipの理由と、結果から主張できない事項
- 関連する仕様書、テスト仕様書、実装へのリンク

| 資料 | 内容 |
| :--- | :--- |
| [architecture_review_report.md](docs/qa/architecture_review_report.md) | アーキテクチャ概要、下位仕様、形式モデル、WIT、pysim間のレビュー結果と修正順 |
| [integration_test_scenarios.md](docs/qa/integration_test_scenarios.md) | コンポーネント間の結合受入シナリオと実装検証範囲 |
| [verification_factor_matrix.md](docs/qa/verification_factor_matrix.md) | 検証因子、必要成果物、テスト仕様、シナリオの対応マトリクス |
| [wasm_core_mvp_test_results.md](docs/qa/wasm_core_mvp_test_results.md) | 選定したMVP互換WASTテストのファイル別成功・skip件数と集計上の制約 |
| [`run_all.py`](experiments/pysim/qa/workloads/run_all.py) | SDK/libcゲストとWebAssembly Core Spec Testsを実行する入口 |
| [pairwise_factors.csv](docs/qa/specs/pairwise_factors.csv) | 直交表テストで使う因子と水準の正本 |
| [README.md](docs/qa/bug_table/README.md) | `bug_table/` の索引。テストや計測で見つかった不具合の一覧と、不具合ごとの詳細記録 |

## テスト仕様書

コンポーネント別の仕様書は所有Tierで分類する。WASM命令セットの横断テスト仕様は`specs/`に置く。

| Tier | 対象 | テスト仕様書 |
| :--- | :--- | :--- |
| Tier 1 Core | COOS | [os_coos_test_spec.md](docs/qa/tier1_core/os_coos_test_spec.md) |
| Tier 1 Core | Scheduler | [os_scheduler_test_spec.md](docs/qa/tier1_core/os_scheduler_test_spec.md) |
| Tier 1 Core | System Config | [system_config_test_spec.md](docs/qa/tier1_core/system_config_test_spec.md) |
| Tier 1 Core | System Containers | [system_containers_test_spec.md](docs/qa/tier1_core/system_containers_test_spec.md) |
| Tier 3 Platform | WIT Interface | [interface_wit_test_spec.md](docs/qa/tier3_platform/interface_wit_test_spec.md) |
| Tier 1 Interface | IPC Router | [ipc_router_test_spec.md](docs/qa/tier1_interface/ipc_router_test_spec.md) |
| Tier 1 Interface | System Memory | [system_memory_test_spec.md](docs/qa/tier1_interface/system_memory_test_spec.md) |
| Tier 1 Interface | System Service | [system_service_test_spec.md](docs/qa/tier1_interface/system_service_test_spec.md) |
| Tier 3 Plugins | Debugger | [debugger_test_spec.md](docs/qa/tier3_plugins/debugger_test_spec.md) |
| Tier 2 Runtime | HAL Dispatch | [hal_dispatch_test_spec.md](docs/qa/tier2_runtime/hal_dispatch_test_spec.md) |
| Tier 2 Runtime | Interpreter | [interpreter_test_spec.md](docs/qa/tier2_runtime/interpreter_test_spec.md) |
| Tier 2 Runtime | Loader | [runtime_loader_test_spec.md](docs/qa/tier2_runtime/runtime_loader_test_spec.md) |
| Tier 2 Runtime | Logging | [runtime_logging_test_spec.md](docs/qa/tier2_runtime/runtime_logging_test_spec.md) |
| Tier 2 Runtime | Memory | [runtime_memory_test_spec.md](docs/qa/tier2_runtime/runtime_memory_test_spec.md) |
| Tier 2 Runtime | Syscall | [runtime_syscall_test_spec.md](docs/qa/tier2_runtime/runtime_syscall_test_spec.md) |
| Tier 2 Runtime | vMMIO | [runtime_vmmio_test_spec.md](docs/qa/tier2_runtime/runtime_vmmio_test_spec.md) |
| Tier 2 Runtime | vSoC | [runtime_vsoc_test_spec.md](docs/qa/tier2_runtime/runtime_vsoc_test_spec.md) |
| Tier 3 Plugins | JIT Compiler | [jit_compiler_test_spec.md](docs/qa/tier3_plugins/jit_compiler_test_spec.md) |
| Tier 3 Plugins | JIT Runtime | [jit_runtime_test_spec.md](docs/qa/tier3_plugins/jit_runtime_test_spec.md) |
| Tier 3 Platform | libfireball | [libfireball_test_spec.md](docs/qa/tier3_platform/libfireball_test_spec.md) |
| Tier 3 Platform | Platform Driver | [platform_driver_test_spec.md](docs/qa/tier3_platform/platform_driver_test_spec.md) |
| Cross-cutting Specs | WASM Instruction Set | [wasm_instruction_set_test_spec.md](docs/qa/specs/wasm_instruction_set_test_spec.md) |

`reports/doc_report.md` など検証ツールが生成する一時レポートは、生成物の配置規則に従い、このディレクトリへ複製しない。
