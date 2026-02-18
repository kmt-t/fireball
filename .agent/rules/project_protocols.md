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

## 2. ナビゲーションと情報検索

*   **ルート**: すべてのパスはワークスペースルートからの相対パスとする。
*   **開始**: コンテキスト把握のため、最初に `Gemini.md` (存在する場合) を読むこと。
*   **参照**: 外部仕様については `@docs/REFERENCES.md` を確認すること。
*   **範囲制限**: 検索範囲は `docs/`, `inc/`, `src/` に限定すること。

## 3. ワークフロー

ドキュメントやコードを参照・作成する前に、以下を実行せよ：

1.  **トレーサビリティ**: `docs/requires/list.md` で定義された`{Keyword}`を必ず使用し、要求と実装・仕様をリンクさせること。
    - **検証**: `python3 .agent/scripts/check_traceability.py` を実行し、未検出キーワードがないことを確認せよ。
    - **理解**: キーワードの意図が不明な場合は `python3 .agent/scripts/search_context.py "{Keyword}"` を使用し、文脈から「様相論理による3行要約」を作成して理解せよ。
2.  **一貫性 (Friction Audit)**: 未定義キーワードや表記揺れ（Friction）がないか確認する。
    - **検証**: `python3 .agent/scripts/friction_audit.py` を実行し、レポート (`docs/temp/friction_report.md`) を確認せよ。
3.  **重複**: 既存の仕様を重複して作成していないか確認する。
4.  **記録 (Recording)**: 作業中に遭遇した技術的問題（ツールパスの不整合、環境依存のエラー等）や解決策は、即座に `.agent/brain/backlog.atc` または関連する `SKILL.md` に記録せよ。
5.  **バックログ**: 情報が不足している場合は、勝手に仕様を作らず、一般的な解決策を提案した上で `docs/backlog/` に記録する。
