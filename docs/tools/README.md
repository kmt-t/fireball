# Fireball Tools & Scripts Documentation

Fireball プロジェクトの検証・監査・テストツール群の仕様書

## ツール一覧

### 1. Document Test (ドキュメント監査)
**パス:** `tools/doc_test/` / `tools/run_doc_tests.sh`

セクション・マトリクスベースの LLM 監査により、仕様ドキュメントの「一貫性」と「品質」を検証。

- **検証レベル:**
  - Module: 単一ドキュメントの策定準拠性・品質
  - Hierarchy: Tier 間（要求 → コア → ランタイム → プラットフォーム）の階層的整合性
  
- **詳細仕様:** [`doc_test_llm.md`](doc_test.md)

```bash
# ワンストップ実行（Module + Tier 1-3）
./tools/run_doc_tests.sh --backend ollama

# クイック（Tier 1 のみ）
./tools/run_doc_tests.sh --quick
```

---

### 2. Check Consistency (仕様整合性チェッカー)
**パス:** `tools/check_consistency/` / `tools/run_check_consistency.sh`

コンポーネント仕様書間の形式的・意味的一貫性を機械的に検証。

- **検証グループ:**
  - **F (FORMAT)**: 仕様書フォーマット規約準拠（見出し、コードブロック、図表）
  - **T (TRACEABILITY)**: 要求キーワード整合性（未定義キーワード、利用率）
  - **A (ARCHITECTURE)**: API 表記ゆれ検出
  - **LLM** (オプション): 意味的整合性

- **詳細仕様:** [`check_consistency.md`](check_consistency.md)

```bash
# 機械的チェック（F/T/A）
./tools/run_check_consistency.sh

# LLM チェック追加
./tools/run_check_consistency.sh --llm

# テーブル再生成
./tools/run_check_consistency.sh --gentable
```

---

### 3. Traceability Audit (トレーサビリティ監査)
**パス:** `tools/traceability_audit/` / `tools/run_traceability_audit.sh`

セクション × キーワード マッピングを検証し、設計漏れと矛盾を検出。

- **検証項目:**
  - **S2**: 出所不明セクション（キーワード未紐付けのセクション）
  - **S3**: 要求漏れ（セクション未紐付けのキーワード）
  - **L1** (オプション): 意味的不整合

- **詳細仕様:** [`traceability_audit.md`](traceability_audit.md)

```bash
# 機械的チェック（S2/S3）
./tools/run_traceability_audit.sh

# LLM チェック追加（L1）
./tools/run_traceability_audit.sh --llm

# 詳細ログ
./tools/run_traceability_audit.sh --verbose
```

---

## 実行方法

各ツールを個別に実行：

```bash
# 一貫性チェック
./tools/run_check_consistency.sh

# トレーサビリティ監査
./tools/run_traceability_audit.sh

# ドキュメント LLM 監査
./tools/run_doc_tests.sh
```

**推奨順序:**
1. Check Consistency (FORMAT/Traceability/Architecture)
2. Traceability Audit (S2/S3 detection)
3. Document Test (Module + Tier 1-3)

---

## 環境設定

### LLM バックエンド

3つのバックエンド対応（優先順位順）：

```bash
# Option 1: Sakura AI
export SAKURA_AI_API_KEY="your-key"

# Option 2: OpenRouter
export OPEN_ROUTER_API_KEY="your-key"

# Option 3: Ollama (ローカル、キー不要)
# http://localhost:11434
```

---

## 出力ファイル

| ファイル | 説明 |
| :--- | :--- |
| `docs/components/spec_matrix.csv` | コンポーネント仕様書 × 要求キーワード 行列 |
| `docs/components/consistency_checklist.csv` | LLM 用チェックリスト（仕様書ペアの検証項目） |
| `docs/components/traceability_matrix.csv` | セクション × キーワード マッピング行列 |
| `docs/tools/audit_reports/audit_*.log` | Document Test の詳細ログ（時系列） |
| `tmp/traceability_YYYYMMDD_HHMMSS.txt` | Traceability Audit のコンソール出力ログ |

---

## 推奨実行パターン

### パターン 1: 日常的な検証（軽量）

```bash
./tools/run_check_consistency.sh
./tools/run_traceability_audit.sh
./tools/run_doc_tests.sh --quick
```

実行時間: ~2-3 分

### パターン 2: コミット前検証（中程度）

```bash
./tools/run_check_consistency.sh
./tools/run_traceability_audit.sh
./tools/run_doc_tests.sh --quick
```

実行時間: ~5 分

### パターン 3: リリース前検証（完全）

```bash
./tools/run_check_consistency.sh --llm
./tools/run_traceability_audit.sh --llm
./tools/run_doc_tests.sh
```

実行時間: ~15-20 分（LLM バックエンド依存）

---

## トラブルシューティング

### LLM API エラー

```
Error: LLM API Error (HTTP 401)
```

→ 環境変数確認：
```bash
echo $SAKURA_AI_API_KEY
echo $OPEN_ROUTER_API_KEY
```

### CSV ファイルが見つからない

```
Error: consistency_checklist.csv not found
```

→ テーブル再生成:
```bash
./tools/run_check_consistency.sh --gentable
```

### Ollama 接続エラー

```
Error: Cannot connect to Ollama (http://localhost:11434)
```

→ Ollama サーバーを起動:
```bash
ollama serve
```

---

## 各ツールの詳細

- [Document Test (LLM Auto-Tester)](doc_test.md)
- [Check Consistency](check_consistency.md)
- [Traceability Audit](traceability_audit.md)
