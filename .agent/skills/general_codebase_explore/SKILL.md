---
name: Codebase Explorer
description: >
  インタラクティブにコードベースを探索し、構造把握、シンボル要約、文脈解析を行う統合ツール。
  WHEN: 構造把握、関数追跡、シンボル一覧取得、キーワード文脈理解が必要な時
  SCOPE: プロジェクト全域
  RELATED: project_friction_audit, general_docker_run
---

# Codebase Explorer

大規模なコードベースを構造化・視覚化し、エージェントのワーキングメモリを保護しながら効率的な探索を可能にするスキルです。

## 1. 概要 (Overview / Benefits)

「木を見て森を見ず」という状態を回避し、システムの全体像と詳細な実装を自在に行き来できるようにします。

- **構造把握の高速化**: 巨大なディレクトリやソースファイルから、重要なシンボル（クラス、関数）のみを抽出して要約します。
- **正確な文脈理解**: Clang AST 解析に基づき、マクロ展開後や型解決済みのシンボル情報をプログラム的に取得します。
- **ノイズの除去**: コールグラフ生成時、標準ライブラリなどの外部依存をフィルタリングし、純粋なプロジェクトロジックのみを可視化します。

## 2. 環境・前提条件 (Prerequisites)

- **Docker コンテナ (推奨)**: Clang AST 解析、`cflow` によるグラフ生成にはコンテナ環境が必須です。
- **WSL2 Bash**: Windows ホスト環境で実行する場合は、PowerShell から `bash` と入力して WSL2 シェルを使用してください。

## 3. 使用方法 (Usage)

### 統合実行 (推奨: コンテナ経由)

```bash
# ファイルまたはディレクトリのシンボル要約 (Summary)
bash .agent/skills/general_docker_run/scripts/docker-explore-codebase.sh summary src/

# Clang AST 解析 (JSON 出力)
# -Iinc など、ホスト側の相対パスをそのまま渡せます。
bash .agent/skills/general_docker_run/scripts/docker-explore-codebase.sh ast src/main.cxx --json -Iinc

# 関数の呼び出し元検索 (Callers)
bash .agent/skills/general_docker_run/scripts/docker-explore-codebase.sh callers "vmmio_read" --depth 2 --search-dir src/

# ノイズ除去済みコールグラフの生成 (Graph)
bash .agent/skills/general_docker_run/scripts/docker-explore-codebase.sh graph src/main.cxx --search-dir src/

# 統合レポートの生成
bash .agent/skills/general_docker_run/scripts/docker-explore-codebase.sh report src/main.cxx -Iinc --search-dir src/
```

### パイプ連携 (Batch Processing)

```bash
# src 内の全ファイルを 5 件まで要約表示
find src -name "*.cxx" | head -n 5 | xargs -I {} bash .agent/skills/general_docker_run/scripts/docker-explore-codebase.sh summary {}
```

## 4. 構成要素の詳細 (Component Details)

### scripts/
- **[explorer.py](file:///w:/mysrc/fireball/.agent/skills/general_codebase_explore/scripts/explorer.py)**: 解析エンジンのコア。Clang Python Bindings を使用して AST を処理。
- **[explore-codebase.sh](file:///w:/mysrc/fireball/.agent/skills/general_codebase_explore/scripts/explore-codebase.sh)**: ホスト（WSL2）側から呼び出すためのメインエントリポイント。

### サブコマンド
- `summary`: ファイル/ディレクトリの概要抽出。
- `ast`: AST 情報のダンプ。
- `graph`: `cflow` 連携によるコールグラフ。
- `symbols`: ユニークなシンボルリストの取得。

## 5. 品質・検証ルール (Quality & Validation)

- **情報の密度**: 解析結果から自明なコメントや標準ライブラリ由来のシンボルを極力排除し、情報の密度を高めて出力します。
- **絶対パスの禁止**: 出力されるレポートやパスリストは常にプロジェクトルートからの相対パスとして正規化されます。

## 6. トラブルシューティング (Troubleshooting)

**Windows シェルでの誤作動**:
コマンドプロンプトや PowerShell では、`find` の挙動差異やクオーティング問題が発生します。必ず **WSL2 Bash** を使用してください。

**AST 解析でエラーが発生する**:
インクルードパスが不足しています。`-I` オプション（例: `-Iinc`）を使用して、正しいパスをツールに渡してください。
