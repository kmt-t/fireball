---
name: WebAssembly Spec Reader
description: Local WebAssembly Core Specification (.rst files) を読み解き、設計上の疑問を解決するスキル
---

# WebAssembly Spec Reader スキル

このスキルは、プロジェクト内にサブモジュールとして配置された WebAssembly Core Specification を参照し、正確な仕様に基づいた設計・実装を支援します。

## 1. 仕様書の場所

- **Base Path**: `docs/orders/references/webassembly/document/core/`

## 2. ディレクトリ構造と主な内容

| ディレクトリ | 内容 | 活用シーン |
|:---|:---|:---|
| `syntax/` | 抽象構文（Types, Instructions, Modules） | 命令セットや型の定義を確認したい時 |
| `binary/` | バイナリフォーマットの定義 | デコーダの実装やバイナリ構造の確認 |
| `text/` | テキストフォーマット（WAT）の定義 | パーサの実装やWATの書き方の確認 |
| `valid/` | バリデーションルール | モジュールの妥当性チェックのロジック確認 |
| `exec/` | 実行時の振る舞い（Runtime, Instructions） | ストア、スタック、各種命令の動作詳細の確認 |
| `appendix/` | 付録（演算の定義、索引など） | 数値演算の厳密な定義などを確認したい時 |

## 3. 推奨される調査手順

1. **キーワード検索**: `grep_search` を用いて、調査対象（例: `i32.add`, `call_indirect`, `memory.grow`）に関連するファイルを特定する。
2. **抽象構文の確認**: `syntax/` 下のファイルで、対象の構文上の定義を確認する。
3. **実行セマンティクスの確認**: `exec/` 下のファイルで、実行時のスタック操作や状態遷移を確認する。

## 4. 注意事項

- 本ドキュメントは `.rst` (reStructuredText) 形式で記述されています。Sphinxなどのツールでレンダリングされた状態を想定して読み解いてください。
- 数式や定義は数学的な記法で書かれていることが多いため、前後の文脈から意味を正確に把握してください。
- 仕様に迷った場合は、必ず `exec/` 下の定義を最終的な正解として参照してください。
