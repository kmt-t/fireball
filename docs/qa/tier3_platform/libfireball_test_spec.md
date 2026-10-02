# libfireball raw host-callバインディング テスト仕様書

## 1. 目的と対象範囲

正本: [`libfireball.md`](docs/components/tier3_platform/libfireball.md)

本書は [`libfireball.py`](experiments/pysim/tier3_platform/libfireball.py) の参照wrapperと、WIT由来のraw 4 importのC++ guest bindingを検証する。WASI Preview1アダプタの実装は対象外である。ホスト側WASIとvIRQ/vDMA処理は、対応するTier 2/3テスト仕様書で検証する。

## 2. テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-LIBFB-01 | 0〜6引数host-callの配置 | 記録用host-call portがある | `fireball_call0`〜`fireball_call6`を呼ぶ | syscall IDと引数が順序を保ち、不足引数は0で埋められる。0・非0・u32最大値の戻り値を透過する | `test_libfireball_host_call_argument_packing` |
| TEST-LIBFB-02 | 入力値のu32境界検証 | 符号なし32bit外の値を指定できる | 汎用のIDと6引数、専用の全引数に負値とu32上限超過を渡す | 全てのportを呼ぶ前に `assert` する。呼び出し記録は空のままとなる | `test_libfireball_rejects_non_u32_host_call_values`、`test_libfireball_rejects_invalid_dedicated_arguments` |
| TEST-LIBFB-03 | host-call戻り値のu32境界検証 | host-call portがu32外の値を返す | 汎用・専用wrapperへ負値とu32上限超過を返す | 不正戻り値を `assert` する | `test_libfireball_rejects_non_u32_host_call_result`、`test_libfireball_rejects_invalid_dedicated_results` |
| TEST-LIBFB-04 | 専用vIRQ/vDMA host-call転送 | 記録用portがある | register/unregister/startを呼ぶ | それぞれの引数が対応する専用portへ渡る。戻り値を透過し、汎用portは呼ばない | `test_libfireball_dedicated_host_calls` |

## 3. テスト検証実績と網羅状況

- TEST-LIBFB-01〜04を [`test_libfireball.py`](experiments/pysim/qa/tier3_platform/test_libfireball.py) で実行する。
- 同じスイートでTEST-WIT-14のホスト側module/field解決と、TEST-WIT-15のHAL world宣言も検査する。
実行コマンドを示す。

```bash
uv run pytest -q experiments/pysim/qa/tier3_platform/test_libfireball.py
```

- TEST-WIT-10/14はWITから生成したbindingをobjectと静的archiveへ変換し、コンパイル済みguestを実NativeInterpreterとCOOSで実行する。
- 最終guestのimport集合と型、0〜6引数の順序・0補完、専用入口、u32上位bitおよびguestが書いた戻り値を確認する。
- archiveなしのリンク失敗と未対応WIT契約の生成失敗も確認する。world前の空interfaceをworldへimportする有効な宣言配置も、対応範囲外として拒否する。
- Linux x86_64、Clang 21.1.8、llvm-ar-21、wasm-ld-21で50件成功、失敗0件、skip 0件を確認した。実機のABI適合は対象外である。
- build入口は [`build_guest.py`](tools/guest_bindings/build_guest.py) である。Clang 17以上、llvm-ar、wasm-ldが必要である。PATH外のツールは`FIREBALL_CLANG`、`FIREBALL_LLVM_AR`、`FIREBALL_WASM_LD`で指定する。

## 4. 未検証・スコープ外

- raw 4 import以外のゲスト側C/C++実装とComponent Model Canonical ABI。
- WASI Preview1 syscallの変換、URI Resolver、HALバッファの確保・返却。これらは本参照モデルの機能ではない。
- ホスト側vIRQ/vDMA検証と物理ドライバ処理。
