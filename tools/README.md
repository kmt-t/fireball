# Fireball Tools

`tools/` は Fireball の文書監査と補助スクリプトの入口です。
細かいコマンドは覚えず、ここから各 shell スクリプトに入ります。

## Entry Points

- `./tools/run_all_tests.sh`: ドキュメント監査の統合実行（`--llm` で semantic、`--quick` で Tier 1 まで）
- `./verify/run_all.sh`: 形式検証の一括実行
- `./tools/run_consistency_check.sh`: 仕様整合性監査
- `./tools/run_traceability_audit.sh`: トレーサビリティ監査

## Documentation

- `tools/docs/README.md`: tools 配下の説明書き索引
- `verify/README.md`: 形式検証ワークスペースの索引

## Layout

- `tools/common/`: 監査処理の共通モジュール
- `tools/scripts/`: Python 入口スクリプト
- `tools/mechanical/`: 機械的チェック
- `tools/llm/`: LLM 監査
- `tools/config/`: 監査用設定データ
- `tools/docs/`: 各監査の説明文

## Policy

- 実行は shell スクリプトを使う。
- 細かい `python3` や `tlc` の引数は、ここではなく各スクリプトに閉じ込める。
