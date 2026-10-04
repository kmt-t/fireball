# WebAssembly MVP適合テスト結果

<!-- traceability: {ROMParsing} {LightweightVerifier} {ThreadedInterpreter} -->

## 再現可能な固定Core Spec Suite実行 (2026-10-04)

[`test_wasm_core_spec.py`](experiments/pysim/qa/workloads/test_wasm_core_spec.py) は、公式WebAssembly Core Spec Testsから固定した37個のMVP互換WASTファイルをFireballインタープリタで実行する。WAST入力はrevision `970c4116e644e2bf7acb39aab8b733db14ccdf28`へ固定し、WABT 1.0.36の`wast2json`で実行前に変換する。

| 項目 | 結果 |
| :--- | :--- |
| Core Spec選択セット | 37/37ファイル |
| 実行した期待値assert | 15,230件 |
| 成功 | 37 pytest cases |
| skip | 0件 |
| 失敗 | 0件 |
| 所要時間 | 2.11秒 |

```bash
.venv/bin/python tools/guest_bindings/fetch_wasm_core_suite.py
.venv/bin/python -m pytest -q -s experiments/pysim/qa/workloads/test_wasm_core_spec.py
```

この選択セットは公式Core Spec Suite全体ではない。未選択の機能、提案機能、埋込みimportを必要とするケース、対応外のテキスト形式ケースについて適合性を主張しない。従来の49ファイル集計は実行版・個別skip理由が記録されていない旧記録であり、上記の再現可能な実行結果とは別に保持する。

同日のWASMバイナリワークロード入口[`run_all.py`](experiments/pysim/qa/workloads/run_all.py)は、SDK/libcゲスト52件、HALゲスト11件、Core Specファイル37件の計3スイートを実行した。3/3スイート成功、失敗0件、所要時間5.46秒である。Core Specの15,230件は内部assert数であり、pytestの37件に加算していない。HALスタブの直接契約・IPC結合試験99件は、別の結合テストsuiteとして実行する。

## 結果概要

| 項目 | 記録 |
| :--- | :--- |
| 対象 | 選定したMVP互換WASTテスト49ファイル |
| 成功 | 16,082件 |
| skip | 358件 |
| 失敗 | 0件 |
| 完全性 | 公式WebAssembly Core Test Suite全体の完走ではない |

skipは未検証として扱う。成功件数には含めない。ファイル別の失敗件数はすべて0件である。

## 分類別集計

| 分類 | ファイル数 | 成功 | skip | 失敗 |
| :--- | ---: | ---: | ---: | ---: |
| 制御・関数 | 11 | 581 | 2 | 0 |
| 整数・スタック | 8 | 1,472 | 100 | 0 |
| 浮動小数点 | 10 | 11,937 | 80 | 0 |
| メモリ・バイト順 | 11 | 949 | 87 | 0 |
| モジュール・形式・名前 | 9 | 1,143 | 89 | 0 |
| **合計** | **49** | **16,082** | **358** | **0** |

## ファイル別結果

| 分類 | WASTファイル | 成功 | skip | 失敗 |
| :--- | :--- | ---: | ---: | ---: |
| 制御・関数 | `br_if.wast` | 117 | 0 | 0 |
| 制御・関数 | `forward.wast` | 4 | 0 | 0 |
| 制御・関数 | `labels.wast` | 28 | 0 | 0 |
| 制御・関数 | `left-to-right.wast` | 95 | 0 | 0 |
| 制御・関数 | `return.wast` | 83 | 0 | 0 |
| 制御・関数 | `stack.wast` | 5 | 0 | 0 |
| 制御・関数 | `start.wast` | 9 | 2 | 0 |
| 制御・関数 | `switch.wast` | 27 | 0 | 0 |
| 制御・関数 | `traps.wast` | 32 | 0 | 0 |
| 制御・関数 | `unreachable.wast` | 63 | 0 | 0 |
| 制御・関数 | `unreached-invalid.wast` | 118 | 0 | 0 |
| 整数・スタック | `const.wast` | 300 | 76 | 0 |
| 整数・スタック | `i32.wast` | 457 | 2 | 0 |
| 整数・スタック | `i64.wast` | 413 | 2 | 0 |
| 整数・スタック | `int_exprs.wast` | 89 | 0 | 0 |
| 整数・スタック | `int_literals.wast` | 30 | 20 | 0 |
| 整数・スタック | `local_get.wast` | 35 | 0 | 0 |
| 整数・スタック | `local_set.wast` | 52 | 0 | 0 |
| 整数・スタック | `local_tee.wast` | 96 | 0 | 0 |
| 浮動小数点 | `f32.wast` | 2,511 | 2 | 0 |
| 浮動小数点 | `f32_bitwise.wast` | 363 | 0 | 0 |
| 浮動小数点 | `f32_cmp.wast` | 2,406 | 0 | 0 |
| 浮動小数点 | `f64.wast` | 2,511 | 2 | 0 |
| 浮動小数点 | `f64_bitwise.wast` | 363 | 0 | 0 |
| 浮動小数点 | `f64_cmp.wast` | 2,406 | 0 | 0 |
| 浮動小数点 | `float_exprs.wast` | 794 | 0 | 0 |
| 浮動小数点 | `float_literals.wast` | 83 | 76 | 0 |
| 浮動小数点 | `float_memory.wast` | 60 | 0 | 0 |
| 浮動小数点 | `float_misc.wast` | 440 | 0 | 0 |
| メモリ・バイト順 | `address.wast` | 255 | 1 | 0 |
| メモリ・バイト順 | `align.wast` | 85 | 46 | 0 |
| メモリ・バイト順 | `data.wast` | 22 | 14 | 0 |
| メモリ・バイト順 | `endianness.wast` | 68 | 0 | 0 |
| メモリ・バイト順 | `load.wast` | 83 | 13 | 0 |
| メモリ・バイト順 | `memory_grow.wast` | 91 | 0 | 0 |
| メモリ・バイト順 | `memory_size.wast` | 38 | 0 | 0 |
| メモリ・バイト順 | `memory_redundancy.wast` | 4 | 0 | 0 |
| メモリ・バイト順 | `memory_trap.wast` | 180 | 0 | 0 |
| メモリ・バイト順 | `memory.wast` | 63 | 6 | 0 |
| メモリ・バイト順 | `store.wast` | 60 | 7 | 0 |
| モジュール・形式・名前 | `comments.wast` | 0 | 0 | 0 |
| モジュール・形式・名前 | `custom.wast` | 8 | 0 | 0 |
| モジュール・形式・名前 | `imports.wast` | 38 | 87 | 0 |
| モジュール・形式・名前 | `names.wast` | 482 | 0 | 0 |
| モジュール・形式・名前 | `nop.wast` | 87 | 0 | 0 |
| モジュール・形式・名前 | `token.wast` | 0 | 2 | 0 |
| モジュール・形式・名前 | `utf8-custom-section-id.wast` | 176 | 0 | 0 |
| モジュール・形式・名前 | `utf8-import-field.wast` | 176 | 0 | 0 |
| モジュール・形式・名前 | `utf8-import-module.wast` | 176 | 0 | 0 |

## この集計の制約

- 実行日、正確な実行コマンド、テストスイートのコミット、ランナーの版は元の集計に含まれていない。再現可能な実行記録ではない。
- skipのファイル別件数は記録されているが、各skipの個別理由は記録されていない。集計時の主な理由として、未対応のassertion種別、未対応の呼び出し形式、変換できないテキスト形式テストが挙げられている。
- `comments.wast` の成功・skipがともに0件であり、このファイルの振る舞いを検証した根拠はない。
- MVP互換として選定した範囲のみを集計した。公式Core Test Suite全体や、MVP以降の機能の適合性を示す結果ではない。

次回の実行では、テストスイートとランナーの版、実行日、正確なコマンド、環境、skip理由を保存し、再現可能な品質証跡にする。
