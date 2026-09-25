# libfireball ゲストアダプタ テスト仕様書

## 1. 目的と対象範囲

本書は、WASM ゲストへ組み込む `libfireball` の host-call 境界テストを定義する。汎用システムコールは `fireball:host/trap`、vIRQは `fireball:host/virq`、vDMAは `fireball:host/vdma` の専用importを通じてホストへ接続し、SYSCTL／VDMAの vMMIO レジスタを使用しない。vIRQのイベント本体はISR → COOS FIFO → COOS協調境界 → vSoCの順に配送する。Tier 2 のホスト側 `runtime_syscall`、`hal_dispatch` と Tier 3 の物理ドライバは、それぞれのテスト仕様書を正本とする。

## 2. テストケース一覧

| テストケースID | テスト内容 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-LIBFB-01 | `fireball_call0`〜`fireball_call6` のhost-call引数配置 | host-callハンドラが接続済み | 各ラッパーを呼び出す | ID と各引数が契約どおりホストハンドラへ直接渡される | `libfireball.md` |
| TEST-LIBFB-02 | 未定義 syscall ID の呼び出し | 未定義IDを指定できる | `fireball_call`を呼び出す | `WasiErrno.NOSYS` 相当へ安全に変換される | `libfireball.md` |
| TEST-LIBFB-03 | `fd_write` の URI 解決とバッファ確保・解放 | stdout/stderrが解決可能 | `fd_write`を呼び出す | stdout/stderr のストリームへ生バイト列が渡る | `libfireball.md` |
| TEST-LIBFB-04 | `fd_write` の iovec 境界検証 | 境界外iovecを含む | `fd_write`を呼び出す | 全 iovec 検証前に出力せず、違反時は `EFAULT` になる | `libfireball.md` |
| TEST-LIBFB-05 | `fd_read` のバッファ返却 | 読み取りデータが存在する | `fd_read`を呼び出す | 読み出し後に HAL バッファが解放される | `libfireball.md` |
| TEST-LIBFB-06 | `clock_time_get` の時刻変換 | HALの単調時刻が取得可能 | `clock_time_get`を呼び出す | HAL の単調時刻が Preview1 の戻り値へ変換される | `libfireball.md` |
| TEST-LIBFB-07 | HAL エラーの errno 変換 | HALがエラーを返す | 対応するラッパーを呼び出す | HAL の失敗結果が Preview1 の errno へ一貫して変換される | `libfireball.md` |
| TEST-LIBFB-08 | ライブラリの配置 | libfireballをロード済み | タスク・サービス登録を確認する | `libfireball` がサービスや COOS タスクとして登録されない | `libfireball.md` |
| TEST-LIBFB-09 | vIRQ登録host call | 静的root・分類・デバイスノードが公開されている | `fireball_virq_register` が `fireball:host/virq.register(node_id, function_index)` を発行する | vSoCの保留登録へ渡され、範囲外ノードは拒否される。vMMIO固定スロットへの直接書込みは発生しない | `libfireball.md` |
| TEST-LIBFB-10 | vIRQ解除host call | 有効または保留中の登録が存在する | `fireball_virq_unregister` が `fireball:host/virq.unregister(node_id)` を発行する | 解除が保留され、次のCOOS協調境界で無効化される | `libfireball.md` |
| TEST-LIBFB-11 | vIRQ関数シグネチャ拒否 | 不一致シグネチャのWASM関数 | 登録ラッパーから登録を試みる | vSoCが拒否し、有効な登録を変更しない | `libfireball.md` |
| TEST-LIBFB-12 | COOS協調境界での反映と原因5引数 | 登録Aと保留登録B、原因レコード | 協調境界の前後で同じ原因を配送する | 境界の前後で呼ばれる関数が原子的に切り替わり、5ワードが順序を保って渡る | `libfireball.md` |
| TEST-LIBFB-13 | 階層結果の変換 | root・分類・デバイス関数が登録済み | `HANDLED`、`PASS_THROUGH`、`REJECT`を返す | `HANDLED`は終了、`PASS_THROUGH`だけが子へ進み、`REJECT`は再帰配送されない | `libfireball.md` |
| TEST-LIBFB-14 | WASIポーリング非干渉 | `poll-check`/`poll-wait`ハンドルが存在 | vIRQ登録・配送とポーリングを実行する | vIRQ操作がWASI poll APIを追加・変更せず、両経路が独立して完了する | `libfireball.md` |
| TEST-LIBFB-15 | vDMA転送host call | source/destinationと転送長が有効 | `fireball_vdma_start` が `fireball:host/vdma.start(source, destination, byte_count)` を発行する | vSoCの転送要求へ渡され、VDMAレジスタへの書込みは発生しない | `libfireball.md` |
| TEST-LIBFB-16 | vDMA権限拒否 | 転送先が未許可または所有権外 | `fireball_vdma_start` を発行する | 共通vMMIO権限ゲートで拒否され、転送状態を変更しない | `libfireball.md` |
| TEST-LIBFB-17 | host-call portの単一借用 | 4操作を実装するhost-call portが存在する | `Libfireball`を構築して汎用・vIRQ・vDMAラッパーを呼ぶ | すべての呼出しが同じportへ渡り、個別の関数参照を保持しない | `libfireball.md` |

## 3. テスト検証実績と網羅状況

- 汎用host-call、WASI変換、vIRQ、vDMA、およびhost-call portの17ケースを定義する。
- 実行結果は、実行環境とゲスト側テストスイートを確定した後に記録する。
- 実装着手時には、各行をゲスト側の実行テストへ結線し、Tier 2/3の既存テストと同じ境界を重複実装しない。

## 4. 未検証・スコープ外

- Tier 2 の `runtime_syscall` と `hal_dispatch` のホスト側実装は、それぞれのテスト仕様書で検証する。
- Tier 3 の物理ドライバ実装は、`platform_driver_test_spec.md` で検証する。
