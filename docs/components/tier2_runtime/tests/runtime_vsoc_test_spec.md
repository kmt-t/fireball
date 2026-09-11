# vSoC (統合実行エンジン) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`runtime_vsoc.md`](docs/components/tier2_runtime/runtime_vsoc.md)
参考実装: [`runtime_engine_concept.py`](docs/components/tier2_runtime/concepts/runtime_engine_concept.py)（統合シミュレーションとして`jit_runtime_test_spec.md`と一部重複。本書はvSoC固有の統合責務——ハーネスによる静的DI、Safepoint/デバッガ協調、マルチモジュールリンク——に焦点を当てる）

Loader/Interpreter/JIT/vMMIO/Debuggerを統合する`vsoc_harness`（静的DI）、`exec_trace`委譲、Safepoint/JITキャッシュ協調モデル、マルチモジュール動的リンクを検証する。

## 2. テストケース一覧

### ハーネス統合 (runtime_vsoc.md (Harness))

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-01 | vSoCはTier3実装の内部ヘッダに依存しない | - | 依存関係を確認 | ハーネスに集約されたPOD関数ポインタ経由でのみ呼び出す（仮想関数・動的ディスパッチを使わない） | `{META_StaticDI}` |
| TEST-VSOC-02 | `exec_trace`の統一呼び出し規約 | インタープリタ実行/JIT実行の双方 | `step()`を呼ぶ | 呼び出し側は実行エンジンの種別を意識しない（同一の`__fastcall` CPS 4引数 `(ctx, sp, local_base, tos)` シグネチャ） | 「実行エンジン委譲」, `{AAPCS_FastCall}` |
| TEST-VSOC-03 | `register-hook`はvMMIOへの薄い転送 | - | `register-hook`を呼ぶ | `harness.vmmio`経由で`runtime_vmmio.md`の同名APIへそのまま転送され、事前/事後条件はvmmio層が正本 | register-hook |

### Safepoint/JITキャッシュ協調 ({Safepoint_JIT_Flush})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-10 | Safepointはループ背進辺/関数呼出前/メモリアクセス後に埋め込まれる | JIT生成コード | コード生成を確認 | `{JIT_Safepoint}` の3箇所すべてにチェックが入る | `{JIT_Safepoint}` |
| TEST-VSOC-11 | 保留interrupt-eventの構成 | - | 保留イベント構造を確認 | `vector_id`、`source_id`、`cause_code`、`payload0`、`payload1`の固定5ワードで保持される | `{JIT_Safepoint}` |
| TEST-VSOC-12 | デバッガのメモリ書き換えでキャッシュFlush | デバッガがメモリ変更 | `request_debugger_interrupt`相当を呼ぶ | 次のSafepointでフラグ検出され、Active/Warm/Oldest全バンクのメタデータが破棄される(generation cookie increment) | `{Debugger_Jit_Flush}` |
| TEST-VSOC-13 | IRQ/JITレース不在 | JIT実行中に割り込み発生 | 形式検証プロパティを確認 | Safepoint同期を経ずに割り込み処理が開始されない(`AG(Not(handling_irq & jit_mode))`) | irq_jit_race_freedom_proof |
| TEST-VSOC-14 | flush完了性 | dirty状態になったキャッシュ | 形式検証プロパティを確認 | `AG(dirty -> AF(flushed))`（dirtyになったflushは必ず完了する） | `../formal/vsoc_cache_coherency_model.py` |
| TEST-VSOC-15 | 世代の逆行不在 | 3面ローテーション | 各バンクのgeneration cookieを確認 | 全バンク一括更新され、逆行・不一致が生じない | cache_generation_never_regresses |
| TEST-VSOC-16 | Purgeと回収の不可分性 | ローテーション時 | Oldestバンクのpurge処理を確認 | Purgeとエントリ表スロット回収が同一トランザクションで行われ、未回収スロットが蓄積しない | rotation_reclaims_every_bank |
| TEST-VSOC-17 | 形式検証の変異反証 | 通常モデルと`guards=False`モデル | `vsoc_state_model.py`を実行 | 通常モデルでは2つの性質が成立し、ガードを無効化した変異モデルでは両方の性質が失敗する | `../formal/vsoc_state_model.py` |

### vSoC Engineライフサイクル

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-20 | ロード失敗でError状態 | 不正なWASM | `prepare(module)` | `Loading→Error`に遷移 | `{VSOC_Lifecycle}` |
| TEST-VSOC-21 | yield閾値到達でReadyへ復帰 | InterpreterRun中 | トレース数が閾値超過 | `InterpreterRun→Ready`、ホットスポット検出結果がJITキューに投入される | - |
| TEST-VSOC-22 | Safepointで原因付き割り込みイベント検出時はインタープリタへフォールバック | JitRun中 | `interrupt-event`が保留される | `JitRun→SafepointCheck→Ready`（インタープリタへ） | - |

### vIRQ登録と原因付き階層配送

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-50 | 静的vIRQノードの登録 | root・4分類・デバイスの固定ノード | 各ノードの関数インデックスを登録 | 静的ノードだけが受け付けられ、親子関係と原因源表は変更されない | `runtime_vsoc.md` `register-virq-dispatcher` |
| TEST-VSOC-51 | WASM関数シグネチャ拒否 | 登録対象に不一致シグネチャの関数 | vIRQスロットへ登録 | `(u32,u32,u32,u32,u32) -> u32` 以外は拒否され、有効登録を上書きしない | `runtime_vsoc.md` `register-virq-dispatcher` |
| TEST-VSOC-52 | Safepoint前の登録変更不可視性 | 有効登録A、保留登録B | Safepoint前とSafepoint後に同じイベントを配送 | 前半はA、Safepoint後はBだけが観測され、途中状態は観測されない | `{JIT_Safepoint}` |
| TEST-VSOC-53 | 原因レコードの階層伝播 | root/category/deviceに登録済み関数 | `PASS_THROUGH`を返すイベントを配送 | `root → 分類 → デバイス → ゲスト関数`の順に1回ずつ呼ばれる | `runtime_vsoc.md` `dispatch-interrupt-event` |
| TEST-VSOC-54 | HANDLEDとREJECTの終端 | 各階層の関数が結果を返す | `HANDLED`または`REJECT`を返す | `HANDLED`は子へ進まず、`REJECT`は診断後に終了し、FAULTへ再帰配送しない | `runtime_vsoc.md` `dispatch-interrupt-event` |
| TEST-VSOC-55 | WASIポーリングとの分離 | vIRQイベントとHALポーリングハンドルが同時に存在 | 両経路を独立して処理 | vIRQ配送が`poll-check`/`poll-wait`を起動せず、ポーリングがvIRQ登録を変更しない | `interface_wit.md` §6 |
| TEST-VSOC-23 | ブレークポイントヒットでDebugging状態へ | 任意の実行状態 | ブレークポイント到達 | `(any)→Debugging` | - |
| TEST-VSOC-24 | resume(interp)でJITキャッシュflush | Debugging状態 | `resume(interp)`を呼ぶ | JITキャッシュがflushされ、PCを保持したままInterpreterRunへ | `{VSOC_Lifecycle}` |

### マルチモジュール動的リンク

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-30 | インポートセクションからのシンボル解決 | 複数モジュールロード済み | `resolve_symbol(module_name, func_name)` | Module Registryを介して正しく解決される | `{MultiModule_Support}` |
| TEST-VSOC-31 | インタープリタテーブルへのパッチ | シンボル解決成功 | `patch_interp_table(func_addr)` | 呼び出し先アドレスが正しくパッチされる | {Debugger_Jit_Flush} |

### `fireball_call`シグネチャの整合性

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-40 | `fireball_call`の引数個数とパッキング | システムコール発行 | 引数のパッキング状態を検証 | `fireball_call(id, arg0..arg5)`（計7引数）として統一され、`id`（上位16bit: service_id, 下位16bit: command_id）および6つの汎用レジスタ引数で正しく低レイヤーへ渡る | `{Syscall_Mapping}` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-VSOC-01 | JITキャッシュ再判定の主体分離（インタープリタ完全ステートレス） | インタープリタ実行中ブロックから次のホットブロックへ遷移 | `step()` の実行ループを確認 | インタープリタ自身は JIT キャッシュを一切保持・参照せず、トレース境界で制御が vSoC に戻るたびに vSoC の `step()` 内でキャッシュを再判定して JIT 実行へ切り替える。**実装の勘所**: インタープリタ内部に JIT キャッシュ参照コードを埋め込むと、ハンドラがステートフルになりデバッグ切り替えやキャッシュフラッシュの同期が破綻する | `{Interpreter_LazyJITSwitch}` |
| GOTCHA-VSOC-02 | 概算Yieldの主体分離（コルーチン責務の局所化） | JIT トレースまたはインタープリタ実行中 | トレース終了時の制御フローを確認 | インタープリタおよび JIT トレース自身は `co_yield` を発行せず単に関数復帰し、戻り値を受け取った vSoC 自身が `yield_threshold` を評価して `co_yield` を発行する。**実装の勘所**: インタープリタをコルーチン化すると命令ディスパッチの `[[clang::musttail]]` 直結が不可能になり性能が崩壊する | `{ADR_TraceBoundaryYield}` |
| GOTCHA-VSOC-03 | `execution_context` 内包レイアウトと委譲シグネチャ | WASM スタック初期化 | コンテキストオフセットを確認 | `vsoc_runtime`（`mem_base` `+0x28`, `mem_size` `+0x2C`, `globals_base` `+0x30`, `globals_limit` `+0x34`）は独立構造体ではなく `execution_context`（R0: `ctx`, 計60バイト）の末尾に内包され、`exec_trace` 呼び出し時は `(ctx, sp, local_base, tos)` の4引数で委譲される。**実装の勘所**: コンテキスト外にポインタを分散させると、レジスタ圧迫とキャッシュミスが増加する | `{ExecutionContext_Layout}` `{EnvironmentPointer}` `{VsocRuntime_Layout}` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- `../wit/vsoc_runtime.wit`によるWIT型定義そのものとの整合性。
- Cortex-M33実機でのSafepointチェック周期の精度（`{Challenge_ApproximateYield}`は仕様上も「検討中」の未解決課題）。
- マルチコア環境でのメモリ可視性（「既知の制限」でスコープ外と明記）。
