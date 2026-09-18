# libfireball ゲストアダプタ テスト仕様書

本書は、WASM ゲストへ組み込む `libfireball` の host-call 境界テストを定義する。ゲストライブラリは `fireball:host/trap` の import を通じてホストへ接続し、SYSCTL／VDMAの vMMIO レジスタを使用しない。vIRQの登録・解除もhost callで行い、イベント本体の配送だけをISR → COOS FIFO → vSoC Safepointの非同期経路で行う。Tier 2 のホスト側 `runtime_syscall`、`hal_dispatch` と Tier 3 の物理ドライバは、それぞれのテスト仕様書を正本とする。

| テストケースID | テスト内容 | 前提条件 | 手順 | 期待結果 |
| :--- | :--- | :--- | :--- | :--- |
| TEST-LIBFB-01 | `fireball_call0`〜`fireball_call6` のhost-call引数配置 | host-callハンドラが接続済み | 各ラッパーを呼び出す | ID と各引数が契約どおりホストハンドラへ直接渡される |
| TEST-LIBFB-02 | 未定義 syscall ID の呼び出し | 未定義IDを指定できる | `fireball_call`を呼び出す | `WasiErrno.NOSYS` 相当へ安全に変換される |
| TEST-LIBFB-03 | `fd_write` の URI 解決とバッファ確保・解放 | stdout/stderrが解決可能 | `fd_write`を呼び出す | stdout/stderr のストリームへ生バイト列が渡る |
| TEST-LIBFB-04 | `fd_write` の iovec 境界検証 | 境界外iovecを含む | `fd_write`を呼び出す | 全 iovec 検証前に出力せず、違反時は `EFAULT` になる |
| TEST-LIBFB-05 | `fd_read` のバッファ返却 | 読み取りデータが存在する | `fd_read`を呼び出す | 読み出し後に HAL バッファが解放される |
| TEST-LIBFB-06 | `clock_time_get` の時刻変換 | HALの単調時刻が取得可能 | `clock_time_get`を呼び出す | HAL の単調時刻が Preview1 の戻り値へ変換される |
| TEST-LIBFB-07 | HAL エラーの errno 変換 | HALがエラーを返す | 対応するラッパーを呼び出す | HAL の失敗結果が Preview1 の errno へ一貫して変換される |
| TEST-LIBFB-08 | ライブラリの配置 | libfireballをロード済み | タスク・サービス登録を確認する | `libfireball` がサービスや COOS タスクとして登録されない |
| TEST-LIBFB-09 | vIRQ登録host call | 静的root・分類・デバイスノードが公開されている | `fireball_virq_register` が `fireball_call(VIRQ_REGISTER, node_id, function_index, ...)` を発行する | vSoCの保留登録へ渡され、範囲外ノードは拒否される。vMMIO固定スロットへの直接書込みは発生しない |
| TEST-LIBFB-10 | vIRQ解除host call | 有効または保留中の登録が存在する | `fireball_virq_unregister` が `fireball_call(VIRQ_UNREGISTER, node_id, ...)` を発行する | 解除が保留され、次のSafepointで無効化される |
| TEST-LIBFB-11 | vIRQ関数シグネチャ拒否 | 不一致シグネチャのWASM関数 | 登録ラッパーから登録を試みる | vSoCが拒否し、有効な登録を変更しない |
| TEST-LIBFB-12 | Safepoint反映と原因5引数 | 登録Aと保留登録B、原因レコード | Safepoint前後で同じ原因を配送する | Safepoint前後で呼ばれる関数が原子的に切り替わり、5ワードが順序を保って渡る |
| TEST-LIBFB-13 | 階層結果の変換 | root・分類・デバイス関数が登録済み | `HANDLED`、`PASS_THROUGH`、`REJECT`を返す | `HANDLED`は終了、`PASS_THROUGH`だけが子へ進み、`REJECT`は再帰配送されない |
| TEST-LIBFB-14 | WASIポーリング非干渉 | `poll-check`/`poll-wait`ハンドルが存在 | vIRQ登録・配送とポーリングを実行する | vIRQ操作がWASI poll APIを追加・変更せず、両経路が独立して完了する |

実装着手時には、各行をゲスト側の実行テストへ結線し、Tier 2/3 の既存テストと同じ境界を重複実装しない。
