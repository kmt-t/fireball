# HAL 抽象化層 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [hal_dispatch.md](docs/components/tier2_runtime/hal_dispatch.md)
IPCルータ経由の全アクセス契約、`hal-buffer-id`による生ポインタ渡し禁止契約、操作期間だけ行う`map-buffer`/`unmap-buffer`契約、ゼロコピー転送契約、およびGPIOのvMMIO高速経路とIPC制御の区別を検証する。物理的なバッファプール配置やRSPトランスポートの実装詳細は [`platform_driver_test_spec.md`](docs/qa/tier3_platform/platform_driver_test_spec.md) の責務とする。

## 2. テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-HAL-01 | 全アクセスはIPCルータ経由 | 任意のデバイスアクセス | `stream-read`/`stream-write`/`control`を呼ぶ | `hal-buffer-id`によるキャッシュ済み参照であっても、必ず`role_matrix`照合を経由する（キャッシュが照合を代替・省略しない） | 「URIインスタンスとの対応」 |
| TEST-HAL-14 | ドライバのコマンド登録と自己起動 | ドライバ実装が存在する | コマンドIDとコールバックを登録して`start`する。未登録IDをdispatchする | 登録済みコールバックだけが呼ばれ、未登録IDは拒否される。HAL共通層はデバイス列挙・代理起動を行わない | `hal_dispatch.md`「ドライバ登録と起動」 |
| TEST-HAL-02 | `stream-read`/`stream-write`はhal-buffer-id経由（生ポインタ渡し禁止） | - | シグネチャを確認 | `dst`/`src`は`hal-buffer-id`型であり、任意のアドレス/ポインタを直接渡す経路がない | stream-read/stream-write |
| TEST-HAL-04 | vIRQ配送とWASIポーリングの分離 | ゲスト実行エンジンが動作中 | Safepoint到達と`poll-check`/`poll-wait`を個別に実行 | vSoCは`interrupt-event`をvIRQ階層へ配送し、HALは操作完了ポーリングを提供する。どちらも他方を起動・変更しない | `runtime_vsoc.md`、{WASI_Implementation} |
| TEST-HAL-09 | ゼロコピー転送(bus_master/streaming)の契約保証 | tx/rx共にHALバッファ | `map-buffer`で必要なスロットをマップし、`transfer(tx, rx)`後に`unmap-buffer`する | CPUを介さずバッファ間データ移動が完了し、I/O終了後にDYNAMICマッピングが残らない（物理DMA実装はplatform_driver側） | 「ゼロコピー転送」 |
| TEST-HAL-10 | `control`はIPCオーバーヘッドを伴う非高速パス | デバイス固有操作 | `control(id, cmd, params)` | `ipc-message`経由で処理され、`{Fast_Path_GPIO}`の高速パスではないことが明示される | 「非標準制御」 |
| TEST-HAL-11 | `CMD_CLOCK_GET_NOW`の応答契約 | - | 発行し、`response_code`と`ARG_RESULT_LO`/`ARG_RESULT_HI`を読む | `response_code=0`であり、2つのKVを`lo \| (hi << 32)`で復元した値がナノ秒単位のu64になることを確認する | `hal_dispatch.md` 階層型 URI 命名規則 & WASI 0.3p IPC コマンド仕様 |
| TEST-HAL-12 | `CMD_BUS_TRANSFER_BUFFER`はHALバッファハンドルのみ受理 | ゲストのリニアメモリポインタを渡そうとする | 無効・期限切れ・マッピングされていない`hal-buffer-id`を渡す | 有効なHALバッファとして解決されず、ゲストのリニアメモリを指すポインタを直接渡す経路も存在しない。不正ハンドルはassert対象である | 「ゲストのリニアメモリ上のポインタを直接渡すことはできない」 |
| TEST-HAL-13 | バス受信コマンドの返却バイト数契約 | 送信側からのデータがある | 受信コマンドを発行する | 実際に転送したバイト数を返す契約であることを確認する | - |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（契約レベルの不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- 物理割り込み処理（TEST-HAL-03）、GPIO vMMIO高速パスの物理実装（TEST-HAL-05）、HALバッファプールの物理配置（TEST-HAL-06）、RSPトランスポート物理層（TEST-HAL-07, TEST-HAL-08）、GOTCHA（物理実装の勘所）は [`platform_driver_test_spec.md`](docs/qa/tier3_platform/platform_driver_test_spec.md) を参照。
- 実ハードウェア（UART/RTT/GPIO/I2C）そのものの電気的特性。
