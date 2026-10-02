# HAL ドライバ実装 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`platform_driver.md`](docs/components/tier3_platform/platform_driver.md)
参考実装: [`platform_driver_concept.py`](docs/components/tier3_platform/concepts/platform_driver_concept.py)

物理割り込みのイベント投函経路、GPIO vMMIOストアの唯一の高速パス、HALバッファプール(vMMIO/DYNAMIC)への操作期間マッピング、RSPのRawバイトトランスポートを検証する。RSP Parser、チェックサム、ACK/NAK、およびデバッグコマンド解釈はTier 3 debuggerの責務である。契約レベルの振る舞い（IPCルータ経由アクセス、hal-buffer-id契約等）は [`hal_dispatch_test_spec.md`](docs/qa/tier2_runtime/hal_dispatch_test_spec.md) の責務とする。

## 2. テストケース一覧
<!-- traceability: {BufferedLogging} {Fast_Path_GPIO} {HAL_Interface} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-HAL-03 | 割り込みイベント投函経路: ISRは状態を直接変更しない | 物理割り込み発生 | [`platform_driver_concept.py`](docs/components/tier3_platform/concepts/platform_driver_concept.py)で5ワードの`interrupt-event`を`notify_interrupt(event)`相当で固定長SPSCロックフリーFIFOへ投函し、[`interrupt_boundary_model.py`](docs/components/tier3_platform/formal/interrupt_boundary_model.py)を通常モデルと`guards=False`で実行する | 原因レコードがFIFOへ順序を保って投函されるのみで、タスク状態はスケジューラの協調境界までREADYへ遷移しない。満杯時は既存イベントを上書きせず拒否する。通常モデルでは2つの性質が成立し、変異モデルでは両方が反証される | 「割り込み通知（event）」、`platform_driver_concept.py`、`interrupt_boundary_model.py` |
| TEST-HAL-05 | GPIO vMMIOストアの唯一の高速パス | GPIOへの書き込み要求 | `Fast_Path_GPIO`のvMMIOストアを実行し、`fireball_call`やIPC経由の代替経路がないことを確認 | GPIO高速書き込みはvMMIOストアだけで完結する。GPIO設定・制御を別の`fireball_call`高速経路として実装しない | `Fast_Path_GPIO`, runtime_syscall.md |
| TEST-HAL-06 | HAL固定バッファプール(vMMIO/DYNAMIC)物理マッピングと境界検査 | - | I/O開始時に選択したスロットを`map-buffer`し、操作完了後に`unmap-buffer`する。同時に別スロットのマップを要求し、256バイトを超えるスライスも要求する | 選択したスロットだけがFC=13 DYNAMIC領域へマップされ、I/O終了後にアンマップされる。競合するマップ要求は`BUSY`を返し、上限超過スライスや不正ハンドルだけがassert対象となる（`GOTCHA-HAL-01`） | 「固定スロット」, runtime_vmmio.md |
| TEST-HAL-07 | RSP Rawトランスポートの選択可能性 | - | UART/RTTそれぞれで接続し、受信・送信バイト列をそのまま渡す | 双方の物理層でRaw RSPバイト列の送受信が可能で、物理ドライバはパケット解析を行わない | platform_driver.md |
| TEST-HAL-08 | RSP Parserの責務分離 | Raw RSPバイト列を受信 | platform_driverの出力をDebugger Pluginへ渡し、物理ドライバにチェックサム・ACK/NAK・コマンド解析がないことを確認 | platform_driverはRawバイトの物理入出力と専用Sinkへの受け渡しだけを行い、RSP ParserはDebugger Plugin側にだけ存在する | platform_driver.md |
| TEST-HAL-15 | ファイルSinkへのロガー出力分離 | `FileLogSink`を`System`へ注入し、標準出力用ドライバを起動 | システムログを1行出力してフラッシュし、ゲストの標準出力へ別のバイト列を書く | ログ行は注入したファイルSinkだけに現れ、標準出力にはゲストのバイト列だけが残る。ログ出力にロガーHALタスクの起動を要求しない | `BufferedLogging`, `HAL_Interface` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-HAL-01 | `HalBufferPool` のマップ境界厳格検査（隣接汚染防止） | 固定サイズバッファプール（各バッファ 256 バイト） | `map-buffer`またはI/O範囲で256バイトを超えるスライス（例: 512バイト）を要求 | 即座にassert違反で拒絶される。**実装の勘所**: プールアロケータでサイズ検証を省略すると、隣接する別スロットのバッファ領域へ書き込みがはみ出し、システム破壊を招く | `platform_driver.md` |
| GOTCHA-HAL-02 | UART パイプトランスポートの双方向独立性とノンブロッキング | UART トランスポート初期化 | 送信側が連続書き込み、受信側が非同期読み出し | 送信と受信が互いに干渉せず、EOF やバッファ満杯時にも安全にエラーまたはブロックなしで制御が返る。**実装の勘所**: 組み込み UART ドライバで送受信のリングバッファを不用意に共有すると、全二重通信時にデータ化けが発生する | `platform_driver.md` |
| GOTCHA-HAL-03 | 32bitタイマーの差分計算安全性（ラップアラウンド耐性） | 32bitカウンタ値の開始・終了サンプルがある | `platform_driver_concept.py` の `test_u32_timer_elapsed_handles_wraparound` を実行し、通常区間と `0xFFFFFFF0 -> 0x00000010` の折り返し区間を確認する | 通常区間は40 tick、折り返し区間は32 tickを返す。測定区間が1周期以上の場合は単一カウンタ値から復元できない。**実装の勘所**: 絶対値の大小比較ではなく、32bit符号なしモジュロ差分を使う | `platform_driver.md`, `platform_driver_concept.py` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（物理実装レベルの不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

### pysimの固定スロットとstdoutの観測

[`test_hal.py`](experiments/pysim/qa/tier3_platform/test_hal.py)の`test_hal_task_ipc_communication`は、TEST-HAL-06の操作期間マッピングをstream-writeの実経路で部分確認する。offset 0の128 byteとoffset 17の32 byteを固定スロットへ書く。専用HALタスクへのIPCの完了後、stdoutが指定sliceと完全一致し、送信元スロットが保持されることを確認する。続けてunmapし、同じハンドルのguest viewが拒否され、vMMIOアクセスが`UNREGISTERED_PAGE`となることを確認する。IPCの契約と実行記録は[`hal_dispatch_test_spec.md`](docs/qa/tier2_runtime/hal_dispatch_test_spec.md)のTEST-HAL-16を正本とする。

`test_hal_02_dummy_stdio_driver_streams_stdin_and_stdout`は、ドライバ単体のRxとTxの全byteを直接比較する。`test_hal_05_hal_buffer_slice_bounds_and_guest_mapping`は、別guestのアクセス拒否、ドライバ側の書込みが同じ固定スロットへ現れること、unmap後のアクセス拒否を確認する。いずれも実機UARTやDMAの検証済みとは扱わない。

`test_hal_06_rejection_preserves_mapping_and_all_slot_bytes`は、TEST-HAL-06とGOTCHA-HAL-01の拒否経路を10条件で検査する。
因子は別slotの同時map、負値と上限外ID、負のoffset/長さ、末尾外offset、末尾超過、257 byte、未mapのhandleと誤ったunmap対象である。
各拒否後に、選択slotのマッピング、全slotの全byte、他slotの未登録状態を保存する。
拒否後の末尾slice書込みは同じHAL実体へ届き、元slotをunmapした後は別slotの正常I/Oを開始できる。

- 実行日: 2026-10-02。
- 対象ソース: [`hal_dispatch.py`](experiments/pysim/tier2_runtime/hal_dispatch.py)。
- スイート: [`test_hal.py`](experiments/pysim/qa/tier3_platform/test_hal.py)。
- 環境: Linux、プロジェクトのuv環境。
- 結果: 成功23件、失敗0件、skip 0件、xfail 0件。

```bash
UV_CACHE_DIR=/tmp/fireball-test-refactor-uv uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier3_platform/test_hal.py
```

## 4. 未検証・スコープ外

- 契約レベルの振る舞い（TEST-HAL-01, TEST-HAL-02, TEST-HAL-04, TEST-HAL-09〜TEST-HAL-13）は [`hal_dispatch_test_spec.md`](docs/qa/tier2_runtime/hal_dispatch_test_spec.md) を参照。
- 実ハードウェア（UART/RTT/GPIO/I2C）そのものの電気的特性。
- stdoutの2入力によるpysim統合試験は、固定プールの全slot・全offset・全長、物理DMAのCPU非介在性を網羅しない。
