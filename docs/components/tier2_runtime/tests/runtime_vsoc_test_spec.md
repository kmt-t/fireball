# vSoC (統合実行エンジン) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier2_runtime/runtime_vsoc.md`
参考実装: `docs/components/tier2_runtime/concepts/runtime_engine_concept.py`（統合シミュレーションとして`jit_runtime_test_spec.md`と一部重複。本書はvSoC固有の統合責務——ハーネスによる静的DI、Safepoint/デバッガ協調、マルチモジュールリンク——に焦点を当てる）
現行実装: なし（`experiments/pysim`はvSoC相当の統合レイヤを持たず、`main.py`が個々のコンポーネントを直接呼び出している）

Loader/Interpreter/JIT/vMMIO/Debuggerを統合する`vsoc_harness`（静的DI）、`exec_trace`委譲、Safepoint/JITキャッシュ協調モデル、マルチモジュール動的リンクを検証する。

## 2. テストケース一覧

### ハーネス統合 (§3, §5.1)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| VSOC-01 | vSoCはTier3実装の内部ヘッダに依存しない | - | 依存関係を確認 | ハーネスに集約されたPOD関数ポインタ経由でのみ呼び出す（仮想関数・動的ディスパッチを使わない） | §2 `{META_StaticDI}` |
| VSOC-02 | `exec_trace`の統一呼び出し規約 | インタープリタ実行/JIT実行の双方 | `step()`を呼ぶ | 呼び出し側は実行エンジンの種別を意識しない（同一の`__fastcall`4引数シグネチャ） | §4.1「実行エンジン委譲」 |
| VSOC-03 | `register-hook`はvMMIOへの薄い転送 | - | `register-hook`を呼ぶ | `harness.vmmio`経由でrun time_vmmio.mdの同名APIへそのまま転送され、事前/事後条件はvmmio層が正本 | §5.1 register-hook |

### Safepoint/JITキャッシュ協調 (§4.2.1)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| VSOC-10 | Safepointはループ背進辺/関数呼出前/メモリアクセス後に埋め込まれる | JIT生成コード | コード生成を確認 | 表4.2.1の3箇所すべてにチェックが入る | §4.2.1「Safepointの動作メカニズム」 |
| VSOC-11 | interrupt_flagsのビット構成 | - | フラグ構造を確認 | `[0]Async Break, [1]Debugger Intervention, [2]JIT Cache Invalid, [3]Yield Request`の32bit構成 | §4.2.1「フラグの構造」 |
| VSOC-12 | デバッガのメモリ書き換えでキャッシュFlush | デバッガがメモリ変更 | `request_debugger_interrupt`相当を呼ぶ | 次のSafepointでフラグ検出され、Active/Warm/Oldest全バンクのメタデータが破棄される(generation cookie increment) | §4.2.1「Debugger 介入時のキャッシュ一貫性」 |
| VSOC-13 | IRQ/JITレース不在 | JIT実行中に割り込み発生 | 形式検証プロパティを確認 | Safepoint同期を経ずに割り込み処理が開始されない(`AG(Not(handling_irq & jit_mode))`) | §6.1 irq_jit_race_freedom_proof |
| VSOC-14 | flush完了性 | dirty状態になったキャッシュ | 形式検証プロパティを確認 | `AG(dirty -> AF(flushed))`（dirtyになったflushは必ず完了する） | §6.1, §6.3 |
| VSOC-15 | 世代の逆行不在 | 3面ローテーション | 各バンクのgeneration cookieを確認 | 全バンク一括更新され、逆行・不一致が生じない | §6.1 cache_generation_never_regresses |
| VSOC-16 | Purgeと回収の不可分性 | ローテーション時 | Oldestバンクのpurge処理を確認 | Purgeとエントリ表スロット回収が同一トランザクションで行われ、未回収スロットが蓄積しない | §6.1 rotation_reclaims_every_bank |

### vSoC Engineライフサイクル (§4.2)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| VSOC-20 | ロード失敗でError状態 | 不正なWASM | `prepare(module)` | `Loading→Error`に遷移 | §4.2状態遷移図 |
| VSOC-21 | yield閾値到達でReadyへ復帰 | InterpreterRun中 | トレース数が閾値超過 | `InterpreterRun→Ready`、ホットスポット検出結果がJITキューに投入される | §4.2 |
| VSOC-22 | Safepointで割り込み検出時はインタープリタへフォールバック | JitRun中 | 割り込みフラグが立つ | `JitRun→SafepointCheck→Ready`（インタープリタへ） | §4.2 |
| VSOC-23 | ブレークポイントヒットでDebugging状態へ | 任意の実行状態 | ブレークポイント到達 | `(any)→Debugging` | §4.2 |
| VSOC-24 | resume(interp)でJITキャッシュflush | Debugging状態 | `resume(interp)`を呼ぶ | JITキャッシュがflushされ、PCを保持したままInterpreterRunへ | §4.2遷移詳細表 |

### マルチモジュール動的リンク (§5.3, §4.3)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| VSOC-30 | インポートセクションからのシンボル解決 | 複数モジュールロード済み | `resolve_symbol(module_name, func_name)` | Module Registryを介して正しく解決される | §4.3マルチモジュール動的リンクシーケンス |
| VSOC-31 | インタープリタテーブルへのパッチ | シンボル解決成功 | `patch_interp_table(func_addr)` | 呼び出し先アドレスが正しくパッチされる | §4.3 |

### `fireball_call`シグネチャの整合性（要確認・矛盾あり）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| VSOC-40 | `fireball_call`の引数個数 | - | §5.2のシグネチャと`system_syscall.md`/`interface_wit.md`のシグネチャを比較 | **矛盾を検出**: runtime_vsoc.md §5.2は`fireball_call(service_id, command_id, arg0..arg5)`＝8引数と記述するが、system_syscall.md §3・interface_wit.md §4.1は`fireball_call(id, arg0..arg5)`＝7引数と定義している。どちらが正かは本書だけでは判断できない | runtime_vsoc.md §5.2 vs system_syscall.md §3 |

## 3. 現状のギャップ（pysim実装との差分）

- **重大**: pysimには`vsoc_harness`/`vsoc_context`/`vsoc_runtime`に相当する統合レイヤが存在しない。`main.py`が`Interpreter`・`ModuleJIT`・`System`を個別に直接インスタンス化しており、静的DI・`exec_trace`統一呼び出し・Safepoint/JITキャッシュ協調のいずれも実装されていない（VSOC-01〜24はJIT/インタープリタ双方の未実装アーキテクチャに起因し、全件未検証）。
- **要解決**: VSOC-40の`fireball_call`シグネチャの矛盾（7引数 vs 8引数）。pysimは`system_syscall.md`/`interface_wit.md`側（7引数: id+arg0..arg5）を採用しているが、runtime_vsoc.md §5.2との不一致は解消されていない。**この場で独断で解決しない**。
- マルチモジュールリンク（VSOC-30/31）はpysim・runtime_loader双方で未実装（runtime_loader_test_spec.md LOAD-20〜24と同根）。

## 4. 未検証・スコープ外

- `wit/vsoc_runtime.wit`によるWIT型定義そのものとの整合性。
- Cortex-M33実機でのSafepointチェック周期の精度（`{Challenge_ApproximateYield}`は仕様上も「検討中」の未解決課題）。
- マルチコア環境でのメモリ可視性（§6.4「既知の制限」でスコープ外と明記）。
