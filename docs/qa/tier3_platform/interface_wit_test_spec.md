# WITインターフェース / リカバリー戦略 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`interface_wit.md`](docs/components/tier3_platform/interface_wit.md)
参考実装: [`libfireball.py`](experiments/pysim/tier3_platform/libfireball.py) と専用host-callの参照モデルを用いる。WIT宣言とホスト側の契約適合を検査し、ゲストC/C++ライブラリの静的リンクは対象外とする。

`recovery-strategy-category`（ignore/retry/restart/panic）、低レベルトラップインターフェース（`fireball-call`）、vIRQ/vDMA専用ホストコール、コンソール生バイト出力経路に関する契約を検証する。個別デバイスのIPCコマンドID実装（GPIO/タイマー/バス等）は [`hal_dispatch_test_spec.md`](docs/qa/tier2_runtime/hal_dispatch_test_spec.md) / [`platform_driver_test_spec.md`](docs/qa/tier3_platform/platform_driver_test_spec.md) の責務とする。

## 2. テストケース一覧

### リカバリー戦略

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WIT-01 | `ignore`の選択基準 | 一時的なバッファ空/満杯通知など、データ喪失を伴わない事象 | 該当操作を発生させる | `ignore`が返り、状態変化なく呼び出し元が継続する | 表、`test_recovery_05_ignore_returns_original_result_without_recovery_actions` |
| TEST-WIT-02 | `retry`の選択基準とバックオフ | 一時的なリソース競合・タイムアウト | 失敗操作を発生させ、待機時間と呼出し順序を記録する | `FB_CONF_RETRY_BACKOFF_MS`（既定10ms）待機後に再試行する。1・2・3回目の成功後は操作を再実行せず、sleep・reset・panicも実行しない。連続失敗3回で打ち切り、失敗の間の2回だけ待機する | system_config.md、`test_recovery_01_retry_success_within_limit`、`test_recovery_02_retry_exhaustion_escalates_to_restart` |
| TEST-WIT-03 | `retry`上限到達後の挙動 | 3回連続失敗 | resetの成功と失敗を与える | `restart`へエスカレーションし、resetを1回呼び出す。成功時も元操作を再実行せず、元errorとRESTARTを返す。失敗時は`panic`を1回通知し、追加retryを開始しない | `interface_wit.md`のリトライ上限の設計判断、`test_recovery_02_retry_exhaustion_escalates_to_restart`、`test_recovery_07_unrecoverable_restart_escalates_to_panic_once` |
| TEST-WIT-04 | `restart`の選択基準 | サービスコンテキスト/メモリ破損の疑い | 該当操作を発生させる | 該当タスク/サービスのTCB・ヒープが初期化され再起動する。他サービス・カーネルのメモリ空間は隔離される | 表、`test_recovery_06_restart_skips_retry_backoff_and_invokes_reset_once`はreset通知と待機なしだけを検査する |
| TEST-WIT-05 | `panic`の選択基準 | MPU違反・二重解放・デッドロック検知 | 該当操作を発生させる | 全タスク停止、クラッシュダンプ出力、フェイルセーフ停止 | 表、`test_recovery_03_panic_invokes_hook_immediately_without_retry_or_reset`はpanic通知と追加操作なしだけを検査する |

### 低レベル・トラップインターフェース
<!-- traceability: {WIT_Interface_Spec} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WIT-10 | WIT由来raw Core Wasm binding | raw host-call WITとClang・llvm-ar・wasm-ldがある | WITを生成器へ渡し、bindingをarchiveへ格納してguestへリンクする | 実guestのimportが4fieldと規定のi32型になる。archiveを省くとリンク失敗する。未対応WITは生成失敗する | `test_wit_generated_static_guest_raw_four_imports`、`test_wit_guest_requires_static_archive`、`test_wit_raw_generator_rejects_unsupported_contract` |
| TEST-WIT-11 | Trigger(GPIO)の直接マッピング | `FB_SYSCALL_TRIGGER_SET_PIN`等 | `fireball_call`に直接該当IDを渡す | ハンドルルックアップを経由せず直接操作される | WIT_Interface_Spec |

### vIRQ / vDMA 専用ホストコール

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WIT-12 | vIRQ専用importの分離 | `fireball:host/virq` が公開されている | `register` / `unregister` を呼び出す | 汎用 `fireball_call` のIDディスパッチを経由せず、vSoCの保留表へ渡される | `test_syscall.py`のTEST-SYS-30〜32、`test_libfireball_dedicated_host_calls` |
| TEST-WIT-13 | vDMA専用importの分離 | `fireball:host/vdma` が公開されている | `start` を呼び出す | 汎用 `fireball_call` のIDディスパッチやVDMA vMMIOレジスタを経由せず、転送要求へ渡される | `test_syscall.py`のTEST-SYS-20/21、`test_libfireball_dedicated_host_calls` |
| TEST-WIT-14 | Core Wasm C ABI写像 | ホスト側のimport resolverがある | `fireball` moduleの4fieldを解決し、各引数と戻り値を照合する | 各操作が対応portへ渡る。別moduleと不明fieldは解決されない | `test_wit_14_core_wasm_import_names_select_the_matching_host_port`、`test_wit_generated_static_guest_raw_four_imports`、`test_wit_guest_requires_static_archive` |
| TEST-WIT-15 | HAL worldの公開方向 | ゲストが`fireball-hal` worldを利用する | worldのimport／exportを確認する | `types`と`resolver`はホスト提供importであり、ゲスト実装を要求するexportが存在しない | `test_wit_15_hal_world_imports_host_services_without_guest_exports` |

### コンソール生バイト出力経路 (`fireball://hal/stdout/0`)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WIT-20 | 任意長生バイト列の出力 | ゲストが`print`/`eprint`相当を実行 | `resolver.get-interface("fireball://hal/stdout/0")`、固定バッファスロット、`stream-write`を順に利用 | データがそのまま物理トランスポートへ渡される（辞書変換もリングバッファ構造化もされない） | `libfireball_test_spec.md` |
| TEST-WIT-21 | 内部ロガーとの排他性なし（インターリーブ許容） | 内部ロガーのflushとコンソール出力経路の書き込みが同時期に発生 | 両方を実行 | 出力順序の保証はされない（インターリーブし得る）ことを仕様として確認する（バグではない） | 末尾 |
| TEST-WIT-22 | WASI_FD_WRITE→コンソール出力経路への自動ルーティング | ゲストの`print`/`eprint` | `libfireball` が `fireball_call(WASI_FD_WRITE,...)`を発行 | `fireball://hal/stdout/0`を解決し、HALの`stream-write`へ変換される | `libfireball_test_spec.md` |
| TEST-WIT-23 | WASI親和性のあるHAL汎用操作 | `resolver` が公開されている | `stream-read/write`、`stream-flush/close`、`clock-get-now/resolution`、`poll-check/wait`の型を確認 | 個別デバイスresource型なしに、同じハンドル境界で操作できる | `hal_dispatch.md` |

## 3. テスト検証実績と網羅状況

リカバリーの実行コマンドを示す。

```bash
uv run pytest -q experiments/pysim/qa/tier2_runtime/test_recovery.py
```

8ケースでTEST-WIT-01〜05の戦略選択、既定10msの待機値、3回上限、reset・panic通知順序を検査する。待機はspyで記録し、実時間を待たない。最大3回の初期操作とresetを区別する。reset成功後に元要求を再実行せず、元のエラーとRESTART戦略を返す。`TEST-RECOVERY-*`と存在しない`system_recovery_spec`への旧参照を、本書のTEST-WIT IDへ置き換えた。

TEST-WIT-12/13は専用ホスト入口の転送と状態を検査する。TEST-WIT-14はホスト側のCore Wasm名解決を検査する。TEST-WIT-15はコメントを除いたworld宣言のimport/export方向を検査する。WITの構文検査と機能適合を区別する。

実行コマンドを示す。

```bash
uv run pytest -q experiments/pysim/qa/tier3_platform/test_libfireball.py experiments/pysim/qa/tier2_runtime/test_syscall.py
```

実行結果は [`test_reconstruction_review.md`](docs/qa/test_reconstruction_review.md) を参照する。

## 4. 未検証・スコープ外

- TEST-WIT-10/14はWIT由来bindingの生成、静的archive経由のguestリンク、raw 4 importの実NativeInterpreter実行で検査する。Component ModelのCanonical ABI、WASI Preview1およびHAL loweringのguest統合は対象外である。
- TEST-WIT-01〜05のリカバリー選択は`test_recovery.py`で検査する。対象サービスだけの再起動、全タスク停止、クラッシュダンプの実動作は同試験の合格だけでは検証済みとしない。

- 個別デバイス（GPIO/タイマー/バス/ストリーム）のIPCコマンドID実装は [`hal_dispatch_test_spec.md`](docs/qa/tier2_runtime/hal_dispatch_test_spec.md) / [`platform_driver_test_spec.md`](docs/qa/tier3_platform/platform_driver_test_spec.md) を参照。
- `input-stream`/`output-stream`の詳細な非同期セマンティクス（wasi:io標準への準拠度）。
- `wasi:filesystem`のPASSTHROUGH/SHM「事前オープン済み仮想ファイル記述子」エミュレーション（-4）。
