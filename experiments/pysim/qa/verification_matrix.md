# PySim QA 実行マトリクス

<!-- traceability: {Pairwise_Combinatorial_Testing} -->

## 1. 範囲

この文書は Python 参照シミュレータ PySim の実行可能 QA スイート、登録ランナー、因子組合せを管理する。ここでの合格は PySim の検査結果だけを示し、製品実装の適合性を示さない。製品仕様とテスト仕様の対応は別の資料で管理する。

因子の組合せを少ないケースで網羅する一般的な試験手法を {Pairwise_Combinatorial_Testing} と定義する。 <!-- definition: {Pairwise_Combinatorial_Testing} -->

成果物を追加・削除したときは、本表、実行ランナー、`spec-integrator.yaml` の期待件数を同じ変更で更新する。検査はファイルの実在、登録の一致、ペアワイズ被覆を確認する。

## 2. 実行資産

### 2.1 ランナー

| 用途 | 実行入口 |
| :--- | :--- |
| コンポーネント単体スイート | [`run_all.py`](experiments/pysim/qa/run_all.py) |
| 結合スイート | [`run_all.py`](experiments/pysim/qa/integration/run_all.py) |
| システムシナリオ | [`run_all.py`](experiments/pysim/qa/scenarios/run_all.py) |
| WASMワークロード | [`run_all.py`](experiments/pysim/qa/workloads/run_all.py) |

### 2.2 コンポーネント単体スイート（30件）

| 実行ファイル |
| :--- |
| [`test_entrypoint.py`](experiments/pysim/qa/cross_cutting/test_entrypoint.py) |
| [`test_gotchas.py`](experiments/pysim/qa/cross_cutting/test_gotchas.py) |
| [`test_containers.py`](experiments/pysim/qa/tier1_core/test_containers.py) |
| [`test_coos.py`](experiments/pysim/qa/tier1_core/test_coos.py) |
| [`test_scheduler.py`](experiments/pysim/qa/tier1_core/test_scheduler.py) |
| [`test_ipc_router.py`](experiments/pysim/qa/tier1_interface/test_ipc_router.py) |
| [`test_cps_interpreter.py`](experiments/pysim/qa/tier2_runtime/interpreter/test_cps_interpreter.py) |
| [`test_interpreter.py`](experiments/pysim/qa/tier2_runtime/interpreter/test_interpreter.py) |
| [`test_interpreter_abi.py`](experiments/pysim/qa/tier2_runtime/test_interpreter_abi.py) |
| [`test_logging.py`](experiments/pysim/qa/tier2_runtime/test_logging.py) |
| [`test_recovery.py`](experiments/pysim/qa/tier2_runtime/test_recovery.py) |
| [`test_runtime_composer.py`](experiments/pysim/qa/tier2_runtime/test_runtime_composer.py) |
| [`test_syscall.py`](experiments/pysim/qa/tier2_runtime/test_syscall.py) |
| [`test_vdma.py`](experiments/pysim/qa/tier2_runtime/test_vdma.py) |
| [`test_vmmio.py`](experiments/pysim/qa/tier2_runtime/test_vmmio.py) |
| [`test_vsoc.py`](experiments/pysim/qa/tier2_runtime/test_vsoc.py) |
| [`test_wasm_differential.py`](experiments/pysim/qa/tier2_runtime/test_wasm_differential.py) |
| [`test_wasm_reader.py`](experiments/pysim/qa/tier2_runtime/test_wasm_reader.py) |
| [`test_hal.py`](experiments/pysim/qa/tier3_platform/test_hal.py) |
| [`test_libfireball.py`](experiments/pysim/qa/tier3_platform/test_libfireball.py) |
| [`test_memory.py`](experiments/pysim/qa/tier3_platform/test_memory.py) |
| [`test_debugger.py`](experiments/pysim/qa/tier3_plugins/debugger/test_debugger.py) |
| [`test_gdb_remote.py`](experiments/pysim/qa/tier3_plugins/debugger/test_gdb_remote.py) |
| [`test_exec_memory.py`](experiments/pysim/qa/tier3_plugins/jit/test_exec_memory.py) |
| [`test_jit_differential.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_differential.py) |
| [`test_jit_runtime.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py) |
| [`test_jit_scoring.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_scoring.py) |
| [`test_x64_jit.py`](experiments/pysim/qa/tier3_plugins/jit/test_x64_jit.py) |
| [`test_runtime_event_logger.py`](experiments/pysim/qa/tier3_plugins/logger/test_runtime_event_logger.py) |
| [`test_guest_profiler.py`](experiments/pysim/qa/tier3_plugins/profiler/test_guest_profiler.py) |

### 2.3 結合スイート（4件）

| 実行ファイル |
| :--- |
| [`test_driver_stubs.py`](experiments/pysim/qa/integration/test_driver_stubs.py) |
| [`test_guest_profiler_runtime.py`](experiments/pysim/qa/integration/test_guest_profiler_runtime.py) |
| [`test_lldb_debugger.py`](experiments/pysim/qa/integration/test_lldb_debugger.py) |
| [`test_pairwise_combinations.py`](experiments/pysim/qa/integration/test_pairwise_combinations.py) |

### 2.4 システムシナリオ（12件）

| シナリオ | 実行ファイル |
| :--- | :--- |
| 1 | [`scenario1_loader_and_memory.py`](experiments/pysim/qa/scenarios/scenario1_loader_and_memory.py) |
| 2 | [`scenario2_wasi_syscall_io.py`](experiments/pysim/qa/scenarios/scenario2_wasi_syscall_io.py) |
| 3 | [`scenario3_recursion_and_tables.py`](experiments/pysim/qa/scenarios/scenario3_recursion_and_tables.py) |
| 4 | [`scenario4_hybrid_jit_loop.py`](experiments/pysim/qa/scenarios/scenario4_hybrid_jit_loop.py) |
| 5 | [`scenario5_multimodule_unified_pc.py`](experiments/pysim/qa/scenarios/scenario5_multimodule_unified_pc.py) |
| 6 | [`scenario6_coos_multitask_yield.py`](experiments/pysim/qa/scenarios/scenario6_coos_multitask_yield.py) |
| 7 | [`scenario7_gdb_socket_debugger.py`](experiments/pysim/qa/scenarios/scenario7_gdb_socket_debugger.py) |
| 8 | [`scenario8_comprehensive_storage_coverage.py`](experiments/pysim/qa/scenarios/scenario8_comprehensive_storage_coverage.py) |
| 9 | [`scenario9_ipc_router_and_logging.py`](experiments/pysim/qa/scenarios/scenario9_ipc_router_and_logging.py) |
| 10 | [`scenario10_vmmio_virtual_devices.py`](experiments/pysim/qa/scenarios/scenario10_vmmio_virtual_devices.py) |
| 11 | [`scenario11_hal_and_wasi_drivers.py`](experiments/pysim/qa/scenarios/scenario11_hal_and_wasi_drivers.py) |
| 12 | [`scenario12_wasi03p_uri_resolver.py`](experiments/pysim/qa/scenarios/scenario12_wasi03p_uri_resolver.py) |

### 2.5 WASMワークロード（3件）

| 実行ファイル |
| :--- |
| [`test_driver_stubs_guest.py`](experiments/pysim/qa/workloads/test_driver_stubs_guest.py) |
| [`test_wasi_guest.py`](experiments/pysim/qa/workloads/test_wasi_guest.py) |
| [`test_wasm_core_spec.py`](experiments/pysim/qa/workloads/test_wasm_core_spec.py) |

### 2.6 因子データと実行ケース

| 用途 | 正本 |
| :--- | :--- |
| 因子名・水準 | [`pairwise_factors.csv`](experiments/pysim/qa/pairwise_factors.csv) |
| 26行の実行ケースとケース実行関数 | [`test_pairwise_combinations.py`](experiments/pysim/qa/integration/test_pairwise_combinations.py) |

## 3. 因子カタログ

ペアワイズテストの因子は7個とし、各水準はQA資料の [pairwise_factors.csv](experiments/pysim/qa/pairwise_factors.csv) だけで定義する。`PAIRWISE_CASES` はCSVの水準を使う実行データであり、因子名や水準を再定義しない。`n` 個の水準を持つ2因子の組は `n_left × n_right` 個と数える。

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

26行の計画が全288水準対を含むことを集合比較で確認する。
各行は独立したpytestケースとして実行し、`TEST-PAIR-01`〜`TEST-PAIR-26`で識別する。
許可12行は実ワークロード、禁止14行は構成時の拒否を検査する。
禁止行では実行器・観測プラグインを生成せず、他因子の動作を検証済みと数えない。

| 検証対象 | 操作と期待結果 |
| :--- | :--- |
| 実行経路 | Interpreter専用構成はJITを呼ばない。JITとHybridは実生成コードを呼び、ゲスト結果が独立した期待値と一致する |
| キャッシュ | 許可5行でcold・evictを検査する。追加4ケースではJIT／Hybridとwarm／flushを組み合わせ、COOS・SHM・grow・IPCの結果を照合する。Interpreter行のcache水準は適用外である |
| メモリと保管場所 | local、global、RAM、SHMの更新結果を照合する。幅別store後の全ゲスト・物理メモリを比較する。growは旧ページ数、拡張領域末尾のstore/load、未書込み領域のゼロを確認する |
| ホスト境界 | ゲストimportからWASI・IPC・HALを呼ぶ。引数、応答、全出力、VFSのbyteとcursor、HALの受信待ち状態を確認する。戻り値をゲストの最終結果にも含める |
| バッファと実行制御 | 全HALスロットとunmap後の閲覧拒否を確認する。同期呼出しは呼出元を保持する。COOSはyield後に再開し、複数タスクはguest完了前に相手を進め、両結果と終了状態を保持する |
| デバッガ | 独立した2ケースでレジスタ参照、breakpoint停止、同じ実行状態からの再開結果を照合する。主ワークロードとの同時結線の証拠にはしない |

追加ケースを26行の計画被覆へ加算しない。
本試験は全288組の動的被覆、実機性能、物理ドライバ、未実装経路の適合性を示さない。

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

シナリオ集合は12件で、ランナー `run_all.py` に全件を登録する。ファイルだけ存在してランナーから呼ばれないケースは合格としない。

## 5. 因子からPySim実装不変条件への対応

| 因子 | 直接確認する不変条件 | 主テスト | 失敗時の扱い |
| :--- | :--- | :--- | :--- |
| `engine` | Interpreter と JIT が同じ Native スタック状態を観測する | [`test_interpreter.py`](experiments/pysim/qa/tier2_runtime/interpreter/test_interpreter.py), [`test_x64_jit.py`](experiments/pysim/qa/tier3_plugins/jit/test_x64_jit.py), [`test_wasm_differential.py`](experiments/pysim/qa/tier2_runtime/test_wasm_differential.py) | `assert` で停止 |
| `cache` | hit、eviction、flush 後の実行経路が一意になる | [`test_jit_runtime.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py), [`test_x64_jit.py`](experiments/pysim/qa/tier3_plugins/jit/test_x64_jit.py), [`test_jit_differential.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_differential.py) | `assert` で停止 |
| `mem_width` | WASM の幅、符号拡張、境界、grow 後の位置が一致する | [`test_interpreter.py`](experiments/pysim/qa/tier2_runtime/interpreter/test_interpreter.py), [`test_wasm_reader.py`](experiments/pysim/qa/tier2_runtime/test_wasm_reader.py), [`test_syscall.py`](experiments/pysim/qa/tier2_runtime/test_syscall.py) | `assert` で停止 |
| `storage` | storage が所有し、view は非所有である。所有権を共有しない | [`test_containers.py`](experiments/pysim/qa/tier1_core/test_containers.py), [`test_memory.py`](experiments/pysim/qa/tier3_platform/test_memory.py), [`test_ipc_router.py`](experiments/pysim/qa/tier1_interface/test_ipc_router.py) | `assert` で停止 |
| `host_call` | WASI、IPC、HAL の責務境界とバッファ権限が混ざらない | [`test_syscall.py`](experiments/pysim/qa/tier2_runtime/test_syscall.py), [`test_ipc_router.py`](experiments/pysim/qa/tier1_interface/test_ipc_router.py), [`test_hal.py`](experiments/pysim/qa/tier3_platform/test_hal.py) | `assert` で停止 |
| `scheduler` | current task はスケジューラだけが変更し、yield 後に継続状態を保持する | [`test_scheduler.py`](experiments/pysim/qa/tier1_core/test_scheduler.py), [`test_coos.py`](experiments/pysim/qa/tier1_core/test_coos.py), [`test_vsoc.py`](experiments/pysim/qa/tier2_runtime/test_vsoc.py) | `assert` で停止 |
| `debugger` | 読み取り、書き込み、breakpoint、およびInterpreter-only構成の境界が明示される | [`test_debugger.py`](experiments/pysim/qa/tier3_plugins/debugger/test_debugger.py), [`test_gdb_remote.py`](experiments/pysim/qa/tier3_plugins/debugger/test_gdb_remote.py), [`test_runtime_composer.py`](experiments/pysim/qa/tier2_runtime/test_runtime_composer.py) | `assert` で停止 |

## 6. PySim QAゲート

| 検証 | 入力 | 合格条件 |
| :--- | :--- | :--- |
| 実行資産と登録 | 本表、`spec-integrator.yaml`、各ランナー | パス実在、件数一致、登録漏れなし、pairwise 288組を網羅 |
| PySim静的検査と単体スイート | `./tools/check-src.sh -g pysim` | PySim規約、Tier import、登録済み単体スイートが合格 |
| 結合スイートとシナリオ | [`run_all.py`](experiments/pysim/qa/integration/run_all.py) | 4結合スイートと12シナリオを実行し、全登録が合格 |
| WASMワークロード | [`run_all.py`](experiments/pysim/qa/workloads/run_all.py) | 3ワークロードを実行し、準備状態とskip理由を記録 |
| ベンチマーク | [`benchmarks`](experiments/pysim/benchmarks) | 性能・資源計測として記録し、機能適合テストの件数へ含めない |

### 6.1 実行順序

マトリクス検査を先に行い、次に単体スイート、結合スイートとシナリオ、WASMワークロードを実行する。ベンチマークは対象の機能検証が通った後に個別の計測として行う。

## 7. 不足を作らないための変更規則

- 新しいコンセプトは、対応するコンポーネント行、テスト仕様、実行テスト、シナリオ因子を同時に追加する。
- 形式モデルを持たない契約は、`N/A（理由）` と対応する直接テストを記載する。空欄は禁止する。
- シナリオを追加したら、シナリオファイル、`run_all.py`、本表、設定の期待件数を同じ変更で更新する。
- 因子や水準を追加したら、設定、本表、`PAIRWISE_CASES`、期待する組数を同じ変更で更新する。
- 速度を理由に、検証対象・因子・状態・副作用の記録を削除しない。効率化はツール実装側で行い、判定基準は緩めない。
