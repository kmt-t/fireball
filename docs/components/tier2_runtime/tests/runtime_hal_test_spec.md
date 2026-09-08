# HAL 抽象化層 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`runtime_hal.md`](docs/components/tier2_runtime/runtime_hal.md)
参考実装: なし（物理実装は [`platform_driver_test_spec.md`](docs/components/tier3_platform/tests/platform_driver_test_spec.md) を参照）

IPCルータ経由の全アクセス契約、`hal-buf-id`による生ポインタ渡し禁止契約、割り込みpush/pull二経路の役割分担契約、ゼロコピー転送契約、および高速パスとIPC経由制御の区別を検証する。物理的なバッファプール配置やRSPトランスポートの実装詳細は [`platform_driver_test_spec.md`](docs/components/tier3_platform/tests/platform_driver_test_spec.md) の責務とする。

## 2. テストケース一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| HAL-01 | 全アクセスはIPCルータ経由 | 任意のデバイスアクセス | `read`/`write`/`control`を呼ぶ | `device-id`によるキャッシュ済み参照であっても、必ず`role_matrix`照合を経由する（キャッシュが照合を代替・省略しない） | 「device-idとの対応」 |
| HAL-02 | `read`/`write`はhal-buf-id経由（生ポインタ渡し禁止） | - | シグネチャを確認 | `dst`/`src`は`hal-buf-id`型であり、任意のアドレス/ポインタを直接渡す経路がない | read/write |
| HAL-04 | 割り込みpull経路: Safepointでの自己確認（契約上の役割分担） | ゲスト実行エンジンが動作中 | Safepoint到達 | `vsoc_context.interrupt_flags`を自ら確認する（本コンポーネントの管轄外、`runtime_vsoc.md`が正本という契約上の境界） | 「割り込み確認（pull）」 |
| HAL-09 | ゼロコピー転送(bus_master/streaming)の契約保証 | tx/rx共にHALバッファ | `transfer(tx, rx)` | CPUを介さずバッファ間データ移動が完了する契約が保たれる（物理DMA実装は platform_driver 側） | 「ゼロコピー転送」 |
| HAL-10 | `control`はIPCオーバーヘッドを伴う非高速パス | デバイス固有操作 | `control(id, cmd, params)` | `ipc-message`経由で処理され、`{Fast_Path_GPIO}`の高速パスではないことが明示される | 「非標準制御」 |
| HAL-11 | `CMD_CLOCK_GET_NOW`の単位契約 | - | 発行する | ナノ秒単位のu64を返す契約であることを確認する | interface_wit.md §6 |
| HAL-12 | `CMD_BUS_TRANSFER_BUFFER`はHALバッファハンドルのみ受理 | ゲストのリニアメモリポインタを渡そうとする | `hal-buffer-slice`型でない値を渡す | 型として受理されない（ゲストのリニアメモリを指すポインタを直接渡す経路が存在しない） | 「ゲストのリニアメモリ上のポインタを直接渡すことはできない」 |
| HAL-13 | バス受信コマンドの返却バイト数契約 | 送信側からのデータがある | 受信コマンドを発行する | 実際に転送したバイト数を返す契約であることを確認する | - |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（契約レベルの不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- 物理割り込み処理（HAL-03）、GPIO高速パスの物理実装（HAL-05）、HALバッファプールの物理配置（HAL-06）、RSPトランスポート物理層（HAL-07, HAL-08）、GOTCHA（物理実装の勘所）は [`platform_driver_test_spec.md`](docs/components/tier3_platform/tests/platform_driver_test_spec.md) を参照。
- 実ハードウェア（UART/RTT/GPIO/I2C）そのものの電気的特性。
