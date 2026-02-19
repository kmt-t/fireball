# Script Documentation Format (SCRIPTS.md)

本ドキュメントは、各スキルの `scripts/` ディレクトリ内に配置する `README.md` の標準的な構成を定義します。

## 1. 必須セクション構成

各プログラム/スクリプトのドキュメントは、以下の項目を網羅しなければなりません。

### 1.1 役割と数学的性質 (Role & Axioms)
- **目的**: 何を解決するツールか。
- **不変条件 (Invariants)**: 実行中および実行後に維持される論理性（例: 「常に有効な C++ シンボル名のみを出力する」）。
- **影響範囲 (Side Effects)**: ファイル生成、環境変数の変更、外部通信など。

### 1.2 インターフェース (Full-Spec CLI & Interface)
- **フルスペック宣言**: 全ての引数、フラグ、オプションを網羅すること。デバッグ用の「隠しフラグ」であっても、副作用や挙動が定義されているものは記述せよ。
- **使用法 (Usage)**: `python3 example.py [options] <args>`。
- **引数 (Arguments)**: 各引数の意味、必須/任意、および期待されるデータ型（型語彙に準拠）。
- **オプション (Options)**: 各フラグの効果。特に、他のフラグとの排他制御や依存関係がある場合は「制約条件」として明記せよ。


### 1.3 パイプ・合成可能性 (Composition & Pipe)
[全体設計ルール](file:///w:/mysrc/fireball/docs/architecture/general_design_rule.md) の「汎用性と合成可能性」に基づき、以下の挙動を明記します。
- **標準入力 (STDIN)**: パイプ経由で渡せるデータ（例: ファイルパスのリスト）。
- **標準出力 (STDOUT)**: 次のツールに渡せる形式（JSON, Plain text, File list等）。
- **xargs対応**: `find ... | xargs python3 script.py` という形式での動作可否。

### 1.4 入出力データ構造 (Data Schema)
- **入力形式**: JSON スキーマ、WIT 定義、または期待されるテキスト形式。
- **出力形式**: 生成されるファイル構造や、返される JSON の構造。

### 1.5 エラーコードとリカバリ (Errors & Recovery)
- **終了コード (Exit Codes)**: 0 (Success), 1 (Logic Error), 2 (IO Error) 等の意味。
- **エラー出力**: stderr に出力されるメッセージの様式。

### 1.6 実行例 (Examples)
- **基本操作**: 最も一般的なユースケース。
- **パイプ連携**: 前後のツールと組み合わせた高度な例。

---

## 2. 記述スタイルガイドライン

- **再帰的説明の回避**: スクリプトが何をしているかを説明する際、スクリプト名そのものを動詞として使わず、具体的に記述せよ。
- **再現性の保証**: 書かれている例をそのままコピー＆ペーストして動作することを確認する。
- **依存関係**: 外部ライブラリ（pip等）が必要な場合は、冒頭に明記する。

---

## 3. 使用方法（Usage）の記述サンプル

スクリプトの `README.md` に記載すべき「使用方法」の具体例です。単一の実行例だけでなく、プロジェクトの「合成可能性（Composition）」を活かす例を必ず含めてください。

### パターンA: 単一ファイル・ディレクトリの処理
最も標準的な起動方法。

```bash
# 特定のファイルを監査
python3 scripts/audit_file.py src/main.cxx

# ディレクトリ以下の全ファイルを再帰的に監査
python3 scripts/audit_file.py src/inc/
```

### パターンB: 標準入力（パイプ）経由のファイルリスト処理
大量のファイルを外部（find や git 等）から供給する場合。

```bash
# git で変更された C++ ファイルのみを抽出して監査
git ls-files -m | grep "\.cxx$" | python3 scripts/audit_file.py

# 特定のキーワードを含むファイルのみを抽出して監査
grep -l "TODO" src/*.cxx | python3 scripts/audit_file.py
```

### パターンC: xargs を用いた並列・バッチ処理
スクリプトが引数としてパスリストを受け取れる場合の効率的な実行例。

```bash
# find で抽出したファイルを xargs 経由で一括処理
find inc -name "*.hxx" | xargs python3 scripts/audit_file.py --strict
```

### パターンD: JSON 出力のパイプ連携（後続ツールへの接続）
ツール間でデータを構造化して受け渡す例。

```bash
# JSON 形式で出力し、jq でエラー数のみをカウント
python3 scripts/audit_file.py src/ --format json | jq '.summary.errors'

# エラーがあるファイルのみを抽出して自動修正スクリプトに渡す
python3 scripts/audit_file.py src/ --format json | jq -r '.violations[].file' | sort -u | xargs python3 scripts/fix_code.py
```

---

## 4. ドキュメント・テンプレート (README.md Template)

新しくスクリプトを作成した際は、以下の内容をコピーして `scripts/README.md` を作成してください。

```markdown
# [Script Name]

[Role & Axioms: スクリプトの目的と論理的な不変条件を1-2行で記述]

## Full-Spec Interface
[全フラグ・引数の完全なリスト。隠しフラグやデバッグオプションも含む]

```bash
python3 scripts/[script_name].py --help
```

## Composition (Pipe)
[標準入出力の形式と、パイプによる連携例を記述]

## Schema
[JSON 等のデータ構造がある場合に記述]

## Recovery
[エラー時の挙動とリトライ方法]
```
