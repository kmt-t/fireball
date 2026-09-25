# vSoC (統合実行エンジン) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`runtime_vsoc.md`](docs/components/tier2_runtime/runtime_vsoc.md)
参考実装: [`runtime_engine.py`](experiments/pysim/tier3_executer/jit/runtime_engine.py) および [`test_vsoc.py`](experiments/pysim/qa/tier2_runtime/test_vsoc.py)。本書はvSoC固有の統合責務——ハーネスによる静的DI、割り込み/デバッガ協調、マルチモジュールリンク——に焦点を当てる。

Loader/Interpreter/JIT/vMMIO/Debuggerを統合する`vsoc_harness`（静的DI）、C++ native dispatchのyield境界、JITキャッシュ協調モデル、マルチモジュール動的リンクを検証する。

## 2. テストケース一覧

### ハーネス統合 (runtime_vsoc.md (Harness))
<!-- traceability: {CPS_4Args} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-01 | vSoCはTier3実装の内部ヘッダに依存しない | - | 依存関係を確認 | ハーネスに集約されたPOD関数ポインタ経由でのみ呼び出す（仮想関数・動的ディスパッチを使わない） | `{META_StaticDI}` |
| TEST-VSOC-02 | `exec_trace`の統一論理引数契約 | インタープリタ実行/JIT実行の双方 | 実行入口を確認する | 呼び出し側は実行エンジンの種別を意識しない（同一の4論理引数 `(ctx, sp, local_base, tos)`）。x64物理配置はABI定義に従い、ARMv8-MはTBD | 「実行エンジン委譲」 |
| TEST-VSOC-03 | `register-hook`はvMMIOへの薄い転送 | - | `register-hook`を呼ぶ | `harness.vmmio`経由で`runtime_vmmio.md`の同名APIへそのまま転送され、事前/事後条件はvmmio層が正本 | register-hook |

### LOOP後方分岐yieldとJITキャッシュ協調
<!-- traceability: {JIT_BackedgeYield} {ADR_LoopBackedgeYield} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-10 | C++ dispatchのLOOP後方分岐は有限回数で協調境界へ戻る | 同一フレームLOOP後方分岐と有限の`FB_CONF_RUNTIME_YIELD_THRESHOLD` | C++ branch handler回数、後続trace実行、yield境界を検証する | 各取得済み後方分岐でC++ Interpreter handlerが制御状態を更新する。しきい値到達まではC++ dispatcherが実行を続け、到達後にRuntimeEngineがyield要求を返す。C++ Interpreter単独経路も同じ条件を使う | pysim `test_jitr_loop_backedge_stays_in_cpp_until_coos_yield` |
| TEST-VSOC-11 | 保留interrupt-eventの構成 | - | 保留イベント構造を確認 | `vector_id`、`source_id`、`cause_code`、`payload0`、`payload1`の固定5ワードで保持される | `{GLOBAL_InterruptWakeup}` |
| TEST-VSOC-13 | IRQ/JITレース不在 | JIT実行中に割り込みイベントが待機 | 形式検証プロパティを確認 | JITネイティブ実行中は割り込みハンドラを開始せず、COOS協調境界から配送する(`AG(Not(handling_irq & jit_mode))`) | irq_jit_race_freedom_proof |
| TEST-VSOC-14 | flush完了性 | dirty状態になったキャッシュ | 形式検証プロパティを確認 | `AG(dirty -> AF(flushed))`（dirtyになったflushは必ず完了する） | [`vsoc_cache_coherency_model.py`](docs/components/tier2_runtime/formal/vsoc_cache_coherency_model.py) `dirty_cache_always_flushes_promptly` |
| TEST-VSOC-15 | 世代の逆行不在 | 3面ローテーション | 各バンクのgeneration cookieを確認 | 全バンク一括更新され、逆行・不一致が生じない | [`vsoc_cache_coherency_model.py`](docs/components/tier2_runtime/formal/vsoc_cache_coherency_model.py) `generation_monotonicity_across_banks` |
| TEST-VSOC-16 | Purgeと回収の不可分性 | ローテーション時 | Oldestバンクのpurge処理を確認 | Purgeとエントリ表スロット回収が同一トランザクションで行われ、未回収スロットが蓄積しない | [`vsoc_cache_coherency_model.py`](docs/components/tier2_runtime/formal/vsoc_cache_coherency_model.py) `bounded_cache_rotation_memory` |
| TEST-VSOC-17 | 形式検証の変異反証 | 通常モデルと`guards=False`モデル | `vsoc_state_model.py`を実行 | 通常モデルでは2つの性質が成立し、ガードを無効化した変異モデルでは両方の性質が失敗する | [`vsoc_state_model.py`](docs/components/tier2_runtime/formal/vsoc_state_model.py) |

### vSoC Engineライフサイクル
<!-- traceability: {VSOC_Lifecycle} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-20 | ロード失敗でError状態 | 不正なWASM | `prepare(module)` | `Loading→Error`に遷移 | `VSOC_Lifecycle` |
| TEST-VSOC-21 | LOOP後方分岐しきい値到達でRuntimeEngine境界へ復帰 | C++ InterpreterまたはHybrid JIT実行中 | 共通回数しきい値まで取得後方辺を実行する | しきい値到達後にC++ dispatchがyield statusを返す。ホットスポット記録とcompile queue処理はRuntimeEngine境界で行う | `{JIT_BackedgeYield}` |
| TEST-VSOC-22 | LOOPしきい値到達時にC++ handlerを通ってCOOSへ戻る | 同一制御フレームのLOOP後方分岐を実行するJITトレース | `loop_jump_count`が`FB_CONF_RUNTIME_YIELD_THRESHOLD`へ達するまで実行する | C++ dispatcherは取得済み後方辺ごとにC++ Interpreter handlerを実行し、共通しきい値に達した後にyield statusを返す。RuntimeEngineは`yield_requested`を返し、`System.run_guest()`は`on_yield()`後にCOOSへ制御を返す。イベント配送はCOOS境界の別処理として行う | TEST-VSOC-10, `{ADR_LoopBackedgeYield}` |
| TEST-VSOC-23 | デバッガとJITの同時構成拒否 | `RuntimeCompositionConfig(execution=JIT, debugger=True)` | ランタイム構成を合成する | 構成時 `assert` で拒否され、デバッガがJITキャッシュを操作する経路は生成されない | `{DebuggerInterpreterComposition}` |
| TEST-VSOC-24 | アタッチ中のインタープリタ専用実行 | `Interpreter + Debugger` 構成 | デバッガをアタッチして `step()` または `continue` を実行する | PCを保持したままインタープリタだけが実行され、JITの動的切替とキャッシュ操作は発生しない | `{DebuggerInterpreterComposition}` |

### vIRQ登録と原因付き階層配送

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-50 | 静的vIRQノードの登録 | root・4分類・デバイスの固定ノード | `fireball:host/virq.register(node_id, function_index)` を実行 | 静的ノードだけが受け付けられ、親子関係と原因源表は変更されない | `runtime_vsoc.md` `register-virq-dispatcher` |
| TEST-VSOC-51 | WASM関数シグネチャ拒否 | 登録対象に不一致シグネチャの関数 | `fireball:host/virq.register` 経由で登録 | `(u32,u32,u32,u32,u32) -> u32` 以外は拒否され、有効登録を上書きしない | `runtime_vsoc.md` `register-virq-dispatcher` |
| TEST-VSOC-52 | COOS協調境界前の登録変更不可視性 | 有効登録A、保留登録B | C++のyield statusを受け取る前後に同じイベントを配送 | yield前はA、次のCOOS協調境界で変更反映後はBだけが観測され、途中状態は観測されない | `{GLOBAL_InterruptWakeup}` |
| TEST-VSOC-53 | 原因レコードの階層伝播 | root/category/deviceに登録済み関数 | `PASS_THROUGH`を返すイベントを配送 | `root → 分類 → デバイス → ゲスト関数`の順に1回ずつ呼ばれる | `runtime_vsoc.md` `dispatch-interrupt-event` |
| TEST-VSOC-54 | HANDLEDとREJECTの終端 | 各階層の関数が結果を返す | `HANDLED`または`REJECT`を返す | `HANDLED`は子へ進まず、`REJECT`は診断後に終了し、FAULTへ再帰配送しない | `runtime_vsoc.md` `dispatch-interrupt-event` |
| TEST-VSOC-55 | WASIポーリングとの分離 | vIRQイベントとHALポーリングハンドルが同時に存在 | 両経路を独立して処理 | vIRQ配送が`poll-check`/`poll-wait`を起動せず、ポーリングがvIRQ登録を変更しない | [`interface_wit.md`](docs/components/tier3_platform/interface_wit.md) のポーリング契約 |
| TEST-VSOC-56 | 再スケジュール世代境界でのCOOS再開可能実行 | C++ dispatch中にCOOSの再スケジュール世代が更新され、LOOP後方分岐yieldしきい値にも達する | `System.run_guest()`をCOOSタスクとして実行し、別タスクの実行後にゲストを再開する | 再スケジュール世代はhandlerごとに読まず、後方分岐しきい値でC++ dispatchが戻った後にCOOS境界で観測する。同じ実行コンテキストから再開して結果を保持し、別タスクはゲスト完了前に実行される | `{ADR_InterruptRescheduleGeneration}` `{ADR_LoopBackedgeYield}` |
| TEST-VSOC-25 | ブレークポイントヒットでDebugging状態へ | 任意の実行状態 | ブレークポイント到達 | `(any)→Debugging` | - |
| TEST-VSOC-26 | resume(interp)でインタープリタ実行を継続 | Debugging状態 | `resume(interp)`を呼ぶ | PCを保持したままInterpreterRunへ遷移し、JITキャッシュ操作を行わない | `{VSOC_Lifecycle}` |

### マルチモジュール動的リンク
<!-- traceability: {MultiModule_Support} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-30 | インポートセクションからのシンボル解決 | 複数モジュールロード済み | `resolve_symbol(module_name, func_name)` | Module Registryを介して正しく解決される | `MultiModule_Support` |
| TEST-VSOC-31 | インタープリタテーブルへのパッチ | シンボル解決成功 | `patch_interp_table(func_addr)` | 呼び出し先アドレスが正しくパッチされる | `interpreter.md` |

### `fireball_call`シグネチャの整合性

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-40 | `fireball_call` host-call の引数個数とパッキング | 汎用システムコール発行 | `fireball:host/trap` import の受け渡しを検証 | `fireball_call(id, arg0..arg5)`（計7引数）として統一され、6つの汎用引数がvMMIOレジスタを経由せずホストハンドラへ直接渡る。vIRQ/vDMA専用host callはこのABIに含めない | `{Syscall_Mapping}` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）
<!-- traceability: {GOTCHA-VSOC-01} {GOTCHA-VSOC-02} {GOTCHA-VSOC-03} {Interpreter_LazyJITSwitch} {ADR_LoopBackedgeYield} {ExecutionContext_Layout} {EnvironmentPointer} {VsocRuntime_Layout} -->

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-VSOC-01 | C++ Interpreter handler後のdispatch | C++ handlerが分岐後のPCを確定した状態 | native dispatchの制御遷移を確認する | C++ dispatcherが同一native dispatch内で次PCのtraceまたはhandlerを実行し、Python/vSoCへ命令ごとに戻らない。このhandler-mediated遷移をchainと呼ばない | `{JIT_BackedgeYield}`, `test_native_interpreter_returns_to_python_at_loop_yield_counts` |
| GOTCHA-VSOC-02 | 共通LOOP後方分岐yield条件 | C++ Interpreter単独またはHybrid JIT経路 | branch回数とdispatch statusを確認する | 共通contextのLOOP後方分岐数が共通しきい値に達した時だけC++ dispatcherがyield statusを返す。両経路の復帰条件は一致する | - |
| GOTCHA-VSOC-03 | `execution_context` 内包レイアウトと委譲シグネチャ | WASM スタック初期化 | コンテキストオフセットを確認 | `vsoc_runtime`（`mem_base` `+0x28`, `mem_size` `+0x2C`, `globals_base` `+0x30`, `globals_limit` `+0x34`）は独立構造体ではなく `execution_context` の内部に配置され、x86-64 Tier 2 ABIはLOOPカウンタとしきい値を含む128バイトである。`exec_trace` 呼び出し時は `(ctx, sp, local_base, tos)` の4引数で委譲される。**実装の勘所**: コンテキスト外にポインタを分散させると、レジスタ圧迫とキャッシュミスが増加する | `ExecutionContext_Layout`, `EnvironmentPointer`, `VsocRuntime_Layout` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- [`runtime_vsoc_contract.wit`](docs/components/tier2_runtime/wit/runtime_vsoc_contract.wit)によるWIT型定義そのものとの整合性。
- ARMv8-M実機でのLOOP分岐回数しきい値と壁時計応答時間の対応（ハードウェア仕様・実測条件はTBD）。
- マルチコア環境でのメモリ可視性（「既知の制限」でスコープ外と明記）。
