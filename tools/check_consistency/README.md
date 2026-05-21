# Check Consistency - コンポーネント仕様整合性チェッカー

仕様書間の形式的整合性と意味的一貫性を検証します。

## 機械的チェック（常時実行）

- **F (FORMAT)**: 仕様書フォーマット規約準拠
  - F1: 見出しが C++ 識別子で始まっていない
  - F2: C++ コードブロックが使われていない
  - F3: 図が Mermaid 記法

- **T (TRACEABILITY)**: 要求キーワード整合性
  - T1: 未定義キーワード参照検出
  - T2: 参照されないキーワード警告
  - T3: コンポーネント未紐付けキーワード警告

- **A (ARCHITECTURE)**: API 表記ゆれ検出
  - A1: 公開 API の camelCase / kebab-case 揺らぎ

## LLM チェック（オプション）

`consistency_checklist.csv` を用いた意味的整合性検証

## 使用方法

```bash
# 機械的チェックのみ
./tools/scripts/check_consistency/run.sh

# LLM チェック追加
./tools/scripts/check_consistency/run.sh --llm

# テーブル再生成
./tools/scripts/check_consistency/run.sh --gentable

# 特定モデル指定
./tools/scripts/check_consistency/run.sh --llm --model gpt-oss-120b
```

## 出力ファイル

- `docs/components/spec_matrix.csv` - コンポーネント × 要求キーワード行列
- `docs/components/consistency_checklist.csv` - LLM 生成チェックリスト
