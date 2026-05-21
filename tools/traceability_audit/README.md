# Traceability Audit - トレーサビリティ監査スクリプト

セクション × 要求キーワード の紐付けを検証し、設計漏れを検出します。

## 検証内容

### 機械的チェック（常時実行）

- **S2**: 出所不明セクション - キーワード未紐付けのセクションを検出
- **S3**: 要求漏れ - セクション未紐付けのキーワードを検出

### LLM チェック（オプション）

- **L1**: 意味的不整合 - セクションとキーワードの意味的ズレを検出

## 使用方法

```bash
# 機械的チェックのみ
./tools/scripts/traceability_audit/run.sh

# LLM チェック追加
./tools/scripts/traceability_audit/run.sh --llm

# 詳細ログ表示
./tools/scripts/traceability_audit/run.sh --verbose

# 特定モデル指定
./tools/scripts/traceability_audit/run.sh --llm --model gpt-oss-120b

# デバッグ出力
./tools/scripts/traceability_audit/run.sh --debug
```

## 出力ファイル

- `docs/components/traceability_matrix.csv` - セクション × キーワード マッピング行列
- `tmp/traceability_YYYYMMDD_HHMMSS.txt` - コンソール出力ログ
