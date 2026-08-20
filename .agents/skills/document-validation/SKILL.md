---
name: document-validation
description: Fireball リポジトリの標準ドキュメント検証パイプライン (spec-integrator) を実行するスキル。静的リンク、要求トレーサビリティ、Tier 階層一貫性、形式検証 (pyModelChecking)、WIT インターフェース定義、および LLM as a Judge を実行する際に使用する。
---

# Document Validation (spec-integrator)

Fireball のドキュメント品質、トレーサビリティ、形式モデル、WIT インターフェースを包括的に検証するための標準エントリポイントです。

## 実行コマンド

### 1. 総合検証パイプライン (全静的チェック + 形式検証 + WIT検証)
```powershell
# PowerShell (Windows)
powershell -ExecutionPolicy Bypass -File tools/run_all_tests.ps1 -clean

# Bash (Linux / CI)
./tools/run_all_tests.sh --clean
```

### 2. LLM as a Judge セマンティック評価
```powershell
powershell -ExecutionPolicy Bypass -File tools/run_all_tests.ps1 -llm -backend sakura
```

### 3. spec-integrator CLI を直接使用する場合
```bash
# ドキュメント検証
uv run --system-certs --project tools/spec-integrator python -m spec_integrator.cli check --config spec-integrator.yaml --report doc_report.md

# DocGraph 可視化 (Mermaid / JSON)
uv run --system-certs --project tools/spec-integrator python -m spec_integrator.cli graph --config spec-integrator.yaml -f mermaid

# LLM as a Judge 単体実行
uv run --system-certs --project tools/spec-integrator python -m spec_integrator.cli judge --config spec-integrator.yaml --backend sakura
```

---

## 監査される 5 つの品質ゲート (Quality Gates)

| ゲート名 | 検証内容 | 違反時の重要度 |
| :--- | :--- | :--- |
| **Format Gate** | 壊れた Markdown リンク、無効なアンカー（`#heading`）の検知 | **ERROR** (Exit 1) |
| **Traceability Gate** | 未定義キーワードの参照、Tier 0 要件の未参照検知 | **ERROR** (Exit 1) |
| **Hierarchy Gate** | 上位 Tier から下位 Tier への具象逆流依存の検知 | **ERROR** (Exit 1) |
| **Formal Gate** | `docs/components/<tier>/formal/*.py` の pyModelChecking 実行 | **ERROR** (Exit 1) |
| **WIT Gate** | `docs/components/<tier>/wit/*.wit` の構文・構造検証 | **ERROR** (Exit 1) |

---

## 設定と真実の源泉 (Source of Truth)
- システム設定: [`spec-integrator.yaml`](file:///x:/hotspot/workspace/mysrc/fireball/spec-integrator.yaml)
- 階層・キーワード規約: [`docs/architecture/document_structure.md`](file:///x:/hotspot/workspace/mysrc/fireball/docs/architecture/document_structure.md)
- 要求仕様正本: [`docs/requires/requirement_list.md`](file:///x:/hotspot/workspace/mysrc/fireball/docs/requires/requirement_list.md)
