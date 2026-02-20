---
name: Ollama Query Proxy
description: >
  ローカルの Ollama インスタンスを活用し、広範なソースコードの横断的要約や巨大なログの一次解析を行う「部下」エージェント。
  WHEN: クラウドトークンの節約、大規模ファイルの一次解析、多量ファイルの横断的要約が必要な時
  SCOPE: プロジェクト全域、外部ドキュメント、ビルドログ
  RELATED: general_codebase_explore, project_friction_audit
---

# Ollama Query Proxy

ローカルで稼働する Ollama モデル（`phi3:mini` 等）を「部下（コエージェント）」として利用し、高密度な推論（Tiered Inference）を実現するスキルです。

## 1. 概要 (Overview / Benefits)

巨大なファイルや多量のソースコードをメインエージェント（クラウド側）が直接読み込むのは、トークンコストとレイテンシの観点から非効率です。
さらに最大の問題として、**メインエージェント（クラウドAI）にはトークンを節約するアルゴリズムが組み込まれており、ローカルの広範なデータに直接アクセスして網羅的に解析することができません。**

そのため、広範なローカルデータ（多量のドキュメントや巨大ログ）に関する情報の「露払い（事前調査・構造化）」をローカルの部下に行わせることは**必須（Mandatory）**のステップとなります。

- **真価 (Mass Processing)**: 単一ファイルの読み込みではなく、`find` や `explore_codebase.py` の出力をパイプで流し込み、広範なコンテキストを一度に部下が一次解析させ、構造化デーへ圧縮させることにあります。
- **メインの制約回避**: トークン節約ロジックに引っかかることなく、ローカルの全域データを要約・検索できます。
- **情報の高密度化**: 部下が情報をごっそりと抜き出し、高密度なマークダウン形式で構造化してからメインに渡すことで、メインエージェントが散らばったファイルを直接読まずに済むようにサポートします。

## 2. 環境・前提条件 (Prerequisites)

- **Ollama**: ホストマシンで Ollama が稼働していること (`http://localhost:11434`)。
- **推奨モデル**: `phi3:mini` (論理推論に特化)。
- **Python 3**: プロキシスクリプトの実行環境。

## 3. 使用方法 (Usage)

### 統合実行 (Integrated Runner)

引数としてファイルパスを直接渡すか、標準入力経由でテキスト情報を渡します。
コマンド書式: `query_ollama.py <scope> <instruction> [files...]`

```bash
# 特定のファイル内容を詳細解析させる
python .agent/skills/project_ollama_query/scripts/query_ollama.py code_review "構造を分析せよ" "src/main.cxx"

# 複数ファイルを一括解析させる
python .agent/skills/project_ollama_query/scripts/query_ollama.py arch_review "インターフェースを抽出せよ" "include/a.h" "include/b.h"

# 標準入力（パイプ）からデータをまとめ流し込む
cat build.log | python .agent/skills/project_ollama_query/scripts/query_ollama.py build_analysis "エラーの原因を要約せよ"
```

### パイプ連携 (Mass Summarizing)
大量のファイルを対象にする場合、本スキルの真価が発揮されます。`find` 等で取得した複数ファイルの中身を流し込みます。

```bash
# src 内の全 C++ ファイルの内容を展開し、一括要約させる
find src -name "*.cxx" | xargs cat | python .agent/skills/project_ollama_query/scripts/query_ollama.py mass_summary "全体構造を状態遷移モデルとして抽出せよ"
```

## 4. 構成要素の詳細 (Component Details)

### scripts/
- **[query_ollama.py](file:///.agent/skills/project_ollama_query/scripts/query_ollama.py)**: Ollama API 通信と、ATC フォーマットおよび Naming Rule (scope_target.atc) の適用を担うコア実行体。

## 5. 品質・検証ルール (Quality & Validation)

- **高密度マークダウンの強制**: 出力は高密度な箇条書きや構造化マークダウンであり、重要な定量的データやメカニズムが欠落しないようにすること（`query_ollama.py`のシステムプロンプトに準拠）。
- **戦略的利用**: 些細な一件ずつの探索（Agentic Search）で時間を費やす前に、まずは本スキルで広域スキャンを命じること。

## 6. トラブルシューティング (Troubleshooting)

**Connection Refused**:
- WSL2 から Windows ホストの Ollama にアクセスする場合、ネットワーク設定（localhost の転送）を確認してください。
