# Fireball Explorer Usage Manual

`explorer` スキルは、情報の海に溺れず、設計の真髄のみを脳にロードするための解析スイートです。

## 1. なぜこのスキルを使うのか

- **ワーキングメモリの保護**: 1000行のソースコードを読むコストを、10行の「重要シンボル」への凝縮に変換します。
- **解析手数の削減 (High Leverage)**: 手動でファイルを開く手間を、Pipe連携による一括解析に置き換えます。
- **コンテキストの即時同期**: ドキュメントを検索する時間を、`context` コマンドによる「3行要約」の即時ロードに短縮します。

**Note for Windows Users**:
Windows環境では、**Git Bash** (`C:\Program Files\Git\git-bash.exe`) を使用してください。PowerShellでは各コマンドが正しく動作しない場合があります。

---

## 2. 実行例とレバレッジ

### docker-explorer.sh
ホスト環境の制約を無視し、`clang` による深層解析を 1 コマンドで実行します。

```bash
# ドキュメントの「エッセンス」のみを抽出（機械可読なJSON）
# メリット: 人間が全読せずとも、エージェントが情報の不整合を即座に計算できる
bash .agent/skills/docker_workaround/scripts/docker-explorer.sh summary docs/components/jit.md --json

# 修正の影響を受けるソース群を一括要約 (High Leverage)
# メリット: ファイルを一つずつ開く手間を 0 にし、全体像を数秒で把握する
# Linux/Mac/Windows(Git Bash):
find src -name "*.cxx" | bash .agent/skills/docker_workaround/scripts/docker-explorer.sh pipe summary

# ソースコードの一括スキャン (Docker / Git Bash)
# プロジェクト内の全C++ファイルを検索してシンボル（関数・型）を抽出
bash .agent/skills/docker_workaround/scripts/docker-cmd.sh find src -name "*.cxx" | bash .agent/skills/docker_workaround/scripts/docker-explorer.sh pipe summary
```

---

## 3. 主要コマンドの提供価値

- **`summary <file>`**: コードを「読む」のではなく「俯瞰」する（シンボル抽出）。
- **`context <keyword>`**: 記憶の外部化。設計キーワードの意図を 3 行で再ロード。
- **`tree <dir>`**: ディレクトリ構造のノイズを除去し、トポロジーのみを把握。
