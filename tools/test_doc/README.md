# Fireball Document Test Suite (LLM-Based)

セクション・マトリクス統合型のドキュメント自動監査ツール群

## ツール構成

- **`doc_test_llm.py`** - メインの監査スクリプト
  - モジュール単位の検証（ポリシー・トレーサビリティ・品質）
  - Tier 1-3 の階層検証（セクション・マトリクスベース）
  
- **`extract_sections.py`** - Markdown セクション抽出
  - 見出し単位でドキュメント分割
  - キーワード抽出
  
- **`build_section_matrix.py`** - セクション・マトリクス生成
  - 親子ドキュメント間のセクションマッチング
  - マトリクス（CSV/Markdown）出力

- **`run_doc_tests.sh`** - ワンストップ監査実行スクリプト
  - Module + Tier 1-3 Hierarchy 一括実行
  - レポート自動保存

## 使用方法

### 全体監査（推奨）

```bash
./tools/doc_test/run_doc_tests.sh --backend ollama
```

### クイック検証（Tier 1 のみ）

```bash
./tools/doc_test/run_doc_tests.sh --backend ollama --quick
```

### 個別実行

#### モジュール監査

```bash
python3 tools/doc_test/doc_test_llm.py --all --backend ollama
```

#### Tier 別階層検証

```bash
python3 tools/doc_test/doc_test_llm.py --hierarchy --tier 1 --backend ollama
python3 tools/doc_test/doc_test_llm.py --hierarchy --tier 2 --backend ollama
python3 tools/doc_test/doc_test_llm.py --hierarchy --tier 3 --backend ollama
```

#### セクション・マトリクス生成（単体）

```bash
python3 tools/doc_test/build_section_matrix.py \
  docs/requires/requirement_list.md \
  docs/components/core/os_coos.md \
  --format markdown \
  --output /tmp/matrix.md
```

## オプション

- `--backend`: LLM バックエンド選択（sakura/openrouter/gemini/ollama）
- `--model`: モデル指定（デフォルトは自動選択）
- `--max-tokens`: 生成トークン上限（デフォルト: 2048）
- `--quick`: クイックモード（`run_doc_tests.sh` のみ）

## 環境変数

```bash
export SAKURA_AI_API_KEY="your-key"
export OPEN_ROUTER_API_KEY="your-key"
export GEMINI_API_KEY="your-key"
# OLLAMA は環境変数不要（ローカル実行）
```

## レポート出力

```
docs/tools/audit_reports/
  ├── audit_module_20260522_075300.log
  ├── audit_tier1_20260522_075310.log
  ├── audit_tier2_20260522_075400.log
  └── audit_tier3_20260522_075450.log
```

## 検証フロー

### Module Audit（単一ドキュメント）
1. 開発ポリシー適合性 - ヒープ禁止、STL 規約等
2. 要求トレーサビリティ - {Keyword} 充足性
3. 品質・曖昧さ検証 - プレースホルダー、TBD/TODO 検出

### Hierarchy Audit（Tier 間）
1. セクション抽出・マッチング
2. セクションペアごとの詳細レビュー（複数 LLM コール）
3. リスク水準・信頼度を含むレポート出力

## パフォーマンス

- Module Audit（全ファイル）: ~30 秒
- Tier 1 Hierarchy（セクション・マトリクス）: ~60 秒
- Full Audit (1-3): ~5-10 分（LLM 呼び出し回数に依存）
