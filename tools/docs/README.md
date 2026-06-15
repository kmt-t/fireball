# Tools Documentation Index

このディレクトリは `tools/` 配下の個別説明をまとめた索引です。
実行は shell スクリプトを使い、細かいコマンドは覚えなくてよいようにします。

この配下の文書はツールの説明であり、判定基準の正本は `.claude/rules/` にあります。

## Upstream Rules

- `.claude/rules/development-policy.md`
- `.claude/rules/documentation.md`
- `.claude/rules/documentation_format.md`

## Documents

- `audit_traceability.md`: トレーサビリティ監査の説明
- `check_consistency.md`: 仕様整合性チェックの説明
- `test_doc.md`: LLM ドキュメント監査の説明

## Related Entry Points

- `./tools/run_all_tests.sh`（`--llm` で semantic、`--quick` で Tier 1 まで）
- `./tools/run_consistency_check.sh`
- `./tools/run_traceability_audit.sh`
- `./verify/run_all.sh`
