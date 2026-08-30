# WITインターフェイス / リカバリー戦略 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier1_interface/interface_wit.md`
参考実装: なし（WIT定義そのものはコンセプトコードを持たない。`recovery.py`は`experiments/pysim`独自の解釈実装）
現行実装: `experiments/pysim/recovery.py`, `experiments/pysim/logger.py`(ConsoleOutput), `experiments/pysim/system.py`(fireball_call経由のWASI呼び出し)

`recovery-strategy-category`（ignore/retry/restart/panic）、低レベルトラップインターフェイス（`fireball-call`）、`console-output`（生バイト出力）に関する契約を検証する。

## 2. テストケース一覧

### リカバリー戦略 (§3.2)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| WIT-01 | `ignore`の選択基準 | 一時的なバッファ空/満杯通知など、データ喪失を伴わない事象 | 該当操作を発生させる | `ignore`が返り、状態変化なく呼び出し元が継続する | §3.2 表 |
| WIT-02 | `retry`の選択基準とバックオフ | 一時的なリソース競合・タイムアウト | 失敗操作を発生させ、リトライさせる | `FB_CONF_RETRY_BACKOFF_MS`（既定10ms）待機後に再試行し、再試行上限3回を超えない | §3.2, system_config.md §3.3.7 |
| WIT-03 | `retry`上限到達後の挙動 | 3回連続失敗 | 4回目の判定 | 何らかの明示的戦略（`{META_RecoveryStrategy}`の「呼び出し元は常にアクションを得る」原則により、無限リトライにも無戦略にもならない）にエスカレーションする | pysim README「retry-exhaustion escalation」で`RESTART`へのエスカレーションを独自解釈として採用（§3参照） |
| WIT-04 | `restart`の選択基準 | サービスコンテキスト/メモリ破損の疑い | 該当操作を発生させる | 該当タスク/サービスのTCB・ヒープが初期化され再起動する。他サービス・カーネルのメモリ空間は隔離される | §3.2 表 |
| WIT-05 | `panic`の選択基準 | MPU違反・二重解放・デッドロック検知 | 該当操作を発生させる | 全タスク停止、クラッシュダンプ出力、フェイルセーフ停止 | §3.2 表 |
| WIT-06 | IPCキュー満杯の分類 | ipc_router.mdのQueue-Full（Rollback） | キュー満杯状態でsend | `retry`に分類される（`ignore`ではない。データが実質失われるため） | pysim README「ignore-vs-retry ambiguity」を「retry」に解決した独自判断 |

### 低レベル・トラップインターフェイス (§4)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| WIT-10 | `fireball-call`のkebab-case→snake_caseマッピング | - | C++バインディング生成物を確認 | `fireball_call`として公開される | §4.1 |
| WIT-11 | Trigger(GPIO)の直接マッピング | `FB_SYSCALL_TRIGGER_SET_PIN`等 | `fireball_call`に直接該当IDを渡す | ハンドルルックアップを経由せず直接操作される | §4.2 |

### `console-output` (§5.5)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| WIT-20 | 任意長生バイト列の出力 | ゲストが`print`/`eprint`相当を実行 | `console-output.write(data)`を呼ぶ | `data`がそのまま`HAL_Transport`へ渡される（辞書変換もリングバッファ構造化もされない） | §5.5 |
| WIT-21 | 内部ロガーとの排他性なし（インターリーブ許容） | 内部ロガーのflushとconsole-outputのwriteが同時期に発生 | 両方を実行 | 出力順序の保証はされない（インターリーブし得る）ことを仕様として確認する（バグではない） | §5.5 末尾 |
| WIT-22 | WASI_FD_WRITE→console-outputの自動ルーティング | ゲストの`print`/`eprint` | `fireball_call(WASI_FD_WRITE,...)`を発行 | 自動的に`console-output.write`にルーティングされる | §5.6-3 |

### HALインターフェイス (§5.1, 5.3, 5.4)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| WIT-30 | `periodic-timer.get-now`の単位 | - | 呼び出す | ナノ秒単位のu64を返す | §5.1 |
| WIT-31 | `bus-master.transfer-data`はSHMハンドルのみ受理 | ゲストのリニアメモリポインタを渡そうとする | `shm-slice`型でない値を渡す | 型として受理されない（ゲストのリニアメモリを指すポインタを直接渡す経路が存在しない） | §5.3「ゲストのリニアメモリ上のポインタを直接渡すことはできない」 |
| WIT-32 | `bus-slave.get-received`の返却バイト数 | 送信側からのデータがある | `get_received(dest)`を呼ぶ | 実際に転送したバイト数を返す | §5.3 |

## 3. 現状のギャップ（pysim実装との差分）

- `recovery.py`の`classify_ipc_enqueue_failure`（WIT-06）と`RetryExhausted`のRESTARTエスカレーション（WIT-03）は、interface_wit.mdの記述だけでは一意に決まらない曖昧さを、pysim側が独自に解決したものである（README「Two spec gaps」に明記済み）。**仕様書自体が明確化されるまでは正式な正解ではない**ことに注意。
- `experiments/pysim/hal.py`の`BusMaster`/`BusSlave`はWIT-31/32を満たしている。
- WIT-20〜22（console-output周り）はpysim `system.py`の`_wasi_fd_write`実装で検証済み。

## 4. 未検証・スコープ外

- `pollable`/`input-stream`/`output-stream`の詳細な非同期セマンティクス（wasi:io標準への準拠度）。
- `wasi:filesystem`のPASSTHROUGH/SHM「事前オープン済み仮想ファイル記述子」エミュレーション（§5.6-4）。
