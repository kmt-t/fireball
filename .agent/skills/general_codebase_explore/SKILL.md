---
name: Codebase Explorer
description: >
  インタラクティブな探索を廃し、AST解析やシンボル抽出といった「構造データの抽出」に特化したツール。
  WHEN: 構造把握、関数追跡、シンボル一覧取得、キーワード文脈理解が必要な時
  SCOPE: プロジェクト全域
  RELATED: project_ollama_query, project_friction_audit
---

# Codebase Explorer

大規模なコードベースを構造的に解析し、エージェントやコエージェントに対して高密度な「事実データ」を供給するスキルです。

## 1. 概要 (Overview / Benefits)

「森（全体構造）」を機械的に抽出し、「木（詳細ロジック）」の解析を LLM に効率的に渡すためのブリッジとして機能します。

- **高密度な構造抽出**: クラス定義、関数シグネチャ、メンバ変数のみを AST ベースで抽出します。
- **データ供給の専門化**: 冗長なテキスト出力を廃止し、JSON やシンボルリストといった「純粋な事実」のみを出力することに特化しています。
- **Tiered Inference の基盤**: 本スキルが抽出した事実を `project_ollama_query` に渡すことで、クラウドトークンの消費を抑えつつ深い理解を実現します。

## 2. 環境・前提条件 (Prerequisites)

- **WSL2 Bash / Python**: Python 3.x が動作する環境。
- **Clang (Optional)**: `--ast` オプションを使用する場合に必要。

## 3. 使用方法 (Usage)

### 統合実行

```bash
# シンボルリストの取得
python .agent/skills/general_codebase_explore/scripts/explore_codebase.py src/main.cxx --symbols

# AST 構造のダンプ
python .agent/skills/general_codebase_explore/scripts/explore_codebase.py src/main.cxx --ast

# 関数の呼び出し元検索 (Callers)
python .agent/skills/general_codebase_explore/scripts/explore_codebase.py src/ --callers "vmmio_read"

# トレーサビリティキーワード {Keyword} の抽出
python .agent/skills/general_codebase_explore/scripts/explore_codebase.py docs/requires/ --keywords
```

### パイプ利用

```bash
# src 内の全ファイルのシンボルを JSON 形式で抽出
find src -name "*.cxx" | xargs -I {} python .agent/skills/general_codebase_explore/scripts/explore_codebase.py {} --symbols --json
```

## 4. 構成要素の詳細 (Component Details)

### scripts/
- **[explore_codebase.py](file:///.agent/skills/general_codebase_explore/scripts/explore_codebase.py)**: AST 解析、シンボル抽出、キーワード検索などを一括して行うコアスクリプト。
- **[graph_tool.py](file:///.agent/skills/general_codebase_explore/scripts/graph_tool.py)**: `cflow` 出力をフィルタリングするための補助ツール。

## 5. 品質・検証ルール (Quality & Validation)

- **事実性の維持**: 要約や解釈を行わず、コード上の定義事実（AST/シンボル）のみを忠実に出力すること。
- **相対パスの正規化**: 出力に含まれるファイルパスは常にプロジェクトルートからの相対パスとして出力すること。
- **パイプ指向**: すべての出力は後続のツール（`jq`, `xargs`, `query_ollama` 等）で処理可能な形式を維持すること。

## 6. トラブルシューティング (Troubleshooting)

**AST 解析でエラーが出る**:
- インクルードパスが足りない可能性があります。`-- extra_args -Iinc` のようにコンパイラフラグを渡してください。
