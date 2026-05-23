# Fireball Analysis & Validation Scripts

設計検証・整合性監査の統合スクリプト群。要求定義とコンポーネント仕様書の整合性を担保するための機械的検証および SQLite / LLM を活用した高速なセマンティック検証を、モジュール化された体系的設計で提供します。

---

## 1. ディレクトリ構成 (Modular Directory Structure)

本ツールの構成は以下の通りに役割ごとに分割されています。

```
tools/
├── run_audit.py                     # 統一Pythonエントリーポイント
│
├── common/                          # 共通基盤
│   ├── db.py                        # SQLite3 操作、キーワードマスタ、キャッシュ管理
│   ├── llm.py                       # バックエンド統一（Gemini/OpenRouter/Sakura/Ollama）
│   └── parser.py                    # Markdownパース、共通セクション/キーワード抽出
│
├── mechanical/                      # 機械的チェックモジュール（M-* シリーズ）
│   ├── check_format.py              # M-FORMAT-* (HEADING, CODE, MERMAID)
│   ├── check_traceability.py        # M-TRACE-* (UNDEFINED, ORPHAN-SEC, UNCOVERED)
│   └── check_api.py                 # M-ARCH-NAMING
│
└── llm/                             # LLM意味監査モジュール（S-* シリーズ）
    ├── audit_module.py              # S-POLICY-MEM, S-QUALITY-*, S-TRACE-ALIGN
    ├── audit_consistency.py         # S-ARCH-PAIR
    └── audit_hierarchy.py           # S-ARCH-HIERARCHY (Tier 1-3)
```

---

## 2. データベース管理 (SQLite3)

検証結果、要求定義キーワード、用語集、トレーサビリティ情報は、すべて SQLite データベース `temp/doc_audit.db` に集約・管理されます。
LLMの検証結果は SHA256 ハッシュキーにより厳密にキャッシュされ、ドキュメントに差分がない場合はミリ秒単位で監査が完了します。

---

## 3. 提供される検証ルール一覧 (Rule Taxonomy)

### 機械的チェックルール (`M-` シリーズ)

| ルールコード | カテゴリ | 検証項目と判定基準 |
| :--- | :--- | :--- |
| **`M-FORMAT-HEADING`** | FORMAT | **見出しフォーマット**: 見出しにC++識別子を直接使わない。 |
| **`M-FORMAT-CODE`** | FORMAT | **C++コードブロック制限**: コンポーネント仕様書内の `cpp` コードブロック埋め込み禁止。 |
| **`M-FORMAT-MERMAID`** | FORMAT | **Mermaid構文検証**: 非Mermaidダイアグラムツール使用やタグ漏れを検出。 |
| **`M-TRACE-UNDEFINED`** | TRACE | **未定義キーワード検出**: 要求定義マスタに登録されていない `{Keyword}` を検出。 |
| **`M-TRACE-ORPHAN-SEC`** | TRACE | **出所不明セクション検出**: 要求キーワードが1つも紐付けられていないセクションを検出。 |
| **`M-TRACE-UNCOVERED`** | TRACE | **未カバー要求検出**: ドキュメントから一度も引用されていない孤立要求を検出。 |
| **`M-ARCH-NAMING`** | ARCH | **API命名規約チェック**: 公開API定義の表記ゆれ・命名スタイル違反を機械的に検出。 |

### 意味的（LLM）チェックルール (`S-` シリーズ)

| ルールコード | カテゴリ | 検証項目と判定基準 |
| :--- | :--- | :--- |
| **`S-TRACE-ALIGN`** | TRACE | **要求意味適合性**: セクション内の記述が要求キーワードの定義と意味的に適合しているか検証。 |
| **`S-POLICY-MEM`** | POLICY | **メモリ/STL規約適合性**: 動的メモリ確保や例外処理、RTTIの使用に言及していないか検証。 |
| **`S-QUALITY-PLACEHOLDER`**| QUALITY | **プレースホルダー検出**: `TBD`, `TODO`, `未定` などの記述が残っていないか検出。 |
| **`S-QUALITY-AMBIGUITY`** | QUALITY | **曖昧記述の排除**: 「適切な処理」「必要に応じて」などの曖昧表現を排除。 |
| **`S-QUALITY-API`** | QUALITY | **API定義の完全性**: 引数・戻り値の型や説明が欠落していないか検証。 |
| **`S-ARCH-PAIR`** | ARCH | **ペア間インターフェイス整合**: 2つの仕様書間のAPI定義や状態遷移、メモリ解釈の矛盾を検証。 |
| **`S-ARCH-HIERARCHY`** | HIERARCHY| **階層境界整合 (Tier 1-3)**: 上位・下位仕様間で抽象化漏れがないか検証。 |

---

## 4. 実行方法 (Usage)

### 統一エントリーポイント (`run_audit.py`)

```bash
# 全ての機械的チェックを実行
python3 tools/run_audit.py

# 特定のルールのみを実行 (例: メモリ・STLポリシー)
python3 tools/run_audit.py --rule S-POLICY-MEM

# 特定のドキュメントの全検証（機械 + 意味）を実行
python3 tools/run_audit.py --module docs/components/core/system_config.md

# 全ドキュメントの全検証（機械 + 意味）を実行
python3 tools/run_audit.py --all

# 階層整合性検証を実行 (Tier 1)
python3 tools/run_audit.py --hierarchy --tier 1
```

### 後方互換実行スクリプト (Bash Wrappers)

従来通りの実行方法も、`run_audit.py` への橋渡しとして互換性が維持されています。

```bash
# 1. 仕様整合性チェッカー (機械的 + LLM整合性マトリクス)
./tools/run_consistency_check.sh [--llm] [--gentable]

# 2. トレーサビリティ監査
./tools/run_traceability_audit.sh [--llm]

# 3. LLM ドキュメント一括監査
./tools/run_doc_test.sh [--backend SAKURA|gemini|openrouter|ollama] [--model MODEL_NAME] [--quick]

# 4. 統合テストランナー (一括実行)
./tools/run_all_tests.sh [--llm] [--quick] [--backend BACKEND] [--model MODEL]
```
