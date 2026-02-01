# プロトコルと配置パス

## 情報のたどり方

※ ファイルパスのルートディレクトリはワーススペースのルートです。

1. **必読**: 開始時に @docs/order/SUMMARY.md (存在しない場合は @docs/oders/architecture/overview.md ) および @docs/oders/requires/list.md を読むこと。 @docs/gen/summary.md は更新されているとは限らないが、参考にとどめる。
2. **参照**: 外部仕様および技術の詳細は @docs/oders/REFERENCES.md を参照すること。
3. **規約**: `.agent/rules/coding_style.md` を厳守すること。
4. **範囲**: `docs/oders/**/**.md`、`inc/**/*.hxx`、`src/**/*.cxx`のみ参照すること。

## ドキュメントの配置

参照するドキュメントは下記の4階層とする。

(各フォルダに `FORMAT.md` が配置されているのでドキュメントをメンテナンスするときには参考にしてほしい)

1. 要求（`docs/oders/requires/*.md`）
2. 準拠する項目（`docs/oders/items/*.md`）
3. アーキテクチャ（`docs/oders/architecture/*.md`）
4. コンポーネント（`docs/oders/components/*.md`）
5. パターン（`docs/oders/patterns/*.md`）
6. 設計コンセプト（`docs/oders/concept/*.md`）

ドキュメントを参照し作業をする前に下記のリストに従いチェックし、フィードバックせよ。

1. 仕様のトレーサビリティマトリクスを作成する
2. 仕様の単語、キーワードの表記揺れをチェックする。
3. 複数のコンポーネントの仕様に参照ではなく重複した記述があればユーザに指摘する。
4. 未定義、紐づけがなされていない部分の修正案をユーザに提示する。
5. 表記ゆれのリストをユーザに提示する。

参照するドキュメントを元に生成するドキュメントは下記のフォルダに出力する。元のファイルは変更しないこと。具体的なコードではなく高レイヤーの仕様としてまとめること。

1. バックログ（`docs/backlog/`）
2. 要求（`docs/gen/requires/`）
3. アーキテクチャ（`docs/gen/architecture/`）
4. コンポーネント（`docs/gen/components/`）
5. パターン（`docs/gen/patterns/`）
6. コンセプトコード（`docs/gen/concept/`）
7. 直行表・単語・キーワードトレーサビリティチェック（`docs/gen/trace/`）
