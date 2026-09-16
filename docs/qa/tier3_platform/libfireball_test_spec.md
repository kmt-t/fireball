# libfireball ゲストアダプタ テスト仕様書

本書は、WASM ゲストへ組み込む `libfireball` の境界テストを定義する。ゲストライブラリ実装が着手されるまで実行コードは作らない。Tier 2 のホスト側 `runtime_syscall`、`hal_dispatch` と Tier 3 の物理ドライバは、それぞれのテスト仕様書を正本とする。

| テストケースID | テスト内容 | 期待結果 |
| :--- | :--- | :--- |
| TEST-LIBFB-01 | `fireball_call0`〜`fireball_call6` の引数配置 | ID と各引数が契約どおり渡される |
| TEST-LIBFB-02 | 未定義 syscall ID の呼び出し | `WasiErrno.NOSYS` 相当へ安全に変換される |
| TEST-LIBFB-03 | `fd_write` の URI 解決とバッファ確保・解放 | stdout/stderr のストリームへ生バイト列が渡る |
| TEST-LIBFB-04 | `fd_write` の iovec 境界検証 | 全 iovec 検証前に出力せず、違反時は `EFAULT` になる |
| TEST-LIBFB-05 | `fd_read` のバッファ返却 | 読み出し後に HAL バッファが解放される |
| TEST-LIBFB-06 | `clock_time_get` の時刻変換 | HAL の単調時刻が Preview1 の戻り値へ変換される |
| TEST-LIBFB-07 | HAL エラーの errno 変換 | HAL の失敗結果が Preview1 の errno へ一貫して変換される |
| TEST-LIBFB-08 | ライブラリの配置 | `libfireball` がサービスや COOS タスクとして登録されない |
| TEST-LIBFB-09 | vIRQ固定スロット登録 | 静的root・分類・デバイスノードが公開されている | `fireball_virq_register`で関数インデックスを書き込む | vMMIOの保留登録へ渡され、範囲外ノードは拒否される |
| TEST-LIBFB-10 | vIRQ関数シグネチャ拒否 | 不一致シグネチャのWASM関数 | 登録ラッパーから登録を試みる | vSoCが拒否し、有効な登録を変更しない |
| TEST-LIBFB-11 | Safepoint反映と原因5引数 | 登録Aと保留登録B、原因レコード | Safepoint前後で同じ原因を配送する | Safepoint前後で呼ばれる関数が原子的に切り替わり、5ワードが順序を保って渡る |
| TEST-LIBFB-12 | 階層結果の変換 | root・分類・デバイス関数が登録済み | `HANDLED`、`PASS_THROUGH`、`REJECT`を返す | `HANDLED`は終了、`PASS_THROUGH`だけが子へ進み、`REJECT`は再帰配送されない |
| TEST-LIBFB-13 | WASIポーリング非干渉 | `poll-check`/`poll-wait`ハンドルが存在 | vIRQ登録・配送とポーリングを実行する | vIRQ操作がWASI poll APIを追加・変更せず、両経路が独立して完了する |

実装着手時には、各行をゲスト側の実行テストへ結線し、Tier 2/3 の既存テストと同じ境界を重複実装しない。
