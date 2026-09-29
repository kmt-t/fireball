# libfireball 参照バインディング テスト仕様書

## 1. 目的と対象範囲

正本: [`libfireball.md`](docs/components/tier3_platform/libfireball.md)

本書は [`libfireball.py`](experiments/pysim/tier3_platform/libfireball.py) が実装する Fireball host-call の参照バインディングだけを検証する。これはゲスト側C/C++ライブラリやWASI Preview1アダプタの実装テストではない。ホスト側WASIとvIRQ/vDMA処理は、対応するTier 2/3テスト仕様書で検証する。

## 2. テストケース一覧

| テストケースID | テスト内容 | 前提条件 | 手順 | 期待結果 | 実装 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-LIBFB-01 | 0〜6引数host-callの配置 | 記録用host-call portがある | `fireball_call0`〜`fireball_call6`を呼ぶ | syscall IDと引数が順序を保ち、不足引数は0で埋められる | `test_libfireball_host_call_argument_packing` |
| TEST-LIBFB-02 | 入力値のu32境界検証 | 符号なし32bit外の値を指定できる | host-call wrapperへ負値を渡す | host-call portを呼ぶ前に `assert` する | `test_libfireball_rejects_non_u32_host_call_values` |
| TEST-LIBFB-03 | host-call戻り値のu32境界検証 | host-call portがu32外の値を返す | wrapperを呼ぶ | 不正戻り値を `assert` する | `test_libfireball_rejects_non_u32_host_call_result` |
| TEST-LIBFB-04 | 専用vIRQ/vDMA host-call転送 | 記録用portがある | register/unregister/startを呼ぶ | それぞれの引数が対応する専用portへ渡る | `test_libfireball_dedicated_host_calls` |

## 3. テスト検証実績と網羅状況

- 4ケースを [`test_libfireball.py`](experiments/pysim/qa/tier3_platform/test_libfireball.py) で実行する。
- テストは参照モデルの入力・出力契約に限定する。ゲストABIの実機動作を証明しない。

## 4. 未検証・スコープ外

- ゲスト側C/C++ `libfireball` の静的リンクとWIT import結線。リポジトリ内に実装がない。
- WASI Preview1 syscallの変換、URI Resolver、HALバッファの確保・返却。これらは本参照モデルの機能ではない。
- ホスト側vIRQ/vDMA検証と物理ドライバ処理。
