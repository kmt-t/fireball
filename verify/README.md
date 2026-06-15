# Verification Workspace

`verify/` は Fireball の形式検証資産の正本です。
ここには TLA+ モデル、TLC 設定、検証レポート、実行用 shell スクリプトを置きます。

## Layout

- `models/`: TLA+ モデル
- `configs/`: TLC 設定
- `reports/`: 検証レポート
- `components.sh`: 検証コンポーネント名の解決ルール
- `run_component.sh`: 単一コンポーネントを実行する共通ランナー
- `run_*.sh`: 各検証の実行用 shell スクリプト

## Entry Points

- `./verify/run_all.sh`: すべての検証をコンポーネント順に実行
- `./verify/run_all.sh list`: サポート対象コンポーネント一覧を表示
- `./verify/run_all.sh <component-name>`: 単一コンポーネントを名前規則で実行
- `./verify/run_component.sh <component-name>`: 名前規則に従って単一コンポーネントを実行
- `./verify/run_eventdriven_coos.sh`: COOS 3-state 検証
- `./verify/run_ipc_deadlock.sh`: IPC デッドロック検証
- `./verify/run_loader_rollback.sh`: Loader ロールバック検証
- `./verify/run_vmmio.sh`: vMMIO 検証

## Notes

- 実行は repo ルートから行う。
- 細かい `tlc` コマンドは覚えなくてよい。上記の shell スクリプトを使う。

## Naming Rule

`verify/run_component.sh` は、引数で受けたコンポーネント名を正規化して `verify/models/`、`verify/configs/`、`verify/reports/` の basename から対応ファイルを探す。
正規化は「小文字化して `_` と `-` を除去する」だけに絞る。

新しい検証を追加する場合は、コンポーネント名が basename に含まれるように置く。

例:

- `ipc_deadlock` -> `IPCDeadlockVerification.tla`, `IPCDeadlockVerification.cfg`, `IPC_DEADLOCK_VERIFICATION_REPORT.md`
- `eventdriven_coos` -> `EventDrivenCOOS_ThreeState.tla`, `EventDrivenCOOS_ThreeState.cfg`, `EVENTDRIVEN_COOS_VERIFICATION_REPORT.md`
