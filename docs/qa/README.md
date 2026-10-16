# 品質保証資料

`docs/qa/` は、製品の検証条件、期待結果、横断的な検証証跡を管理する。テスト仕様書は対応する設計正本から導出し、試験ケースID、前提条件、手順、期待結果を定義する。実装コードの配置や実行手順は本ディレクトリで管理しない。

`*_test_spec.md` は [`FORMAT.md`](docs/qa/FORMAT.md) に従う。製品の結合受入条件と検証結果は各資料の目的に従って記述する。

## 資料

| 資料 | 内容 |
| :--- | :--- |
| [architecture_review_report.md](docs/qa/architecture_review_report.md) | アーキテクチャと設計資料のレビュー結果 |
| [integration_test_scenarios.md](docs/qa/integration_test_scenarios.md) | コンポーネント間の結合受入シナリオ |
| [product_verification_matrix.md](docs/qa/product_verification_matrix.md) | 製品仕様、コンセプト、形式モデル、テスト仕様の対応マトリクス |
| [wasm_core_mvp_test_results.md](docs/qa/wasm_core_mvp_test_results.md) | 選定したWASM Core Spec試験の結果 |

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
