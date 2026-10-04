# HAL 抽象化層 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [hal_dispatch.md](docs/components/tier2_runtime/hal_dispatch.md)

本書は IPC ルータ経由のアクセス契約を検証する。`hal-buffer-id` による生ポインタ渡し禁止、操作中だけ行う `map-buffer` / `unmap-buffer`、ゼロコピー転送も対象である。GPIO の vMMIO 高速経路と IPC 制御の違いも確認する。

物理バッファプールの配置と RSP トランスポートの実装詳細は対象外である。これらは [`platform_driver_test_spec.md`](docs/qa/tier3_platform/platform_driver_test_spec.md) を正本とする。

試験用デバイスは[`hal_stubs.py`](experiments/pysim/qa/shared/fixtures/hal_stubs.py)を使う。
ADCは設定コマンドとストリームのバッファI/O、PWMはコマンド操作とする。
この形はユーザー指示に従う。
ADC/PWMの具体的なID・設定項目とGPIOの数値表現はQAローカル設定である。
製品のデバイス固有ABIへ読み替えない。

## 2. テストケース一覧
<!-- traceability: {Fast_Path_GPIO} {WASI_Implementation} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-HAL-01 | 全アクセスはIPCルータ経由 | 任意のデバイスアクセス | `stream-read`/`stream-write`/`control`を呼ぶ | `hal-buffer-id`によるキャッシュ済み参照であっても、必ず`role_matrix`照合を経由する（キャッシュが照合を代替・省略しない） | 「URIインスタンスとの対応」 |
| TEST-HAL-14 | ドライバのコマンド登録と自己起動 | ドライバ実装が存在する | コマンドIDとコールバックを登録して`start`する。未登録IDをdispatchする | 登録済みコールバックだけが呼ばれ、未登録IDは拒否される。HAL共通層はデバイス列挙・代理起動を行わない | `hal_dispatch.md`「ドライバ登録と起動」 |
| TEST-HAL-15 | タイマーpollableの期限判定・協調待機・解放 | タイマードライバと別のREADYタスクが存在する | 期限を予約し、期限前に`POLL_CHECK`、期限後まで`POLL_WAIT`を発行し、同じスロットを再利用する | 待機タスクだけが`BLOCKED_TIMER`になり、他のREADYタスクは期限前に実行される。待機後はready、破棄後は古い世代ハンドルを拒否し、再予約は異なるハンドルを返す | `hal_dispatch.md`「Clock/Timer」「Poll」、`os_scheduler.md`「時刻待機」 |
| TEST-HAL-02 | `stream-read`/`stream-write`はhal-buffer-id経由（生ポインタ渡し禁止） | - | シグネチャを確認 | `dst`/`src`は`hal-buffer-id`型であり、任意のアドレス/ポインタを直接渡す経路がない | stream-read/stream-write |
| TEST-HAL-04 | vIRQ配送とWASIポーリングの分離 | ゲスト実行エンジンが動作中 | COOS協調境界への復帰と`poll-check`/`poll-wait`を個別に実行 | vSoCは`interrupt-event`をvIRQ階層へ配送し、HALは操作完了ポーリングを提供する。どちらも他方を起動・変更しない | `runtime_vsoc.md`、WASI_Implementation |
| TEST-HAL-09 | ゼロコピー転送(bus_master/streaming)の契約保証 | tx/rx共にHALバッファ | `map-buffer`で必要なスロットをマップし、`transfer(tx, rx)`後に`unmap-buffer`する | CPUを介さずバッファ間データ移動が完了し、I/O終了後にDYNAMICマッピングが残らない（物理DMA実装はplatform_driver側） | 「ゼロコピー転送」 |
| TEST-HAL-10 | `control`はIPCオーバーヘッドを伴う非高速パス | デバイス固有操作 | `control(id, cmd, params)` | `ipc-message`経由で処理され、`Fast_Path_GPIO`の高速パスではないことが明示される | 「非標準制御」 |
| TEST-HAL-11 | `CMD_CLOCK_GET_NOW`の応答契約 | - | 発行し、`response_code`と`ARG_RESULT_LO`/`ARG_RESULT_HI`を読む | `response_code=0`であり、2つのKVを`lo \| (hi << 32)`で復元した値がナノ秒単位のu64になることを確認する | `hal_dispatch.md` 階層型 URI 命名規則 & WASI 0.3p IPC コマンド仕様 |
| TEST-HAL-12 | `CMD_BUS_TRANSFER_BUFFER`はHALバッファハンドルのみ受理 | ゲストのリニアメモリポインタを渡そうとする | 無効・期限切れ・マッピングされていない`hal-buffer-id`を渡す | 有効なHALバッファとして解決されず、ゲストのリニアメモリを指すポインタを直接渡す経路も存在しない。不正ハンドルはassert対象である | 「ゲストのリニアメモリ上のポインタを直接渡すことはできない」 |
| TEST-HAL-13 | バス受信コマンドの返却バイト数契約 | 送信側からのデータがある | 受信コマンドを発行する | 実際に転送したバイト数を返す契約であることを確認する | - |
| TEST-HAL-16 | IPCによるstream-writeのデータ完全性 | Runtimeタスク、専用HALタスク、マップ済み固定バッファが存在する | ハンドル、offset、lengthをIPCで渡し、応答と実stdoutを読み出す | 成功statusが0となり、返却byte数が指定長と一致する。実stdoutが指定sliceの全byteと一致し、重複出力しない。送信元の全固定スロットが不変である | [`test_hal.py`](experiments/pysim/qa/tier3_platform/test_hal.py) `test_hal_task_ipc_communication`、正本5.2の`CMD_STREAM_WRITE_BUFFER` |
| TEST-HAL-17 | デバイス別のコマンド登録・URI分離 | 各スタブと専用HALタスクがある | capability queryと未登録IDを試す。同じロールのUART/RTTへ別のbyte列を送る | queryが登録内容を返す。未登録IDはassertし、観測記録を変更しない。8台のタスクIDと各URIの出力が独立する | `test_stub_capabilities_match_each_device_profile`、`test_stub_platform_keeps_eight_devices_and_same_role_instances_separate` |
| TEST-HAL-18 | 双方向ストリーム・短い転送・境界保護 | UART/RTT/stdout/ADCの入力を供給する | 0byte、末尾の空slice、非0offset、256byteと転送上限を組み合わせる。境界超過とcloseも試す | 指定byteだけを転送する。入力残量、全スロット、返却長、unmapが一致する。無効sliceは入力を消費しない。close後の入力供給はassertする | `test_stream_stubs_preserve_slices_and_independent_rx_tx`、`test_stream_close_flush_and_invalid_slice_preserve_state` |
| TEST-HAL-19 | ストリームの操作履歴 | 各試験が独立したスタブ状態を持つ | Hypothesisで1〜3回のbyte列を生成する | 各操作後に全出力が入力と一致する。他スロットを保存し、マッピングが残らない | `test_stream_stub_histories_reuse_slots_without_losing_bytes` |
| TEST-HAL-20 | GPIO設定・エッジ・pollable | QAのピンとエッジ設定を選ぶ | 設定、別ピン入力、同値入力、反転、poll、drop、再購読を行う | 選択したピンとエッジだけがreadyとなる。getが入力レベルを返す。古い世代ハンドルをassertで拒否する | `test_gpio_stub_commands_and_edges_are_pin_scoped` |
| TEST-HAL-21 | I2C/SPI設定コマンドと転送 | 既知の応答byteとマップ済みスロットがある | BUS_CONFIGで速度・スレーブアドレス・モードを設定する。アドレスを変更して再転送する | 各転送が直前の設定を記録する。書換え前のTXと実RXの全byte、短い転送数、残存応答、全スロットが一致する | `test_bus_stubs_capture_tx_before_in_place_rx_and_preserve_tails`、`test_clang_bus_configuration_command_selects_subsequent_transfer_address` |
| TEST-HAL-22 | バスの拒否前検査 | 応答と全スロットを保存する | 不正TX、未マップRX、境界超過、必須引数欠落を試す | assertが発生する。応答byte、観測記録、転送履歴、全スロット、既存マッピングは変わらない | `test_bus_stub_rejection_preserves_reply_and_all_buffers` |
| TEST-HAL-23 | ADC/PWM設定とIPCビューの寿命 | QAコマンドprofileを使う | 設定前操作を拒否する。設定後に元の引数配列を書き換え、PWM出力を設定する | 設定前はassertする。保存した設定は元配列の変更を受けない。ADCはストリーム、PWMはコマンドとして登録する | `test_adc_pwm_stubs_require_setup_and_own_configuration_snapshots` |
| TEST-HAL-24 | 制御可能なTimer・待機・pollable容量 | u64の試験時刻と16個の購読スロットがある | 期限直前・期限到達、別READYタスクによる時計進行、満杯、drop、再利用を試す | 全64bit時刻を返す。未完了HALタスクがBLOCKED_TIMERとなり、別タスクが時計を進められる。満杯時に既存購読を壊さず、古い世代を拒否する | `test_timer_stub_manual_deadlines_keep_u64_and_stale_handles`、`test_timer_stub_wait_allows_peer_to_advance_test_time`、`test_stub_pollable_capacity_rejection_and_reuse` |
| TEST-HAL-25 | Clangゲストから各デバイスへの層横断 | SDKとQA専用importアダプタがある | CソースをClangでコンパイルし、9種類を個別に実行する | 実NativeInterpreter、URI/RBAC、CSP、COOS、HALタスクを経由する。実行主体、設定、全byte、u64結果、範囲外byte、unmapが一致する | `test_clang_guest_reaches_each_stub_through_real_ipc`、`test_clang_bus_configuration_command_selects_subsequent_transfer_address` |

### スタブ構成の境界
<!-- traceability: {HAL_Interface} {IPCRouter} {IPC_ZeroCopy} {TypeSafeMessaging} -->

UART、RTT、stdout、GPIO、I2C、SPI、Timer、ADC、PWMの9種類を用意する。
現行Systemの容量に従い、一度に起動するのは選択した8台までとする。
スタブは起動前にコマンドを登録し、各URIの専用HALタスクで処理する。
QAのURI表は実ルータを生成する前に選択する。
ルータ、権限行列、CSPチャネル、COOS、固定バッファプールは実実装を使う。

ADCの設定、PWMの設定と出力は、QAローカルのコマンドIDで登録する。
設定値はu32のキー・値として保存する。
サンプル形式、PWMの単位や物理範囲はこのスタブで定義しない。
ADC/PWM/RTTの製品ロールも決定しない。
QAの結線と操作方法は[`README.md`](experiments/pysim/qa/shared/fixtures/README.md)を参照する。

現行バッファプールは1スロットだけを操作期間中にマップする。
バス試験は同一ハンドルのTX/RXで、送信byteを保存してから受信byteを書き込む。
異なるTX/RXスロットの同時マッピングが成立したとは扱わない。
未マップのRXは、応答を消費する前にassertする。

ClangゲストはQA専用の`fireball-qa.command`と`fireball-qa.stream`をimportする。
このアダプタは実IPCへ渡し、バッファI/Oの操作期間だけ実スロットをマップする。
製品のHAL WIT loweringは、この試験の構成に含めない。

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（契約レベルの不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

### pysimのstream-write統合試験

[`test_hal.py`](experiments/pysim/qa/tier3_platform/test_hal.py)の`test_hal_task_ipc_communication`は、実Runtimeタスクから`Wasi03pEngine`、IPCルータ、COOSのHALタスク、`DummyDriver`、`StreamTransport`までを通す。要求は不透明ハンドルとoffset、lengthで指定する。応答statusやドライバ呼出し件数に加えて、ホストが観測するstdoutの全byteを試験入力と直接比較する。

入力はoffset 0の128 byte同値パターンと、offset 17の32 byte昇順パターンの2例である。固定スロットの前後を`0xA5`で埋める。指定sliceだけが送られ、送信元スロットの全byteが保持されることを確認する。I/O後のunmapとアクセス拒否は[`platform_driver_test_spec.md`](docs/qa/tier3_platform/platform_driver_test_spec.md)のTEST-HAL-06を部分確認する。

TEST-HAL-01のstream-writeルーティングとTEST-HAL-02のハンドル指定を部分確認する。全デバイス・全操作での迂回禁止や権限照合を、この2例の成功だけから推定しない。

2026-10-01にLinux x64、Python 3.14.6で次のコマンドを実行し、13件PASS、FAIL 0件、SKIP 0件を確認した。この件数はHAL試験ファイルの実行結果であり、本書の全契約の完了件数ではない。

```bash
UV_CACHE_DIR=/tmp/fireball-test-design-uv uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier3_platform/test_hal.py
```

試験プロセス内だけでstdoutの全byteを反転し、成功statusと返却byte数を維持する変異を実行した。2入力とも実stdoutとの不一致でFAILとなった。製品ソースは変更していない。

### スタブドライバの局所検証
<!-- traceability: {HAL_Interface} {IPCRouter} {IPC_ZeroCopy} {TypeSafeMessaging} -->

2026-10-02にLinux x86_64、CPython 3.14.6で実行した。
SDKゲストはWASI-SDK 27.0のClang 20.1.8でC23ソースから生成した。
ビルド入口は[`build_wasi_guest.py`](tools/guest_bindings/build_wasi_guest.py)の`--driver-stub`である。
テスト実行時にコンパイルし、最終importとコンパイラ情報を確認する。

```bash
.venv/bin/python tools/guest_bindings/build_wasi_guest.py --prepare-sdk
.venv/bin/python -m pytest -q \
  experiments/pysim/qa/integration/test_driver_stubs.py \
  experiments/pysim/qa/workloads/test_driver_stubs_guest.py --hypothesis-show-statistics
```

110件成功、失敗0件、skip 0件だった。
固定106条件と生成試験4件を含む。
生成履歴は各ストリーム24成功例であり、pytest件数へ加算しない。
直接契約・IPC試験は [`test_driver_stubs.py`](experiments/pysim/qa/integration/test_driver_stubs.py)、Clang生成guestの11ケースは [`test_driver_stubs_guest.py`](experiments/pysim/qa/workloads/test_driver_stubs_guest.py) に分けた。両方の合計110件であり、前者は結合テスト、後者はWASMバイナリワークロードに分類する。

直接関連する回帰8ファイルも局所実行した。

```bash
.venv/bin/python -m pytest -q \
  experiments/pysim/qa/integration/test_driver_stubs.py \
  experiments/pysim/qa/workloads/test_driver_stubs_guest.py \
  experiments/pysim/qa/workloads/test_wasi_guest.py \
  experiments/pysim/qa/tier3_platform/test_hal.py \
  experiments/pysim/qa/tier1_interface/test_ipc_router.py \
  experiments/pysim/qa/tier2_runtime/test_syscall.py \
  experiments/pysim/qa/cross_cutting/test_entrypoint.py \
  experiments/pysim/qa/integration/test_pairwise_combinations.py
```

364件成功、失敗0件、skip 0件、所要時間3.02秒だった。
この集計はスタブ試験110件とSDK/libc試験52件を含む。
関連Scenario 2、11、12は3件成功、失敗0件、skip 0件、所要時間0.20秒だった。
全スイートの再実行結果とは区別する。

隔離したプロセスで4種類の変異を与えた。
ADCが正しい返却長だけを返し、受信byteを書かない変異は全スロット照合で失敗した。
PWMが成功だけを返して出力設定を保存しない変異も失敗した。
BUS_CONFIGがスレーブアドレスを無視する変異は、I2C/SPIの両条件で失敗した。
実IPCルータがRTTをUARTへ誤配送する変異は、出力byteの照合で失敗した。
製品ファイルは変異検査で変更していない。

変更したpysim Python 3ファイルは公式formatterと正本設定のSourceFacadeで検査した。
静的指摘は0件だった。
全スイートを起動するrun_testsだけを検査呼出し内で無効にし、上記の局所回帰を別に実行した。
設定ファイルの検査ルールは無効化していない。
ビルド入口のRuffと検証マトリクスも成功した。

検証対象はHEAD `c42a2246`を基点とするユーザー変更を含んだ作業ツリーである。
pysimのPython・C・C++ソースとヘッダ、ビルド入口、設定と依存定義の183ファイルを識別した。
リポジトリ相対パスで整列し、UTF-8パス、NUL、全byte、NULを順にSHA-256へ入力した。
集約値は`6b30c4ee70861bf89733b3b48ae25b889974019924fe306a8e6d2f89c5a6ad5e`である。

## 4. 未検証・スコープ外

- 物理割り込み処理（TEST-HAL-03）と GPIO vMMIO 高速パスの物理実装（TEST-HAL-05）は、[`platform_driver_test_spec.md`](docs/qa/tier3_platform/platform_driver_test_spec.md) を参照する。
- HAL バッファプールの物理配置（TEST-HAL-06）と RSP トランスポート物理層（TEST-HAL-07、TEST-HAL-08）も同仕様書を参照する。実装上の注意点（GOTCHA）の定義は[`platform_driver.md`](docs/components/tier3_platform/platform_driver.md)を参照する。
- 実ハードウェア（UART/RTT/GPIO/I2C）そのものの電気的特性。
- 上記2入力はstream-writeのデータ完全性を検査する。物理DMAによるCPU非介在転送、全デバイス種別、全長・全offsetの網羅は未検証である。
- スタブの成功は、製品のADC/PWMコマンドABI、ロール方針、GPIO vMMIO高速経路、物理ISR配送、電気的特性の証拠ではない。
- 異なるTX/RXスロットの同時マッピングは現行pysimで未実装である。同一スロットのバス試験で達成へ変更しない。
