# テスト用プラットフォームドライバ

[`hal_stubs.py`](hal_stubs.py)は、実HALコマンド契約へ接続するQA用ドライバ群である。
`StubDrivers.create()`は、各テストで独立したドライバと状態を生成する。
`stub_platform(drivers, uris)`は、選択した各URIへ専用HALタスクを起動する。
実ルータ、RBAC行列、CSPチャネル、COOS、HALバッファプールを使用する。
起動できるのは、現行Systemの容量に従う8台までである。
9種類のうち必要なURIを選択する。

| デバイス | スタブ | 制御と観測 |
| :--- | :--- | :--- |
| UART・RTT・stdout | `StreamStubDriver` | 独立した送受信バッファ、read/write、flush/close、短い転送の上限 |
| GPIO | `GpioStubDriver` | ピン設定、set/get、入力刺激、エッジ購読、poll/drop |
| I2C・SPI | `BusStubDriver` | `BUS_CONFIG`で速度・スレーブアドレス・モードを設定する。`BUS_TRANSFER_BUFFER`はその設定で転送する |
| Timer | `TimerStubDriver` | `advance`で明示的に進めるu64時刻、期限購読、poll/drop |
| ADC | `AdcStubDriver` | 設定コマンドとストリームのHALバッファread/write。入力byteは`feed_input`で供給する |
| PWM | `PwmStubDriver` | 設定・出力コマンドの内容を保存する。ストリーム操作は登録しない |

全スタブは受理したコマンドの内容と実行タスクIDを記録する。
設定と観測記録は所有したスナップショットであり、応答後のIPCビューを保持しない。
`StreamStubDriver`の`read_limit`と`write_limit`、`BusStubDriver`の`transfer_limit`で短い転送を指定する。
バスの応答byteは`feed_reply`で供給する。
転送時の設定は`transfer_configurations`で観測する。

ADC/PWMのコマンドIDは`StubCommand`のQAローカル値である。
設定項目はIPCのu32キー・値として保存し、単位や物理範囲を推測しない。
GPIOのmode 0/1/2/3とedge 1/2/3もQA用の設定である。
modeは入力・出力・入力pull-up・入力pull-downを表す。
edgeは立上り・立下り・両方を表す。
これらを製品WITやデバイス固有ABIの正本として使用しない。

QAの静的URI表はSystemの生成前に選択し、終了後に復元する。
ADCとRTTは既存UARTロール、PWMは既存Timerロールをこの構成内で使う。
各URIのチャネルは独立している。
製品のADC/PWM/RTTロール方針を、このQA構成から導出しない。

現行HALバッファプールは一度に1スロットだけをマップする。
I2C/SPIの実IPC試験は、同じハンドルのTX/RXで送信byteを読んでから受信byteを書き込む。
別々のTX/RXスロットを両方マップできたとは扱わない。
未マップの受信ハンドルは、応答byteやバッファを変更する前にassertする。

[`test_driver_stubs.py`](../tier3_platform/test_driver_stubs.py)は直接契約試験と実IPC試験を持つ。
Clang生成のゲストからの試験は、QA専用importアダプタを経由する。
製品ゲストのHAL WIT loweringはこのアダプタに含まれない。
要求対応と実行入口は[HALテスト仕様](../../../../docs/qa/tier2_runtime/hal_dispatch_test_spec.md)を参照する。
