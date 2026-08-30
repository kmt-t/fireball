# HAL テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier3_platform/platform_hal.md`
参考実装: なし

デバイスレジストリ、IPCルータ経由の全アクセス、割り込みのpush/pull二経路、GPIOの高速パス、SHM(FC=14)へのバッファマッピング、RSPトランスポートを検証する。

## 2. テストケース一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| HAL-01 | 全アクセスはIPCルータ経由 | 任意のデバイスアクセス | `read`/`write`/`control`を呼ぶ | `device-id`によるキャッシュ済み参照であっても、必ず`role_matrix`照合を経由する（キャッシュが照合を代替・省略しない） | §5.3「device-idとの対応」 |
| HAL-02 | `read`/`write`はshm-id経由（生ポインタ渡し禁止） | - | シグネチャを確認 | `dst`/`src`は`shm-id`型であり、任意のアドレス/ポインタを直接渡す経路がない | §5.1 read/write |
| HAL-03 | 割り込みpush経路: ISRは状態を直接変更しない | 物理割り込み発生 | ISRが`notify_interrupt(irq_id)`を呼ぶ | INT イベントが有界キューへ投函されるのみで、タスク状態はスケジューラのyield点までREADYへ遷移しない | §4.1「割り込み通知（push）」 |
| HAL-04 | 割り込みpull経路: Safepointでの自己確認 | ゲスト実行エンジンが動作中 | Safepoint到達 | `vsoc_context.interrupt_flags`を自ら確認する（HALの管轄外、runtime_vsoc.mdが正本） | §4.1「割り込み確認（pull）」 |
| HAL-05 | GPIO直接ストアの高速パス | GPIOへの書き込み要求 | `{Fast_Path_GPIO}`経由でアクセス | IPCルータのメッセージパッシングを経由しない直接vMMIOストア（`fireball_call`経由の`control`とは別の、より低レイテンシな経路） | §1 `{Fast_Path_GPIO}`, system_syscall.md §6.2 |
| HAL-06 | `acquire_buffer`のSHM(FC=14)マッピング | - | `acquire_buffer(size)` | 確保されたバッファがvMMIOのSHM領域(`0xE000_0000`〜)のスロットにマッピングされる | §5.1「バッファの確保」, runtime_vmmio.md §4.6 |
| HAL-07 | RSPトランスポートの選択可能性 | - | UART/RTTそれぞれで接続 | 双方の物理層でRSPパケット送受信が可能 | §5.4 |
| HAL-08 | RSPチェックサム検証とACK/NAK | 正常/不正なチェックサムのパケット | 受信処理 | 一致時ACK(`+`)、不一致時NAK(`-`)を返す | §4.1「コマンド取得」 |
| HAL-09 | ゼロコピー転送(bus_master/streaming) | tx/rx共にSHMバッファ | `transfer(tx, rx)` | CPUを介さずバッファ間データ移動が完了する | §5.1「ゼロコピー転送」 |
| HAL-10 | `control`はIPCオーバーヘッドを伴う非高速パス | デバイス固有操作 | `control(id, cmd, params)` | `ipc-message`経由で処理され、`{Fast_Path_GPIO}`の高速パスではないことが明示される | §5.1「非標準制御」 |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- 実ハードウェア（UART/RTT/GPIO/I2C）そのものの電気的特性。
