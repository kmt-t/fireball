# Fireball 設計複雑度 & リスク評価レポート (Risk Assessment Report)

- **評価セクション総数**: 835
- **形式検証 (pyModelChecking) 推奨セクション**: 29
- **LLM 意味監査 推奨セクション**: 2

---

## 1. 形式検証 (pyModelChecking) が推奨される重要セクション

| ファイル | セクション | 複雑度 | リスク | 推奨検証 | 推奨タグ | 主なリスク要因 |
| :--- | :--- | :---: | :---: | :--- | :--- | :--- |
| `components/tier1_interface/ipc_router.md` | **4.1 アルゴリズム** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_core/os_coos.md` | **4.2 状態遷移図 (SMD: COOS システムレベル)** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_core/os_coos.md` | **4.1 アルゴリズム** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_interface/ipc_router.md` | **4.2 状態遷移図 (SysML SMD: IPC Router ルーティングフロー)** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier3_jit/jit_compiler.md` | **7.2 安全性制約と方策** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_core/os_scheduler.md` | **4.2 状態遷移図 (SysML SMD: Scheduler 視点)** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_interface/ipc_router.md` | **メッセージルーティング（route_message）** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_interface/ipc_router.md` | **4.1.1 名前解決パイプラインとアクセス制御フロー** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_interface/ipc_router.md` | **4.2.1 所有権移譲状態機械 (Ownership Transfer State Machine)** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier3_platform/platform_memory.md` | **9. ハードウェアメモリ保護 (MPU) & W^X 設計** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_core/os_coos.md` | **チャネル送受信動作の挙動定義** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_interface/ipc_router.md` | **3.2 内部ブロック図** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_core/os_coos.md` | **6.1 検証対象の不変条件** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier3_platform/platform_hal.md` | **5.3 URI/IPCインターフェイス** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_core/os_coos.md` | **3.2 内部ブロック図** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_core/os_coos.md` | **COOS フルセット・コンセプトコード (`concepts/coos_concept.py`)** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_interface/ipc_router.md` | **IPC ルータ フルセット・コンセプトコード (`concepts/ipc_router_concept.py`)** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_interface/ipc_router.md` | **所有権移譲フロー (Zero-Copy Handoff)** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_core/os_coos.md` | **ハーネスによる依存性注入パターン** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_core/os_coos.md` | **2. 公開 API インターフェイス** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_core/os_coos.md` | **4.3 タスク状態遷移図 (SMD: Task ライフサイクル)** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_interface/ipc_router.md` | **4.3.2 CSP Handoff スターベーション防止対策** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_interface/ipc_router.md` | **6.3 検証モデル概要** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier3_platform/platform_memory.md` | **9.1 Cortex-M33 PMSAv8 MPU リージョン配分** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier3_platform/platform_memory.md` | **トランザクションバッチ化によるレイテンシ両立** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier3_platform/platform_memory.md` | **9.2 JIT W^X (Write XOR Execute) 切替プロトコル** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier3_platform/platform_memory.md` | **属性切替シーケンス** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier2_runtime/runtime_interpreter.md` | **統合 Tiered ランタイムエンジン・コンセプトコード (`concepts/runtime_engine_concept.py`)** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier3_jit/jit_compiler.md` | **統合 Tiered ランタイムエンジン・コンセプトコード (`../tier2_runtime/concepts/runtime_engine_concept.py`)** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |

---

## 2. 全セクションの複雑度・リスク評価一覧 (降順)

| ファイル | セクション | Tier | 複雑度 | リスク | 推奨手法 | 推奨タグ | 評価サマリー |
| :--- | :--- | :---: | :---: | :---: | :--- | :--- | :--- |
| `components/tier1_interface/ipc_router.md` | 4.1 アルゴリズム | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier1_core/os_coos.md` | 4.2 状態遷移図 (SMD: COOS システムレベル) | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '4.2 状態遷移図 (SMD: COOS システムレベル)'. |
| `components/tier1_core/os_coos.md` | 4.1 アルゴリズム | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier1_interface/ipc_router.md` | 4.2 状態遷移図 (SysML SMD: IPC Router ルーティングフロー) | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '4.2 状態遷移図 (SysML SMD: IPC Router ルーティングフロー)'. |
| `components/tier3_jit/jit_compiler.md` | 7.2 安全性制約と方策 | 3 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '7.2 安全性制約と方策'. |
| `components/tier1_core/os_scheduler.md` | 4.2 状態遷移図 (SysML SMD: Scheduler 視点) | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '4.2 状態遷移図 (SysML SMD: Scheduler 視点)'. |
| `components/tier1_interface/ipc_router.md` | メッセージルーティング（route_message） | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for 'メッセージルーティング（route_message）'. |
| `components/tier1_interface/ipc_router.md` | 4.1.1 名前解決パイプラインとアクセス制御フロー | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '4.1.1 名前解決パイプラインとアクセス制御フロー'. |
| `components/tier1_interface/ipc_router.md` | 4.2.1 所有権移譲状態機械 (Ownership Transfer State Machine) | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '4.2.1 所有権移譲状態機械 (Ownership Transfer State Machine)'. |
| `components/tier3_platform/platform_memory.md` | 9. ハードウェアメモリ保護 (MPU) & W^X 設計 | 3 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '9. ハードウェアメモリ保護 (MPU) & W^X 設計'. |
| `components/tier1_core/os_coos.md` | チャネル送受信動作の挙動定義 | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for 'チャネル送受信動作の挙動定義'. |
| `components/tier1_interface/ipc_router.md` | 3.2 内部ブロック図 | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier1_core/os_coos.md` | 6.1 検証対象の不変条件 | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '6.1 検証対象の不変条件'. |
| `components/tier3_platform/platform_hal.md` | 5.3 URI/IPCインターフェイス | 3 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '5.3 URI/IPCインターフェイス'. |
| `components/tier1_core/os_coos.md` | 3.2 内部ブロック図 | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier1_core/os_coos.md` | COOS フルセット・コンセプトコード (`concepts/coos_concept.py`) | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for 'COOS フルセット・コンセプトコード (`concepts/coos_concept.py`)'. |
| `components/tier1_interface/ipc_router.md` | IPC ルータ フルセット・コンセプトコード (`concepts/ipc_router_concept.py`) | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for 'IPC ルータ フルセット・コンセプトコード (`concepts/ipc_router_concept.py`)'. |
| `components/tier1_interface/ipc_router.md` | 所有権移譲フロー (Zero-Copy Handoff) | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '所有権移譲フロー (Zero-Copy Handoff)'. |
| `components/tier1_core/os_coos.md` | ハーネスによる依存性注入パターン | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for 'ハーネスによる依存性注入パターン'. |
| `components/tier1_core/os_coos.md` | 2. 公開 API インターフェイス | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '2. 公開 API インターフェイス'. |
| `components/tier1_core/os_coos.md` | 4.3 タスク状態遷移図 (SMD: Task ライフサイクル) | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '4.3 タスク状態遷移図 (SMD: Task ライフサイクル)'. |
| `components/tier1_interface/ipc_router.md` | 4.3.2 CSP Handoff スターベーション防止対策 | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '4.3.2 CSP Handoff スターベーション防止対策'. |
| `components/tier1_interface/ipc_router.md` | 6.3 検証モデル概要 | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '6.3 検証モデル概要'. |
| `components/tier3_platform/platform_memory.md` | 9.1 Cortex-M33 PMSAv8 MPU リージョン配分 | 3 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '9.1 Cortex-M33 PMSAv8 MPU リージョン配分'. |
| `components/tier3_platform/platform_memory.md` | トランザクションバッチ化によるレイテンシ両立 | 3 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for 'トランザクションバッチ化によるレイテンシ両立'. |
| `components/tier3_platform/platform_memory.md` | 9.2 JIT W^X (Write XOR Execute) 切替プロトコル | 3 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '9.2 JIT W^X (Write XOR Execute) 切替プロトコル'. |
| `components/tier3_platform/platform_memory.md` | 属性切替シーケンス | 3 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '属性切替シーケンス'. |
| `components/tier2_runtime/runtime_interpreter.md` | 統合 Tiered ランタイムエンジン・コンセプトコード (`concepts/runtime_engine_concept.py`) | 2 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '統合 Tiered ランタイムエンジン・コンセプトコード (`concepts/runtime_engine_concept.py`)'. |
| `components/tier3_jit/jit_compiler.md` | 統合 Tiered ランタイムエンジン・コンセプトコード (`../tier2_runtime/concepts/runtime_engine_concept.py`) | 3 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '統合 Tiered ランタイムエンジン・コンセプトコード (`../tier2_runtime/concepts/runtime_engine_concept.py`)'. |
| `components/tier1_core/os_scheduler.md` | 6. 設計判断 (ADR) | 1 | 3/5 | 3/5 | `LLM_Judge` | `{VERIFY_LLM}` | Independent heuristic evaluation for '6. 設計判断 (ADR)'. |
| `components/tier3_jit/jit_compiler.md` | 8. 設計判断 (ADR) | 3 | 3/5 | 3/5 | `LLM_Judge` | `{VERIFY_LLM}` | Independent heuristic evaluation for '8. 設計判断 (ADR)'. |
| `components/tier2_runtime/runtime_interpreter.md` | 4.1 アルゴリズム | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier1_core/system_config.md` | 3.3.4 vSoC / vMMIO | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3.4 vSoC / vMMIO'. |
| `requires/requirement_list.md` | 3.1.1 WASM実行 (vSoC) | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1.1 WASM実行 (vSoC)'. |
| `requires/requirement_list.md` | 3.1.3 システム連携 (IPC/HAL/WIT) | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1.3 システム連携 (IPC/HAL/WIT)'. |
| `components/tier1_core/system_config.md` | 3.3.1 メモリ管理 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3.1 メモリ管理'. |
| `components/tier2_runtime/runtime_vmmio.md` | 1. コンセプト | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `architecture/architecture_overview.md` | 1. アーキテクチャコンセプトと基本思想 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. アーキテクチャコンセプトと基本思想'. |
| `requires/requirement_list.md` | 4. 設計課題・制約追跡 (Design Challenges & ADRs) | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 設計課題・制約追跡 (Design Challenges & ADRs)'. |
| `components/tier1_core/system_containers.md` | 1. コンセプト | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `architecture/document_structure.md` | 4.2 メタキーワード（共通非機能要件・設計方針）の定義 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2 メタキーワード（共通非機能要件・設計方針）の定義'. |
| `architecture/keyword_dictionary.md` | 2.1 WASM実行 & ランタイム (vSoC / Interpreter / Loader / vMMIO) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2.1 WASM実行 & ランタイム (vSoC / Interpreter / Loader / vMMIO)'. |
| `components/tier1_core/os_coos.md` | 1. コンセプト | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `architecture/architecture_overview.md` | 8. アーキテクチャスタイルと設計判断 (ADR) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '8. アーキテクチャスタイルと設計判断 (ADR)'. |
| `requires/requirement_list.md` | 3.2.1 パフォーマンス・効率 | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2.1 パフォーマンス・効率'. |
| `components/tier2_runtime/debug_manager.md` | 1. コンセプト | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier1_core/system_config.md` | 1. コンセプト | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier3_jit/jit_runtime.md` | 1. コンセプト | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `requires/requirement_list.md` | 3.1.2 タスク管理・通信 (COOS) | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1.2 タスク管理・通信 (COOS)'. |
| `components/tier2_runtime/runtime_interpreter.md` | オプコードハンドラ / トレース実行（opcode_handler / exec_trace） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'オプコードハンドラ / トレース実行（opcode_handler / exec_trace）'. |
| `components/tier3_jit/jit_compiler.md` | 1. コンセプト | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier2_runtime/runtime_vsoc.md` | 4.1 アルゴリズム | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `architecture/keyword_dictionary.md` | 2.4 デバッガ & システムコア & プラットフォーム (Debug / Syscall / Logging / HAL) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2.4 デバッガ & システムコア & プラットフォーム (Debug / Syscall / Logging / HAL)'. |
| `components/tier1_core/system_containers.md` | 4.1 アルゴリズム | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier2_runtime/runtime_interpreter.md` | 実行コンテキスト（execution_context @ スタックボトム） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '実行コンテキスト（execution_context @ スタックボトム）'. |
| `requires/requirement_list.md` | 3.1.5 共通基盤・実装パターン | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1.5 共通基盤・実装パターン'. |
| `components/tier1_core/os_scheduler.md` | 1. コンセプト | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier1_core/system_containers.md` | 3.1 データ構造 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier1_interface/system_service.md` | 4.1 アルゴリズム | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier1_interface/ipc_router.md` | 1. コンセプト | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `architecture/architecture_overview.md` | 4. 物理レジスタ＆ABI規約 (Physical Register & ABI Map) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 物理レジスタ＆ABI規約 (Physical Register & ABI Map)'. |
| `components/tier1_core/system_containers.md` | ビット詰めビュー（bit_view） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ビット詰めビュー（bit_view）'. |
| `architecture/keyword_dictionary.md` | 3. システム横断 メタキーワード (Meta & Global Keywords) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. システム横断 メタキーワード (Meta & Global Keywords)'. |
| `components/tier2_runtime/runtime_loader.md` | 4.1 アルゴリズム | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `architecture/architecture_overview.md` | 3.1 Pillar 1: 統合スタックフレーム・モデル (Unified Stack Frame Model) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 Pillar 1: 統合スタックフレーム・モデル (Unified Stack Frame Model)'. |
| `components/tier1_core/os_coos.md` | CSPチャネル（channel） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'CSPチャネル（channel）'. |
| `architecture/document_structure.md` | 4.3 グローバルキーワード（広域仕様・横断ポリシー）の定義 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.3 グローバルキーワード（広域仕様・横断ポリシー）の定義'. |
| `components/tier2_runtime/runtime_vsoc.md` | 6.1 検証対象の不変条件 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1 検証対象の不変条件'. |
| `components/tier3_platform/platform_hal.md` | 1. コンセプト | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier3_platform/platform_hal.md` | 4.1 アルゴリズム | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier1_core/system_logging.md` | 1. コンセプト | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier1_core/system_containers.md` | 6.2 メモリ制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 メモリ制約と方策'. |
| `components/tier2_runtime/runtime_interpreter.md` | 4.2 状態遷移図 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2 状態遷移図'. |
| `components/tier3_platform/platform_memory.md` | 7. 共有メモリ (shared-block) のライフサイクル | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7. 共有メモリ (shared-block) のライフサイクル'. |
| `components/tier2_runtime/runtime_vsoc.md` | vSoCランタイム環境（vsoc_runtime） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'vSoCランタイム環境（vsoc_runtime）'. |
| `components/tier3_jit/jit_compiler.md` | コピーアンドパッチエンジン（CopyAndPatchEngine）クラス | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'コピーアンドパッチエンジン（CopyAndPatchEngine）クラス'. |
| `architecture/keyword_dictionary.md` | 2.2 タスク管理・スケジューリング・通信 (COOS / Scheduler / IPC) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2.2 タスク管理・スケジューリング・通信 (COOS / Scheduler / IPC)'. |
| `components/tier2_runtime/runtime_vsoc.md` | 4.2 状態遷移図 (SysML SMD: vSoC Engine ライフサイクル) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2 状態遷移図 (SysML SMD: vSoC Engine ライフサイクル)'. |
| `plans/backlog_list.md` | オーナーレビュー観点 & チェックリスト | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'オーナーレビュー観点 & チェックリスト'. |
| `components/tier2_runtime/runtime_loader.md` | 1. コンセプト | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier2_runtime/debug_manager.md` | 4.1 アルゴリズム | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `requires/requirement_list.md` | 3.2.2 開発方針・品質 | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2.2 開発方針・品質'. |
| `specs/jit_stencil_catalog.md` | 1. 概要と基本思想 | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 概要と基本思想'. |
| `specs/wasm_instruction_set.md` | 1. 概要と適用方針 | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 概要と適用方針'. |
| `components/tier1_core/system_containers.md` | 6.1 性能制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1 性能制約と方策'. |
| `components/tier2_runtime/runtime_interpreter.md` | 1. コンセプト | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier3_platform/platform_memory.md` | 1. コンセプト | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `architecture/keyword_dictionary.md` | 2.3 JIT コンパイラ & ランタイム (JIT Compiler / Runtime) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2.3 JIT コンパイラ & ランタイム (JIT Compiler / Runtime)'. |
| `components/tier2_runtime/runtime_vsoc.md` | 1. コンセプト | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier2_runtime/tests/runtime_interpreter_test_spec.md` | デバッガ・プロファイラ統合とハンドラテーブル切り替え (§3.3, §4.1, {DebuggerLabelTableSwitch}, {Debug_Integrated}) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'デバッガ・プロファイラ統合とハンドラテーブル切り替え (§3.3, §4.1, {DebuggerLabelTableSwitch}, {Debug_Integrated})'. |
| `components/tier3_platform/platform_hal.md` | 2. アーキテクチャ分類 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier1_core/system_config.md` | 3.3.2 IPCルータ | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3.2 IPCルータ'. |
| `components/tier1_core/system_syscall.md` | 5.7. WASI (`0x80`-`0xBF`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.7. WASI (`0x80`-`0xBF`)'. |
| `components/tier1_core/system_syscall.md` | 6.2. 高応答 Trigger のマッピング例 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2. 高応答 Trigger のマッピング例'. |
| `components/tier1_interface/interface_wit.md` | 5.3 `fireball:host/bus` (Master/Slave Bus) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.3 `fireball:host/bus` (Master/Slave Bus)'. |
| `specs/jit_stencil_catalog.md` | 3.7 メモリアクセス系ステンシル (Linear Memory Load & Store with Boundary Protection) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.7 メモリアクセス系ステンシル (Linear Memory Load & Store with Boundary Protection)'. |
| `components/tier1_interface/ipc_router.md` | 4.3.1 二分探索による O(log N) 低遅延ルックアップ | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.3.1 二分探索による O(log N) 低遅延ルックアップ'. |
| `components/tier2_runtime/debug_manager.md` | デバッガ（Debugger）クラス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'デバッガ（Debugger）クラス'. |
| `architecture/architecture_overview.md` | 3.2 Pillar 2: 3段直接 JIT 検索パイプライン (3-Stage Direct JIT Lookup Pipeline) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 Pillar 2: 3段直接 JIT 検索パイプライン (3-Stage Direct JIT Lookup Pipeline)'. |
| `architecture/architecture_overview.md` | 3.3 Pillar 3: 3面世代交代回転コードキャッシュ (3-Bank Generational Rotating Code Cache) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 Pillar 3: 3面世代交代回転コードキャッシュ (3-Bank Generational Rotating Code Cache)'. |
| `components/tier1_interface/ipc_router.md` | 6.1 検証対象の不変条件 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1 検証対象の不変条件'. |
| `components/tier3_jit/jit_compiler.md` | 検索範囲取得（get_search_range） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '検索範囲取得（get_search_range）'. |
| `components/tier3_platform/platform_memory.md` | 8. 設計判断 (ADR) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '8. 設計判断 (ADR)'. |
| `plans/backlog_list.md` | Phase 1.3: Copy-and-Patch JIT Compiler (`jit_compiler`) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 1.3: Copy-and-Patch JIT Compiler (`jit_compiler`)'. |
| `components/tier2_runtime/runtime_vsoc.md` | 7.2 メモリ制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7.2 メモリ制約と方策'. |
| `requires/requirement_list.md` | 3.1.4 デバッグ・運用 | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1.4 デバッグ・運用'. |
| `specs/gdb_rsp_protocol.md` | 1. 概要と基本思想 | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 概要と基本思想'. |
| `specs/wasi_preview1_abi.md` | 1. 概要と基本思想 | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 概要と基本思想'. |
| `components/tier1_core/os_coos.md` | 6.2 直交表: CSP通信と状態遷移 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 直交表: CSP通信と状態遷移'. |
| `components/tier2_runtime/runtime_interpreter.md` | 4.3 内部シーケンス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.3 内部シーケンス'. |
| `components/tier1_core/system_containers.md` | 3.2 内部ブロック図 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier1_interface/ipc_router.md` | 4.4 内部シーケンス図 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.4 内部シーケンス図'. |
| `components/tier1_interface/tests/ipc_router_test_spec.md` | 2. テストケース一覧 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier3_jit/jit_compiler.md` | 4.1 アルゴリズム | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier1_interface/interface_wit.md` | 3.2 リカバリー戦略とエラーハンドリング | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 リカバリー戦略とエラーハンドリング'. |
| `components/tier2_runtime/runtime_interpreter.md` | コールフレーム（call_frame @ 統合スタックインライン） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'コールフレーム（call_frame @ 統合スタックインライン）'. |
| `components/tier3_jit/jit_compiler.md` | 5.1 直交表: 検索・昇格・代謝 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 直交表: 検索・昇格・代謝'. |
| `components/tier3_platform/platform_memory.md` | `allocate-shared` (IPC転送データ専用) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`allocate-shared` (IPC転送データ専用)'. |
| `components/tier1_core/system_containers.md` | 疎集合ビュー（flat_set_view） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '疎集合ビュー（flat_set_view）'. |
| `components/tier1_core/system_syscall.md` | 5.5. IRQ (`0x30`-`0x3F`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.5. IRQ (`0x30`-`0x3F`)'. |
| `components/tier1_interface/interface_wit.md` | 5.5 `fireball:host/console` (`wasi:cli/stdout` / `stderr` 用の生バイト出力) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.5 `fireball:host/console` (`wasi:cli/stdout` / `stderr` 用の生バイト出力)'. |
| `components/tier2_runtime/runtime_vmmio.md` | FlatMap ページテーブル定義 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'FlatMap ページテーブル定義'. |
| `components/tier1_core/os_scheduler.md` | 4.1 アルゴリズム | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier1_interface/ipc_router.md` | 3.1 データ構造 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier1_interface/ipc_router.md` | サービス検索と接続フロー | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'サービス検索と接続フロー'. |
| `components/tier1_interface/system_service.md` | 4.2 状態遷移図 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2 状態遷移図'. |
| `components/tier2_runtime/runtime_vmmio.md` | 6.1 性能制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1 性能制約と方策'. |
| `components/tier2_runtime/runtime_vsoc.md` | 2. アーキテクチャ分類 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier3_jit/jit_compiler.md` | バッチコンパイル (周期実行またはアイドル時) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'バッチコンパイル (周期実行またはアイドル時)'. |
| `components/tier3_jit/jit_compiler.md` | 4.2 状態遷移図 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2 状態遷移図'. |
| `components/tier3_jit/jit_compiler.md` | 5.2 内部コンポーネントのデコンポジション | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2 内部コンポーネントのデコンポジション'. |
| `components/tier1_core/system_config.md` | 5.1 性能・メモリ制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 性能・メモリ制約と方策'. |
| `components/tier1_core/system_logging.md` | 6.3 安全性制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.3 安全性制約と方策'. |
| `components/tier1_interface/ipc_router.md` | 2. アーキテクチャ分類 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier1_interface/system_service.md` | 1. コンセプト | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier1_interface/system_service.md` | 2. アーキテクチャ分類 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier1_interface/system_service.md` | 6.2 メモリ制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 メモリ制約と方策'. |
| `components/tier2_runtime/runtime_loader.md` | 4.4 状態遷移図 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.4 状態遷移図'. |
| `components/tier3_jit/jit_compiler.md` | JIT トレース物理メモリレイアウト (`jit_trace_header`) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'JIT トレース物理メモリレイアウト (`jit_trace_header`)'. |
| `architecture/architecture_overview.md` | 2.2 コンポーネント定義図 (BDD) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2.2 コンポーネント定義図 (BDD)'. |
| `components/tier1_interface/system_service.md` | 4.4 WASI API から HAL への変換ラッパー (コンセプトコード) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.4 WASI API から HAL への変換ラッパー (コンセプトコード)'. |
| `components/tier2_runtime/tests/debug_manager_test_spec.md` | GDB RSP プロトコル & 仮想レジスタ (§3.3, §4.1, gdb_rsp_protocol.md) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'GDB RSP プロトコル & 仮想レジスタ (§3.3, §4.1, gdb_rsp_protocol.md)'. |
| `components/tier3_jit/jit_compiler.md` | トレース・チェイニング（連鎖実行）と専用分岐ハンドラ分離 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'トレース・チェイニング（連鎖実行）と専用分岐ハンドラ分離'. |
| `architecture/architecture_overview.md` | 5. Conceptベース・ハーネス設計 (Concept Harness) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. Conceptベース・ハーネス設計 (Concept Harness)'. |
| `components/tier1_core/system_syscall.md` | 5.6. IPC (`0x40`-`0x4F`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.6. IPC (`0x40`-`0x4F`)'. |
| `components/tier2_runtime/runtime_interpreter.md` | 制御フレーム（control_frame @ 統合スタックインライン） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '制御フレーム（control_frame @ 統合スタックインライン）'. |
| `components/tier2_runtime/runtime_vsoc.md` | WASM実行およびJIT遷移シーケンス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'WASM実行およびJIT遷移シーケンス'. |
| `components/tier2_runtime/tests/runtime_interpreter_test_spec.md` | CPSディスパッチ方式そのもの (§1, interpreter_concept.py冒頭) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'CPSディスパッチ方式そのもの (§1, interpreter_concept.py冒頭)'. |
| `components/tier1_core/system_syscall.md` | 5.3. vMMIO Generic (`0x10`-`0x1F`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.3. vMMIO Generic (`0x10`-`0x1F`)'. |
| `components/tier3_jit/tests/jit_compiler_test_spec.md` | 安全性制約 (§7.2) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '安全性制約 (§7.2)'. |
| `architecture/architecture_overview.md` | 3.5 Pillar 5: 折りたたみXOR TLB ＆ 平坦ページ表 (Folding XOR TLB & FlatMap Page Table) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.5 Pillar 5: 折りたたみXOR TLB ＆ 平坦ページ表 (Folding XOR TLB & FlatMap Page Table)'. |
| `components/tier1_core/system_config.md` | 3.3.5 ロギング・デバッガ | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3.5 ロギング・デバッガ'. |
| `components/tier1_core/system_containers.md` | 疎マップビュー（flat_map_view） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '疎マップビュー（flat_map_view）'. |
| `components/tier1_core/system_logging.md` | 4.4 状態遷移図 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.4 状態遷移図'. |
| `components/tier1_core/system_syscall.md` | 5.1. カテゴリ一覧 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1. カテゴリ一覧'. |
| `components/tier3_platform/platform_hal.md` | 非標準制御 (control) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '非標準制御 (control)'. |
| `architecture/architecture_overview.md` | 3.4 Pillar 4: 対称直接ハンドオフ・エンジン (Symmetric Direct Handoff Engine) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.4 Pillar 4: 対称直接ハンドオフ・エンジン (Symmetric Direct Handoff Engine)'. |
| `components/tier1_core/system_config.md` | 3.3.7 リカバリー戦略 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3.7 リカバリー戦略'. |
| `components/tier1_interface/ipc_router.md` | 5.3 サービスファサード | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.3 サービスファサード'. |
| `components/tier2_runtime/runtime_vsoc.md` | 7.3 安全性制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7.3 安全性制約と方策'. |
| `components/tier3_platform/platform_hal.md` | 4.2 状態遷移図 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2 状態遷移図'. |
| `components/tier2_runtime/runtime_vmmio.md` | アドレスフィールド定義 (vmmio_address) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'アドレスフィールド定義 (vmmio_address)'. |
| `components/tier2_runtime/runtime_vsoc.md` | Active/Warm/Oldest 3面マルチバッファとキャッシュローテーション | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Active/Warm/Oldest 3面マルチバッファとキャッシュローテーション'. |
| `components/tier2_runtime/runtime_vsoc.md` | 4.3 内部シーケンス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.3 内部シーケンス'. |
| `components/tier3_jit/jit_compiler.md` | コンパイル単位とインタープリタ協調方針 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'コンパイル単位とインタープリタ協調方針'. |
| `specs/wasm_instruction_set.md` | 3.4 メモリアクセス命令 (Memory Access - 32-bit Linear Memory) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.4 メモリアクセス命令 (Memory Access - 32-bit Linear Memory)'. |
| `components/tier1_core/os_scheduler.md` | タスク生成（spawn_task - ネイティブタスク用） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'タスク生成（spawn_task - ネイティブタスク用）'. |
| `components/tier1_core/system_containers.md` | 5.3 利用箇所 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.3 利用箇所'. |
| `components/tier1_core/system_syscall.md` | 3. `fireball_call` WIT定義 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. `fireball_call` WIT定義'. |
| `components/tier1_core/tests/system_logging_test_spec.md` | 2. テストケース一覧 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier2_runtime/runtime_vsoc.md` | Safepoint の動作メカニズム | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Safepoint の動作メカニズム'. |
| `specs/wasm_instruction_set.md` | 3.3 変数アクセス命令 (Variable Access) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 変数アクセス命令 (Variable Access)'. |
| `components/tier1_core/system_syscall.md` | 2. 背景 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. 背景'. |
| `components/tier1_core/system_syscall.md` | 7.1. 役割 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7.1. 役割'. |
| `components/tier1_core/tests/os_scheduler_test_spec.md` | 2. テストケース一覧 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier1_interface/system_service.md` | WASI呼び出しシーケンス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'WASI呼び出しシーケンス'. |
| `components/tier2_runtime/runtime_vmmio.md` | 4.6 共有メモリマッピング (FC=14) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.6 共有メモリマッピング (FC=14)'. |
| `components/tier2_runtime/runtime_vsoc.md` | マルチモジュール動的リンクシーケンス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'マルチモジュール動的リンクシーケンス'. |
| `components/tier2_runtime/tests/debug_manager_test_spec.md` | デバッガ協調 & 統合プロファイラ (§1, §4.1) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'デバッガ協調 & 統合プロファイラ (§1, §4.1)'. |
| `plans/backlog_list.md` | Phase 1.2: WASM Stackless Fast Interpreter (`runtime_interpreter`) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 1.2: WASM Stackless Fast Interpreter (`runtime_interpreter`)'. |
| `plans/roadmap_phase.md` | Phase 0: Foundation（約6ヶ月） | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 0: Foundation（約6ヶ月）'. |
| `components/tier1_core/os_coos.md` | 2.1 構成要素 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2.1 構成要素'. |
| `components/tier1_core/os_coos.md` | 5.1 `coos_harness` (システムハーネス) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 `coos_harness` (システムハーネス)'. |
| `components/tier1_core/system_containers.md` | 2. アーキテクチャ分類 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier1_core/system_containers.md` | 4.3 内部シーケンス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.3 内部シーケンス'. |
| `components/tier1_core/system_containers.md` | 6.3 安全性制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.3 安全性制約と方策'. |
| `components/tier1_core/system_logging.md` | 4.1 アルゴリズム | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier1_interface/interface_wit.md` | 4.2. 高応答トラインターフェイス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2. 高応答トラインターフェイス'. |
| `components/tier1_interface/ipc_router.md` | IPCメッセージ（message） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'IPCメッセージ（message）'. |
| `components/tier2_runtime/debug_manager.md` | 2. アーキテクチャ分類 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier2_runtime/runtime_vsoc.md` | 5.5 関連コンポーネントとの連携 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.5 関連コンポーネントとの連携'. |
| `components/tier3_platform/platform_hal.md` | バッファの確保 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'バッファの確保'. |
| `components/tier3_platform/tests/platform_hal_test_spec.md` | 2. テストケース一覧 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `specs/wasm_instruction_set.md` | 3.1 制御フロー命令 (Control Flow) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 制御フロー命令 (Control Flow)'. |
| `architecture/architecture_overview.md` | 3.6 Pillar 6: 有界ゼロコピー・ランデブー・メールボックス (Bounded Zero-Copy Rendezvous Mailbox) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.6 Pillar 6: 有界ゼロコピー・ランデブー・メールボックス (Bounded Zero-Copy Rendezvous Mailbox)'. |
| `components/tier1_core/os_coos.md` | 協調型OS COOS コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} {VERIFY_BENCHMARK} | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '協調型OS COOS コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} {VERIFY_BENCHMARK}'. |
| `components/tier1_core/os_coos.md` | 2. アーキテクチャ分類 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier1_core/os_coos.md` | 3.1 データ構造 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier1_core/os_scheduler.md` | COOS スケジューラ コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'COOS スケジューラ コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}'. |
| `components/tier1_core/system_config.md` | 2. アーキテクチャ分類 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier1_core/system_config.md` | 3.3.3 HAL | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3.3 HAL'. |
| `components/tier1_core/system_containers.md` | 静的コンテナ語彙 コンポーネント設計書 {VERIFY_BENCHMARK} {VERIFY_LLM} | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '静的コンテナ語彙 コンポーネント設計書 {VERIFY_BENCHMARK} {VERIFY_LLM}'. |
| `components/tier1_core/system_logging.md` | 6.2 メモリ制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 メモリ制約と方策'. |
| `components/tier1_core/system_syscall.md` | 4.2. 戻り値 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2. 戻り値'. |
| `components/tier1_interface/interface_wit.md` | WIT インターフェイス仕様書 (WASI 準拠版) {VERIFY_WIT} {VERIFY_LLM} {VERIFY_FORMAL} | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'WIT インターフェイス仕様書 (WASI 準拠版) {VERIFY_WIT} {VERIFY_LLM} {VERIFY_FORMAL}'. |
| `components/tier1_interface/interface_wit.md` | 3.1 基礎インターフェイス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 基礎インターフェイス'. |
| `components/tier1_interface/ipc_router.md` | IPCルータ コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} {VERIFY_BENCHMARK} | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'IPCルータ コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} {VERIFY_BENCHMARK}'. |
| `components/tier1_interface/ipc_router.md` | 7.2 メモリ制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7.2 メモリ制約と方策'. |
| `components/tier1_interface/ipc_router.md` | 7.3 安全性制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7.3 安全性制約と方策'. |
| `components/tier2_runtime/debug_manager.md` | Debug Manager コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Debug Manager コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}'. |
| `components/tier2_runtime/runtime_interpreter.md` | Interpreter コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Interpreter コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}'. |
| `components/tier2_runtime/runtime_interpreter.md` | 6.3 安全性制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.3 安全性制約と方策'. |
| `components/tier2_runtime/runtime_loader.md` | WASMローダ コンポーネント設計書 {VERIFY_LLM} {VERIFY_FORMAL} | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'WASMローダ コンポーネント設計書 {VERIFY_LLM} {VERIFY_FORMAL}'. |
| `components/tier2_runtime/runtime_loader.md` | 6.2 メモリ制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 メモリ制約と方策'. |
| `components/tier2_runtime/runtime_loader.md` | 6.3 安全性制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.3 安全性制約と方策'. |
| `components/tier2_runtime/runtime_vmmio.md` | 6.2 メモリ制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 メモリ制約と方策'. |
| `components/tier2_runtime/runtime_vmmio.md` | 6.3 安全性制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.3 安全性制約と方策'. |
| `components/tier2_runtime/runtime_vsoc.md` | vSoC コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'vSoC コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}'. |
| `components/tier2_runtime/runtime_vsoc.md` | 5.4 URI/IPCインターフェイス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.4 URI/IPCインターフェイス'. |
| `components/tier2_runtime/runtime_vsoc.md` | 7.1 性能制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7.1 性能制約と方策'. |
| `components/tier3_jit/jit_compiler.md` | JIT コンパイラ コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} {VERIFY_BENCHMARK} | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'JIT コンパイラ コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} {VERIFY_BENCHMARK}'. |
| `components/tier3_jit/jit_compiler.md` | 2. アーキテクチャ分類 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier3_jit/jit_compiler.md` | JIT トレース検索 & 3面キャッシュ代謝オーケストレーション | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'JIT トレース検索 & 3面キャッシュ代謝オーケストレーション'. |
| `components/tier3_jit/jit_compiler.md` | 7.1 性能制約と方策 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7.1 性能制約と方策'. |
| `components/tier3_jit/jit_runtime.md` | JIT ランタイム管理 コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} {VERIFY_BENCHMARK} | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'JIT ランタイム管理 コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} {VERIFY_BENCHMARK}'. |
| `components/tier3_jit/jit_runtime.md` | 2. アーキテクチャ分類 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier3_platform/platform_memory.md` | COOS メモリマネージャ コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'COOS メモリマネージャ コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}'. |
| `components/tier3_platform/platform_memory.md` | 2. アーキテクチャ分類 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `plans/backlog_list.md` | Phase 1: vSoC First 実装（約3ヶ月） 【GO 判定後に着手】 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 1: vSoC First 実装（約3ヶ月） 【GO 判定後に着手】'. |
| `specs/jit_stencil_catalog.md` | 3.1 プロローグ & エピローグ・ステンシル (Prologue, Epilogue & Spill Flush) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 プロローグ & エピローグ・ステンシル (Prologue, Epilogue & Spill Flush)'. |
| `architecture/architecture_overview.md` | アーキテクチャ設計書：Fireball システム概要 {VERIFY_LLM} | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'アーキテクチャ設計書：Fireball システム概要 {VERIFY_LLM}'. |
| `architecture/architecture_overview.md` | 3. 6大物理コアメカニズム (The 6 Physical Pillars) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 6大物理コアメカニズム (The 6 Physical Pillars)'. |
| `architecture/architecture_overview.md` | 6. リソース予算 (RAM/ROM/SLOC) とスケーラビリティ | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. リソース予算 (RAM/ROM/SLOC) とスケーラビリティ'. |
| `components/tier1_core/os_scheduler.md` | スケジューラ フルセット・コンセプトコード (`concepts/scheduler_concept.py`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'スケジューラ フルセット・コンセプトコード (`concepts/scheduler_concept.py`)'. |
| `components/tier1_core/system_config.md` | システムコンフィグ コンポーネント設計書 {VERIFY_LLM} | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'システムコンフィグ コンポーネント設計書 {VERIFY_LLM}'. |
| `components/tier1_core/system_logging.md` | ロギング コンポーネント設計書 {VERIFY_LLM} {VERIFY_FORMAL} | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ロギング コンポーネント設計書 {VERIFY_LLM} {VERIFY_FORMAL}'. |
| `components/tier1_core/system_syscall.md` | システムコール仕様 コンポーネント設計書 {VERIFY_FORMAL} | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'システムコール仕様 コンポーネント設計書 {VERIFY_FORMAL}'. |
| `components/tier1_interface/interface_wit.md` | 5.6. WASI標準APIの実装仕様 (WASI Standard API Implementation Specification) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.6. WASI標準APIの実装仕様 (WASI Standard API Implementation Specification)'. |
| `components/tier1_interface/ipc_router.md` | ロール間通信許可マトリクス (FB_CONF_ROUTER_ROLE_MATRIX) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ロール間通信許可マトリクス (FB_CONF_ROUTER_ROLE_MATRIX)'. |
| `components/tier1_interface/system_service.md` | サービス コンポーネント設計書 {VERIFY_LLM} {VERIFY_FORMAL} | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'サービス コンポーネント設計書 {VERIFY_LLM} {VERIFY_FORMAL}'. |
| `components/tier2_runtime/debug_manager.md` | 6.2 メモリ制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 メモリ制約と方策'. |
| `components/tier2_runtime/runtime_interpreter.md` | WASM インタプリタ フルセット・コンセプトコード (`concepts/interpreter_concept.py`) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'WASM インタプリタ フルセット・コンセプトコード (`concepts/interpreter_concept.py`)'. |
| `components/tier2_runtime/runtime_loader.md` | 4.5 内部シーケンス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.5 内部シーケンス'. |
| `components/tier2_runtime/runtime_loader.md` | 6.1 性能制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1 性能制約と方策'. |
| `components/tier2_runtime/runtime_vmmio.md` | vMMIO コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'vMMIO コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}'. |
| `components/tier3_jit/jit_compiler.md` | 4.3 内部シーケンス | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.3 内部シーケンス'. |
| `components/tier3_jit/tests/jit_compiler_test_spec.md` | Copy-and-Patchエンジン (§3.1, §4.1) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Copy-and-Patchエンジン (§3.1, §4.1)'. |
| `components/tier3_platform/platform_hal.md` | HAL コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'HAL コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}'. |
| `specs/gdb_rsp_protocol.md` | GDB Remote Serial Protocol 物理仕様書 (Supported GDB RSP Protocol) {VERIFY_LLM} | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'GDB Remote Serial Protocol 物理仕様書 (Supported GDB RSP Protocol) {VERIFY_LLM}'. |
| `specs/jit_stencil_catalog.md` | JIT ステンシルテンプレート・カタログ物理仕様書 (JIT Stencil Template Catalog) {VERIFY_LLM} {VERIFY_FORMAL} | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'JIT ステンシルテンプレート・カタログ物理仕様書 (JIT Stencil Template Catalog) {VERIFY_LLM} {VERIFY_FORMAL}'. |
| `specs/jit_stencil_catalog.md` | 3.5 整数算術 & 論理演算ステンシル (32-bit Integer Arithmetic & Logic) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.5 整数算術 & 論理演算ステンシル (32-bit Integer Arithmetic & Logic)'. |
| `specs/wasi_preview1_abi.md` | WASI Preview 1 ABI 物理仕様書 (Supported WASI Preview 1 ABI) {VERIFY_FORMAL} | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'WASI Preview 1 ABI 物理仕様書 (Supported WASI Preview 1 ABI) {VERIFY_FORMAL}'. |
| `specs/wasm_instruction_set.md` | WASM 命令セット物理仕様書 (Supported WASM Instruction Set) {VERIFY_FORMAL} | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'WASM 命令セット物理仕様書 (Supported WASM Instruction Set) {VERIFY_FORMAL}'. |
| `specs/wasm_instruction_set.md` | 3.5 整数算術・論理・比較命令 (Integer Arithmetic, Logic & Comparison) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.5 整数算術・論理・比較命令 (Integer Arithmetic, Logic & Comparison)'. |
| `components/tier2_runtime/runtime_vmmio.md` | 3.2 内部ブロック図 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier2_runtime/tests/runtime_loader_test_spec.md` | ゼロコピー索引化 (§4.1) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ゼロコピー索引化 (§4.1)'. |
| `components/tier3_jit/concepts/README.md` | Tier 3 JIT — コンセプトコード & 検証スクリプト | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Tier 3 JIT — コンセプトコード & 検証スクリプト'. |
| `components/tier3_jit/jit_compiler.md` | トレース境界不変条件とスタックフレーム整合性 (Trace Boundary Invariants) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'トレース境界不変条件とスタックフレーム整合性 (Trace Boundary Invariants)'. |
| `components/tier1_interface/interface_wit.md` | リカバリー戦略の事前・事後条件と不変条件 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'リカバリー戦略の事前・事後条件と不変条件'. |
| `components/tier2_runtime/runtime_loader.md` | 3.1 データ構造 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier2_runtime/runtime_loader.md` | 3.2 内部ブロック図 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier2_runtime/runtime_vmmio.md` | 3.1 データ構造 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier2_runtime/runtime_vmmio.md` | 4.8 ソフトウェアTLB | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.8 ソフトウェアTLB'. |
| `components/tier2_runtime/runtime_vsoc.md` | 3.2 内部ブロック図 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `specs/wasm_instruction_set.md` | 2. 非サポート機能 (Explicit Non-Goals) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. 非サポート機能 (Explicit Non-Goals)'. |
| `architecture/document_structure.md` | 1. 設計複雑度に基づく Tier（分解階層）の定義 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 設計複雑度に基づく Tier（分解階層）の定義'. |
| `components/tier1_core/os_scheduler.md` | 3.2 内部ブロック図 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier1_core/system_config.md` | 3.3.6 タスクID型・予約値 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3.6 タスクID型・予約値'. |
| `components/tier1_core/system_syscall.md` | 8.1.1. 仮想割り込みID | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '8.1.1. 仮想割り込みID'. |
| `components/tier1_interface/system_service.md` | service_load_result_t の定義 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'service_load_result_t の定義'. |
| `components/tier2_runtime/runtime_loader.md` | 4.3 軽量検証スコープ | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.3 軽量検証スコープ'. |
| `components/tier2_runtime/runtime_vmmio.md` | 静的デバイスページテーブルエントリ (vmmio_pte_static) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '静的デバイスページテーブルエントリ (vmmio_pte_static)'. |
| `plans/backlog_list.md` | Phase 2: Integration（周辺コンポーネント統合 / 将来予定） | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 2: Integration（周辺コンポーネント統合 / 将来予定）'. |
| `specs/jit_stencil_catalog.md` | 3.8 トレース内レジスタバリアント (Register Variants) と `variant_id` | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.8 トレース内レジスタバリアント (Register Variants) と `variant_id`'. |
| `components/tier1_core/system_config.md` | 3.2 内部ブロック図 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier1_core/system_logging.md` | 5.2 URI/IPCインターフェイス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2 URI/IPCインターフェイス'. |
| `components/tier1_interface/interface_wit.md` | 1. 目的 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的'. |
| `components/tier1_interface/interface_wit.md` | 2. アーキテクチャ原則 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ原則'. |
| `components/tier1_interface/interface_wit.md` | 5.4 `fireball:host/streaming` (wasi:io 準拠) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.4 `fireball:host/streaming` (wasi:io 準拠)'. |
| `components/tier1_interface/ipc_router.md` | レジストリエントリ（registry_entry） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'レジストリエントリ（registry_entry）'. |
| `components/tier2_runtime/debug_manager.md` | 仮想レジスタセット（virtual_register_set） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '仮想レジスタセット（virtual_register_set）'. |
| `components/tier2_runtime/runtime_vmmio.md` | 4.1 アルゴリズム: アクセスディスパッチ | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム: アクセスディスパッチ'. |
| `components/tier3_jit/jit_compiler.md` | JITコンパイルおよび検索シーケンス | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'JITコンパイルおよび検索シーケンス'. |
| `components/tier3_jit/tests/jit_compiler_test_spec.md` | 1. 目的と対象範囲 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier3_platform/platform_memory.md` | 初期化 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '初期化'. |
| `plans/backlog_list.md` | Phase 0.8: 仕様最終レビュー & GO 判定 【進行中 / レビュー中】 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 0.8: 仕様最終レビュー & GO 判定 【進行中 / レビュー中】'. |
| `plans/backlog_list.md` | Phase 1.1: WASM 32-bit Binary Loader (`runtime_loader`) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 1.1: WASM 32-bit Binary Loader (`runtime_loader`)'. |
| `specs/gdb_rsp_protocol.md` | 2. パケット構造とチェックサム規約 | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. パケット構造とチェックサム規約'. |
| `components/tier1_core/system_config.md` | メモリ総量と個別プールの依存関係 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'メモリ総量と個別プールの依存関係'. |
| `components/tier1_core/system_logging.md` | 4.5 内部シーケンス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.5 内部シーケンス'. |
| `components/tier1_core/tests/os_coos_test_spec.md` | 2. テストケース一覧 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier1_interface/ipc_router.md` | 3.3 主要なクラス・構造体・配列・定数 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なクラス・構造体・配列・定数'. |
| `components/tier1_interface/ipc_router.md` | 4.3 メッセージライフサイクルと所有権管理 (SysML Parametric Diagram 相当) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.3 メッセージライフサイクルと所有権管理 (SysML Parametric Diagram 相当)'. |
| `components/tier2_runtime/runtime_vsoc.md` | 4.2.1 Safepoint と JIT キャッシュ協調モデル | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2.1 Safepoint と JIT キャッシュ協調モデル'. |
| `components/tier2_runtime/runtime_vsoc.md` | 6.3 検証モデル概要（vsoc_cache_coherency_model.py） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.3 検証モデル概要（vsoc_cache_coherency_model.py）'. |
| `components/tier3_platform/platform_hal.md` | 4.3 内部シーケンス | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.3 内部シーケンス'. |
| `specs/gdb_rsp_protocol.md` | 4. WASM 仮想レジスタ番号マッピング (GDB Target XML Map) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. WASM 仮想レジスタ番号マッピング (GDB Target XML Map)'. |
| `specs/jit_stencil_catalog.md` | ローカル変数アクセスの基底ポインタと静的オフセット畳み込み (`ContextPointerRegister`) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ローカル変数アクセスの基底ポインタと静的オフセット畳み込み (`ContextPointerRegister`)'. |
| `specs/wasi_preview1_abi.md` | 3.1 文字入出力 & ストリーム API (I/O & Streams) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 文字入出力 & ストリーム API (I/O & Streams)'. |
| `components/tier2_runtime/runtime_vsoc.md` | vSoC構成（vsoc_config） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'vSoC構成（vsoc_config）'. |
| `components/tier3_jit/jit_runtime.md` | 4.1 アルゴリズム | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier3_jit/tests/jit_runtime_test_spec.md` | ホットスポット判定 (yield時) と バッチコンパイル (§4.1) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ホットスポット判定 (yield時) と バッチコンパイル (§4.1)'. |
| `specs/jit_stencil_catalog.md` | 3.6 整数比較演算ステンシル (32-bit Integer Comparisons) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.6 整数比較演算ステンシル (32-bit Integer Comparisons)'. |
| `specs/wasi_preview1_abi.md` | 2. WASI 型定義と物理レイアウト (WASI Type Vocabulary) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. WASI 型定義と物理レイアウト (WASI Type Vocabulary)'. |
| `specs/wasi_preview1_abi.md` | 3.3 プロセス制御 & 乱数 & スケジューラ API (Process, Random & Scheduler) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 プロセス制御 & 乱数 & スケジューラ API (Process, Random & Scheduler)'. |
| `components/tier1_interface/interface_wit.md` | 設計判断 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '設計判断'. |
| `components/tier2_runtime/runtime_interpreter.md` | 制御フレーム整合性とリーク防止不変条件 (Control Frame Integrity Invariant) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '制御フレーム整合性とリーク防止不変条件 (Control Frame Integrity Invariant)'. |
| `components/tier2_runtime/runtime_interpreter.md` | 割り込み同期 (`sync_interrupts`) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '割り込み同期 (`sync_interrupts`)'. |
| `components/tier2_runtime/runtime_vsoc.md` | Debugger 介入時のキャッシュ一貫性 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Debugger 介入時のキャッシュ一貫性'. |
| `components/tier2_runtime/runtime_vsoc.md` | `register-hook` | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`register-hook`'. |
| `components/tier2_runtime/runtime_vsoc.md` | 5.2 ネイティブAPI エクスポート | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2 ネイティブAPI エクスポート'. |
| `components/tier2_runtime/tests/debug_manager_test_spec.md` | 実行制御 & ブレークポイント (§3.3, §4.1) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '実行制御 & ブレークポイント (§3.3, §4.1)'. |
| `specs/jit_stencil_catalog.md` | 2. プレースホルダ（穴 / Relocations）の種類とパッチ規約 | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. プレースホルダ（穴 / Relocations）の種類とパッチ規約'. |
| `architecture/architecture_overview.md` | 7.1 起動およびタスク登録 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7.1 起動およびタスク登録'. |
| `architecture/architecture_overview.md` | 7.2 IPC通信 (URIベース) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7.2 IPC通信 (URIベース)'. |
| `components/tier1_core/os_coos.md` | 1. 所有権管理 `CoValue` | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 所有権管理 `CoValue`'. |
| `components/tier1_core/os_scheduler.md` | 実行譲渡（yield） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '実行譲渡（yield）'. |
| `components/tier1_core/system_containers.md` | 添字区間による絞り込み（slice） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '添字区間による絞り込み（slice）'. |
| `components/tier1_core/system_containers.md` | 添字による状態参照（at / put） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '添字による状態参照（at / put）'. |
| `components/tier1_core/system_logging.md` | 4.2 辞書構造 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2 辞書構造'. |
| `components/tier1_core/system_logging.md` | ログ出力シーケンス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ログ出力シーケンス'. |
| `components/tier1_core/system_logging.md` | ログイベント記録 (`log_event`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ログイベント記録 (`log_event`)'. |
| `components/tier1_interface/system_service.md` | リカバリー戦略の種類と具体的ポリシー | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'リカバリー戦略の種類と具体的ポリシー'. |
| `components/tier2_runtime/debug_manager.md` | 3.2 内部ブロック図 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier2_runtime/runtime_interpreter.md` | 3.2 内部ブロック図 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier2_runtime/runtime_interpreter.md` | Interpreter 実行シーケンス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Interpreter 実行シーケンス'. |
| `components/tier2_runtime/runtime_interpreter.md` | 実行ステップ (`run_step`) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '実行ステップ (`run_step`)'. |
| `components/tier2_runtime/runtime_loader.md` | 4.2 メモリ制約 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2 メモリ制約'. |
| `components/tier2_runtime/runtime_loader.md` | モジュールロードシーケンス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'モジュールロードシーケンス'. |
| `components/tier2_runtime/runtime_loader.md` | `lookup-by-file-offset` | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`lookup-by-file-offset`'. |
| `components/tier2_runtime/runtime_vmmio.md` | コントローラ群 (VmmioController) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'コントローラ群 (VmmioController)'. |
| `components/tier2_runtime/runtime_vmmio.md` | 4.2 アルゴリズム: 仮想DMA (VDMA) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2 アルゴリズム: 仮想DMA (VDMA)'. |
| `components/tier2_runtime/runtime_vmmio.md` | 4.3 仮想デバイスマップ | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.3 仮想デバイスマップ'. |
| `components/tier2_runtime/runtime_vsoc.md` | ステップ実行（step） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ステップ実行（step）'. |
| `components/tier2_runtime/tests/debug_manager_test_spec.md` | 3. テスト検証実績と網羅状況 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier2_runtime/tests/runtime_interpreter_test_spec.md` | 3. テスト検証実績と網羅状況 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier2_runtime/tests/runtime_loader_test_spec.md` | ファイル内データ位置 & シンボルハッシュ RadixBinaryTreeView 索引 (§1, §3.1, §4.1, §5.1, {META_BinarySearch}) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ファイル内データ位置 & シンボルハッシュ RadixBinaryTreeView 索引 (§1, §3.1, §4.1, §5.1, {META_BinarySearch})'. |
| `components/tier3_jit/jit_compiler.md` | 3.2 内部ブロック図 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier3_jit/jit_compiler.md` | 初期化（initialize） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '初期化（initialize）'. |
| `components/tier3_jit/jit_compiler.md` | トレース検索（lookup_trace） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'トレース検索（lookup_trace）'. |
| `components/tier3_jit/jit_runtime.md` | 3.2 内部ブロック図 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier3_platform/platform_memory.md` | パーティションの貸与（acquire-partition） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'パーティションの貸与（acquire-partition）'. |
| `components/tier3_platform/platform_memory.md` | 所有権要求（claim） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '所有権要求（claim）'. |
| `components/tier3_platform/platform_memory.md` | 6. 所有権追跡 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 所有権追跡'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_FALLBACK_FLUSH_D2` (TOS & NOS 書き戻し $\to$ インタープリタ末尾ジャンプ) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_FALLBACK_FLUSH_D2` (TOS & NOS 書き戻し $\to$ インタープリタ末尾ジャンプ)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_I64_CONST_D0` (`0x42` 64-bit 即値 $\to$ R4:R5) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_I64_CONST_D0` (`0x42` 64-bit 即値 $\to$ R4:R5)'. |
| `architecture/combinatorial_test_spec.md` | 4. テスト実行環境・ランナー構成 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. テスト実行環境・ランナー構成'. |
| `architecture/integration_test_scenarios.md` | シナリオ 7: GDB Remote Serial Protocol (RSP) Socket Debugger | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'シナリオ 7: GDB Remote Serial Protocol (RSP) Socket Debugger'. |
| `components/tier1_core/os_scheduler.md` | 3.1 データ構造 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier1_core/os_scheduler.md` | 実行（run） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '実行（run）'. |
| `components/tier1_core/system_config.md` | 3.1 データ構造 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier1_core/system_containers.md` | キー範囲による絞り込み（narrow） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'キー範囲による絞り込み（narrow）'. |
| `components/tier1_core/system_containers.md` | 区間内探索（find） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '区間内探索（find）'. |
| `components/tier1_core/system_containers.md` | 所属判定（contains） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '所属判定（contains）'. |
| `components/tier1_core/system_logging.md` | 3.2 内部ブロック図 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier1_core/system_logging.md` | ログ構成（logging_config） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ログ構成（logging_config）'. |
| `components/tier1_core/system_logging.md` | 4.3 COOS Idle Hook 連携 (Flush Protocol) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.3 COOS Idle Hook 連携 (Flush Protocol)'. |
| `components/tier1_core/system_syscall.md` | 1. 目的 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的'. |
| `components/tier1_core/system_syscall.md` | トラップ高速パスとレジスタ直接マッピング | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'トラップ高速パスとレジスタ直接マッピング'. |
| `components/tier1_core/system_syscall.md` | 5.2. System (`0x00`-`0x0F`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2. System (`0x00`-`0x0F`)'. |
| `components/tier1_core/system_syscall.md` | 5.4. VDMA (`0x20`-`0x2F`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.4. VDMA (`0x20`-`0x2F`)'. |
| `components/tier1_core/system_syscall.md` | 8. ホストからゲストへの非同期通知メカニズム | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '8. ホストからゲストへの非同期通知メカニズム'. |
| `components/tier1_core/system_syscall.md` | 9. メモリ安全性 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '9. メモリ安全性'. |
| `components/tier1_core/tests/os_scheduler_test_spec.md` | 1. 目的と対象範囲 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier1_interface/interface_wit.md` | 4. 低レベル・トラップ・インターフェイス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 低レベル・トラップ・インターフェイス'. |
| `components/tier1_interface/interface_wit.md` | 5.1 `fireball:host/timer` (wasi:clocks 準拠) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 `fireball:host/timer` (wasi:clocks 準拠)'. |
| `components/tier1_interface/interface_wit.md` | 6. 非同期通知メカニズム | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 非同期通知メカニズム'. |
| `components/tier1_interface/ipc_router.md` | サービス検索（lookup_service） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'サービス検索（lookup_service）'. |
| `components/tier1_interface/ipc_router.md` | 5.2 URI/IPCインターフェイス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2 URI/IPCインターフェイス'. |
| `components/tier1_interface/ipc_router.md` | 6.2 検証対象のプロパティ | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 検証対象のプロパティ'. |
| `components/tier1_interface/system_service.md` | 3.2 内部ブロック図 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier1_interface/system_service.md` | サービス構成（service_config） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'サービス構成（service_config）'. |
| `components/tier1_interface/system_service.md` | 5.3 URI/IPCインターフェイス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.3 URI/IPCインターフェイス'. |
| `components/tier1_interface/system_service.md` | 6.1 性能制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1 性能制約と方策'. |
| `components/tier1_interface/tests/system_service_test_spec.md` | 2. テストケース一覧 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier2_runtime/debug_manager.md` | 4.2 状態遷移図 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2 状態遷移図'. |
| `components/tier2_runtime/debug_manager.md` | デバッグコマンド処理シーケンス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'デバッグコマンド処理シーケンス'. |
| `components/tier2_runtime/runtime_interpreter.md` | 2. アーキテクチャ分類 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier2_runtime/runtime_interpreter.md` | インタプリタ構成（interpreter_config） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'インタプリタ構成（interpreter_config）'. |
| `components/tier2_runtime/runtime_loader.md` | 2. アーキテクチャ分類 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier2_runtime/runtime_loader.md` | モジュールビュー（module_view） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'モジュールビュー（module_view）'. |
| `components/tier2_runtime/runtime_loader.md` | 検証結果（verification_result） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '検証結果（verification_result）'. |
| `components/tier2_runtime/runtime_vmmio.md` | 2. アーキテクチャ分類 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier2_runtime/runtime_vmmio.md` | 4.7 仮想割り込みマッピング | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.7 仮想割り込みマッピング'. |
| `components/tier2_runtime/runtime_vsoc.md` | 3.1 データ構造 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier2_runtime/runtime_vsoc.md` | 5.3 マルチモジュール対応 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.3 マルチモジュール対応'. |
| `components/tier2_runtime/tests/debug_manager_test_spec.md` | 1. 目的と対象範囲 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier3_jit/jit_compiler.md` | `constexpr_assembler` (DSL) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`constexpr_assembler` (DSL)'. |
| `components/tier3_jit/jit_compiler.md` | ホットスポット判定 (yield 時) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ホットスポット判定 (yield 時)'. |
| `components/tier3_jit/jit_compiler.md` | カード状態取得（get_card_state） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'カード状態取得（get_card_state）'. |
| `components/tier3_jit/jit_runtime.md` | 3.1 データ構造 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier3_jit/jit_runtime.md` | 4.2 状態遷移図 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2 状態遷移図'. |
| `components/tier3_platform/platform_hal.md` | 3.2 内部ブロック図 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 内部ブロック図'. |
| `components/tier3_platform/platform_hal.md` | HAL構成（hal_config） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'HAL構成（hal_config）'. |
| `components/tier3_platform/platform_hal.md` | RSPパケット受信とコマンド供給シーケンス | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'RSPパケット受信とコマンド供給シーケンス'. |
| `components/tier3_platform/platform_hal.md` | ゼロコピー転送 (bus_master/streaming) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ゼロコピー転送 (bus_master/streaming)'. |
| `components/tier3_platform/platform_hal.md` | 5.4 RSPトランスポート構成 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.4 RSPトランスポート構成'. |
| `components/tier3_platform/platform_memory.md` | 4. インターフェイス設計 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. インターフェイス設計'. |
| `requires/requirement_list.md` | 2.1 ユースケース図 | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2.1 ユースケース図'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_EPILOGUE_FLUSH_D1` (TOS 書き戻し + Callee-saved 復元 & リターン) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_EPILOGUE_FLUSH_D1` (TOS 書き戻し + Callee-saved 復元 & リターン)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_EPILOGUE_FLUSH_D2` (TOS & NOS 書き戻し + Callee-saved 復元 & リターン) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_EPILOGUE_FLUSH_D2` (TOS & NOS 書き戻し + Callee-saved 復元 & リターン)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_FALLBACK_FLUSH_D1` (TOS 書き戻し + Callee-saved 復元 $\to$ インタープリタ末尾ジャンプ) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_FALLBACK_FLUSH_D1` (TOS 書き戻し + Callee-saved 復元 $\to$ インタープリタ末尾ジャンプ)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_EXTERNAL_CALL_STUB` (外部 AAPCS C/C++ 関数呼出境界) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_EXTERNAL_CALL_STUB` (外部 AAPCS C/C++ 関数呼出境界)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_BR_IF_DEPTH_1` (`0x0D` 条件分岐: TOS != 0) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_BR_IF_DEPTH_1` (`0x0D` 条件分岐: TOS != 0)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_SELECT_DEPTH_3` (`0x1B` 3値選択: c, val2, val1) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_SELECT_DEPTH_3` (`0x1B` 3値選択: c, val2, val1)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_I32_CONST_D0` (`0x41` Depth 0 $\to$ R4) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_I32_CONST_D0` (`0x41` Depth 0 $\to$ R4)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_I32_CONST_D1` (`0x41` Depth 1 $\to$ R5=旧TOS, R4=新TOS) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_I32_CONST_D1` (`0x41` Depth 1 $\to$ R5=旧TOS, R4=新TOS)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_GLOBAL_GET_D0` (`0x23` Env globals_base 経由ロード) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_GLOBAL_GET_D0` (`0x23` Env globals_base 経由ロード)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_GLOBAL_SET_D1` (`0x24` Env globals_base 経由ストア) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_GLOBAL_SET_D1` (`0x24` Env globals_base 経由ストア)'. |
| `architecture/combinatorial_test_spec.md` | 3. ペアワイズ テストケース マトリクス (Test Matrix) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. ペアワイズ テストケース マトリクス (Test Matrix)'. |
| `architecture/integration_test_scenarios.md` | 1.1 コンポーネント × 結合テストシナリオ カバレッジマトリクス (Coverage Matrix) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1.1 コンポーネント × 結合テストシナリオ カバレッジマトリクス (Coverage Matrix)'. |
| `architecture/integration_test_scenarios.md` | 1.2 仕様キーワード・不変条件カバレッジ追跡表 (Requirements Traceability Matrix: RTM) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1.2 仕様キーワード・不変条件カバレッジ追跡表 (Requirements Traceability Matrix: RTM)'. |
| `architecture/integration_test_scenarios.md` | 3. 実行方法と検証結果 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 実行方法と検証結果'. |
| `components/tier1_core/os_coos.md` | 5. インターフェイス設計 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. インターフェイス設計'. |
| `components/tier1_core/os_scheduler.md` | 2. アーキテクチャ分類 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier1_core/os_scheduler.md` | タスク生成 (`spawn`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'タスク生成 (`spawn`)'. |
| `components/tier1_core/system_config.md` | 4.1 アルゴリズム | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier1_core/system_config.md` | 5.2 安全性制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2 安全性制約と方策'. |
| `components/tier1_core/system_logging.md` | 2. アーキテクチャ分類 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. アーキテクチャ分類'. |
| `components/tier1_core/system_logging.md` | 6.1 性能制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1 性能制約と方策'. |
| `components/tier1_interface/ipc_router.md` | 7.1 性能制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7.1 性能制約と方策'. |
| `components/tier1_interface/system_service.md` | 4.3 内部シーケンス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.3 内部シーケンス'. |
| `components/tier1_interface/system_service.md` | 5.1 エラーハンドリング戦略 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 エラーハンドリング戦略'. |
| `components/tier1_interface/system_service.md` | 設計判断 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '設計判断'. |
| `components/tier1_interface/system_service.md` | 6.3 安全性制約と方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.3 安全性制約と方策'. |
| `components/tier2_runtime/debug_manager.md` | 6.1 性能制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1 性能制約と方策'. |
| `components/tier2_runtime/debug_manager.md` | 6.3 安全性制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.3 安全性制約と方策'. |
| `components/tier2_runtime/runtime_interpreter.md` | 6.1 性能制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1 性能制約と方策'. |
| `components/tier2_runtime/tests/debug_manager_test_spec.md` | 実ソケット GDB RSP リモート接続・対話セッション (§4.1, gdb_rsp_protocol.md) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '実ソケット GDB RSP リモート接続・対話セッション (§4.1, gdb_rsp_protocol.md)'. |
| `components/tier3_jit/jit_runtime.md` | 6.2 メモリ制約 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 メモリ制約'. |
| `components/tier3_platform/platform_hal.md` | 5.5 メッセージ形式 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.5 メッセージ形式'. |
| `components/tier3_platform/platform_hal.md` | 6.1 性能制約と方策 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1 性能制約と方策'. |
| `components/tier3_platform/platform_hal.md` | 6.2 メモリ制約と方策 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 メモリ制約と方策'. |
| `components/tier3_platform/platform_hal.md` | 6.3 安全性制約と方策 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.3 安全性制約と方策'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_PROLOGUE_FULL` (Callee-saved 全域退避 + LR) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_PROLOGUE_FULL` (Callee-saved 全域退避 + LR)'. |
| `specs/jit_stencil_catalog.md` | 3.2 制御フロー系ステンシル (Control Flow) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 制御フロー系ステンシル (Control Flow)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_BR` (`0x0C` 無条件ジャンプ) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_BR` (`0x0C` 無条件ジャンプ)'. |
| `specs/jit_stencil_catalog.md` | 3.3 定数ロード系ステンシル (Constants) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 定数ロード系ステンシル (Constants)'. |
| `specs/jit_stencil_catalog.md` | 3.4 変数アクセス系ステンシル (Local & Global Variables) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.4 変数アクセス系ステンシル (Local & Global Variables)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_LOCAL_GET_D0` (`0x20` Depth 0 $\to$ R4) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_LOCAL_GET_D0` (`0x20` Depth 0 $\to$ R4)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_LOCAL_SET_D1` (`0x21` R4 $\to$ Local) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_LOCAL_SET_D1` (`0x21` R4 $\to$ Local)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_LOCAL_TEE_D1` (`0x22` R4 $\to$ Local, R4 維持) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_LOCAL_TEE_D1` (`0x22` R4 $\to$ Local, R4 維持)'. |
| `architecture/document_structure.md` | 5.2 形式検証モデル（`formal/*.py`）の責任分担正本表 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2 形式検証モデル（`formal/*.py`）の責任分担正本表'. |
| `architecture/integration_test_scenarios.md` | シナリオ 11: HAL Peripheral Drivers & WASI Preview 1 Full Dummy Stack | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'シナリオ 11: HAL Peripheral Drivers & WASI Preview 1 Full Dummy Stack'. |
| `components/tier1_core/system_syscall.md` | 型のエイリアス定義 (Type Vocabulary) `{Type_Vocabulary}` | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '型のエイリアス定義 (Type Vocabulary) `{Type_Vocabulary}`'. |
| `components/tier1_core/tests/system_syscall_test_spec.md` | vMMIO Generic (`0x10`-`0x1F`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'vMMIO Generic (`0x10`-`0x1F`)'. |
| `components/tier1_core/tests/system_syscall_test_spec.md` | IPC (`0x40`-`0x4F`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'IPC (`0x40`-`0x4F`)'. |
| `components/tier1_interface/ipc_router.md` | Key-Valueペア（kv_pair） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Key-Valueペア（kv_pair）'. |
| `components/tier1_interface/tests/interface_wit_test_spec.md` | リカバリー戦略 (§3.2) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'リカバリー戦略 (§3.2)'. |
| `components/tier2_runtime/tests/runtime_loader_test_spec.md` | 複数モジュール・インポート解決 (§4.1「インポートテーブル検索と依存関係解決」) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '複数モジュール・インポート解決 (§4.1「インポートテーブル検索と依存関係解決」)'. |
| `components/tier3_jit/tests/jit_compiler_test_spec.md` | レジスタ規約とTOS/NOS非対称性 (§3.3, §8 ADR_TosCacheAsymmetry) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'レジスタ規約とTOS/NOS非対称性 (§3.3, §8 ADR_TosCacheAsymmetry)'. |
| `components/tier3_jit/tests/jit_runtime_test_spec.md` | 3段検索・3面キャッシュ (jit_compiler.md §5.1直交表) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3段検索・3面キャッシュ (jit_compiler.md §5.1直交表)'. |
| `components/tier2_runtime/runtime_vmmio.md` | 4.4 SYSCTL レジスタ詳細 (FC=12) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.4 SYSCTL レジスタ詳細 (FC=12)'. |
| `components/tier2_runtime/runtime_vmmio.md` | フック登録 (`register-hook`) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'フック登録 (`register-hook`)'. |
| `components/tier3_jit/tests/jit_compiler_test_spec.md` | トレース境界不変条件とハンドラ委譲 (§3.3, §4.1) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'トレース境界不変条件とハンドラ委譲 (§3.3, §4.1)'. |
| `components/tier3_jit/tests/jit_runtime_test_spec.md` | トレース・チェイニング (jit_compiler.md §4.1「トレース・チェイニング」) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'トレース・チェイニング (jit_compiler.md §4.1「トレース・チェイニング」)'. |
| `components/tier3_platform/tests/platform_memory_test_spec.md` | パーティション管理 (§1, §4-6) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'パーティション管理 (§1, §4-6)'. |
| `components/tier1_core/os_scheduler.md` | 初期化 (`init-scheduler`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '初期化 (`init-scheduler`)'. |
| `components/tier1_core/system_syscall.md` | トラップ実行の制御フロー | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'トラップ実行の制御フロー'. |
| `components/tier1_core/tests/system_config_test_spec.md` | 2. テストケース一覧 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier1_core/tests/system_containers_test_spec.md` | 2. テストケース一覧 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier1_core/tests/system_syscall_test_spec.md` | System (`0x00`-`0x0F`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'System (`0x00`-`0x0F`)'. |
| `components/tier2_runtime/debug_manager.md` | 3.1 データ構造 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier2_runtime/runtime_interpreter.md` | 5.3 関連コンポーネントとの連携 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.3 関連コンポーネントとの連携'. |
| `components/tier2_runtime/runtime_loader.md` | バイナリストリーム（BinaryStream） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'バイナリストリーム（BinaryStream）'. |
| `components/tier2_runtime/runtime_vmmio.md` | 4.5 VDMA レジスタ詳細 (FC=12) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.5 VDMA レジスタ詳細 (FC=12)'. |
| `components/tier2_runtime/runtime_vmmio.md` | ライフサイクル（ipc_router.md §4.1 に従属） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ライフサイクル（ipc_router.md §4.1 に従属）'. |
| `components/tier2_runtime/runtime_vsoc.md` | `notify-interrupt` | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`notify-interrupt`'. |
| `components/tier2_runtime/tests/runtime_interpreter_test_spec.md` | ラベルアリティ・スタックプルーニング (interpreter_concept.py §1, `prune_stack`) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ラベルアリティ・スタックプルーニング (interpreter_concept.py §1, `prune_stack`)'. |
| `components/tier2_runtime/tests/runtime_vsoc_test_spec.md` | ハーネス統合 (§3, §5.1) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ハーネス統合 (§3, §5.1)'. |
| `components/tier3_jit/jit_runtime.md` | JITエントリインデックス（JitEntryIndex）クラス | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'JITエントリインデックス（JitEntryIndex）クラス'. |
| `components/tier3_platform/platform_hal.md` | データの読み出し | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'データの読み出し'. |
| `plans/roadmap_phase.md` | フェーズ概要 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'フェーズ概要'. |
| `specs/tests/wasm_instruction_set_test_spec.md` | 非サポート機能の拒否 (§2) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '非サポート機能の拒否 (§2)'. |
| `specs/wasi_preview1_abi.md` | 3.2 システム時刻 & クロック API (Clocks & Timers) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 システム時刻 & クロック API (Clocks & Timers)'. |
| `specs/wasi_preview1_abi.md` | 4. 非サポート API 一覧 (Explicit Non-Goals) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 非サポート API 一覧 (Explicit Non-Goals)'. |
| `specs/wasm_instruction_set.md` | 3.2 パラメトリック命令 (Parametric) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 パラメトリック命令 (Parametric)'. |
| `architecture/document_structure.md` | Fireball ドキュメント体系定義書 (Document Structure & Metadata) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Fireball ドキュメント体系定義書 (Document Structure & Metadata)'. |
| `architecture/integration_test_scenarios.md` | シナリオ 8: Storage Coverage (Globals / Locals / Memory Full-Width) & GDB Debugger | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'シナリオ 8: Storage Coverage (Globals / Locals / Memory Full-Width) & GDB Debugger'. |
| `architecture/integration_test_scenarios.md` | シナリオ 10: Tier 2 Runtime vMMIO Virtual Devices & Address Translation | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'シナリオ 10: Tier 2 Runtime vMMIO Virtual Devices & Address Translation'. |
| `components/tier1_core/system_syscall.md` | 4.1. 引数のパッキング | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1. 引数のパッキング'. |
| `components/tier1_core/system_syscall.md` | 8.1.2. 仮想割り込みペイロード | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '8.1.2. 仮想割り込みペイロード'. |
| `components/tier1_core/system_syscall.md` | 10. トラップ状態プロトコル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '10. トラップ状態プロトコル'. |
| `components/tier1_core/tests/os_coos_test_spec.md` | 1. 目的と対象範囲 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier1_interface/interface_wit.md` | 4.1. `fireball:host/trap` の定義 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1. `fireball:host/trap` の定義'. |
| `components/tier2_runtime/debug_manager.md` | デバッガ接続 (`attach`) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'デバッガ接続 (`attach`)'. |
| `components/tier2_runtime/debug_manager.md` | コマンド処理 (`poll_commands`) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'コマンド処理 (`poll_commands`)'. |
| `components/tier2_runtime/runtime_loader.md` | 関数アクセサ（function_accessor） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '関数アクセサ（function_accessor）'. |
| `components/tier2_runtime/runtime_loader.md` | グローバルアクセサ（global_accessor） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'グローバルアクセサ（global_accessor）'. |
| `components/tier2_runtime/tests/runtime_interpreter_test_spec.md` | 1. 目的と対象範囲 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier2_runtime/tests/runtime_vmmio_test_spec.md` | FlatMap PTE + TLB (§1, §4.8) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'FlatMap PTE + TLB (§1, §4.8)'. |
| `components/tier3_jit/jit_compiler.md` | バッチコンパイル処理（process_batch_compile） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'バッチコンパイル処理（process_batch_compile）'. |
| `components/tier3_platform/platform_hal.md` | データの書き込み (write) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'データの書き込み (write)'. |
| `components/tier3_platform/platform_memory.md` | 9.3 アライメントおよび境界制約 (PMSAv8) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '9.3 アライメントおよび境界制約 (PMSAv8)'. |
| `components/tier3_platform/tests/platform_memory_test_spec.md` | `shared-block`ライフサイクル (§7-8) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`shared-block`ライフサイクル (§7-8)'. |
| `specs/gdb_rsp_protocol.md` | 3. サポートコマンド・マトリクス (GDB RSP Commands) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. サポートコマンド・マトリクス (GDB RSP Commands)'. |
| `specs/gdb_rsp_protocol.md` | 5. 非サポートコマンド (Explicit Non-Goals) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. 非サポートコマンド (Explicit Non-Goals)'. |
| `architecture/architecture_overview.md` | 6.3 コード規模予算 (SLOC) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.3 コード規模予算 (SLOC)'. |
| `architecture/combinatorial_test_spec.md` | システム全体 ペアワイズ組み合わせテスト仕様書 (Combinatorial Test Specification) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'システム全体 ペアワイズ組み合わせテスト仕様書 (Combinatorial Test Specification)'. |
| `architecture/combinatorial_test_spec.md` | 2. 因子（Factors）および水準（Levels）定義 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. 因子（Factors）および水準（Levels）定義'. |
| `architecture/document_structure.md` | 1.1 各 Tier の定義と配置ディレクトリ | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1.1 各 Tier の定義と配置ディレクトリ'. |
| `architecture/document_structure.md` | 5.1 検証タグとエビデンスの対応表 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 検証タグとエビデンスの対応表'. |
| `architecture/integration_test_scenarios.md` | シナリオ 1: Tier 1 Core + Tier 2 Loader & Linear Memory | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'シナリオ 1: Tier 1 Core + Tier 2 Loader & Linear Memory'. |
| `architecture/integration_test_scenarios.md` | シナリオ 3: Tier 2 Interpreter + Recursion & Indirect Table Dispatch | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'シナリオ 3: Tier 2 Interpreter + Recursion & Indirect Table Dispatch'. |
| `architecture/integration_test_scenarios.md` | シナリオ 5: Multi-Function UnifiedPC & bswap32 Radix Tree | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'シナリオ 5: Multi-Function UnifiedPC & bswap32 Radix Tree'. |
| `architecture/integration_test_scenarios.md` | シナリオ 9: Tier 1 Interface IPC Router & Structured Logging | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'シナリオ 9: Tier 1 Interface IPC Router & Structured Logging'. |
| `components/tier1_core/os_coos.md` | 3.3 主要なデータ定義 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なデータ定義'. |
| `components/tier1_core/os_coos.md` | 5.2 サブコンポーネント・インターフェイス (C++23) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2 サブコンポーネント・インターフェイス (C++23)'. |
| `components/tier1_core/os_scheduler.md` | 3.3 主要なデータ定義 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なデータ定義'. |
| `components/tier1_core/system_containers.md` | 4.2 状態遷移図 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.2 状態遷移図'. |
| `components/tier1_core/system_containers.md` | 5.2 URI/IPCインターフェイス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2 URI/IPCインターフェイス'. |
| `components/tier1_core/system_syscall.md` | 6.1. 役割 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1. 役割'. |
| `components/tier1_core/system_syscall.md` | 8.1. 仮想割り込み | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '8.1. 仮想割り込み'. |
| `components/tier1_core/tests/system_containers_test_spec.md` | 4. 未検証・スコープ外 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier1_interface/ipc_router.md` | スコープ定義 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'スコープ定義'. |
| `components/tier1_interface/tests/ipc_router_test_spec.md` | 4. 未検証・スコープ外 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier2_runtime/runtime_interpreter.md` | 5.2 URI/IPCインターフェイス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2 URI/IPCインターフェイス'. |
| `components/tier2_runtime/runtime_interpreter.md` | 6.2 メモリ制約と方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 メモリ制約と方策'. |
| `components/tier2_runtime/runtime_loader.md` | 3.3 主要なクラス・構造体・配列・定数 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なクラス・構造体・配列・定数'. |
| `components/tier2_runtime/runtime_vmmio.md` | 3.3 主要なクラス・構造体・配列・定数 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なクラス・構造体・配列・定数'. |
| `components/tier2_runtime/runtime_vsoc.md` | 3.3 主要なクラス・構造体・配列・定数 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なクラス・構造体・配列・定数'. |
| `components/tier2_runtime/tests/runtime_vmmio_test_spec.md` | 3層セキュリティゲート・SHM所有権 (§1「3層」, §4.6) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3層セキュリティゲート・SHM所有権 (§1「3層」, §4.6)'. |
| `components/tier2_runtime/tests/runtime_vmmio_test_spec.md` | 4. 未検証・スコープ外 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier2_runtime/tests/runtime_vsoc_test_spec.md` | Safepoint/JITキャッシュ協調 (§4.2.1) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Safepoint/JITキャッシュ協調 (§4.2.1)'. |
| `components/tier2_runtime/tests/runtime_vsoc_test_spec.md` | 4. 未検証・スコープ外 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier3_jit/jit_compiler.md` | 6.2 URI/IPCインターフェイス | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 URI/IPCインターフェイス'. |
| `components/tier3_platform/platform_hal.md` | 5.2 Tier 3 リソースインターフェイス | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2 Tier 3 リソースインターフェイス'. |
| `plans/roadmap_phase.md` | Phase 1: vSoC First（GO後に約3ヶ月） | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 1: vSoC First（GO後に約3ヶ月）'. |
| `requires/requirement_list.md` | 3.2.3 移植性・互換性 | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2.3 移植性・互換性'. |
| `architecture/integration_test_scenarios.md` | 1. 目的と対象範囲 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `architecture/integration_test_scenarios.md` | シナリオ 2: Tier 2 Runtime + System Call & WASI I/O | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'シナリオ 2: Tier 2 Runtime + System Call & WASI I/O'. |
| `architecture/integration_test_scenarios.md` | シナリオ 4: Tier 2 Runtime + Tier 3 JIT Hybrid Compilation | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'シナリオ 4: Tier 2 Runtime + Tier 3 JIT Hybrid Compilation'. |
| `architecture/integration_test_scenarios.md` | シナリオ 6: COOS Cooperative Multitasking & Coroutines | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'シナリオ 6: COOS Cooperative Multitasking & Coroutines'. |
| `components/tier1_core/tests/system_syscall_test_spec.md` | WASI (`0x80`-`0xBF`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'WASI (`0x80`-`0xBF`)'. |
| `components/tier2_runtime/tests/runtime_interpreter_test_spec.md` | i64整数演算 (interpreter_concept.py §3.7, wasm_instruction_set.md §3.4) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'i64整数演算 (interpreter_concept.py §3.7, wasm_instruction_set.md §3.4)'. |
| `components/tier2_runtime/tests/runtime_loader_test_spec.md` | 軽量検証 (§4.3 V1-V6) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '軽量検証 (§4.3 V1-V6)'. |
| `components/tier3_jit/tests/jit_runtime_test_spec.md` | 2-bitカードマーキング (jit_compiler.md §3.1, runtime_engine_concept.py §2) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2-bitカードマーキング (jit_compiler.md §3.1, runtime_engine_concept.py §2)'. |
| `architecture/architecture_overview.md` | 6.1 メモリ予算 (RAM: 評価ターゲット 32KB) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1 メモリ予算 (RAM: 評価ターゲット 32KB)'. |
| `architecture/document_structure.md` | 2.3 矛盾が見つかった場合の解決規則（Clean Architecture の依存ルールに基づく） | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2.3 矛盾が見つかった場合の解決規則（Clean Architecture の依存ルールに基づく）'. |
| `components/tier2_runtime/runtime_vmmio.md` | アクセスディスパッチ (`dispatch-access`) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'アクセスディスパッチ (`dispatch-access`)'. |
| `components/tier2_runtime/tests/runtime_interpreter_test_spec.md` | 統合スタック・関数呼び出し (interpreter_concept.py §1) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '統合スタック・関数呼び出し (interpreter_concept.py §1)'. |
| `components/tier2_runtime/tests/runtime_vmmio_test_spec.md` | アドレス分解・高速バイパス (§1, §3.3) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'アドレス分解・高速バイパス (§1, §3.3)'. |
| `components/tier2_runtime/tests/runtime_vsoc_test_spec.md` | vSoC Engineライフサイクル (§4.2) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'vSoC Engineライフサイクル (§4.2)'. |
| `components/tier3_jit/tests/jit_compiler_test_spec.md` | 3. テスト検証実績と網羅状況 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier3_jit/tests/jit_runtime_test_spec.md` | MPU W^X保護 (jit_compiler.md §7.2) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'MPU W^X保護 (jit_compiler.md §7.2)'. |
| `components/tier3_platform/tests/platform_memory_test_spec.md` | MPU / W^X (§9) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'MPU / W^X (§9)'. |
| `specs/tests/wasm_instruction_set_test_spec.md` | メモリアクセス (§3.4) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'メモリアクセス (§3.4)'. |
| `specs/tests/wasm_instruction_set_test_spec.md` | 整数算術・論理・比較 (§3.5) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '整数算術・論理・比較 (§3.5)'. |
| `architecture/architecture_overview.md` | 2.1 レイヤー構成 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2.1 レイヤー構成'. |
| `architecture/architecture_overview.md` | 6.2 ストレージ予算 (ROM/Flash: 評価ターゲット 96KB) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 ストレージ予算 (ROM/Flash: 評価ターゲット 96KB)'. |
| `architecture/document_structure.md` | 2.1 デコンポジション基準（いつ下位 Tier へ分解するか） | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2.1 デコンポジション基準（いつ下位 Tier へ分解するか）'. |
| `architecture/document_structure.md` | 4.1 分類基準と検証時の挙動 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 分類基準と検証時の挙動'. |
| `components/tier1_core/os_scheduler.md` | スケジューラクラス（Scheduler） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'スケジューラクラス（Scheduler）'. |
| `components/tier1_core/os_scheduler.md` | `notify-interrupt` (内部 API) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`notify-interrupt` (内部 API)'. |
| `components/tier1_core/system_containers.md` | 7. 参考実装リスト | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7. 参考実装リスト'. |
| `components/tier1_core/system_logging.md` | バッファリング出力 (`flush`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'バッファリング出力 (`flush`)'. |
| `components/tier1_core/tests/system_logging_test_spec.md` | 1. 目的と対象範囲 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier1_core/tests/system_syscall_test_spec.md` | VDMA (`0x20`-`0x2F`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'VDMA (`0x20`-`0x2F`)'. |
| `components/tier1_core/tests/system_syscall_test_spec.md` | 共通・エラー処理 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '共通・エラー処理'. |
| `components/tier1_interface/interface_wit.md` | 7. フィードバック：WASI 準拠における制約事項 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7. フィードバック：WASI 準拠における制約事項'. |
| `components/tier1_interface/interface_wit.md` | 8. 命名規則 (Naming Conventions) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '8. 命名規則 (Naming Conventions)'. |
| `components/tier1_interface/system_service.md` | サービスロード（load_service） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'サービスロード（load_service）'. |
| `components/tier1_interface/tests/interface_wit_test_spec.md` | `console-output` (§5.5) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`console-output` (§5.5)'. |
| `components/tier1_interface/tests/interface_wit_test_spec.md` | HALインターフェイス (§5.1, 5.3, 5.4) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'HALインターフェイス (§5.1, 5.3, 5.4)'. |
| `components/tier2_runtime/runtime_interpreter.md` | 3.1 データ構造 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier2_runtime/runtime_interpreter.md` | 初期化（initialize） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '初期化（initialize）'. |
| `components/tier2_runtime/runtime_vsoc.md` | vSoCハーネス（vsoc_harness） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'vSoCハーネス（vsoc_harness）'. |
| `components/tier2_runtime/runtime_vsoc.md` | 準備（prepare） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '準備（prepare）'. |
| `components/tier2_runtime/tests/runtime_interpreter_test_spec.md` | メモリアクセス全幅 (interpreter_concept.py §3.4) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'メモリアクセス全幅 (interpreter_concept.py §3.4)'. |
| `components/tier2_runtime/tests/runtime_interpreter_test_spec.md` | Cooperative Safepoint (interpreter_concept.py §末尾) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Cooperative Safepoint (interpreter_concept.py §末尾)'. |
| `components/tier2_runtime/tests/runtime_loader_test_spec.md` | 容量制約 (§4.2) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '容量制約 (§4.2)'. |
| `components/tier2_runtime/tests/runtime_loader_test_spec.md` | 3. テスト検証実績と網羅状況 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier2_runtime/tests/runtime_vsoc_test_spec.md` | `fireball_call`シグネチャの整合性（要確認・矛盾あり） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`fireball_call`シグネチャの整合性（要確認・矛盾あり）'. |
| `components/tier3_jit/jit_compiler.md` | 3.1 データ構造 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier3_jit/tests/jit_compiler_test_spec.md` | JITトレースヘッダ (§3.3) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'JITトレースヘッダ (§3.3)'. |
| `components/tier3_jit/tests/jit_runtime_test_spec.md` | 1. 目的と対象範囲 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `specs/tests/wasm_instruction_set_test_spec.md` | 1. 目的と対象範囲 | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `specs/tests/wasm_instruction_set_test_spec.md` | 制御フロー (§3.1) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '制御フロー (§3.1)'. |
| `architecture/architecture_overview.md` | 依存性ルール | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '依存性ルール'. |
| `architecture/architecture_overview.md` | 4.1 メモリ常駐構造体の物理バイトオフセット | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 メモリ常駐構造体の物理バイトオフセット'. |
| `architecture/combinatorial_test_spec.md` | 1. 目的と網羅基準 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と網羅基準'. |
| `architecture/combinatorial_test_spec.md` | 1.1 網羅基準 (Coverage Criteria) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1.1 網羅基準 (Coverage Criteria)'. |
| `architecture/document_structure.md` | 2.2 依存方向のルール | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2.2 依存方向のルール'. |
| `architecture/document_structure.md` | 3. ドキュメントの静的チェックルール | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. ドキュメントの静的チェックルール'. |
| `architecture/integration_test_scenarios.md` | 検証実績 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '検証実績'. |
| `architecture/keyword_dictionary.md` | Fireball リンク用キーワード台帳 (Keyword Dictionary & Link Registry) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Fireball リンク用キーワード台帳 (Keyword Dictionary & Link Registry)'. |
| `architecture/keyword_dictionary.md` | 1. リンクキーワードの運用ルール | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. リンクキーワードの運用ルール'. |
| `components/tier1_core/os_scheduler.md` | アイドルハンドラ設定（set_idle_handler） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'アイドルハンドラ設定（set_idle_handler）'. |
| `components/tier1_core/os_scheduler.md` | タスク終了（terminate） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'タスク終了（terminate）'. |
| `components/tier1_core/system_logging.md` | ロガー（Logger）クラス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ロガー（Logger）クラス'. |
| `components/tier1_core/system_syscall.md` | 4.1.1. ゲストメモリ内構造体のレイアウト規則 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1.1. ゲストメモリ内構造体のレイアウト規則'. |
| `components/tier1_core/tests/system_containers_test_spec.md` | 1. 目的と対象範囲 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier1_core/tests/system_logging_test_spec.md` | 4. 未検証・スコープ外 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier1_core/tests/system_syscall_test_spec.md` | 1. 目的と対象範囲 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier1_core/tests/system_syscall_test_spec.md` | IRQ (`0x30`-`0x3F`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'IRQ (`0x30`-`0x3F`)'. |
| `components/tier1_interface/interface_wit.md` | 8.1 設計上の留意点 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '8.1 設計上の留意点'. |
| `components/tier1_interface/ipc_router.md` | サービス登録（register_service） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'サービス登録（register_service）'. |
| `components/tier1_interface/system_service.md` | サービス定義（service） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'サービス定義（service）'. |
| `components/tier1_interface/system_service.md` | 検証対象となる制約事項 (形式検証 pyModelChecking モデリングポイント) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '検証対象となる制約事項 (形式検証 pyModelChecking モデリングポイント)'. |
| `components/tier1_interface/tests/interface_wit_test_spec.md` | 1. 目的と対象範囲 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier1_interface/tests/interface_wit_test_spec.md` | 低レベル・トラップインターフェイス (§4) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '低レベル・トラップインターフェイス (§4)'. |
| `components/tier1_interface/tests/ipc_router_test_spec.md` | 1. 目的と対象範囲 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier2_runtime/runtime_interpreter.md` | インタプリタ（Interpreter）クラス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'インタプリタ（Interpreter）クラス'. |
| `components/tier2_runtime/runtime_interpreter.md` | 7. 参考実装リスト | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7. 参考実装リスト'. |
| `components/tier2_runtime/runtime_loader.md` | WASMローダ（WasmLoader）クラス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'WASMローダ（WasmLoader）クラス'. |
| `components/tier2_runtime/runtime_loader.md` | 準備（prepare） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '準備（prepare）'. |
| `components/tier2_runtime/runtime_loader.md` | ロード（load） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ロード（load）'. |
| `components/tier2_runtime/runtime_loader.md` | `resolve-imports` | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`resolve-imports`'. |
| `components/tier2_runtime/runtime_loader.md` | アンロード（unload） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'アンロード（unload）'. |
| `components/tier2_runtime/runtime_loader.md` | 検索（lookup） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '検索（lookup）'. |
| `components/tier2_runtime/runtime_loader.md` | `get-section` | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`get-section`'. |
| `components/tier2_runtime/runtime_loader.md` | `lookup-export-func` | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`lookup-export-func`'. |
| `components/tier2_runtime/runtime_loader.md` | `get-function` | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`get-function`'. |
| `components/tier2_runtime/runtime_loader.md` | `get-global` | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`get-global`'. |
| `components/tier2_runtime/runtime_vsoc.md` | vSoCコンテキスト（vsoc_context） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'vSoCコンテキスト（vsoc_context）'. |
| `components/tier2_runtime/runtime_vsoc.md` | 6.2 モデル分割の理由 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.2 モデル分割の理由'. |
| `components/tier2_runtime/tests/runtime_loader_test_spec.md` | 1. 目的と対象範囲 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier2_runtime/tests/runtime_vmmio_test_spec.md` | 1. 目的と対象範囲 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier2_runtime/tests/runtime_vmmio_test_spec.md` | VDMA (§4.2, §4.5) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'VDMA (§4.2, §4.5)'. |
| `components/tier2_runtime/tests/runtime_vsoc_test_spec.md` | 1. 目的と対象範囲 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier2_runtime/tests/runtime_vsoc_test_spec.md` | マルチモジュール動的リンク (§5.3, §4.3) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'マルチモジュール動的リンク (§5.3, §4.3)'. |
| `components/tier3_jit/jit_runtime.md` | 検索（lookup） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '検索（lookup）'. |
| `components/tier3_jit/tests/jit_compiler_test_spec.md` | ADR_ScalableCodeOffset (§8) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ADR_ScalableCodeOffset (§8)'. |
| `components/tier3_jit/tests/jit_runtime_test_spec.md` | 4. 未検証・スコープ外 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier3_platform/platform_hal.md` | デバイス情報（device） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'デバイス情報（device）'. |
| `components/tier3_platform/platform_memory.md` | パーティションの返却（release-partition） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'パーティションの返却（release-partition）'. |
| `components/tier3_platform/platform_memory.md` | 型付きプールスロットの貸与・返却（acquire-slot / release-slot） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '型付きプールスロットの貸与・返却（acquire-slot / release-slot）'. |
| `components/tier3_platform/platform_memory.md` | 解放（deallocate） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '解放（deallocate）'. |
| `components/tier3_platform/tests/platform_memory_test_spec.md` | 1. 目的と対象範囲 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `plans/backlog_archive.md` | Phase 0.7: Static DI & Build System [DONE] | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 0.7: Static DI & Build System [DONE]'. |
| `plans/backlog_archive.md` | Phase 0.75: Constexpr Verification & Code Gen Enhancement [DONE] | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 0.75: Constexpr Verification & Code Gen Enhancement [DONE]'. |
| `plans/backlog_list.md` | Phase 1.4: Standalone vSoC Harness & WAMR Benchmark (`runtime_vsoc`) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 1.4: Standalone vSoC Harness & WAMR Benchmark (`runtime_vsoc`)'. |
| `requires/requirement_list.md` | 2.2 主要シナリオ | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2.2 主要シナリオ'. |
| `requires/requirement_list.md` | [Template & Meta] | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '[Template & Meta]'. |
| `requires/requirement_list.md` | 5. 制約事項 | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. 制約事項'. |
| `requires/requirement_list.md` | 6. 用語定義 | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 用語定義'. |
| `specs/tests/wasm_instruction_set_test_spec.md` | パラメトリック (§3.2) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'パラメトリック (§3.2)'. |
| `specs/tests/wasm_instruction_set_test_spec.md` | 変数アクセス (§3.3) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '変数アクセス (§3.3)'. |
| `architecture/architecture_overview.md` | 2. 静的構造とレイヤー構成 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. 静的構造とレイヤー構成'. |
| `architecture/architecture_overview.md` | 7. 動的構造 (主要シーケンス) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7. 動的構造 (主要シーケンス)'. |
| `architecture/document_structure.md` | 2. 階層間デコンポジションと依存性ルール | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. 階層間デコンポジションと依存性ルール'. |
| `architecture/document_structure.md` | 4. 特殊キーワードの分類と検証仕様 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 特殊キーワードの分類と検証仕様'. |
| `architecture/document_structure.md` | 5. 検証タグとエビデンス（Evidence）の対応体系 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. 検証タグとエビデンス（Evidence）の対応体系'. |
| `architecture/integration_test_scenarios.md` | コンポーネント間 結合テスト仕様書 (Integration Test Specification) | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'コンポーネント間 結合テスト仕様書 (Integration Test Specification)'. |
| `architecture/integration_test_scenarios.md` | 2. 結合テストシナリオ一覧 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. 結合テストシナリオ一覧'. |
| `architecture/keyword_dictionary.md` | 2. カテゴリ別 リンクキーワード台帳 | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. カテゴリ別 リンクキーワード台帳'. |
| `components/tier1_core/os_coos.md` | 3. 静的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier1_core/os_coos.md` | 4. 動的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier1_core/os_coos.md` | 6. 形式検証（pyModelChecking / 直交表） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 形式検証（pyModelChecking / 直交表）'. |
| `components/tier1_core/os_scheduler.md` | 3. 静的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier1_core/os_scheduler.md` | 4. 動的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier1_core/os_scheduler.md` | 5. インターフェイス設計 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. インターフェイス設計'. |
| `components/tier1_core/os_scheduler.md` | 5.1 公開API | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 公開API'. |
| `components/tier1_core/system_config.md` | 3. 静的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier1_core/system_config.md` | 3.3 コンフィグマクロ一覧・定義 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 コンフィグマクロ一覧・定義'. |
| `components/tier1_core/system_config.md` | 4. 動的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier1_core/system_config.md` | 5. 制約達成の方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. 制約達成の方策'. |
| `components/tier1_core/system_containers.md` | 3. 静的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier1_core/system_containers.md` | 3.3 主要なクラス・構造体・配列・定数 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なクラス・構造体・配列・定数'. |
| `components/tier1_core/system_containers.md` | 4. 動的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier1_core/system_containers.md` | 5. インターフェイス定義 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. インターフェイス定義'. |
| `components/tier1_core/system_containers.md` | 5.1 公開API | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 公開API'. |
| `components/tier1_core/system_containers.md` | 6. 制約達成の方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 制約達成の方策'. |
| `components/tier1_core/system_logging.md` | 3. 静的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier1_core/system_logging.md` | 3.1 データ構造 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier1_core/system_logging.md` | 3.3 主要なクラス・構造体・配列・定数 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なクラス・構造体・配列・定数'. |
| `components/tier1_core/system_logging.md` | 4. 動的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier1_core/system_logging.md` | 5. インターフェイス定義 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. インターフェイス定義'. |
| `components/tier1_core/system_logging.md` | 5.1 公開API | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 公開API'. |
| `components/tier1_core/system_logging.md` | 6. 制約達成の方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 制約達成の方策'. |
| `components/tier1_core/system_syscall.md` | 4. `fireball_call` 呼び出し規約 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. `fireball_call` 呼び出し規約'. |
| `components/tier1_core/system_syscall.md` | 5. システムコールID | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. システムコールID'. |
| `components/tier1_core/system_syscall.md` | 6. Fireball Shim (`libfireball_shim`) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. Fireball Shim (`libfireball_shim`)'. |
| `components/tier1_core/system_syscall.md` | 7. WASIホスト側実装 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7. WASIホスト側実装'. |
| `components/tier1_core/tests/os_coos_test_spec.md` | COOS (CSPチャネル) テスト仕様書 (Test Specification) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'COOS (CSPチャネル) テスト仕様書 (Test Specification)'. |
| `components/tier1_core/tests/os_coos_test_spec.md` | 3. テスト検証実績と網羅状況 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier1_core/tests/os_coos_test_spec.md` | 4. 未検証・スコープ外 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier1_core/tests/os_scheduler_test_spec.md` | COOSスケジューラ テスト仕様書 (Test Specification) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'COOSスケジューラ テスト仕様書 (Test Specification)'. |
| `components/tier1_core/tests/os_scheduler_test_spec.md` | 3. テスト検証実績と網羅状況 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier1_core/tests/os_scheduler_test_spec.md` | 4. 未検証・スコープ外 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier1_core/tests/system_config_test_spec.md` | システムコンフィグ テスト仕様書 (Test Specification) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'システムコンフィグ テスト仕様書 (Test Specification)'. |
| `components/tier1_core/tests/system_config_test_spec.md` | 1. 目的と対象範囲 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier1_core/tests/system_config_test_spec.md` | 3. テスト検証実績と網羅状況 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier1_core/tests/system_config_test_spec.md` | 4. 未検証・スコープ外 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier1_core/tests/system_containers_test_spec.md` | 静的コンテナ語彙 テスト仕様書 (Test Specification) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '静的コンテナ語彙 テスト仕様書 (Test Specification)'. |
| `components/tier1_core/tests/system_containers_test_spec.md` | 3. テスト検証実績と網羅状況 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier1_core/tests/system_logging_test_spec.md` | システムロギング テスト仕様書 (Test Specification) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'システムロギング テスト仕様書 (Test Specification)'. |
| `components/tier1_core/tests/system_logging_test_spec.md` | 3. テスト検証実績と網羅状況 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier1_core/tests/system_syscall_test_spec.md` | システムコール (`fireball_call`) テスト仕様書 (Test Specification) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'システムコール (`fireball_call`) テスト仕様書 (Test Specification)'. |
| `components/tier1_core/tests/system_syscall_test_spec.md` | 2. テストケース一覧 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier1_core/tests/system_syscall_test_spec.md` | 3. テスト検証実績と網羅状況 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier1_core/tests/system_syscall_test_spec.md` | 4. 未検証・スコープ外 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier1_interface/interface_wit.md` | 3. 共通データ構造 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 共通データ構造'. |
| `components/tier1_interface/interface_wit.md` | 5. HAL インターフェイス | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. HAL インターフェイス'. |
| `components/tier1_interface/ipc_router.md` | 3. 静的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier1_interface/ipc_router.md` | 4. 動的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier1_interface/ipc_router.md` | 5. インターフェイス定義 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. インターフェイス定義'. |
| `components/tier1_interface/ipc_router.md` | 5.1 公開API | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 公開API'. |
| `components/tier1_interface/ipc_router.md` | 6. 形式検証（pyModelChecking / 直交表） | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 形式検証（pyModelChecking / 直交表）'. |
| `components/tier1_interface/ipc_router.md` | 6.4 既知の制限 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.4 既知の制限'. |
| `components/tier1_interface/ipc_router.md` | 7. 制約達成の方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7. 制約達成の方策'. |
| `components/tier1_interface/system_service.md` | 3. 静的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier1_interface/system_service.md` | 3.1 データ構造 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier1_interface/system_service.md` | 3.3 主要なクラス・構造体・配列・定数 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なクラス・構造体・配列・定数'. |
| `components/tier1_interface/system_service.md` | 4. 動的モデル | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier1_interface/system_service.md` | 5. インターフェイス定義 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. インターフェイス定義'. |
| `components/tier1_interface/system_service.md` | 5.2 公開API | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2 公開API'. |
| `components/tier1_interface/system_service.md` | 6. 制約達成の方策 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 制約達成の方策'. |
| `components/tier1_interface/tests/interface_wit_test_spec.md` | WITインターフェイス / リカバリー戦略 テスト仕様書 (Test Specification) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'WITインターフェイス / リカバリー戦略 テスト仕様書 (Test Specification)'. |
| `components/tier1_interface/tests/interface_wit_test_spec.md` | 2. テストケース一覧 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier1_interface/tests/interface_wit_test_spec.md` | 3. テスト検証実績と網羅状況 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier1_interface/tests/interface_wit_test_spec.md` | 4. 未検証・スコープ外 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier1_interface/tests/ipc_router_test_spec.md` | IPCルータ テスト仕様書 (Test Specification) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'IPCルータ テスト仕様書 (Test Specification)'. |
| `components/tier1_interface/tests/ipc_router_test_spec.md` | 3. テスト検証実績と網羅状況 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier1_interface/tests/system_service_test_spec.md` | サービス テスト仕様書 (Test Specification) | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'サービス テスト仕様書 (Test Specification)'. |
| `components/tier1_interface/tests/system_service_test_spec.md` | 1. 目的と対象範囲 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier1_interface/tests/system_service_test_spec.md` | 3. テスト検証実績と網羅状況 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier1_interface/tests/system_service_test_spec.md` | 4. 未検証・スコープ外 | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier2_runtime/debug_manager.md` | 3. 静的モデル | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier2_runtime/debug_manager.md` | 3.3 主要なクラス・構造体・配列・定数 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なクラス・構造体・配列・定数'. |
| `components/tier2_runtime/debug_manager.md` | 4. 動的モデル | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier2_runtime/debug_manager.md` | デバッガ・インタープリタ結合コンセプトコード (`concepts/debugger_concept.py`) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'デバッガ・インタープリタ結合コンセプトコード (`concepts/debugger_concept.py`)'. |
| `components/tier2_runtime/debug_manager.md` | 4.3 内部シーケンス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.3 内部シーケンス'. |
| `components/tier2_runtime/debug_manager.md` | 5. インターフェイス定義 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. インターフェイス定義'. |
| `components/tier2_runtime/debug_manager.md` | 5.1 公開API | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 公開API'. |
| `components/tier2_runtime/debug_manager.md` | 命令ステップ実行 (`step_instruction`) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '命令ステップ実行 (`step_instruction`)'. |
| `components/tier2_runtime/debug_manager.md` | 5.2 URI/IPCインターフェイス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2 URI/IPCインターフェイス'. |
| `components/tier2_runtime/debug_manager.md` | 6. 制約達成の方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 制約達成の方策'. |
| `components/tier2_runtime/runtime_interpreter.md` | 3. 静的モデル | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier2_runtime/runtime_interpreter.md` | 3.3 主要なクラス・構造体・配列・定数 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なクラス・構造体・配列・定数'. |
| `components/tier2_runtime/runtime_interpreter.md` | 4. 動的モデル | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier2_runtime/runtime_interpreter.md` | 5. インターフェイス定義 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. インターフェイス定義'. |
| `components/tier2_runtime/runtime_interpreter.md` | 5.1 公開API | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 公開API'. |
| `components/tier2_runtime/runtime_interpreter.md` | 6. 制約達成の方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 制約達成の方策'. |
| `components/tier2_runtime/runtime_loader.md` | 3. 静的モデル | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier2_runtime/runtime_loader.md` | 4. 動的モデル | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier2_runtime/runtime_loader.md` | 5. インターフェイス定義 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. インターフェイス定義'. |
| `components/tier2_runtime/runtime_loader.md` | 5.1 公開API | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 公開API'. |
| `components/tier2_runtime/runtime_loader.md` | 5.2 URI/IPCインターフェイス | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.2 URI/IPCインターフェイス'. |
| `components/tier2_runtime/runtime_loader.md` | 6. 制約達成の方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 制約達成の方策'. |
| `components/tier2_runtime/runtime_vmmio.md` | 3. 静的モデル | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier2_runtime/runtime_vmmio.md` | ハンドラ定義 (vmmio_handler) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'ハンドラ定義 (vmmio_handler)'. |
| `components/tier2_runtime/runtime_vmmio.md` | 4. 動的モデル | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier2_runtime/runtime_vmmio.md` | vMMIO フルセット・コンセプトコード | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'vMMIO フルセット・コンセプトコード'. |
| `components/tier2_runtime/runtime_vmmio.md` | 5. インターフェイス定義 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. インターフェイス定義'. |
| `components/tier2_runtime/runtime_vmmio.md` | 5.1 公開API | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 公開API'. |
| `components/tier2_runtime/runtime_vmmio.md` | 6. 制約達成の方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 制約達成の方策'. |
| `components/tier2_runtime/runtime_vsoc.md` | 3. 静的モデル | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier2_runtime/runtime_vsoc.md` | 4. 動的モデル | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier2_runtime/runtime_vsoc.md` | 形式検証 (pyModelChecking) 検証対象 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '形式検証 (pyModelChecking) 検証対象'. |
| `components/tier2_runtime/runtime_vsoc.md` | 5. インターフェイス定義 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. インターフェイス定義'. |
| `components/tier2_runtime/runtime_vsoc.md` | 5.1 公開API | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 公開API'. |
| `components/tier2_runtime/runtime_vsoc.md` | 6. 形式検証（pyModelChecking / 直交表） | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 形式検証（pyModelChecking / 直交表）'. |
| `components/tier2_runtime/runtime_vsoc.md` | 6.4 既知の制限 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.4 既知の制限'. |
| `components/tier2_runtime/runtime_vsoc.md` | 7. 制約達成の方策 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7. 制約達成の方策'. |
| `components/tier2_runtime/tests/debug_manager_test_spec.md` | Debug Manager テスト仕様書 (Test Specification) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Debug Manager テスト仕様書 (Test Specification)'. |
| `components/tier2_runtime/tests/debug_manager_test_spec.md` | 2. テストケース一覧 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier2_runtime/tests/debug_manager_test_spec.md` | 4. 未検証・スコープ外 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier2_runtime/tests/runtime_interpreter_test_spec.md` | WASMインタープリタ テスト仕様書 (Test Specification) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'WASMインタープリタ テスト仕様書 (Test Specification)'. |
| `components/tier2_runtime/tests/runtime_interpreter_test_spec.md` | 2. テストケース一覧 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier2_runtime/tests/runtime_interpreter_test_spec.md` | 4. 未検証・スコープ外 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier2_runtime/tests/runtime_loader_test_spec.md` | (Overview) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '(Overview)'. |
| `components/tier2_runtime/tests/runtime_loader_test_spec.md` | 2. テストケース一覧 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier2_runtime/tests/runtime_loader_test_spec.md` | 4. 未検証・スコープ外 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier2_runtime/tests/runtime_vmmio_test_spec.md` | vMMIO テスト仕様書 (Test Specification) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'vMMIO テスト仕様書 (Test Specification)'. |
| `components/tier2_runtime/tests/runtime_vmmio_test_spec.md` | 2. テストケース一覧 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier2_runtime/tests/runtime_vmmio_test_spec.md` | 3. テスト検証実績と網羅状況 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier2_runtime/tests/runtime_vsoc_test_spec.md` | vSoC (統合実行エンジン) テスト仕様書 (Test Specification) | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'vSoC (統合実行エンジン) テスト仕様書 (Test Specification)'. |
| `components/tier2_runtime/tests/runtime_vsoc_test_spec.md` | 2. テストケース一覧 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier2_runtime/tests/runtime_vsoc_test_spec.md` | 3. テスト検証実績と網羅状況 | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier3_jit/jit_compiler.md` | 3. 静的モデル | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier3_jit/jit_compiler.md` | 3.3 主要なクラス・構造体・定数 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なクラス・構造体・定数'. |
| `components/tier3_jit/jit_compiler.md` | 4. 動的モデル | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier3_jit/jit_compiler.md` | 5. 検証 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. 検証'. |
| `components/tier3_jit/jit_compiler.md` | 6. インターフェイス定義 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. インターフェイス定義'. |
| `components/tier3_jit/jit_compiler.md` | 6.1 公開API | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1 公開API'. |
| `components/tier3_jit/jit_compiler.md` | 7. 制約達成の方策 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '7. 制約達成の方策'. |
| `components/tier3_jit/jit_runtime.md` | 3. 静的モデル | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier3_jit/jit_runtime.md` | 3.3 主要なクラス・構造体・配列・定数 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なクラス・構造体・配列・定数'. |
| `components/tier3_jit/jit_runtime.md` | 4. 動的モデル | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier3_jit/jit_runtime.md` | 5. インターフェイス定義 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. インターフェイス定義'. |
| `components/tier3_jit/jit_runtime.md` | 5.1 公開API | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 公開API'. |
| `components/tier3_jit/jit_runtime.md` | エントリ登録（register_entry） | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'エントリ登録（register_entry）'. |
| `components/tier3_jit/jit_runtime.md` | 6. 制約達成の方策 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 制約達成の方策'. |
| `components/tier3_jit/jit_runtime.md` | 6.1 性能制約 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6.1 性能制約'. |
| `components/tier3_jit/tests/jit_compiler_test_spec.md` | JITコンパイラ (コード生成コア) テスト仕様書 (Test Specification) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'JITコンパイラ (コード生成コア) テスト仕様書 (Test Specification)'. |
| `components/tier3_jit/tests/jit_compiler_test_spec.md` | 2. テストケース一覧 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier3_jit/tests/jit_compiler_test_spec.md` | 4. 未検証・スコープ外 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier3_jit/tests/jit_runtime_test_spec.md` | JITランタイム (キャッシュ・ホットスポット検出) テスト仕様書 (Test Specification) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'JITランタイム (キャッシュ・ホットスポット検出) テスト仕様書 (Test Specification)'. |
| `components/tier3_jit/tests/jit_runtime_test_spec.md` | 2. テストケース一覧 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier3_jit/tests/jit_runtime_test_spec.md` | 3. テスト検証実績と網羅状況 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier3_platform/platform_hal.md` | 3. 静的モデル | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier3_platform/platform_hal.md` | 3.1 データ構造 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier3_platform/platform_hal.md` | 3.3 主要なクラス・構造体・配列・定数 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.3 主要なクラス・構造体・配列・定数'. |
| `components/tier3_platform/platform_hal.md` | 4. 動的モデル | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 動的モデル'. |
| `components/tier3_platform/platform_hal.md` | 5. インターフェイス定義 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5. インターフェイス定義'. |
| `components/tier3_platform/platform_hal.md` | 5.1 公開API | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '5.1 公開API'. |
| `components/tier3_platform/platform_hal.md` | `gpio-controller` (物理GPIO制御) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`gpio-controller` (物理GPIO制御)'. |
| `components/tier3_platform/platform_hal.md` | `periodic-timer` (時刻とタイマー) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`periodic-timer` (時刻とタイマー)'. |
| `components/tier3_platform/platform_hal.md` | `bus-master` / `bus-slave` (I2C/SPI通信) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`bus-master` / `bus-slave` (I2C/SPI通信)'. |
| `components/tier3_platform/platform_hal.md` | `debug-server` (GDB RSP サーバ) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`debug-server` (GDB RSP サーバ)'. |
| `components/tier3_platform/platform_hal.md` | 6. 制約達成の方策 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '6. 制約達成の方策'. |
| `components/tier3_platform/platform_memory.md` | 3. 静的モデル | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 静的モデル'. |
| `components/tier3_platform/platform_memory.md` | 3.1 データ構造 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 データ構造'. |
| `components/tier3_platform/platform_memory.md` | 3.2 依存関係 (Zero-cost DI) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 依存関係 (Zero-cost DI)'. |
| `components/tier3_platform/tests/platform_hal_test_spec.md` | HAL テスト仕様書 (Test Specification) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'HAL テスト仕様書 (Test Specification)'. |
| `components/tier3_platform/tests/platform_hal_test_spec.md` | 1. 目的と対象範囲 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 目的と対象範囲'. |
| `components/tier3_platform/tests/platform_hal_test_spec.md` | 3. テスト検証実績と網羅状況 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier3_platform/tests/platform_hal_test_spec.md` | 4. 未検証・スコープ外 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `components/tier3_platform/tests/platform_memory_test_spec.md` | 物理メモリ (COOSメモリマネージャ) テスト仕様書 (Test Specification) | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '物理メモリ (COOSメモリマネージャ) テスト仕様書 (Test Specification)'. |
| `components/tier3_platform/tests/platform_memory_test_spec.md` | 2. テストケース一覧 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `components/tier3_platform/tests/platform_memory_test_spec.md` | 3. テスト検証実績と網羅状況 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `components/tier3_platform/tests/platform_memory_test_spec.md` | 4. 未検証・スコープ外 | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `plans/backlog_archive.md` | バックログアーカイブ | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'バックログアーカイブ'. |
| `plans/backlog_list.md` | Fireball アクティブバックログ | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Fireball アクティブバックログ'. |
| `plans/backlog_list.md` | Phase 3: PoC（ターゲットボード移植 / 将来予定） | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 3: PoC（ターゲットボード移植 / 将来予定）'. |
| `plans/roadmap_phase.md` | 開発ロードマップ | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '開発ロードマップ'. |
| `plans/roadmap_phase.md` | Phase 2: Integration（約4ヶ月） | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 2: Integration（約4ヶ月）'. |
| `plans/roadmap_phase.md` | Phase 3: PoC（約2ヶ月） | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 3: PoC（約2ヶ月）'. |
| `plans/roadmap_phase.md` | Phase 4: OSS（継続） | meta | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Phase 4: OSS（継続）'. |
| `requires/requirement_list.md` | Fireball システム要求仕様書 | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'Fireball システム要求仕様書'. |
| `requires/requirement_list.md` | 1. 概要 | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. 概要'. |
| `requires/requirement_list.md` | 2. ユースケース | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. ユースケース'. |
| `requires/requirement_list.md` | 3. 命題リスト | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. 命題リスト'. |
| `requires/requirement_list.md` | 3.1 機能要求 | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.1 機能要求'. |
| `requires/requirement_list.md` | 3.2 非機能要求 | 0 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3.2 非機能要求'. |
| `specs/jit_stencil_catalog.md` | 3. ステンシル・カタログ (Thumb-2 Stencil Catalog) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. ステンシル・カタログ (Thumb-2 Stencil Catalog)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_UNREACHABLE` (`0x00`) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_UNREACHABLE` (`0x00`)'. |
| `specs/jit_stencil_catalog.md` | `STENCIL_NOP` (`0x01`) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '`STENCIL_NOP` (`0x01`)'. |
| `specs/tests/wasm_instruction_set_test_spec.md` | WASM命令セット テスト仕様書 (Test Specification) | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for 'WASM命令セット テスト仕様書 (Test Specification)'. |
| `specs/tests/wasm_instruction_set_test_spec.md` | 2. テストケース一覧 | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '2. テストケース一覧'. |
| `specs/tests/wasm_instruction_set_test_spec.md` | 3. テスト検証実績と網羅状況 | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. テスト検証実績と網羅状況'. |
| `specs/tests/wasm_instruction_set_test_spec.md` | 4. 未検証・スコープ外 | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4. 未検証・スコープ外'. |
| `specs/wasi_preview1_abi.md` | 3. WASI Preview 1 サポート API マトリクス | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. WASI Preview 1 サポート API マトリクス'. |
| `specs/wasm_instruction_set.md` | 3. WASM MVP オプコード物理マトリクス | None | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '3. WASM MVP オプコード物理マトリクス'. |