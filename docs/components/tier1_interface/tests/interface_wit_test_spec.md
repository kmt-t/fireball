# WITインターフェース / リカバリー戦略 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`interface_wit.md`](docs/components/tier1_interface/interface_wit.md)
参考実装: なし（WIT定義そのものはコンセプトコードを持たない。`recovery-strategy-category` の実験的実装はバージョン管理外の `experiments/pysim` ディレクトリに置かれているが、本書の検証対象外である）。

`recovery-strategy-category`（ignore/retry/restart/panic）、低レベルトラップインターフェース（`fireball-call`）、コンソール生バイト出力経路に関する契約を検証する。個別デバイスのIPCコマンドID実装（GPIO/タイマー/バス等）は [`hal_dispatch_test_spec.md`](docs/components/tier2_runtime/tests/hal_dispatch_test_spec.md) / [`platform_driver_test_spec.md`](docs/components/tier3_platform/tests/platform_driver_test_spec.md) の責務とする。

## 2. テストケース一覧

### リカバリー戦略

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WIT-01 | `ignore`の選択基準 | 一時的なバッファ空/満杯通知など、データ喪失を伴わない事象 | 該当操作を発生させる | `ignore`が返り、状態変化なく呼び出し元が継続する | 表 |
| TEST-WIT-02 | `retry`の選択基準とバックオフ | 一時的なリソース競合・タイムアウト | 失敗操作を発生させ、リトライさせる | `FB_CONF_RETRY_BACKOFF_MS`（既定10ms）待機後に再試行し、再試行上限3回を超えない | system_config.md |
| TEST-WIT-03 | `retry`上限到達後の挙動 | 3回連続失敗 | 4回目の判定 | 何らかの明示的戦略（`{META_RecoveryStrategy}`の「呼び出し元は常にアクションを得る」原則により、無限リトライにも無戦略にもならない）にエスカレーションする |  README「retry-exhaustion escalation」で`RESTART`へのエスカレーションを独自解釈として採用（参照） |
| TEST-WIT-04 | `restart`の選択基準 | サービスコンテキスト/メモリ破損の疑い | 該当操作を発生させる | 該当タスク/サービスのTCB・ヒープが初期化され再起動する。他サービス・カーネルのメモリ空間は隔離される | 表 |
| TEST-WIT-05 | `panic`の選択基準 | MPU違反・二重解放・デッドロック検知 | 該当操作を発生させる | 全タスク停止、クラッシュダンプ出力、フェイルセーフ停止 | 表 |

### 低レベル・トラップインターフェース

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WIT-10 | `fireball-call`のkebab-case→snake_caseマッピング | - | C++バインディング生成物を確認 | `fireball_call`として公開される | {WIT_Interface_Spec} |
| TEST-WIT-11 | Trigger(GPIO)の直接マッピング | `FB_SYSCALL_TRIGGER_SET_PIN`等 | `fireball_call`に直接該当IDを渡す | ハンドルルックアップを経由せず直接操作される | {WIT_Interface_Spec} |

### コンソール生バイト出力経路 (`fireball://service/stdout/0`)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WIT-20 | 任意長生バイト列の出力 | ゲストが`print`/`eprint`相当を実行 | `resolver.get-interface("fireball://service/stdout/0")`、`acquire-buffer`、`stream-write`を順に利用 | データがそのまま`HAL_Transport`へ渡される（辞書変換もリングバッファ構造化もされない） | `libfireball_test_spec.md` |
| TEST-WIT-21 | 内部ロガーとの排他性なし（インターリーブ許容） | 内部ロガーのflushとコンソール出力経路の書き込みが同時期に発生 | 両方を実行 | 出力順序の保証はされない（インターリーブし得る）ことを仕様として確認する（バグではない） | 末尾 |
| TEST-WIT-22 | WASI_FD_WRITE→コンソール出力経路への自動ルーティング | ゲストの`print`/`eprint` | `libfireball` が `fireball_call(WASI_FD_WRITE,...)`を発行 | `fireball://service/stdout/0`を解決し、HALの`stream-write`へ変換される | `libfireball_test_spec.md` |
| TEST-WIT-23 | WASI親和性のあるHAL汎用操作 | `resolver` が公開されている | `stream-read/write`、`stream-flush/close`、`clock-get-now/resolution`、`poll-check/wait`の型を確認 | 個別デバイスresource型なしに、同じハンドル境界で操作できる | `hal_dispatch.md` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- 個別デバイス（GPIO/タイマー/バス/ストリーム）のIPCコマンドID実装は [`hal_dispatch_test_spec.md`](docs/components/tier2_runtime/tests/hal_dispatch_test_spec.md) / [`platform_driver_test_spec.md`](docs/components/tier3_platform/tests/platform_driver_test_spec.md) を参照。
- `input-stream`/`output-stream`の詳細な非同期セマンティクス（wasi:io標準への準拠度）。
- `wasi:filesystem`のPASSTHROUGH/SHM「事前オープン済み仮想ファイル記述子」エミュレーション（-4）。
