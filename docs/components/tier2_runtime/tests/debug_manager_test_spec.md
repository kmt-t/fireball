# Debug Manager テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier2_runtime/debug/debug_manager.md`
関連正本: `docs/specs/gdb_rsp_protocol.md`（未読。RSPコマンド/レジスタ番号マッピングの正本、要別途確認）
参考実装: `docs/components/tier2_runtime/concepts/debugger_concept.py`（**未読**）

GDB RSPコマンド処理、ブレークポイント管理、ハンドラテーブル切り替え（デバッグ有効時のみオーバーヘッドを持つ設計）、JITキャッシュとの協調（Debugger_Jit_Flush）、プロファイラ機能を検証する。

## 2. テストケース一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| DBG-01 | ブレークポイント判定はflat_set_view | 複数ブレークポイント設定済み | `contains(pc)`相当 | O(log n)二分探索で判定される（線形スキャンではない） | §3.3「ブレークポイントリスト」, system_containers.md |
| DBG-02 | デバッグ無効時はゼロオーバーヘッド | デバッガ未接続 | 通常実行 | インタープリタのハンドラテーブルが切り替わらず、通常速度を維持 | §6.1 `{DebuggerLabelTableSwitch}` |
| DBG-03 | アタッチ時のハンドラテーブル切り替え | デバッガattach | 実行 | インタープリタが`debug_handler_table`に切り替わり、1命令ずつ実行制御される | §4.1 手順3 |
| DBG-04 | ステップ実行 | Stopped状態 | `step_instruction()` | ちょうど1命令実行後、再びStopped(SIGTRAP)になる | §4.1 手順4, §5.1 step_instruction |
| DBG-05 | ブレークポイントヒットで停止 | ブレークポイント設定済みPCに到達 | 実行継続 | Running→Stoppedに遷移 | §4.2状態遷移図 |
| DBG-06 | メモリ書き換え時のみJITキャッシュFlush | デバッガがメモリを書き換える | 書き換え実行 | 該当タスクのJITキャッシュ(Active/Warm/Oldest全バンク)が無効化される。**アタッチ中常時ではない**（ステップ実行だけではFlushしない） | §1「JITキャッシュの無効化はアタッチ中常時ではなく...メモリを書き換えた場合にのみ発生」`{Debugger_Jit_Flush}` |
| DBG-07 | HAL層とのフレーミング責務分離 | RSPパケット受信 | パケット処理 | `$`〜`#`のフレーミング・チェックサム検証はHAL層が行い、Debugger自身はコマンド構文解析のみ行う | §1, §3.1 RspParser |
| DBG-08 | プロファイラのPCサンプリング | 実行中 | プロファイラ有効化 | 実行中PCが記録され、頻度統計として外部ツールへ出力可能 | §4.1 手順5 `{Debug_Integrated}` |
| DBG-09 | デバッグメモリアクセスの境界チェック強制 | `m addr,len`コマンド | 範囲外addrを指定 | WASMリニアメモリの境界チェックが強制される | §6.3 `{MemoryBoundaryCheck}` |
| DBG-10 | 仮想レジスタ番号マッピング | - | `g`（全レジスタ読み出し）コマンド | `0:pc, 1:sp, 2:fp, 3:tos, 4..19:local0..15`の順で返す | §3.3「仮想レジスタセット」, gdb_rsp_protocol.md §4(未読、要確認) |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- `docs/components/tier2_runtime/concepts/debugger_concept.py`（未読。読了後この仕様書を更新すること）。
- `docs/specs/gdb_rsp_protocol.md`（未読。RSPパケット物理仕様の正本、要確認）。
- VSCode/J-Link実機での接続そのもの。
