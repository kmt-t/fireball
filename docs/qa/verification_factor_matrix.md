# 検証因子・成果物マトリクス

<!-- traceability: {Pairwise_Combinatorial_Testing} -->

## 1. 目的と判定規則

この文書は、コンセプト、形式モデル、テスト仕様、pysim テスト、結合シナリオ、および検証ツールを同じ因子表で管理するための正本である。各行は「何を検証するか」、各列は「どの検証手段で証明するか」を表す。因子の組合せを少ないケースで網羅するペアワイズ組合せテストを {Pairwise_Combinatorial_Testing} と定義する。 <!-- definition: {Pairwise_Combinatorial_Testing} -->

判定は次の順序で行う。

1. コンポーネント設計書を母集合とし、コンセプト、形式モデル、テスト仕様、実行テスト、シナリオの対応を列挙する。
2. 責務上不要な成果物だけを `N/A` とする。`N/A` には理由を付け、単なる未作成の省略には使わない。
3. 因子の水準を固定し、シナリオは因子の組み合わせとして記録する。
4. ツールは母集合、登録数、ファイル実在性、ペアワイズ被覆を実データから検査する。期待値をテスト本文へ重複記述しない。

`spec-integrator.yaml` の `verification_matrix.strict: true` がこの判定を有効にする。成果物の追加・削除、シナリオの追加、因子の変更は、設定と本表と実行登録を同じ変更で更新する。因子の水準は [`pairwise_factors.csv`](docs/qa/specs/pairwise_factors.csv) に一元化し、設定ファイルへ複製しない。

## 2. 成果物連鎖マトリクス

`N/A` は責務上不要な欄であり、所有している検証成果物を別の欄へ移したことを意味しない。結合テストやクロスカッティングテストで複数コンポーネントを検証する場合は、同じテストを複数行から参照する。

| Tier | コンポーネント | コンセプト | 形式モデル | テスト仕様 | 単体テスト | 結合テスト | WASMワークロード | 結合シナリオ | 判定 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Tier 1 Core | `os_coos` | `coos_concept.py` | `coos_channel_model.py` | `os_coos_test_spec.md` | `test_coos.py` | — | — | 6, 9 | required |
| Tier 1 Core | `os_scheduler` | `scheduler_concept.py` | N/A（決定的キュー操作はテストで直接検証） | `os_scheduler_test_spec.md` | `test_scheduler.py` | — | — | 6, 9 | required |
| Tier 1 Core | `system_config` | N/A（静的設定の正本） | `system_config_model.py` | `system_config_test_spec.md` | `test_scheduler.py`, `test_memory.py` | — | — | 1, 10 | required |
| Tier 1 Core | `system_containers` | `flat_view_concept.py` | N/A（コンテナ不変条件を直接検証） | `system_containers_test_spec.md` | `test_containers.py` | — | — | 1, 4, 5, 8, 9 | required |
| Tier 3 Platform | `interface_wit` | N/A（WIT 契約） | N/A（WIT の構文・契約検証） | `interface_wit_test_spec.md` | `test_syscall.py`, `test_libfireball.py` | — | — | 2, 11, 12 | contract_only |
| Tier 1 Interface | `ipc_router` | `ipc_router_concept.py` | `csp_handoff_model.py` | `ipc_router_test_spec.md` | `test_ipc_router.py` | — | `test_wasi_guest.py` | 9 | required |
| Tier 1 Interface | `system_memory` | N/A（所有権契約） | `system_memory_model.py` | `system_memory_test_spec.md` | `test_memory.py` | — | — | 1, 8, 10 | contract_only |
| Tier 1 Interface | `system_service` | `service_concept.py` | `service_fault_isolation_model.py`, `wit_resource_lifecycle_model.py` | `system_service_test_spec.md` | `test_ipc_router.py`, `test_vsoc.py` | — | — | 2, 6, 9, 11 | required |
| Tier 3 Plugins | `debugger` | `debugger_concept.py` | N/A（RSP の境界テストで直接検証） | `debugger_test_spec.md` | `test_debugger.py`, `test_gdb_remote.py` | [`test_lldb_debugger.py`](experiments/pysim/qa/integration/test_lldb_debugger.py) | — | 7, 8 | required |
| Tier 2 Runtime | `hal_dispatch` | N/A（WIT/ディスパッチ契約） | `hal_dispatch_contract_model.py` | `hal_dispatch_test_spec.md` | `test_hal.py`, `test_syscall.py` | [`test_driver_stubs.py`](experiments/pysim/qa/integration/test_driver_stubs.py) | `test_wasi_guest.py`, `test_driver_stubs_guest.py` | 10, 11 | contract_only |
| Tier 2 Runtime | `jit_abi` | N/A（ABI 契約） | N/A（ABI の実レイアウトテストで検証） | N/A（ABI配置の検査条件は`jit_abi.md`を正本とする） | `test_interpreter_abi.py`, `test_x64_jit.py` | — | — | 4, 5, 8 | contract_only |
| Tier 2 Runtime | `interpreter` | `interpreter_concept.py`, `bulk_memory_concept.py` | `interpreter_stack_model.py`, `interpreter_control_flow_model.py`, `wasm_bulk_memory_model.py` | `interpreter_test_spec.md`, `wasm_instruction_set_test_spec.md` | `test_interpreter.py`, `test_wasm_differential.py`, `test_gotchas.py` | — | `test_wasm_core_spec.py` | 1〜12 | required |
| Tier 2 Runtime | `runtime_loader` | `loader_concept.py` | `loader_verification_model.py` | `runtime_loader_test_spec.md` | `test_wasm_reader.py` | — | — | 1, 3, 5, 8, 12 | required |
| Tier 2 Runtime | `runtime_logging` | `logging_concept.py` | `logging_flush_model.py` | `runtime_logging_test_spec.md` | `test_logging.py` | — | — | 9 | required |
| Tier 2 Runtime | `runtime_memory` | `runtime_memory_concept.py` | `runtime_memory_model.py` | `runtime_memory_test_spec.md` | `test_memory.py`, `test_vmmio.py` | — | — | 1, 4, 8, 10 | required |
| Tier 2 Runtime | `runtime_syscall` | `syscall_concept.py` | `syscall_trap_model.py` | `runtime_syscall_test_spec.md` | `test_syscall.py`, `test_vdma.py` | — | `test_wasi_guest.py` | 2, 10, 11, 12 | required |
| Tier 2 Runtime | `runtime_vmmio` | `vmmio_concept.py` | `vmmio_mapping_model.py` | `runtime_vmmio_test_spec.md` | `test_vmmio.py`, `test_syscall.py`, `test_vdma.py` | — | — | 10, 11 | required |
| Tier 2 Runtime | `runtime_vsoc` | N/A（旧統合モデルは現行C++ dispatcherと共通chain dispatcherを表現していないため削除。実装テストと形式モデルで検証） | `vsoc_cache_coherency_model.py`, `vsoc_state_model.py` | `runtime_vsoc_test_spec.md` | `test_vsoc.py`, `test_vdma.py`, `test_recovery.py` | `test_pairwise_combinations.py` | — | 4, 5, 6, 8, 10 | required |
| Tier 2 Runtime | `runtime_plugin_architecture` | `runtime_composer.py`（pysim構成モデル） | N/A（C++生成物モデル未実装） | `runtime_plugin_architecture_test_spec.md` | `test_runtime_composer.py` | — | — | N/A（実機Runtime未統合） | contract_only |
| Tier 2 Runtime | `runtime_observability` | `runtime_events.py`, `runtime_composer.py`（公開call境界のみ） | N/A（内部実行イベント未統合） | `runtime_observability_test_spec.md` | `test_runtime_composer.py` | — | — | N/A（実機Runtime未統合） | contract_only |
| Tier 3 Plugins | `guest_profiler` | `guest_profiler.py` | `guest_profiler_model.py` | `guest_profiler_test_spec.md` | `test_guest_profiler.py` | — | — | `test_runtime_composer.py` | required |
| Tier 3 Plugins | `jit_compiler` | N/A (x64 implementation is covered directly; ARMv8-M is TBD) | `jit_cache_model.py` | `jit_compiler_test_spec.md`, `wasm_instruction_set_test_spec.md` | `test_x64_jit.py`, `test_exec_memory.py` | — | — | 4, 5, 8 | required |
| Tier 3 Plugins | `jit_runtime` | N/A (x64 runtime tests cover the confirmed path; ARMv8-M is TBD) | `jit_cache_model.py`, `jit_hotspot_model.py` | `jit_runtime_test_spec.md` | `test_jit_runtime.py`, `test_x64_jit.py`, `test_jit_differential.py` | — | — | 4, 5, 8 | required |
| Tier 3 Platform | `libfireball` | `libfireball.py`（pysim host-call参照モデルのみ） | N/A（raw Core Wasm ABIを実guestで検査） | `libfireball_test_spec.md` | `test_libfireball.py` | — | — | N/A（raw 4 import guest統合。実機ABIは対象外） | contract_only |
| Tier 3 Platform | `platform_driver` | `platform_driver_concept.py` | `interrupt_boundary_model.py` | `platform_driver_test_spec.md` | `test_hal.py` | [`test_driver_stubs.py`](experiments/pysim/qa/integration/test_driver_stubs.py) | `test_wasi_guest.py`, `test_driver_stubs_guest.py` | 10, 11 | required |

単体テストは [`run_all.py`](experiments/pysim/qa/run_all.py)、結合テストは [`run_all.py`](experiments/pysim/qa/integration/run_all.py)、WASMワークロードは [`run_all.py`](experiments/pysim/qa/workloads/run_all.py) をそれぞれ正本とする。各ランナーは試験関数を持つ全モジュールと登録一覧を照合し、未登録、重複、試験がない登録を実行前に拒否する。表の `—` は、その分類の専用試験を本行に割り当てていないことを示す。ケースごとの要求適合と未検証範囲は各テスト仕様を参照する。

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

## 3. 因子カタログ

ペアワイズテストの因子は7個とし、各水準はQA資料の [pairwise_factors.csv](docs/qa/specs/pairwise_factors.csv) だけで定義する。`PAIRWISE_CASES` はCSVの水準を使う実行データであり、因子名や水準を再定義しない。`n` 個の水準を持つ2因子の組は `n_left × n_right` 個と数える。

| 因子 | 意味 | 水準 |
| :--- | :--- | :--- |
| F1 `engine` | 命令実行経路 | `interp`, `jit`, `hybrid` |
| F2 `cache` | JIT キャッシュ状態 | `cold`, `warm`, `evict`, `flush` |
| F3 `mem_width` | メモリ操作・拡張 | `8bit`, `16bit`, `32bit`, `grow` |
| F4 `storage` | 値の保管場所 | `locals`, `globals`, `ram`, `shm` |
| F5 `host_call` | ホスト境界 | `none`, `wasi_console`, `wasi_vfs`, `ipc`, `hal` |
| F6 `scheduler` | 実行制御 | `noint`, `yield`, `multi` |
| F7 `debugger` | デバッガ状態 | `detached`, `inspect`, `active` |

構成制約として、`F7=inspect` または `F7=active` の場合は `F1=interp` だけを実行構成として許可する。`F1=jit` または `F1=hybrid` との組み合わせは有効なワークロードとして実行せず、`RuntimeComposer` の構成時 `assert` による拒否を検証する。ペアワイズ実行データにはこの禁止組み合わせも負の構成テストとして含める。

### 3.1 ペアワイズ計画の静的被覆

26ケースの各行は7因子を一度ずつ持ち、全因子対の組を少なくとも一度含む。組数は次のとおりである。

| 因子対 | 組数 |
| :--- | ---: |
| F1×F2 | 12 |
| F1×F3 | 12 |
| F1×F4 | 12 |
| F1×F5 | 15 |
| F1×F6 | 9 |
| F1×F7 | 9 |
| F2×F3 | 16 |
| F2×F4 | 16 |
| F2×F5 | 20 |
| F2×F6 | 12 |
| F2×F7 | 12 |
| F3×F4 | 16 |
| F3×F5 | 20 |
| F3×F6 | 12 |
| F3×F7 | 12 |
| F4×F5 | 20 |
| F4×F6 | 12 |
| F4×F7 | 12 |
| F5×F6 | 15 |
| F5×F7 | 15 |
| F6×F7 | 9 |
| 合計 | 288 |

実行ケースは [test_pairwise_combinations.py](experiments/pysim/qa/integration/test_pairwise_combinations.py) の `PAIRWISE_CASES` に定義し、ケースIDは行番号から `pairwise_case_id` が自動生成する。ケースデータへIDを書かないため、行の挿入・削除で手書きIDがずれない。ツールは生成IDごとに `run_single_pairwise_case` の呼び出しが存在することを確認し、対応する実行がなければエラーにする。

### 3.2 実行で観測する範囲

288組は26行の計画データの静的被覆を表す。
テストはCSVから導いた期待組の集合と計画組の集合を比較する。
実行では許可された12行のワークロードと、禁止された14行の構成拒否を区別する。
禁止された行では実行器の生成が0件であることを検査する。
その行の他因子の動作を実行済みとして数えない。

許可された行では次の状態と副作用を直接比較する。

| 因子 | 実行時の観測 |
| :--- | :--- |
| 命令実行経路 | `interp`はJIT managerなしで構成し、manager不在とJIT呼出し0件を確認する。`jit`では事前に実トレースを配置し、`hybrid`ではホットスポットから生成する。両者とも生成コードの呼出しと独立した計算結果を確認する |
| キャッシュ | JITを使う5行で空のcacheまたはresident traceのevictionを確認する。`warm`と`flush`は許可されたJIT行に存在しないため、この26行では動作を検証しない。対応する単体試験は[`jit_runtime_test_spec.md`](docs/qa/tier3_plugins/jit_runtime_test_spec.md)を正本とする。Interpreter専用7行のcache水準は適用外である |
| メモリ操作・拡張 | 水準に対応する幅のstoreを実行する。隣接sentinelを含む全ゲストメモリを比較する。`grow`ではWASMの`memory.grow`の旧ページ数と追加ページのゼロ初期化を確認する |
| 値の保管場所 | 水準に応じてlocal、global、RAM、SHMを更新する。SHMでは所有者と物理対応を設定し、ゲストload/store後の全物理バイト列を比較する |
| ホスト境界 | ゲストimportから既存WASI、IPC、HAL経路を呼ぶ。引数、応答、stdout、VFSの読出しバイトとcursor、IPC応答後のHAL受信待ち状態を比較する。VFSの外部環境には決定的なテスト用fixtureを使う |
| 実行制御 | `noint`では呼出し側がRuntimeEngineを同期駆動する。`yield`では既存のLOOP後方分岐境界をCOOSへ渡す。`multi`では別のCOOS coroutineと交互に進め、両タスクの結果と終了状態を比較する |
| デバッガ | 許可された行の`inspect`と`active`は、既存の静的デバッグ構成を使う独立した補助試験で確認する。レジスタ、PC、stack、breakpoint停止、再開結果を比較する。主ワークロードとデバッガの同時実行はこの補助試験の証拠に含めない |

この実行範囲を全288組の動的検証や、全因子を同時に結線した構成の完全被覆として扱わない。
実機の性能、物理ドライバ、未実装の製品経路の適合性は、この試験の合否から導かない。

## 4. シナリオ因子マトリクス

シナリオは単一機能の確認ではなく、次の因子を組み合わせた受入ケースとして扱う。`✓` はシナリオの主検証責務、`補` は副次的に状態を確認することを示す。

| Scenario | Loader | Interpreter | JIT | Memory / Storage | COOS / Scheduler | IPC | vMMIO / HAL / WASI | Debug | Multi-module / URI | 実行ファイル |
| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| 1 | ✓ | ✓ | — | linear / segments / grow | — | — | — | — | — | [scenario1_loader_and_memory.py](experiments/pysim/qa/scenarios/scenario1_loader_and_memory.py) |
| 2 | — | ✓ | — | linear | — | — | WASI / syscall | — | URI | [scenario2_wasi_syscall_io.py](experiments/pysim/qa/scenarios/scenario2_wasi_syscall_io.py) |
| 3 | ✓ | ✓ | — | locals / table | 補 | — | — | — | indirect call | [scenario3_recursion_and_tables.py](experiments/pysim/qa/scenarios/scenario3_recursion_and_tables.py) |
| 4 | 補 | ✓ | ✓ | linear / cache | 補 | — | — | — | — | [scenario4_hybrid_jit_loop.py](experiments/pysim/qa/scenarios/scenario4_hybrid_jit_loop.py) |
| 5 | 補 | ✓ | ✓ | sparse JIT lookup / Code-section PC | — | — | — | — | multi-module | [scenario5_multimodule_unified_pc.py](experiments/pysim/qa/scenarios/scenario5_multimodule_unified_pc.py) |
| 6 | — | ✓ | — | task state | ✓ | 補 | — | — | — | [scenario6_coos_multitask_yield.py](experiments/pysim/qa/scenarios/scenario6_coos_multitask_yield.py) |
| 7 | — | 補 | — | memory view | — | — | — | ✓ | socket RSP | [scenario7_gdb_socket_debugger.py](experiments/pysim/qa/scenarios/scenario7_gdb_socket_debugger.py) |
| 8 | ✓ | ✓ | ✓ | all storage views | 補 | — | — | ✓ | — | [scenario8_comprehensive_storage_coverage.py](experiments/pysim/qa/scenarios/scenario8_comprehensive_storage_coverage.py) |
| 9 | — | 補 | — | log / shared block | ✓ | ✓ | — | — | — | [scenario9_ipc_router_and_logging.py](experiments/pysim/qa/scenarios/scenario9_ipc_router_and_logging.py) |
| 10 | — | ✓ | — | linear / vMMIO | — | — | vMMIO | — | — | [scenario10_vmmio_virtual_devices.py](experiments/pysim/qa/scenarios/scenario10_vmmio_virtual_devices.py) |
| 11 | — | ✓ | — | fixed HAL buffer | 補 | — | HAL / stdout / stdin / timer | — | WASI adapter | [scenario11_hal_and_wasi_drivers.py](experiments/pysim/qa/scenarios/scenario11_hal_and_wasi_drivers.py) |
| 12 | ✓ | ✓ | — | guest memory | — | — | WASI 0.3p | — | URI resolver | [scenario12_wasi03p_uri_resolver.py](experiments/pysim/qa/scenarios/scenario12_wasi03p_uri_resolver.py) |

シナリオ集合は12件で、ランナー [run_all.py](experiments/pysim/qa/scenarios/run_all.py) に全件を登録する。ファイルだけ存在してランナーから呼ばれないケースは合格としない。

## 5. テスト因子から実装不変条件への対応

| 因子 | 直接確認する不変条件 | 主テスト | 失敗時の扱い |
| :--- | :--- | :--- | :--- |
| `engine` | Interpreter と JIT が同じ Native スタック状態を観測する | `test_interpreter.py`, `test_x64_jit.py`, `test_wasm_differential.py` | `assert` で停止 |
| `cache` | hit、eviction、flush 後の実行経路が一意になる | `test_jit_runtime.py`, `test_x64_jit.py`, `test_jit_differential.py` | `assert` で停止 |
| `mem_width` | WASM の幅、符号拡張、境界、grow 後の位置が一致する | `test_interpreter.py`, `test_wasm_reader.py`, `test_syscall.py` | `assert` で停止 |
| `storage` | storage が所有し、view は非所有である。所有権を共有しない | `test_containers.py`, `test_memory.py`, `test_ipc_router.py` | `assert` で停止 |
| `host_call` | WASI、IPC、HAL の責務境界とバッファ権限が混ざらない | `test_syscall.py`, `test_ipc_router.py`, `test_hal.py` | `assert` で停止 |
| `scheduler` | current task はスケジューラだけが変更し、yield 後に継続状態を保持する | `test_scheduler.py`, `test_coos.py`, `test_vsoc.py` | `assert` で停止 |
| `debugger` | 読み取り、書き込み、breakpoint、およびInterpreter-only構成の境界が明示される | `test_debugger.py`, `test_gdb_remote.py`, `test_runtime_composer.py` | `assert` で停止 |

## 6. 検証ゲートマトリクス

| 段階 | ツール | 入力 | 厳格な合格条件 |
| :--- | :--- | :--- | :--- |
| 設定・成果物 | [check-verification-matrix.ps1](tools/check-verification-matrix.ps1) | [spec-integrator.yaml](spec-integrator.yaml)、本表、各成果物 | パス実在、件数一致、`N/A` 理由あり、登録漏れなし |
| 文書 | [check-doc.ps1](tools/check-doc.ps1) | [docs](docs)、各Markdown | 既存の文書8ゲートに加え、本表ゲートも合格 |
| ソース | [check-src.ps1](tools/check-src.ps1) | [pysim](experiments/pysim)、各Python | pysim 規約、import Tier、テスト実行、本表ゲートが合格 |
| 形式検証 | [check-src.ps1](tools/check-src.ps1) | コンポーネント配下の各形式モデル | `pyModelChecking` 実行、guards 変異検査、モデル件数一致 |
| 単体テスト | [run_all.py](experiments/pysim/qa/run_all.py) | 30 suite | コンポーネント単位の試験を登録・実行する |
| 結合テスト | [run_all.py](experiments/pysim/qa/integration/run_all.py) | 3 suites + 12 scenarios | HAL/IPC結合試験、ペアワイズ試験、LLDB/RSP実プロセス結合試験、システム結合シナリオを実行する。ペアワイズは7因子、26ケースで288組を比較し、許可12行の状態・副作用と禁止14行の事前拒否を検証する |
| WASMバイナリワークロード | [run_all.py](experiments/pysim/qa/workloads/run_all.py) | 3 suites | [`test_wasi_guest.py`](experiments/pysim/qa/workloads/test_wasi_guest.py)、[`test_driver_stubs_guest.py`](experiments/pysim/qa/workloads/test_driver_stubs_guest.py)、[`test_wasm_core_spec.py`](experiments/pysim/qa/workloads/test_wasm_core_spec.py) を実行する。WASI-SDK/wasi-libcから生成した2種のゲストと、固定したWebAssembly Core Spec Testsの選択セットを含む。準備・対象範囲・skip理由は各実行結果に記録する |
| ベンチマーク | [benchmarks](experiments/pysim/benchmarks) | — | 性能・資源計測であり、機能適合テストの合格数には含めない |

### 6.1 実行順序

変更後は `build` でドキュメントデータベースを再構築し、次にマトリクス、関連する単体テスト、結合テスト、WASMワークロード、文書、ソースの順で検証する。検証用データベースは成果物ではなく、リポジトリへ追加しない。

## 7. 不足を作らないための変更規則

- 新しいコンセプトは、対応するコンポーネント行、テスト仕様、実行テスト、シナリオ因子を同時に追加する。
- 形式モデルを持たない契約は、`N/A（理由）` と対応する直接テストを記載する。空欄は禁止する。
- シナリオを追加したら、シナリオファイル、`run_all.py`、本表、設定の期待件数を同じ変更で更新する。
- 因子や水準を追加したら、設定、本表、`PAIRWISE_CASES`、期待する組数を同じ変更で更新する。
- 速度を理由に、検証対象・因子・状態・副作用の記録を削除しない。効率化はツール実装側で行い、判定基準は緩めない。
