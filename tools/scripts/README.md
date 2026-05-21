# Fireball Analysis & Validation Scripts

設計検証・整合性監査の統合スクリプト群

## 含まれるツール

### 1. Check Consistency (仕様整合性チェッカー)
```bash
./tools/scripts/check_consistency/run.sh [--llm] [--gentable]
```
- FORMAT規約準拠 (F グループ)
- トレーサビリティ検証 (T グループ)
- アーキテクチャ整合性 (A グループ)
- LLM による意味チェック（オプション）

### 2. Traceability Audit (トレーサビリティ監査)
```bash
./tools/scripts/traceability_audit/run.sh [--llm] [--verbose]
```
- セクション × キーワード マッピング
- 出所不明セクション検出 (S2)
- 要求漏れ検出 (S3)
- 意味的整合性検証（オプション）

## ワンストップ実行

```bash
./tools/scripts/run_all.sh [--llm] [--model MODEL]
```

**実行内容:**
1. Check Consistency (機械的チェック)
2. Traceability Audit (機械的チェック)
3. Document Test (モジュール + Tier 1-3 監査)

## 環境変数

```bash
export SAKURA_AI_API_KEY="your-key"       # Sakura AI
export OPEN_ROUTER_API_KEY="your-key"     # OpenRouter
export GEMINI_API_KEY="your-key"          # Google Gemini
# Ollama は環境変数不要（ローカル実行）
```

## オプション共通

- `--model MODEL` - LLM モデル指定
- `--verbose` - 詳細ログ表示
- `--debug` - デバッグ出力

## レポート出力

- `docs/components/spec_matrix.csv` - 仕様書 × キーワード行列
- `docs/components/consistency_checklist.csv` - LLM チェックリスト
- `docs/components/traceability_matrix.csv` - セクション × キーワード行列
- `docs/tools/audit_reports/` - ドキュメント監査ログ
