# PySIM AO-Bench 性能比較（2026-10-07）
<!-- traceability: {ThreadedInterpreter} {JIT_CopyAndPatch} {Wasm32Only} -->

## 現行結果

同一AO-Bench WASMをFireball NativeInterpreter、Fireball Hybrid JIT、WAMR Fast Interpreterで計測した。現行コードの計測対象はコミット`fd4a217f`である。

320×180ではNativeInterpreterが441.931 ms、Hybrid JITが1,154.761 ms、WAMR Fastが113.812 msだった。NativeInterpreterはWAMRの3.88倍、Hybrid JITはWAMRの10.15倍の時間を要した。Hybrid JITはNativeInterpreterより2.61倍遅い。現行計測ではWAMR Fastが最速である。

| サイズ | rays/フレーム | NativeInterpreter | Hybrid JIT | WAMR Fast | Interpreter/WAMR | JIT/WAMR |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| 64×36 | 7,332 | 18.053 ms（17.793–18.118） | 44.987 ms（44.484–45.620） | 4.568 ms（4.552–4.709） | 3.952× | 9.848× |
| 128×72 | 29,732 | 70.588 ms（70.386–73.706） | 184.216 ms（182.961–186.976） | 18.678 ms（18.375–18.718） | 3.779× | 9.863× |
| 192×108 | 67,032 | 158.473 ms（158.429–166.147） | 409.100 ms（409.037–410.958） | 41.005 ms（40.190–41.421） | 3.865× | 9.977× |
| 256×144 | 119,352 | 279.949 ms（278.552–282.710） | 725.306 ms（725.213–736.856） | 76.452 ms（75.529–76.980） | 3.662× | 9.487× |
| 320×180 | 186,792 | 441.931 ms（435.611–447.211） | 1,154.761 ms（1,148.529–1,157.830） | 113.812 ms（112.607–122.785） | 3.883× | 10.146× |

時間は各プロセス内7サンプルの中央値を取り、3独立プロセスの中央値を集計した値である。括弧内は3プロセスの中央値の最小値と最大値である。

## 計測条件

- ワークロードは [`aobench.wasm`](aobench/aobench.wasm)。5解像度について各エンジンを3独立プロセスで実行した。
- 各プロセスで3フレームをウォームアップし、後続7フレームを計時した。
- NativeInterpreterは計測プロセス内でyieldしきい値を`0xFFFFFFFF`にした。Hybrid JITは既定値64を使った。WAMRにFireballのyield機構はない。
- JIT診断実行で全解像度のトレース呼び出しを確認した。診断実行は時間に含めていない。
- CPUはAMD Ryzen 5 5500GT。Linux `7.0.0-38-generic`上でCPU 2に固定した。CPythonは3.13.15、Clangは21.1.8である。
- FireballのInterpreter/JIT共有ライブラリは現行コードからClang `-O2`でビルドした。
- WAMRはFast InterpreterとWASIを有効、AOT/JITを無効にした。WAMR 2.4.3をClang `-O2 -DNDEBUG`でビルドした。
- 15組すべてで、ウォームアップを含む全出力が3エンジン間でバイト単位に一致した。

## 比較上の制約

計時区間からWASM解析、Runtime初期化、ロード、インスタンス化を除外した。FireballはNativeInterpreterまたはRuntimeEngineのcallを測定し、WAMRは`wasm_runtime_call_wasm()`を測定した。

Fireballの標準出力はPython WASI/HALとメモリ内`StreamTransport`を通る。WAMRの標準出力はC runtime WASIと一時ファイルを通る。したがって、この値はホスト統合を含むワークロード比較であり、opcodeディスパッチまたは生成コード単体の比較ではない。

NativeInterpreterとHybrid JITではyield間隔が異なる。Hybrid JITの測定時間にはRuntimeEngine境界と計測中に発生するJITランタイム処理が含まれる。生成コード本体の性能を判断するには、これらを分離した計測が必要である。

## 詳細記録

試行ごとのサンプル、出力ハッシュ、JIT診断カウンター、WAMRのビルド情報は[3エンジン比較ログ](results/aobench_interpreter_jit_wamr_20261007.txt)に記録した。
