# Fireball Tools & Scripts Documentation

Fireball プロジェクトの検証・監査・テストツール群の仕様書

## ツール一覧

### 1. Document Test (ドキュメント監査)
**パス:** `tools/test_doc/` / `tools/run_doc_test.sh`

セクション・マトリクスベースの LLM 監査により、仕様ドキュメントの「一貫性」と「品質」を検証。

- **検証レベル:**
  - Module: 単一ドキュメントの策定準拠性・品質
  - Hierarchy: Tier 間（要求 → コア → ランタイム → プラットフォーム）の階層的整合性
  
- **詳細仕様:** [`doc_test_llm.md`](test_doc.md)

```bash
# ワンストップ実行（Module + Tier 1-3）
./tools/run_doc_test.sh --backend ollama

# クイック（Tier 1 のみ）
./tools/run_doc_test.sh --quick
```

---

### 2. Check Consistency (仕様整合性チェッカー)
**パス:** `tools/check_consistency/` / `tools/run_consistency_check.sh`

コンポーネント仕様書間の形式的・意味的一貫性を機械的に検証。

- **検証グループ:**
  - **F (FORMAT)**: 仕様書フォーマット規約準拠（見出し、コードブロック、図表）
  - **T (TRACEABILITY)**: 要求キーワード整合性（未定義キーワード、利用率）
  - **A (ARCHITECTURE)**: API 表記ゆれ検出
  - **LLM** (オプション): 意味的整合性

- **詳細仕様:** [`check_consistency.md`](check_consistency.md)

```bash
# 機械的チェック（F/T/A）
./tools/run_consistency_check.sh

# LLM チェック追加
./tools/run_consistency_check.sh --llm

# テーブル再生成
./tools/run_consistency_check.sh --gentable
```

---

### 3. Traceability Audit (トレーサビリティ監査)
**パス:** `tools/audit_traceability/` / `tools/run_traceability_audit.sh`

セクション × キーワード マッピングを検証し、設計漏れと矛盾を検出。

- **検証項目:**
  - **S2**: 出所不明セクション（キーワード未紐付けのセクション）
  - **S3**: 要求漏れ（セクション未紐付けのキーワード）
  - **L1** (オプション): 意味的不整合

- **詳細仕様:** [`audit_traceability.md`](audit_traceability.md)

```bash
# 機械的チェック（S2/S3）
./tools/run_traceability_audit.sh

# LLM チェック追加（L1）
./tools/run_traceability_audit.sh --llm

# 詳細ログ
./tools/run_traceability_audit.sh --verbose
```

---

### 4. Audit Tool (統合監査ツール)
**パス:** `tools/run_audit.py`

ドキュメント・コード・設定を統合的に監査。機械的チェック（FORMAT/TRACE/MERMAID）と LLM チェック、予算検証。

- **検証グループ:**
  - **M-FORMAT**: 仕様書フォーマット準拠
  - **M-TRACE**: トレーサビリティ（キーワード整合性）
  - **M-MERMAID**: Mermaid 図文法チェック（mermaid_config.csv で設定）
  - **M-ARCH**: アーキテクチャ命名規約

- **Mermaid 検証ルール：**
  - 設定: `tools/mechanical/mermaid_config.csv`
  - ルール定義: diagram_type（state/sequence/graph）× rule_name（brace_balance/activate_balance など）
  - 実装: `tools/mechanical/check_mermaid.py`

- **実行方法:**

```bash
# 全監査を実行
python tools/run_audit.py

# 出力例
# ✅ spec_matrix.csv を生成
# ✅ traceability_matrix.csv を生成
# ✅ All checks passed!
```

---

## 実行方法

統合監査ツール（推奨）：

```bash
# 全監査を実行（機械的チェック）
python tools/run_audit.py

# 出力: spec_matrix.csv, traceability_matrix.csv → temp/ に生成
```

**チェック順序:**
1. M-FORMAT: ドキュメントフォーマット準拠
2. M-TRACE: トレーサビリティ（キーワード整合性）
3. M-MERMAID: Mermaid 図文法（mermaid_config.csv 基準）
4. M-ARCH: アーキテクチャ命名規約

---

## 設定ファイル

すべての設定ファイルは `tools/config/` に集約：

| ファイル | 用途 |
| :--- | :--- |
| `tools/config/mermaid_config.csv` | Mermaid 検証ルール定義（diagram_type × rule_name） |
| `tools/config/complex_patterns.csv` | 複雑な設計パターン定義 |
| `tools/config/heading_dictionary.csv` | ドキュメント見出し用語辞書 |

---

## 出力ファイル

| ファイル | 説明 |
| :--- | :--- |
| `temp/spec_matrix.csv` | コンポーネント仕様書 × 要求キーワード 行列 |
| `temp/traceability_matrix.csv` | セクション × キーワード マッピング行列 |
| `temp/doc_audit.db` | LLM 監査結果 SQLite データベース |
| `temp/consistency_YYYYMMDD_HHMMSS.txt` | 一貫性チェックログ |
| `temp/verification_result.txt` | 検証結果サマリー |

---

## トラブルシューティング

### CSV ファイルが見つからない

```
KeyError: spec_matrix.csv not found
```

→ 監査を再実行してテーブルを生成：
```bash
python tools/run_audit.py
```

### Mermaid 検証ルールが反映されない

→ `mermaid_config.csv` を確認：
```bash
cat tools/mechanical/mermaid_config.csv
```

CSV に定義されたルール（diagram_type × rule_name）が `check_mermaid.py` に動的にロードされます。
