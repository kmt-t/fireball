# インタープリタ設計バックログ

## 概要

インタープリタ設計で未確定の仕様・依存関係を整理し、後続の詳細設計で解決する項目を管理する。

## 未確定項目

1. デバッガ状態の粒度と定義
   - `debug_state_` / `debug_flags_` の具体的な状態遷移とビット定義。
   - 参照: `{DebuggerLabelTableSwitch}` [`docs/order/components/runtime.md`](docs/order/components/runtime.md)

2. タイムソースの定義
   - `slice_start_time_` の取得元（HALタイマ or COOSの時刻管理）と精度。
   - 参照: `{YieldOnTimeLimit}` [`docs/order/components/runtime.md`](docs/order/components/runtime.md)

3. 割り込み要因の型と定義元
   - `interrupt_cause_` をHALで定義するかランタイム側で定義するか。
   - 参照: `{InterpreterContextInterruptManagement}` [`docs/order/architecture/overview.md`](docs/order/architecture/overview.md)

4. モジュールメタ情報の具体構造
   - `func_table_` / `type_table_` / `global_table_` / `export_table_` / `import_table_` の構造。
   - 参照: wasm32実行モデル（詳細仕様化が必要）

5. 実行状態とトラップ種別の定義
   - `exec_state_` と `last_trap_` の列挙値、状態遷移、リカバリ方針。
   - 参照: wasm32実行モデル（詳細仕様化が必要）

6. 命令実行時の境界チェック方針
   - load/store の境界外アクセス、`memory.grow` の失敗時の挙動。
   - 参照: wasm32実行モデル（詳細仕様化が必要）
