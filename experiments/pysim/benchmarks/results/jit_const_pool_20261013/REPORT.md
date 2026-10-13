# JIT定数pool縮小と`br_table`注釈計測

2026-10-13に、JIT constant poolからInterpreter注釈の複製を除き、`br_table`注釈の容量計算・実書込形式を照合した。計時はQA counterを含まないrelease libraryで行い、項目別counterはQA buildで別に計測した。git commitは作成していない。

## K: JIT constant pool

AO-Bench 320×180の診断では、1回の計測で8,466 trace、平均5.626命令/trace、8,814 Runtime API recordを生成した。変更後のdiagnostic runは8,342 trace、平均5.575命令/trace、8,684 Runtime API recordだった。trace数はwarm-up中のJIT候補の動的変化を受けるため、before/after間で完全一致する入力件数として扱わない。

| pool項目 | 変更前 | 変更後 | 差 |
| :--- | ---: | ---: | ---: |
| Trace header参照 | 69,120 B | 33,368 B | −35,752 B |
| Runtime API slot/count | 35,256 B | 17,368 B | −17,888 B |
| source IP | 35,256 B | 34,736 B | −520 B |
| Runtime API immediate payload | 2,784 B | 1,710 B | −1,074 B |
| Interpreter注釈の複製 | 23,077 B | 0 B | −23,077 B |
| Runtime API payload span | — | 8,684 B | +8,684 B |
| traceごとのInterpreter注釈offset | — | 33,368 B | +33,368 B |
| **診断中に生成したpool累積byte数** | **165,493 B** | **129,234 B** | **−36,259 B（−21.91%）** |

変更後はtrace先頭にheader参照を一つ置き、各Runtime API recordでslot、immediate count、payload spanを各u8、source IPを`code_ip_t`、immediateを型幅で保持する。trace末尾のannotation offsetから関数所有のInterpreter annotation poolを参照する。値1〜5のうち注釈複製を除き、header参照をtraceごとにまとめた。AO-Benchのafter runにおける他の解像度のpool累積量は次のとおりである。

| 解像度 | trace数 前→後 | 平均命令/trace 前→後 | Runtime API数 前→後 | pool bytes 前→後 | 減少率 |
| :--- | ---: | ---: | ---: | ---: | ---: |
| 64×36 | 365→362 | 5.671→5.649 | 379→377 | 7,125→5,610 | 21.26% |
| 128×72 | 1,349→1,355 | 5.610→5.627 | 1,410→1,416 | 26,498→21,057 | 20.53% |
| 192×108 | 3,027→3,032 | 5.620→5.626 | 3,153→3,154 | 59,231→46,944 | 20.74% |
| 256×144 | 5,295→5,452 | 5.586→5.610 | 5,523→5,681 | 103,756→84,528 | 18.53% |
| 320×180 | 8,466→8,342 | 5.626→5.575 | 8,814→8,684 | 165,493→129,234 | 21.91% |

| 320×180 Runtime API opcode | 変更前 | 変更後 |
| :--- | ---: | ---: |
| `0x02` | 422 | 400 |
| `0x04` | 2,665 | 2,651 |
| `0x05` | 606 | 564 |
| `0x0b` | 2,643 | 2,670 |
| `0x0c` | 1,315 | 1,254 |
| `0x0d` | 815 | 803 |
| `0x3a` | 348 | 342 |

`suite.wasm`ではcompile-all診断で297 trace、平均9.525命令/trace、2,829命令、466 Runtime API recordを生成した。poolは8,963 Bから6,431 Bへ2,532 B（28.25%）減り、複製annotationは867 Bから0 Bになった。opcode別の全診断値は[`before.json`](before.json)と[`after.json`](after.json)の`runtime_api_opcodes`に保存した。この測定はcompile-allのpool使用量で、guestの出力hash比較ではない。

## L: `br_table`注釈

現行注釈はu16 label countと、defaultを含む各targetのu8制御深さ・u16 block indexである。実書込量と容量計算はともに`2 + 3 × (label_count + 1)` byteである。注釈pool構築後に計算byte数と実書込byte数をassertで比較する。

依頼Lの背景に記載された二重書き形式をWASMから再構成したbyte数と、現行形式を比較した。

| モジュール | `br_table`数 | default込みtarget数 | 二重書き形式（再構成） | 現行形式 | 減少 |
| :--- | ---: | ---: | ---: | ---: | ---: |
| AO-Bench | 0 | 0 | 0 B | 0 B | 0 B |
| `suite.wasm` | 11 | 56 | 436 B | 190 B | 246 B（56.42%） |

旧形式の再構成は、各tableの`4 × (label_count + 2) + 3 × (label_count + 1)` byte、現行形式は`2 + 3 × (label_count + 1)` byteで数えた。今回のbytecodeから抽出したlabel数（defaultを除く）は`3, 6, 14, 2, 2, 2, 2, 2, 2, 2, 8`だった。

Loader全体の現行annotation poolはAO-Bench 1,712 B、`suite.wasm` 20,177 Bだった。保存済みIJ計測snapshotにはAO-Bench 1,712 B、suite 20,121 Bとあるが、そのsource SHAは今回の`control_flow.py`と異なる。現行との差56 Bはsuiteの56 target recordへ制御深さ1 byteを追加した量に一致するため、このsnapshotは依頼L背景の旧二重書き形式の実測baselineとしては使わない。旧二重書き形式との246 B削減値は、当時の実行snapshotではなく、依頼L記載の形式からの再構成値である。保存済みIJ snapshotからはsuiteの関数別pool byte数を復元できなかったため、JSONではその内訳を未記録として明示し、aggregate値だけを保持した。

ラベル数0、1、255、256の容量計算テストを実行した。NativeInterpreterではdefaultのみ、1ラベル、255ラベルの分岐を実行した。書込長assertとテストの期待値はいずれも`2 + 3 × (label_count + 1)`に一致した。

## AO-Benchの計時

release libraryを使用し、5解像度、3独立プロセス、各プロセスで3 warmup・7 timed calls、CPU 2固定、yield threshold 4096で計測した。表の時間は3 process-medianの中央値である。5解像度すべてでInterpreter、JIT、WAMR Fastのframe SHA-256が一致した。

| 解像度 | NativeInterpreter ms | JIT ms | WAMR Fast ms | frame SHA-256 |
| :--- | ---: | ---: | ---: | :--- |
| 64×36 | 17.301 | 21.174 | 4.927 | `d44fed786dbb555722932e901bd3f56c56aa2daca6c594a67a858c4591e24567` |
| 128×72 | 68.195 | 78.932 | 20.078 | `e9f20b8f0414f7406aa2f3ea96738e1c7755d0148090bf72f8703cffd3d374a4` |
| 192×108 | 150.950 | 177.010 | 44.351 | `9f2df94afaad4a3750053789ce6be52a0b9b14d66d9b8584edf2718d1ac3c4f1` |
| 256×144 | 266.895 | 311.214 | 78.609 | `f35a578dc277b57ca3bb5f74bc9381297fab5afce787bc92c54e46536fa8d2a4` |
| 320×180 | 423.704 | 493.636 | 124.376 | `6d04ea80d9a1fd773c85d413057d64797f32dbb257a753d767a378d730d7d699` |

このK作業と同条件のpre-change timing runはないため、時間の改善率は算出できない。上表は変更後の性能記録であり、constant pool縮小がAO-Benchの速度を改善したという因果を示さない。

## 検証と制約

- `test_interpreter.py`のannotation、`br_table`、WASM trap選択は23 passed。別実行のtrap選択は14 passed。
- JIT runtimeのcall/indirect選択は10 passed。compact annotation経由の`call_indirect`を追加した3実行系差分テストは単独実行で1 passed、non-kernelのJIT differential全体は11 passedだった。
- JIT runtime trap選択は2 passed、除算・剰余trap選択は1 passed。
- C++ `check-src`は対象3ファイルでpass、0 warningだった。Python/pysimの`check-src`は今回追加した計測scriptの`object`型を検出したため具体型へ直して再実行し、型エラーは解消した。最終runは統合`qa/run_all.py`がゲートの60秒上限を超えて終了した。上記の絞ったpytestはpassしている。
- 対象文書の`check-doc`はworkspace全体のtraceability / obligation検査で319 errors、24 warningsとなった。今回追加したテスト仕様の誤った相対linkは修正済みだが、既存文書に多数の未定義keyword参照が残るため、document gate全体はpassしていない。
- `git diff --check`はpassした。GitはPowerShell build scriptのLF/CRLF変換に関する既存警告を表示した。
- Loaderの旧計測scriptは`--output`指定を処理せず既定のIJ結果pathへ書き込むため、再計測時にそのJSONを上書きした。保存済みIJレポートからaggregate値を戻し、復元できなかったsuiteの関数別pool内訳は`null`と注記した。現行LのLoader生データは`loader_sizes_after.json`へ別に保存した。以前のIJ `SHA256SUMS`は更新済みJSONのhashに合わせたが、同ファイルのshared-library checksumは後続K buildがnative librariesを置換したため一致しない。
- `test_jitr_59_clang_kernel_suite_matches_wasmtime_on_the_jit`は300秒でtimeoutした。短縮した`k_dispatch(4)`比較はWasmtime `0x06418a20`、NativeInterpreterとJIT `0x99424033`で不一致となった。NativeInterpreter単独でも同じ不一致を観測しているため、原因を今回のJIT pool変更に帰属できないが、suite全体の結果一致は確認できていない。
- AO-Benchの出力一致は実測で確認した。`suite.wasm`のcompile-all計測は出力hashを生成しない。したがって他の実WASM workloadに対する出力hash一致は未完了である。
- compile-allのpool byte数は診断中に生成した累積量であり、同時常駐RAM量ではない。固定capacity cacheでのresident occupancyを別途測定していない。

## 実行コマンドと識別子

Release build:

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
```

計時run:

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python experiments/pysim/benchmarks/aobench/compare_wamr.py --wamr-build /tmp/fireball-wamr-fast-o3 --output experiments/pysim/benchmarks/results/jit_const_pool_20261013/aobench_after.json --cpu 2 --trials 3
```

診断専用buildは両方とも`build_native.sh --qa --block-counters`で作成した。counter入りlibraryは計時に使っていない。変更前後のdiagnostic outputは[`before.json`](before.json)と[`after.json`](after.json)、Loader/`br_table`測定は[`loader_sizes_after.json`](loader_sizes_after.json)と[`br_table_measurement.json`](br_table_measurement.json)にある。計時JSONは[`aobench_after.json`](aobench_after.json)である。

| 対象 | SHA-256 |
| :--- | :--- |
| before diagnostic JSON | `357858163d39f6623b37f9190148ab8b619f8ae66029d0f9f9f788360f4bfab9` |
| after diagnostic JSON | `ded6eac0f926739a0f8bea4779ff0267eebaae289da5d760a4a97d2fd0e8ea3a` |
| AO-Bench timing JSON | `71c47ea5eea9b974a4429431fe8293b3a09250a2a1bc8ff95791ee95bab49652` |
| before QA JIT library | `644db79ed53a1676047a021c2b0b45272e871e5449db7a13ca05dcf579a0c286` |
| after QA JIT library | `23093e09c4cc463f05b0748dbc7c58fc62d4f3c722d8649b9277218b92aede89` |
| release Interpreter library | `f6508cd28700e141e270772ee06cd42672bbaefcf85487158acd5083f6381e14` |
| release JIT library | `27eb0141d0efad2a1ec52a7acecce6a395f2d6cd73108898c3bce84d5c3bb3c9` |
| AO-Bench WASM | `9f5dc300e96641bf040551f9f56cb6e5bda084e98bde83a3fb1cff9a719d49e9` |
| suite WASM | `15be324af0ae7708c3b25515b88173fb52a02dd750b1518a5f69704ea23511f6` |
| `trace_compiler.cxx` | `587fb1aad003ff91985db93d3f83c1d419c732cb920326d912a9c7d3014f4a3e` |
| `jit_runtime_apis.cxx` | `39fb3a1aa60bc71ad2a327c102b61b1c7f1ec5c12299f60e1bb87f944448ffa8` |
| `control_flow.py` | `fc95ef01a5cb008a754ec1fae2f305344cdf9c2be7a1c85b35ccf9b1f032f0a8` |
| `native_wasm_operations.cxx` | `3ab612001aac6dad9e1afef745f95637a28586f14ac47993786494e021d0c527` |

Clang 21.1.8、C++23、`-O3 -DNDEBUG -flto=thin -fuse-ld=lld`を計時build条件とした。WAMR Fastはcommit `f5f57c09aee623436f5fb87a90798fdd2cdf39fd`を使った。計測コマンドの一覧は[`commands.md`](commands.md)、全成果物と主要sourceのチェックサムは[`SHA256SUMS`](SHA256SUMS)に記録した。
