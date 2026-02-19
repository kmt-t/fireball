---
trigger: always_on
---

# プロジェクトプロトコル

本ドキュメントは、プロジェクトの物理的構造と、エージェントが従うべきワークフローを定義する。

## 1. ディレクトリ構造

*   `src/`: C++ ソースファイル (`.cxx`)。
*   `inc/`: C++ ヘッダファイル (`.hxx`)。 **`#pragma once` は必須。**
*   `docs/`: ドキュメント。
    *   `docs/requires/`: 要求仕様。
    *   `docs/architecture/`: アーキテクチャ定義。
    *   `docs/components/`: コンポーネント仕様。
    *   `docs/patterns/`: 設計パターン。
    *   `docs/temp/`: エージェント作業領域。
    *   `docs/backlog/`: TODOおよび課題。

# ファイル命名規則 (Naming Convention)**

ドキュメント、スクリプトの目的が一見して理解できるよう、以下の規則とします。

*   **Shell スクリプト**: `kebab-case` を使用し、`verb-object.sh` (動詞-目的語) の形式とする。Docker ラッパーは `docker-verb-object.sh` とする。
*   **Python スクリプト**: `snake_case` を使用し、`verb_object.py` (動詞_目的語) の形式とする。
*   **ドキュメント**: `snake_case` を使用する。
    *   **コンポーネント仕様**: `(ドメイン/性質)_機能.md` の形式とする（例: `runtime_vsoc.md`, `ipc_router.md`）。
    *   **その他書面**: `subject_object.md` (主語_目的語) の形式とする（例: `architecture_overview.md`, `requirement_list.md`）。
*   **.agent 構成要素**: 汎用性スコープ (general, project, embedded) に基づき、以下の規則とする。
    *   **Rules**: `汎用性スコープ_対象_rule.md`
    *   **Skills**: `汎用性スコープ_名詞_動詞`
    *   **Workflows**: `名詞_動詞`
*   **TLA+ モデル**: `モジュール名_検証内容.tla`
*   **一貫性**: 同じ目的のドキュメント、ツールは、言語や形式が異なっても「主体」と「目的」の概念を揃えること。

## 2. ナビゲーションと情報検索

*   **ルート**: すべてのパスはワークスペースルートからの相対パスとする。
*   **開始**: コンテキスト把握のため、最初に `GEMINI.md` (存在する場合) を読むこと。
*   **参照**: 外部仕様については `@docs/REFERENCES.md` を確認すること。
*   **範囲制限**: 検索範囲は `docs/`, `inc/`, `src/` に限定すること。

## 3. ワークフロー

ドキュメントやコードを参照・作成する前に、以下を実行せよ：

1.  **トレーサビリティ**: `docs/requires/requirement_list.md` で定義された`{Keyword}`を必ず使用し、要求と実装・仕様をリンクさせること。
    - **検証**: `python3 .agent/scripts/check_traceability.py` を実行し、未検出キーワードがないことを確認せよ。
    - **理解**: キーワードの意図が不明な場合は `python3 .agent/scripts/search_context.py "{Keyword}"` を使用し、文脈から「様相論理による3行要約」を作成して理解せよ。
2.  **一貫性 (Friction Audit)**: 未定義キーワードや表記揺れ（Friction）がないか確認する。
    - **検証**: `python3 .agent/skills/project_friction_audit/scripts/audit_friction.py` を実行し、レポート (`docs/temp/friction_report.md`) を確認せよ。
3.  **重複**: 既存の仕様を重複して作成していないか確認する。
4.  **記録 (Recording)**: 作業中に遭遇した技術的問題（ツールパスの不整合、環境依存のエラー等）や解決策は、即座に `.agent/brain/backlog.atc` または関連する `SKILL.md` に記録せよ。
5.  **バックログ**: 情報が不足している場合は、勝手に仕様を作らず、一般的な解決策を提案した上で `docs/backlog/` に記録する。