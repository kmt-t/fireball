# PySIM AO-Bench 性能比較（2026-10-07 再計測）
<!-- traceability: {ThreadedInterpreter} {JIT_CopyAndPatch} {Wasm32Only} -->

## 現行結果

現行作業ツリーからInterpreter/JIT共有ライブラリを再ビルドし、同じAO-Bench WASMとWAMR Fast Interpreterを比較した。320×180ではNativeInterpreterが445.210 ms、Hybrid JITが663.797 ms、WAMR Fastが114.060 msだった。Hybrid JITはNativeInterpreterより1.491倍、WAMRより5.820倍の時間を要した。今回もJITはInterpreterに追いついていない。

| サイズ | rays/フレーム | NativeInterpreter | Hybrid JIT | WAMR Fast | JIT/Interpreter | Interpreter/WAMR | JIT/WAMR |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 64×36 | 7,332 | 18.744 ms（18.185–18.853） | 27.553 ms（27.208–28.071） | 4.662 ms（4.558–4.721） | 1.470× | 4.021× | 5.910× |
| 128×72 | 29,732 | 73.199 ms（71.790–73.205） | 107.491 ms（106.714–108.262） | 18.659 ms（18.434–18.748） | 1.468× | 3.923× | 5.761× |
| 192×108 | 67,032 | 162.514 ms（159.873–162.532） | 241.025 ms（237.522–242.424） | 41.095 ms（40.537–41.382） | 1.483× | 3.955× | 5.865× |
| 256×144 | 119,352 | 287.855 ms（286.902–289.428） | 424.883 ms（423.209–425.743） | 73.873 ms（72.434–74.125） | 1.476× | 3.897× | 5.752× |
| 320×180 | 186,792 | 445.210 ms（444.360–446.494） | 663.797 ms（662.301–665.432） | 114.060 ms（113.253–115.204） | 1.491× | 3.903× | 5.820× |

時間は各独立プロセス内7サンプルの中央値を取り、3プロセスの中央値を代表値とした。括弧内は3プロセス中央値の最小値と最大値である。

## 計測条件

- WASMは[`aobench.wasm`](aobench/aobench.wasm)、SHA-256は`9f5dc300e96641bf040551f9f56cb6e5bda084e98bde83a3fb1cff9a719d49e9`。5解像度×3プロセスを計測し、全15組の出力フレームがバイト単位で一致した。
- 各プロセスで3フレームをウォームアップし、後続7フレームを計時した。試行・解像度ごとに実行順を入れ替えた。
- NativeInterpreterとHybrid JITは同じ`NativeInterpreter.call()`入口を使い、Interpreterのyieldしきい値を`0xFFFFFFFF`にした。Pythonから`step_native()`を反復していない。
- JITはウォームアップ後、Interpreterの`idle_hook`経由で待ち列を計時外に排出した。各解像度で2本のtraceを計時開始前にコンパイルした。計時区間は新規コンパイル予算を0にした。計時後の独立診断で全解像度のJIT trace実行を確認した。
- WAMRはFast InterpreterとWASIを有効にし、AOT/JITを無効にした。Fireballのyield機構はWAMRにはない。
- CPUはAMD Ryzen 5 5500GT、Linux kernel `7.0.0-38-generic`。CPU 2に固定した。CPythonは3.13.15、Clangは21.1.8。
- InterpreterとJITの共有ライブラリは2026-10-07に作業ツリーから各`build_native.sh --release`で再ビルドした。最適化条件はClang`-O2 -DNDEBUG`。JIT fast slot数は16。FireballはC++23を使用する。
- WAMRはversion 2.4.3、commit`f5f57c09aee623436f5fb87a90798fdd2cdf39fd`。ソース作業ツリーはcleanで、前回と同じClang`-O2 -DNDEBUG`のrunnerを再利用した。WAMRはプロジェクト既定のC/C++標準でビルドした。
- FireballのWASM解析、System/WASI/Interpreter生成と、WAMRのruntime初期化、WASMロード、instantiate、exec environment生成は計時区間から除外した。Fireballは各フレームの`NativeInterpreter.call()`、WAMRは`wasm_runtime_call_wasm()`を計時した。

## 比較上の制約

Fireballの出力はPython WASI/HALとメモリ内`StreamTransport`を通り、WAMRの出力はC runtime WASIと一時ファイルを通る。したがって、値はホスト統合を含むワークロード比較であり、opcodeディスパッチまたは生成コード単体の比較ではない。x64ホスト上の結果をM33の実行時間やROM/RAM量へ外挿しない。

## 詳細記録

全サンプル、各試行の中央値、出力ハッシュ、JIT診断カウンターを[今回の計測ログ](results/aobench_interpreter_jit_wamr_20261007.txt)に記録した。以前のAO-Bench比較値はこの現行レポートとログに残していない。
