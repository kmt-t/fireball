# 製品仕様・証拠マトリクス

この文書は、コンポーネント設計、コンセプト、形式モデル、テスト仕様の仕様上の対応を管理する。各行は「何を検証するか」、各列は「どの仕様成果物が根拠か」を示す。

判定は次の順序で行う。

1. コンポーネント設計書を母集合とし、コンセプト、形式モデル、テスト仕様の対応を列挙する。
2. 責務上不要な成果物だけを `N/A` とする。`N/A` には理由を付け、単なる未作成の省略には使わない。
3. 各成果物の所有Tierと文書間リンクを対応させる。
4. ツールは母集合、件数、ファイル実在性を実データから検査する。期待値をテスト仕様へ重複記述しない。

`spec-integrator.yaml` の厳格なマトリクス検査は、この仕様成果物集合の完全性を確認する。成果物の追加・削除は設定と本表を同じ変更で更新する。実行テストスイート、結合シナリオ、ワークロードの登録は本表の対象に含めない。

## 2. 成果物連鎖マトリクス

`N/A` は責務上不要な仕様成果物に限る。共通のコンセプト、形式モデル、テスト仕様が複数コンポーネントを覆う場合は、それぞれの行から同じ成果物を参照する。

| Tier | コンポーネント | コンセプト | 形式モデル | テスト仕様 | 判定 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Tier 1 Core | `os_coos` | `coos_concept.py` | `coos_channel_model.py` | `os_coos_test_spec.md` | required |
| Tier 1 Core | `os_scheduler` | `scheduler_concept.py` | N/A（決定的キュー操作はテストで直接検証） | `os_scheduler_test_spec.md` | required |
| Tier 1 Core | `system_config` | N/A（静的設定の正本） | `system_config_model.py` | `system_config_test_spec.md` | required |
| Tier 1 Core | [`printk.md`](docs/components/tier1_core/printk.md) | N/A（診断出力契約） | N/A（診断出力の契約はテスト仕様で定義） | [`runtime_logging_test_spec.md`](docs/qa/tier2_runtime/runtime_logging_test_spec.md) | contract_only |
| Tier 1 Core | `system_containers` | `flat_view_concept.py` | N/A（コンテナ不変条件を直接検証） | `system_containers_test_spec.md` | required |
| Tier 3 Platform | `interface_wit` | N/A（WIT 契約） | N/A（WIT の構文・契約検証） | `interface_wit_test_spec.md` | contract_only |
| Tier 1 Interface | `ipc_router` | `ipc_router_concept.py` | `csp_handoff_model.py` | `ipc_router_test_spec.md` | required |
| Tier 1 Interface | `system_memory` | N/A（所有権契約） | `system_memory_model.py` | `system_memory_test_spec.md` | contract_only |
| Tier 1 Interface | `system_service` | `service_concept.py` | `service_fault_isolation_model.py`, `wit_resource_lifecycle_model.py` | `system_service_test_spec.md` | required |
| Tier 3 Plugins | `debugger` | `debugger_concept.py` | N/A（RSP の境界テストで直接検証） | `debugger_test_spec.md` | required |
| Tier 2 Runtime | `hal_dispatch` | N/A（WIT/ディスパッチ契約） | `hal_dispatch_contract_model.py` | `hal_dispatch_test_spec.md` | contract_only |
| Tier 2 Runtime | `jit_abi` | N/A（ABI 契約） | N/A（ABI の実レイアウトテストで検証） | N/A（ABI配置の検査条件は`jit_abi.md`を正本とする） | contract_only |
| Tier 2 Runtime | `interpreter` | `interpreter_concept.py`, `indirect_call_concept.py`, `bulk_memory_concept.py` | `interpreter_stack_model.py`, `interpreter_control_flow_model.py`, `interpreter_indirect_call_model.py`, `wasm_bulk_memory_model.py` | `interpreter_test_spec.md`, `wasm_instruction_set_test_spec.md` | required |
| Tier 2 Runtime | `runtime_loader` | `loader_concept.py` | `loader_verification_model.py` | `runtime_loader_test_spec.md` | required |
| Tier 2 Runtime | `runtime_logging` | `logging_concept.py` | `logging_flush_model.py` | `runtime_logging_test_spec.md` | required |
| Tier 2 Runtime | `runtime_memory` | `runtime_memory_concept.py` | `runtime_memory_model.py` | `runtime_memory_test_spec.md` | required |
| Tier 2 Runtime | `runtime_syscall` | `syscall_concept.py` | `syscall_trap_model.py` | `runtime_syscall_test_spec.md` | required |
| Tier 2 Runtime | `runtime_vmmio` | `vmmio_concept.py` | `vmmio_mapping_model.py` | `runtime_vmmio_test_spec.md` | required |
| Tier 2 Runtime | `runtime_vsoc` | N/A（旧統合モデルを現行契約との不一致により削除） | `vsoc_cache_coherency_model.py`, `vsoc_state_model.py` | `runtime_vsoc_test_spec.md` | required |
| Tier 2 Runtime | `runtime_plugin_architecture` | N/A（契約文書が責務と境界を定義） | N/A（C++生成物モデル未実装） | `runtime_plugin_architecture_test_spec.md` | contract_only |
| Tier 2 Runtime | `runtime_observability` | N/A（契約文書が責務と境界を定義） | N/A（内部実行イベント未統合） | `runtime_observability_test_spec.md` | contract_only |
| Tier 3 Plugins | `guest_profiler` | N/A（契約文書が責務と境界を定義） | `guest_profiler_model.py` | `guest_profiler_test_spec.md` | required |
| Tier 3 Plugins | `jit_compiler` | N/A (x64 implementation is covered directly; ARMv8-M is TBD) | `jit_cache_model.py` | `jit_compiler_test_spec.md`, `wasm_instruction_set_test_spec.md` | required |
| Tier 3 Plugins | `jit_runtime` | N/A (x64 runtime tests cover the confirmed path; ARMv8-M is TBD) | `jit_cache_model.py`, `jit_hotspot_model.py`, `jit_trace_execution_model.py`（QA計測契約） | `jit_runtime_test_spec.md` | required |
| Tier 3 Platform | `libfireball` | N/A（ゲスト側ABI契約を定義する） | N/A（raw Core Wasm ABIを実guestで検査） | `libfireball_test_spec.md` | contract_only |
| Tier 3 Platform | `platform_driver` | `platform_driver_concept.py` | `interrupt_boundary_model.py` | `platform_driver_test_spec.md` | required |

本表は製品仕様上の成果物連鎖を管理する。実行可能なテストスイートの在庫や実行結果は本表の対象外とする。

### 2.1 成果物インベントリ

以下の一覧は本表の母集合である。ツールは各パスの実在と本表への記載を検査する。

#### コンポーネント設計書

| Tier | パス |
| :--- | :--- |
| Tier 1 Core | [os_coos.md](docs/components/tier1_core/os_coos.md) |
| Tier 1 Core | [os_scheduler.md](docs/components/tier1_core/os_scheduler.md) |
| Tier 1 Core | [system_config.md](docs/components/tier1_core/system_config.md) |
| Tier 1 Core | [system_containers.md](docs/components/tier1_core/system_containers.md) |
| Tier 3 Platform | [interface_wit.md](docs/components/tier3_platform/interface_wit.md) |
| Tier 1 Interface | [ipc_router.md](docs/components/tier1_interface/ipc_router.md) |
| Tier 1 Interface | [system_memory.md](docs/components/tier1_interface/system_memory.md) |
| Tier 1 Interface | [system_service.md](docs/components/tier1_interface/system_service.md) |
| Tier 3 Plugins | [debugger.md](docs/components/tier3_plugins/debugger.md) |
| Tier 2 Runtime | [hal_dispatch.md](docs/components/tier2_runtime/hal_dispatch.md) |
| Tier 2 Runtime | [jit_abi.md](docs/components/tier2_runtime/jit_abi.md) |
| Tier 2 Runtime | [interpreter.md](docs/components/tier2_runtime/interpreter.md) |
| Tier 3 Plugins | [guest_profiler.md](docs/components/tier3_plugins/guest_profiler.md) |
| Tier 2 Runtime | [runtime_plugin_architecture.md](docs/components/tier2_runtime/runtime_plugin_architecture.md) |
| Tier 2 Runtime | [runtime_observability.md](docs/components/tier2_runtime/runtime_observability.md) |
| Tier 2 Runtime | [runtime_loader.md](docs/components/tier2_runtime/runtime_loader.md) |
| Tier 2 Runtime | [runtime_logging.md](docs/components/tier2_runtime/runtime_logging.md) |
| Tier 2 Runtime | [runtime_memory.md](docs/components/tier2_runtime/runtime_memory.md) |
| Tier 2 Runtime | [runtime_syscall.md](docs/components/tier2_runtime/runtime_syscall.md) |
| Tier 2 Runtime | [runtime_vmmio.md](docs/components/tier2_runtime/runtime_vmmio.md) |
| Tier 2 Runtime | [runtime_vsoc.md](docs/components/tier2_runtime/runtime_vsoc.md) |
| Tier 3 Plugins | [jit_compiler.md](docs/components/tier3_plugins/jit_compiler.md) |
| Tier 3 Plugins | [jit_runtime.md](docs/components/tier3_plugins/jit_runtime.md) |
| Tier 3 Platform | [libfireball.md](docs/components/tier3_platform/libfireball.md) |
| Tier 3 Platform | [platform_driver.md](docs/components/tier3_platform/platform_driver.md) |

#### コンセプト、形式モデル、テスト仕様

| 種別 | パス |
| :--- | :--- |
| concept | [coos_concept.py](docs/components/tier1_core/concepts/coos_concept.py) |
| concept | [flat_view_concept.py](docs/components/tier1_core/concepts/flat_view_concept.py) |
| concept | [scheduler_concept.py](docs/components/tier1_core/concepts/scheduler_concept.py) |
| concept | [ipc_router_concept.py](docs/components/tier1_interface/concepts/ipc_router_concept.py) |
| concept | [service_concept.py](docs/components/tier1_interface/concepts/service_concept.py) |
| concept | [debugger_concept.py](docs/components/tier3_plugins/concepts/debugger_concept.py) |
| concept | [interpreter_concept.py](docs/components/tier2_runtime/concepts/interpreter_concept.py) |
| concept | [indirect_call_concept.py](docs/components/tier2_runtime/concepts/indirect_call_concept.py) |
| concept | [bulk_memory_concept.py](docs/components/tier2_runtime/concepts/bulk_memory_concept.py) |
| concept | [loader_concept.py](docs/components/tier2_runtime/concepts/loader_concept.py) |
| concept | [logging_concept.py](docs/components/tier2_runtime/concepts/logging_concept.py) |
| concept | [runtime_memory_concept.py](docs/components/tier2_runtime/concepts/runtime_memory_concept.py) |
| concept | [syscall_concept.py](docs/components/tier2_runtime/concepts/syscall_concept.py) |
| concept | [vmmio_concept.py](docs/components/tier2_runtime/concepts/vmmio_concept.py) |
| concept | [platform_driver_concept.py](docs/components/tier3_platform/concepts/platform_driver_concept.py) |
| formal | [coos_channel_model.py](docs/components/tier1_core/formal/coos_channel_model.py) |
| formal | [system_config_model.py](docs/components/tier1_core/formal/system_config_model.py) |
| formal | [csp_handoff_model.py](docs/components/tier1_interface/formal/csp_handoff_model.py) |
| formal | [service_fault_isolation_model.py](docs/components/tier1_interface/formal/service_fault_isolation_model.py) |
| formal | [system_memory_model.py](docs/components/tier1_interface/formal/system_memory_model.py) |
| formal | [wit_resource_lifecycle_model.py](docs/components/tier3_platform/formal/wit_resource_lifecycle_model.py) |
| formal | [hal_dispatch_contract_model.py](docs/components/tier2_runtime/formal/hal_dispatch_contract_model.py) |
| formal | [interpreter_stack_model.py](docs/components/tier2_runtime/formal/interpreter_stack_model.py) |
| formal | [interpreter_control_flow_model.py](docs/components/tier2_runtime/formal/interpreter_control_flow_model.py) |
| formal | [interpreter_indirect_call_model.py](docs/components/tier2_runtime/formal/interpreter_indirect_call_model.py) |
| formal | [wasm_bulk_memory_model.py](docs/specs/formal/wasm_bulk_memory_model.py) |
| formal | [guest_profiler_model.py](docs/components/tier3_plugins/formal/guest_profiler_model.py) |
| formal | [loader_verification_model.py](docs/components/tier2_runtime/formal/loader_verification_model.py) |
| formal | [logging_flush_model.py](docs/components/tier2_runtime/formal/logging_flush_model.py) |
| formal | [runtime_memory_model.py](docs/components/tier2_runtime/formal/runtime_memory_model.py) |
| formal | [syscall_trap_model.py](docs/components/tier2_runtime/formal/syscall_trap_model.py) |
| formal | [vmmio_mapping_model.py](docs/components/tier2_runtime/formal/vmmio_mapping_model.py) |
| formal | [vsoc_cache_coherency_model.py](docs/components/tier2_runtime/formal/vsoc_cache_coherency_model.py) |
| formal | [vsoc_state_model.py](docs/components/tier2_runtime/formal/vsoc_state_model.py) |
| formal | [jit_cache_model.py](docs/components/tier3_plugins/formal/jit_cache_model.py) |
| formal | [jit_hotspot_model.py](docs/components/tier3_plugins/formal/jit_hotspot_model.py) |
| formal | [jit_trace_execution_model.py](docs/components/tier3_plugins/formal/jit_trace_execution_model.py) |
| formal | [jit_loop_chain_model.py](docs/components/tier3_plugins/formal/jit_loop_chain_model.py) |
| formal | [interrupt_boundary_model.py](docs/components/tier3_platform/formal/interrupt_boundary_model.py) |
| test spec | [os_coos_test_spec.md](docs/qa/tier1_core/os_coos_test_spec.md) |
| test spec | [os_scheduler_test_spec.md](docs/qa/tier1_core/os_scheduler_test_spec.md) |
| test spec | [system_config_test_spec.md](docs/qa/tier1_core/system_config_test_spec.md) |
| test spec | [system_containers_test_spec.md](docs/qa/tier1_core/system_containers_test_spec.md) |
| test spec | [interface_wit_test_spec.md](docs/qa/tier3_platform/interface_wit_test_spec.md) |
| test spec | [ipc_router_test_spec.md](docs/qa/tier1_interface/ipc_router_test_spec.md) |
| test spec | [system_memory_test_spec.md](docs/qa/tier1_interface/system_memory_test_spec.md) |
| test spec | [system_service_test_spec.md](docs/qa/tier1_interface/system_service_test_spec.md) |
| test spec | [debugger_test_spec.md](docs/qa/tier3_plugins/debugger_test_spec.md) |
| test spec | [guest_profiler_test_spec.md](docs/qa/tier3_plugins/guest_profiler_test_spec.md) |
| test spec | [hal_dispatch_test_spec.md](docs/qa/tier2_runtime/hal_dispatch_test_spec.md) |
| test spec | [interpreter_test_spec.md](docs/qa/tier2_runtime/interpreter_test_spec.md) |
| test spec | [wasm_instruction_set_test_spec.md](docs/qa/specs/wasm_instruction_set_test_spec.md) |
| test spec | [runtime_loader_test_spec.md](docs/qa/tier2_runtime/runtime_loader_test_spec.md) |
| test spec | [runtime_logging_test_spec.md](docs/qa/tier2_runtime/runtime_logging_test_spec.md) |
| test spec | [runtime_observability_test_spec.md](docs/qa/tier2_runtime/runtime_observability_test_spec.md) |
| test spec | [runtime_memory_test_spec.md](docs/qa/tier2_runtime/runtime_memory_test_spec.md) |
| test spec | [runtime_plugin_architecture_test_spec.md](docs/qa/tier2_runtime/runtime_plugin_architecture_test_spec.md) |
| test spec | [runtime_syscall_test_spec.md](docs/qa/tier2_runtime/runtime_syscall_test_spec.md) |
| test spec | [runtime_vmmio_test_spec.md](docs/qa/tier2_runtime/runtime_vmmio_test_spec.md) |
| test spec | [runtime_vsoc_test_spec.md](docs/qa/tier2_runtime/runtime_vsoc_test_spec.md) |
| test spec | [jit_compiler_test_spec.md](docs/qa/tier3_plugins/jit_compiler_test_spec.md) |
| test spec | [jit_runtime_test_spec.md](docs/qa/tier3_plugins/jit_runtime_test_spec.md) |
| test spec | [libfireball_test_spec.md](docs/qa/tier3_platform/libfireball_test_spec.md) |
| test spec | [platform_driver_test_spec.md](docs/qa/tier3_platform/platform_driver_test_spec.md) |

## 3. 適用範囲

本表の母集合は製品コンポーネント設計書、独立コンセプトコード、形式モデル、テスト仕様書である。テスト仕様書は設計正本から期待結果を導出し、実装の現在値から期待結果を逆算しない。

N/A は責務上その仕様成果物が不要である場合だけに使い、理由を行内に記す。実行結果は、製品仕様成果物の不足を埋める証拠として数えない。

## 4. 更新規則

- コンポーネント、コンセプト、形式モデル、テスト仕様の追加・削除は、対応する行と成果物インベントリを同じ変更で更新する。
- 形式モデルが不要な契約は、理由を明記し、対応するテスト仕様を記載する。
- 因子組合せを使う別の検証体系は、所有する実行環境の資料で因子、水準、実行登録を管理する。
