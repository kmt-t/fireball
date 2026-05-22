# GEMINI.md

This file provides guidance to Antigravity / Gemini Code when working with code in this repository. It explains the mechanism of `CLAUDE.md` and the `.claude/` directory, and defines how Gemini imports and emulates these configurations.

---

## 1. Claude Code 設定の仕組み (Claude Code Mechanism)

本リポジトリでは、Claude Code 向けに以下の仕組みで開発規約および検証環境が構成されています。

### 1.1 `CLAUDE.md` (エントリーポイント)
- **役割**: Claude Code 起動時に自動で読み込まれるメインガイドライン。
- **内容**: プロジェクトの基本設計思想、主要コマンド（ビルド・検証等）、コーディング規約の概要が記述されています。

### 1.2 `.claude/rules/` (Globベースのルール定義)
- **役割**: ファイル編集・閲覧時に、ファイルパスに応じて動的にルールを適用する仕組み。
- **構成**: 各ルールファイル（Markdown）のヘッダーに以下の YAML フロントマターが定義されています。
  ```yaml
  name: coding-standards-embedded  # ルールの一意な名前
  globs: ["src/**", "inc/**"]       # 適用対象となるファイルのGlobパターン
  instructions: |                  # 適用時にLLMのコンテキストに挿入される指示
    1. メモリ管理: ヒープ禁止(malloc/new禁止)...
  ---
  # 詳細な説明 (Markdown)
  ```

### 1.3 `tools/` (検証スクリプト群)
- **役割**: 仕様書の整合性と、要求定義と設計のトレーサビリティを機械的に担保するための Python スクリプト群。
  - **`tools/check_consistency/check_consistency.py`**: 見出しの命名規則、Mermaidの記述、API名表記ゆれ、C++コードブロックの誤用等を機械的にチェックする。
  - **`tools/audit_traceability/audit_traceability.py`**: `docs/requires/` の要求キーワード `{Keyword}` が、`docs/components/` の仕様書に正しく紐付けられているかを監査する。

### 1.4 `.claude/settings.local.json` (権限設定)
- **役割**: Claude Code 上での外部コマンド実行（例: `tlc -version`）に対する権限許可設定。

---

## 2. Gemini による設定の取り込みとエミュレーション (Importing into Gemini)

Gemini (Antigravity) は、上記の仕組みを理解し、開発時に以下の手順でエミュレートしてください。

### 2.1 ルールの手動適用 (Glob Matching)
Gemini にはファイルパスに応じたルールの自動インジェクション機能がないため、**ソースコードの閲覧・編集時に対象のファイルパスが `.claude/rules/` 内の各ルールの `globs` に合致するかを自身で判断し、ルールを適用**してください。

- **`src/**` および `inc/**` 配下のファイルを変更する場合**:
  - `coding-standards-general.md` / `embedded_cpp.md` / `stdlib_policy.md` をロードし、`instructions` の指示（ヒープ禁止、RAII、C++23 Conceptsの活用など）に従うこと。
- **`docs/**` 配下のドキュメントを変更する場合**:
  - `documentation.md` / `documentation_format.md` / `development-policy.md` 等をロードし、仕様第一 (Specification-First)、Mermaidによる可視化、表記ゆれの防止などの指示に従うこと。

### 2.2 検証スクリプトによるセルフチェック (Self-Verification)
コードや仕様書を変更した際は、コミットまたは回答の前に必ず以下のスクリプトを実行し、警告やエラーが発生していないか検証してください。

```bash
# 整合性検証 (記述規約、未定義キーワード参照、API表記ゆれ等の機械的チェック)
python3 tools/check_consistency/check_consistency.py

# トレーサビリティ監査 (要求キーワードの紐付けチェック)
python3 tools/audit_traceability/audit_traceability.py

# LLM を用いたセマンティック仕様書検証 (開発方針、トレーサビリティ充足性、品質/プレースホルダーのチェック)
# 1. 単一の仕様書モジュールの検証:
python3 tools/test_doc/test_doc_llm.py --module docs/components/core/system_config.md

# 2. docs/components/ 配下の全ファイルの一括モジュール検証:
python3 tools/test_doc/test_doc_llm.py --all

# 3. 2つの仕様書間の境界・組み合わせ整合性の検証 (例: os_coos.md と os_scheduler.md):
python3 tools/test_doc/test_doc_llm.py --pair docs/components/core/os_coos.md docs/components/core/os_scheduler.md

# 4. 指定したシステム階層(Tier 1〜3)の階層一貫性検証:
# Tier 1 (要求仕様 vs Core/Interface)
python3 tools/test_doc/test_doc_llm.py --hierarchy --tier 1
# Tier 2 (Core/Interface vs Runtime/JIT)
python3 tools/test_doc/test_doc_llm.py --hierarchy --tier 2
# Tier 3 (Runtime/JIT vs Platform/HAL)
python3 tools/test_doc/test_doc_llm.py --hierarchy --tier 3
```

> [!NOTE]
> `doc_test_llm.py` は、環境変数 `SAKURA_AI_API_KEY`, `OPEN_ROUTER_API_KEY`, `GEMINI_API_KEY` を検知してそれぞれの LLM バックエンドを使用します。キーがない場合はローカルの Ollama (`qwen2.5-coder:3b`) にフォールバックします。

# 全てのLLM自動テストを一括実行するシェルスクリプト:
# (デフォルトではローカルのOllamaを使用。--backend や --model オプションで他のLLMサービスやモデルを指定可能)
./tools/run_doc_test.sh --backend gemini --model gemini-2.5-pro

### 2.3 記述ルールの踏襲
- **要求キーワードのトレーサビリティ**: 新たなセクションを追加する場合は、行末に `` `{Keyword}` `` を付与し、`docs/requires/` との紐付けを維持する。
- **リンク記法**: ドキュメント内や回答内でファイル/シンボルに言及する際は、`[filename](file:///absolute/path/to/file)` 形式の絶対パスリンクを作成する。

