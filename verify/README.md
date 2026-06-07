# Verification Workspace

`verify/` は Fireball の形式検証資産の正本です。
ここには TLA+ モデル、TLC 設定、検証レポート、実行用 shell スクリプトを置きます。

## Layout

- `models/`: TLA+ モデル
- `configs/`: TLC 設定
- `reports/`: 検証レポート
- `run_*.sh`: 各検証の実行用 shell スクリプト

## Entry Points

- `./verify/run_all.sh`: すべての検証を順に実行
- `./verify/run_eventdriven_coos.sh`: COOS 3-state 検証
- `./verify/run_ipc_deadlock.sh`: IPC デッドロック検証
- `./verify/run_loader_rollback.sh`: Loader ロールバック検証
- `./verify/run_vmmio.sh`: vMMIO 検証

## Notes

- 実行は repo ルートから行う。
- 細かい `tlc` コマンドは覚えなくてよい。上記の shell スクリプトを使う。
