# Fireball 設計複雑度 & リスク評価レポート (Risk Assessment Report)

- **評価セクション総数**: 15
- **形式検証 (pyModelChecking) 推奨セクション**: 3
- **LLM 意味監査 推奨セクション**: 2

---

## 1. 形式検証 (pyModelChecking) が推奨される重要セクション

| ファイル | セクション | 複雑度 | リスク | 推奨検証 | 推奨タグ | 主なリスク要因 |
| :--- | :--- | :---: | :---: | :--- | :--- | :--- |
| `components/tier1_interface/ipc_router.md` | **4.1 アルゴリズム** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_core/os_coos.md` | **4.2 状態遷移図 (SMD: COOS システムレベル)** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |
| `components/tier1_core/os_coos.md` | **4.1 アルゴリズム** | 4/5 | **4/5** | `pyModelChecking` | `{VERIFY_FORMAL}` | Stateful concurrent protocol or hardware safety invariant identified |

---

## 2. 全セクションの複雑度・リスク評価一覧 (降順)

| ファイル | セクション | Tier | 複雑度 | リスク | 推奨手法 | 推奨タグ | 評価サマリー |
| :--- | :--- | :---: | :---: | :---: | :--- | :--- | :--- |
| `components/tier1_interface/ipc_router.md` | 4.1 アルゴリズム | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier1_core/os_coos.md` | 4.2 状態遷移図 (SMD: COOS システムレベル) | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '4.2 状態遷移図 (SMD: COOS システムレベル)'. |
| `components/tier1_core/os_coos.md` | 4.1 アルゴリズム | 1 | 4/5 | 4/5 | `pyModelChecking` | `{VERIFY_FORMAL}` | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier1_core/os_scheduler.md` | 6. 設計判断 (ADR) | 1 | 3/5 | 3/5 | `LLM_Judge` | `{VERIFY_LLM}` | Independent heuristic evaluation for '6. 設計判断 (ADR)'. |
| `components/tier3_jit/jit_compiler.md` | 8. 設計判断 (ADR) | 3 | 3/5 | 3/5 | `LLM_Judge` | `{VERIFY_LLM}` | Independent heuristic evaluation for '8. 設計判断 (ADR)'. |
| `components/tier2_runtime/runtime_interpreter.md` | 4.1 アルゴリズム | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier2_runtime/runtime_vmmio.md` | 1. コンセプト | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier1_core/system_containers.md` | 1. コンセプト | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier1_core/os_coos.md` | 1. コンセプト | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier2_runtime/debug_manager.md` | 1. コンセプト | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier1_core/system_config.md` | 1. コンセプト | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier3_jit/jit_runtime.md` | 1. コンセプト | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier3_jit/jit_compiler.md` | 1. コンセプト | 3 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '1. コンセプト'. |
| `components/tier2_runtime/runtime_vsoc.md` | 4.1 アルゴリズム | 2 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
| `components/tier1_core/system_containers.md` | 4.1 アルゴリズム | 1 | 2/5 | 2/5 | `Static` | - | Independent heuristic evaluation for '4.1 アルゴリズム'. |
