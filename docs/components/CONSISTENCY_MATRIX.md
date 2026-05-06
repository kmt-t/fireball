# 仕様整合性チェックマトリクス

本ドキュメントは、仕様書間の矛盾を体系的に発見するためのチェックシートである。
各「仕様の組」に対して観点を定め、チェックボックスで状態を記録する。
自動検証は `.claude/scripts/check_consistency.py` で補完する。

## 観点 (観点コード)

| コード | 観点名 | 説明 |
| :--- | :--- | :--- |
| **A** | インターフェース整合 | API名・引数名・戻り値型の対応 |
| **B** | データモデル整合 | 構造体フィールド・型定義の一致 |
| **C** | 状態遷移整合 | 状態名・遷移トリガの一致 |
| **D** | メモリ予算整合 | パーティション名・サイズの合計が一致すること |
| **E** | Tier規制整合 | 層分類と依存方向が3層ルールに従うこと |
| **F** | 要求トレーサビリティ | `{Keyword}` 引用が requirement_list.md の定義と矛盾しないこと |
| **G** | セキュリティモデル整合 | RBAC・所有権移譲ポリシーが一貫すること |
| **H** | エラー処理整合 | エラー伝播・リカバリ戦略の一貫性 |
| **I** | IPCモデル整合 | 通信方式（チャネル／イベントキュー）と所有権プロトコルの一貫性 |

---

## 優先度 1：矛盾の可能性が高い組

### [P1-1] `os_coos.md` × `os_event_driven.md`

**対象ファイル**:
- `docs/components/core/os_coos.md`
- `docs/components/os_event_driven.md`

**背景**: `os_event_driven.md` は `[REVISED]` とタイトルにあるが、`os_coos.md` が同時に存在しており、どちらが正規の設計書か不明。IPCモデルが根本的に異なる可能性がある。

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **I** | coos.md §4.1 vs event_driven.md §1 | IPCの基本方式: `send`/`recv`（CSPチャネル直接）vs `call`/`reply`（EventQueue経由）のどちらが正規か明示されているか | [ ] |
| 2 | **C** | coos.md §6.1表 vs event_driven.md §2.1 | タスク状態名の対応: `BLOCKED` (coos) が `BLOCKED_CALL`/`BLOCKED_REPLY` (event_driven) に細分化されているが、スケジューラ仕様に反映されているか | [ ] |
| 3 | **B** | coos.md §3.1 vs event_driven.md §2.1 | データ構造の対応: `channel`（coos）と `EventQueue`（event_driven）の関係が定義されているか | [ ] |
| 4 | **I** | coos.md §4.1 CSP Handoff vs event_driven.md §3.1 | `{CSP_Handoff}`（直接スイッチ）の実装: EventQueue モデルではキューイングするため「直接スイッチ」と矛盾しないか | [ ] |
| 5 | **F** | 両ドキュメントの `{Keyword}` | 両方で引用しているキーワード（`{CooperativeMultitasking}` 等）の意味が同じ文脈で使われているか | [ ] |

---

### [P1-2] `os_event_driven.md` × `ipc_router.md`

**対象ファイル**:
- `docs/components/os_event_driven.md`
- `docs/components/interface/ipc_router.md`

**背景**: `ipc_router.md` は `route_message` + `{CSP_Handoff}` をIPCの中心に置くが、`os_event_driven.md` は EventQueue + `call`/`reply` を中心とする。両者のプロトコルが接続されているか不明。

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **A** | ipc_router.md §5.1 `route_message` vs event_driven.md §3.1 | `route_message` と `call`/`reply` の関係: `route_message` は Event 投入の内部実装か、別の経路か | [ ] |
| 2 | **I** | ipc_router.md §4.1 所有権移譲 vs event_driven.md §3.1 所有権移譲 | 所有権移譲プロトコルの一致: ipc_router の Revoke/Enqueue/Grant が event_driven の `message_owner` 管理と整合するか | [ ] |
| 3 | **G** | ipc_router.md §3.1 ロールマトリックス vs event_driven.md 全体 | アクセス制御: EventQueue 経由のメッセージにロールチェックが適用されるか明示されているか | [ ] |
| 4 | **I** | ipc_router.md §4.1 `{CSP_Handoff}` vs event_driven.md §3.1 | `{CSP_Handoff}` の定義: ipc_router では「待機中の相手への即時スイッチ」だが、EventQueueモデルでは必ずキューを経由する — 矛盾しないか | [ ] |
| 5 | **H** | ipc_router.md §4.1 Rollback vs event_driven.md §3.1 異常系 | キュー満杯時の動作: ipc_router の「所有権を返却（Restore）」と event_driven の「BLOCKED_REPLY へ遷移」が整合するか | [ ] |

---

### [P1-3] `os_scheduler.md` × `os_event_driven.md`

**対象ファイル**:
- `docs/components/core/os_scheduler.md`
- `docs/components/os_event_driven.md`

**背景**: `os_scheduler.md` の状態遷移図には `BLOCKED_CALL`/`BLOCKED_REPLY` がなく、`os_event_driven.md` が導入した状態がスケジューラに反映されていない疑いがある。

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **C** | scheduler.md §4.2 状態遷移図 | `BLOCKED_CALL`/`BLOCKED_REPLY` 状態の欠落: scheduler.md の状態遷移に event_driven で定義された新状態が追加されているか | [ ] |
| 2 | **C** | scheduler.md §4.2 vs event_driven.md §3.1 | `BLOCKED_REPLY` からの復帰条件: キューに空きができたタイミングでの READY 遷移が scheduler.md に記述されているか | [ ] |
| 3 | **A** | scheduler.md §5.1 `notify_interrupt` vs event_driven.md §3.2 ISR | ISRイベント投入: scheduler の `notify_interrupt` API と event_driven の `queue.enqueue(INT event)` の対応関係が明示されているか | [ ] |
| 4 | **A** | scheduler.md §5.1 `set_idle_handler` vs event_driven.md §3.3 IdleAction | アイドル時の割り込み再検出: `IdleAction_WithInterruptPoll` が scheduler の idle handler 登録と連動しているか | [ ] |

---

### [P1-4] `architecture_overview.md` × `resource_budget.md`

**対象ファイル**:
- `docs/architecture/architecture_overview.md`
- `docs/architecture/resource_budget.md`

**背景**: 両ドキュメントがメモリパーティション構成を定義しているが、名称と責務の記述が微妙に異なる。

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **D** | arch_overview.md §5 vs resource_budget.md §1 | パーティション名の対応: `vSoCヒープ`（arch）と `vSoCメタデータ`（budget）が同一パーティションを指しているか | [ ] |
| 2 | **D** | arch_overview.md §5 vs resource_budget.md §1 | サイズの合計一致: 各パーティションのサイズが両ドキュメントで同じ数値（KB）か（22KB合計） | [ ] |
| 3 | **D** | arch_overview.md §5 責務列 vs resource_budget.md §1 責務列 | 責務記述の一貫性: 「ネイティブヒープ」の責務欄に列挙されているコンポーネントが一致しているか | [ ] |
| 4 | **D** | resource_budget.md §2 ROM予算 vs arch_overview.md §2.1 レイヤー構成 | ROMコンポーネント対応: Engine (JIT/Intp) 32KB の内訳が architecture レイヤー構成と矛盾しないか | [ ] |

---

## 優先度 2：中程度リスクの組

### [P2-1] `ipc_router.md` × `system_service.md`

**対象ファイル**:
- `docs/components/interface/ipc_router.md`
- `docs/components/interface/system_service.md`

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **A** | ipc_router.md §5.1 `lookup_service` vs system_service.md §4.3 WASI呼び出し | URI解決の手順: system_service が `lookup("fireball://hal/uart/0")` を呼ぶフローが ipc_router の `lookup_service` シグネチャと一致するか | [ ] |
| 2 | **G** | ipc_router.md §3.1 ロール vs system_service.md §3.1 Tier定義 | ロールとTierの対応: Tier 0 サービスと Tier 1 サービスに割り当てられるロールが ipc_router のアクセス制御マトリックスに定義されているか | [ ] |
| 3 | **I** | ipc_router.md §4.1 所有権移譲 vs system_service.md §4.4 WASI-IPC変換 | 同期WASI↔非同期IPC: `{WASI_Async_Bridge}` の実装が所有権移譲プロトコルと矛盾しないか | [ ] |
| 4 | **E** | system_service.md §2 Tier 1 vs ipc_router.md §2 Tier 1 | 同一Tier分類: 両方とも Tier 1 (アーキテクチャドメイン) だが、依存方向が正しく定義されているか | [ ] |

---

### [P2-2] `runtime_vsoc.md` × `jit_compiler.md`

**対象ファイル**:
- `docs/components/runtime/runtime_vsoc.md`
- `docs/components/jit/jit_compiler.md`

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **A** | vsoc.md §3.3 `vsoc_harness` JITコンパイラ欄 vs jit_compiler.md 公開API | JITハーネスインターフェース: `vsoc_harness` の `JitCompiler*` が参照するインターフェースが jit_compiler.md で定義されているか | [ ] |
| 2 | **B** | vsoc.md §3.3 `vsoc_config` コードキャッシュサイズ vs jit_compiler.md キャッシュ管理 | Active/Oldダブルバッファ: vsoc_config の `コードキャッシュサイズ` と jit_compiler の `2KB x 2` の関係が整合するか | [ ] |
| 3 | **C** | vsoc.md §4.2 状態遷移 Running→Debugging vs jit_compiler.md `{Debugger_Jit_Flush}` | JITキャッシュFlushのトリガ: vsoc が Debugging 状態に遷移した際に jit_compiler のキャッシュ無効化が確実に呼ばれる経路が定義されているか | [ ] |
| 4 | **I** | vsoc.md §4.1 `{JIT_Safepoint}` vs jit_compiler.md コンパイルフロー | Safepointの埋め込み: vsoc の exec_trace が JIT生成コードのバックエッジで Safepoint を呼び出す仕組みが jit_compiler の生成ロジックに記述されているか | [ ] |

---

### [P2-3] `runtime_vsoc.md` × `runtime_interpreter.md`

**対象ファイル**:
- `docs/components/runtime/runtime_vsoc.md`
- `docs/components/runtime/runtime_interpreter.md`

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **A** | vsoc.md §4.1 `exec_trace` vs interpreter.md 公開API | 実行委譲インターフェース: vsoc の `exec_trace` がインタープリタのディスパッチャを呼ぶシグネチャが一致しているか | [ ] |
| 2 | **C** | vsoc.md §4.2 Running vs interpreter.md `{Interpreter_LazyJITSwitch}` | インタープリタ→JIT切り替え: `{Interpreter_LazyJITSwitch}` の実行タイミングが vsoc の状態遷移図に表れているか | [ ] |
| 3 | **F** | interpreter.md `{InterpreterContextStackless}` vs vsoc.md | スタックレス設計: インタープリタのコンテキスト管理が vsoc の `vsoc_context` で保持するスタックレス前提と整合するか | [ ] |

---

### [P2-4] `runtime_vmmio.md` × `ipc_router.md`

**対象ファイル**:
- `docs/components/runtime/runtime_vmmio.md`
- `docs/components/interface/ipc_router.md`

**背景**: vMMIO はIPC経由を明示的に使わないとしているが (`Fast_Path_GPIO`)、セキュリティゲートとしてのRBACポリシーが ipc_router のロールモデルと独立に定義されている。

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **G** | vmmio.md §1 セキュリティモデル vs ipc_router.md §3.1 ロールマトリックス | ロールの共通定義: vMMIO のアクセス権限（perm フラグ）と ipc_router のロールが同一の定義体を参照しているか、それとも独立した別定義か | [ ] |
| 2 | **E** | vmmio.md §2 Tier 3 vs ipc_router.md §2 Tier 1 | 依存方向: Tier 3 (vMMIO) が Tier 1 (IPC Router) のデータ構造や型を直接参照していないか | [ ] |
| 3 | **G** | vmmio.md §3.1 `vmmio_perm_table` vs ipc_router.md §3.1 `registry_entry` セキュリティロール | 権限テーブルの二重管理: 同一リソースの権限が vMMIO と IPC Router の両方で管理される場合、整合性を保つプロトコルが定義されているか | [ ] |

---

### [P2-5] `platform_memory.md` × `resource_budget.md`

**対象ファイル**:
- `docs/components/platform/platform_memory.md`
- `docs/architecture/resource_budget.md`

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **D** | platform_memory.md 物理RAM配置 vs resource_budget.md §1 | 物理アドレスとパーティションの対応: platform_memory で定義する物理アドレス範囲が resource_budget のパーティションサイズと矛盾しないか | [ ] |
| 2 | **B** | platform_memory.md アライメント要件 vs vmmio.md §3.3 `WasmPageAlignment` | ページアライメント整合: platform_memory の物理メモリ配置が vMMIO の64KB境界要件を満たせるか | [ ] |

---

### [P2-6] `system_syscall.md` × `ipc_router.md`

**対象ファイル**:
- `docs/components/core/system_syscall.md`
- `docs/components/interface/ipc_router.md`

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **A** | syscall.md `{Syscall_Mapping}` vs ipc_router.md §5.1 | システムコール→IPC変換: WASMゲストの syscall ID が ipc_router の URI または channel_id にどのようにマッピングされるか明示されているか | [ ] |
| 2 | **H** | syscall.md `{Syscall_Return_Value}` vs ipc_router.md §4.1 エラー時の挙動 | エラー伝播の一貫性: IPC エラー (permission_denied / not_found) がゲストに返るエラーコード体系と一致しているか | [ ] |
| 3 | **I** | syscall.md `{Trap_Interface}` vs vmmio.md §1 コンセプト | トラップ経路: `fireball_call` (vMMIO経由) と IPC Router 経由の syscall の使い分けが明確に定義されているか | [ ] |

---

### [P2-7] `os_coos.md` × `os_scheduler.md`

**対象ファイル**:
- `docs/components/core/os_coos.md`
- `docs/components/core/os_scheduler.md`

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **E** | coos.md §2 Tier 2 vs scheduler.md §2 Tier 3 | Tier階層の整合: COOS (Tier 2) のサブコンポーネントとして Scheduler (Tier 3) が正しく配置されているか | [ ] |
| 2 | **A** | coos.md §5.2 `scheduler` 型 vs scheduler.md §5.1 公開API | ハーネス参照: `coos_harness.scheduler` が参照する型のインターフェースが scheduler.md §5.1 の公開API（`spawn`, `yield`, `wait`, `exit`）と一致しているか | [ ] |
| 3 | **C** | coos.md §6.1 検証表 vs scheduler.md §4.2 状態遷移図 | 検証表との整合: coos の CSP Handoff ケース 3, 6（直接スイッチ）が scheduler の BLOCKED→READY 遷移として表現されているか | [ ] |

---

## 優先度 3：安定しているが確認推奨

### [P3-1] `debug_manager.md` × `debug_gdb_rsp.md`

**対象ファイル**:
- `docs/components/runtime/debug/debug_manager.md`
- `docs/components/runtime/debug/debug_gdb_rsp.md`

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **A** | debug_manager.md 公開API vs debug_gdb_rsp.md RSPコマンド対応 | RSPコマンド→DebugManager API: 各 GDB RSP コマンド（g/G/m/M等）が呼び出す debug_manager の API が定義されているか | [ ] |
| 2 | **F** | debug_gdb_rsp.md `{RSPMinimalSet}` vs requirement_list.md | 最小RSPセット: requirement_list の `{RSPMinimalSet}` で定義された「VSCodeデバッグに必要な最小限」のコマンドセットが debug_gdb_rsp.md に網羅されているか | [ ] |

---

### [P3-2] `runtime_loader.md` × `runtime_vsoc.md`

**対象ファイル**:
- `docs/components/runtime/runtime_loader.md`
- `docs/components/runtime/runtime_vsoc.md`

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **A** | vsoc.md §3.3 `vsoc_harness` WASMローダ欄 vs loader.md 公開API | ローダインターフェース: `vsoc_harness` の `WasmLoader*` が参照するインターフェースが loader.md で定義されているか | [ ] |
| 2 | **B** | vsoc.md §3.3 `vsoc_context` モジュールビュー vs loader.md 出力形式 | ゼロコピーローディング: `{ROMParsing}` により loader が生成する `wasm_module_view*` が vsoc_context の参照型と一致しているか | [ ] |

---

### [P3-3] `platform_hal.md` × `ipc_router.md`

**対象ファイル**:
- `docs/components/platform/platform_hal.md`
- `docs/components/interface/ipc_router.md`

**チェック項目**:

| # | 観点 | 確認箇所 | 確認内容 | 状態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **A** | hal.md §5 `{HAL_Interface}` の公開API vs ipc_router.md §5.1 サービス登録 | HAL→IPC登録: HAL が実装するサービスが ipc_router の `register_service` に正しい URI 形式で登録される経路が定義されているか | [ ] |
| 2 | **G** | hal.md アクセス制御 vs ipc_router.md ロールマトリックス | HW直接アクセスの制限: `{Fast_Path_GPIO}` による IPC バイパスが ipc_router のロール制限の範囲内で許可されているか | [ ] |

---

## 全体横断チェック（自動検証対象）

以下は `.claude/scripts/check_consistency.py` による機械的チェックで補完する。

| # | 観点 | チェック内容 | 自動化 |
| :--- | :--- | :--- | :--- |
| X1 | **F** | すべてのコンポーネント仕様書の `{Keyword}` が requirement_list.md に定義されているか | Python |
| X2 | **F** | requirement_list.md に定義されているキーワードのうち、どのコンポーネント仕様にも引用されていない「孤立キーワード」がないか | Python |
| X3 | **E** | `Tier 3` と宣言したドキュメントが `Tier 1` または `Tier 2` のコンポーネント名を直接参照していないか（テキスト検索） | Python |
| X4 | **D** | resource_budget.md の RAM パーティション合計が 64KB を超えていないか | Python |
| X5 | **A** | `ipc_router.md` の公開API名（`register_service`, `lookup_service`, `route_message`）が他ドキュメントで一貫した名称で参照されているか | Python |

---

## チェック実施ログ

| 日付 | 担当 | チェック対象 | 発見した矛盾 | 対処 |
| :--- | :--- | :--- | :--- | :--- |
| (未実施) | - | - | - | - |

---

## 既知の疑い箇所（本チェックシート作成時点）

以下は本マトリクス作成時の仕様分析で発見した疑い箇所。チェック優先度を上げることを推奨する。

| ID | 場所 | 疑い内容 | 優先度 |
| :--- | :--- | :--- | :--- |
| S1 | P1-3 #1 | `os_scheduler.md` の状態遷移図に `BLOCKED_CALL`/`BLOCKED_REPLY` 状態がなく、`os_event_driven.md` の状態モデルと不一致 | 高 |
| S2 | P1-1 #1 | `os_coos.md`（CSP直接）と `os_event_driven.md`（EventQueue）でIPCモデルが根本的に異なる — 後者への移行が前者に反映されていない疑い | 高 |
| S3 | P1-4 #1 | `architecture_overview.md` で「vSoCヒープ」と呼ぶパーティションを `resource_budget.md` では「vSoCメタデータ」と呼んでいる — 名称不一致 | 中 |
| S4 | P1-2 #4 | `ipc_router.md` の `route_message` が `{CSP_Handoff}`（直接スイッチ）を使うと記述するが、`os_event_driven.md` ではすべての通信がEventQueue経由 — 矛盾の可能性 | 高 |
| S5 | P2-4 #1 | vMMIO のアクセス権限（perm フラグ）と ipc_router のロールが別々に定義されており、共通の定義体が存在しない可能性 | 中 |
