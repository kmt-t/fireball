# HAL ドライバ実装 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`platform_driver.md`](docs/components/tier3_platform/platform_driver.md)
参考実装: なし

物理割り込みのpush経路、GPIO直接ストアの高速パス、HALバッファプール(vMMIO/DYNAMIC)への物理マッピング、RSPトランスポートの物理エンコード/デコードを検証する。契約レベルの振る舞い（IPCルータ経由アクセス、hal-buf-id契約等）は [`hal_dispatch_test_spec.md`](docs/components/tier2_runtime/tests/hal_dispatch_test_spec.md) の責務とする。

## 2. テストケース一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| HAL-03 | 割り込みpush経路: ISRは状態を直接変更しない | 物理割り込み発生 | ISRが`notify_interrupt(irq_id)`を呼ぶ | INT イベントが有界キューへ投函されるのみで、タスク状態はスケジューラのyield点までREADYへ遷移しない | 「割り込み通知（push）」 |
| HAL-05 | GPIO直接ストアの高速パス | GPIOへの書き込み要求 | `{Fast_Path_GPIO}`経由でアクセス | IPCルータのメッセージパッシングを経由しない直接vMMIOストア（`fireball_call`経由の`control`とは別の、より低レイテンシな経路） | `{Fast_Path_GPIO}`, system_syscall.md |
| HAL-06 | `acquire_buffer`のHALバッファプール(vMMIO/DYNAMIC)物理マッピング | - | `acquire_buffer(size)` | 確保されたバッファがHALバッファプール（vMMIO DYNAMIC領域）のスロットに物理マッピングされる | 「バッファの確保」, runtime_vmmio.md |
| HAL-07 | RSPトランスポートの選択可能性 | - | UART/RTTそれぞれで接続 | 双方の物理層でRSPパケット送受信が可能 | - |
| HAL-08 | RSPチェックサム検証とACK/NAK | 正常/不正なチェックサムのパケット | 受信処理 | 一致時ACK(`+`)、不一致時NAK(`-`)を返す | 「コマンド取得」 |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| HAL-GOTCHA-01 | `HalBufferPool` のスライス境界厳格検査（隣接汚染防止） | 固定サイズバッファプール（各バッファ 256 バイト） | 256 バイトを超えるスライス（例: 512 バイト）を要求 | 即座にエラーまたはアサーション違反で拒絶される。**実装の勘所**: プールアロケータでサイズ検証を省略すると、隣接する別スロットのバッファ領域へ書き込みがはみ出し、システム破壊を招く | `platform_driver.md` |
| HAL-GOTCHA-02 | UART パイプトランスポートの双方向独立性とノンブロッキング | UART トランスポート初期化 | 送信側が連続書き込み、受信側が非同期読み出し | 送信と受信が互いに干渉せず、EOF やバッファ満杯時にも安全にエラーまたはブロックなしで制御が返る。**実装の勘所**: 組み込み UART ドライバで送受信のリングバッファを不用意に共有すると、全二重通信時にデータ化けが発生する | `platform_driver.md` |
| HAL-GOTCHA-03 | 単調増加タイマーの差分計算安全性（ラップアラウンド耐性） | モノトニックタイマー稼働中 | 複数回のタイムスタンプ取得と経過時間計測 | 常に $t_1 \le t_2$ が成立し、差分計算 $(t_2 - t_1)$ が正確な経過時間を表す。**実装の勘所**: 32bit ハードウェアカウンタの単純な大小比較（`t1 < t2`）を行うと、約4秒〜数分で発生するタイマーラップアラウンド時に逆行と誤判定される | `platform_driver.md` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（物理実装レベルの不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- 契約レベルの振る舞い（HAL-01, HAL-02, HAL-04, HAL-09, HAL-10）は [`hal_dispatch_test_spec.md`](docs/components/tier2_runtime/tests/hal_dispatch_test_spec.md) を参照。
- 実ハードウェア（UART/RTT/GPIO/I2C）そのものの電気的特性。
