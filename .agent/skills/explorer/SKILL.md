---
name: Codebase Explorer
description: インタラクティブにコードベースを探索し、構造把握、シンボル要約、文脈検索（Imakita）を行う統合ツール。
WHEN: 構造把握、関数追跡、シンボル一覧取得、キーワード文脈理解（今北産業）が必要な時
SCOPE: プロジェクト全域
RELATED: friction_audit, docker_workaround
---

# Codebase Explorer スキル

## 1. 概要 (Overview)

`explorer.py` は、ディレクトリのナビゲーション、ファイルの要約、および関数間の依存関係（呼び出し元・呼び出し先）の探索を統合したインタラクティブなCLIツールです。
**本スキルはコンテナ内での実行を前提としており、`clang` による高度な解析を提供します。**

詳細な使用方法は [USAGE.md](file:///n:/sources/fireball/.agent/skills/explorer/USAGE.md) を参照してください。

## 2. 環境・前提条件

本スキルの実行には **Dockerコンテナ** の使用を強く推奨します。

- **Docker Workaround**: 詳細は [Docker Workaround](../docker_workaround/SKILL.md) を参照してください。
- **Windowsユーザー**: お使いの環境で直接実行するのではなく、**Git Bash** を経由してスクリプトを実行してください。

## 3. 使用方法 (Usage)

### インタラクティブモード (Recommended)

`docker-explorer.sh` を引数なしで実行するとインタラクティブモードに入ります。

```bash
bash .agent/skills/docker_workaround/scripts/docker-explorer.sh
```

- **番号入力**: ディレクトリへの移動、またはファイルの選択。
- **`..`**: 上位ディレクトリへ戻る。
- **`q`**: 終了。

### CLIツール (One-Shot / Pipe)

パイプライン連携や一括処理には `docker-explorer.sh` に引数を渡します。

```bash
# ファイル要約（JSON出力オプションあり）
bash .agent/skills/docker_workaround/scripts/docker-explorer.sh summary docs/README.md

# パイプライン連携（ソースコード一括解析）
# パイプライン連携（ソースコード一括解析）
bash .agent/skills/docker_workaround/scripts/docker-cmd.sh find src -name "*.cxx" | bash .agent/skills/docker_workaround/scripts/docker-explorer.sh pipe summary
```

## 4. 機能詳細

### Summarize / 項目要約
ファイル内のヘッダやシンボル（構造体・関数・引数）を一覧表示します。

### 3-line Summary / 今北産業
キーワードの文脈を検索し、3行要約（定義・用途・設計意図）を生成します。

### Search Callers/Callees / 依存関係列挙
関数の呼び出し元・先をプロジェクト全域から再帰的に追跡します。

## 5. 利点 (Benefits)

- **トークン節約**: 巨大なファイルを全部読まずに、必要なシンボルやレイアウト情報だけを抽出できます。
- **クロスプラットフォーム**: Dockerコンテナ内で実行されるため、ホストOSに依存せず `clang` 解析が可能です。
- **トレーサビリティ連携**: ファイル内の `{Keyword}` を自動検出し、要求仕様との紐付けを可視化します。
`explorer.py` は、ディレクトリのナビゲーション、ファイルの要約、および関数間の依存関係（呼び出し元・呼び出し先）の探索を統合したインタラクティブなCLIツールです。
**本スキルはコンテナ内での実行を前提としており、`clang` による高度な解析を提供します。**

詳細な使用方法は [USAGE.md](file:///n:/sources/fireball/.agent/skills/explorer/USAGE.md) を参照してください。

## 2. 使用方法 (Usage)

### インタラクティブモード (Recommended: Inside Container)

```bash
python3 .agent/skills/explorer/scripts/explorer.py
```

- **番号入力**: ディレクトリへの移動、またはファイルの選択。
- **`..`**: 上位ディレクトリへ戻る。
- **`q`**: 終了。

### ファイル操作メニュー
1.  **Summarize**: ファイル内のヘッダやシンボル（構造体・関数・引数）を一覧表示します。
2.  **3-line Summary**: キーワードの文脈を検索し、3行要約（定義・用途・設計意図）を生成します。
3.  **Search Callers/Callees**: 関数の呼び出し元・先をプロジェクト全域から再帰的に追跡します。
4.  **AST Struct Dump**: 構造体の正確なメモリレイアウト（オフセット・型）を抽出します。

### CLIツール（パイプ対応）

```bash
# シンボル要約 (ASTベース、インクルードパス指定可能)
./.agent/skills/explorer/scripts/explorer-cli summary <ソースファイル> [-I <パス> ...]

# パイプライン連携
ls src/*.cxx | ./.agent/skills/explorer/scripts/explorer-cli pipe summary
```

## 3. 環境・実行 (Environment)

- **推奨**: VSCode DevContainer または Git Bash (Windows)。
- **コンテナ実行**: ホスト環境が整っていない場合（`clang`がない、Windows等）は、**[Docker Workaround](../docker_workaround/SKILL.md)** を参照してください。
    - `explorer-cli` 用のコンテナラッパー `docker-explorer.sh` が利用可能です。

## 4. 利点 (Benefits)
- **トークン節約**: 巨大なファイルを全部読まずに、必要なシンボルやレイアウト情報だけを抽出できます。
- **クロスプラットフォーム**: Windows上でもPythonネイティブ検索により `grep` なしで呼び出し元特定が可能です。
- **トレーサビリティ連携**: ファイル内の `{Keyword}` を自動検出し、要求仕様との紐付けを可視化します。
