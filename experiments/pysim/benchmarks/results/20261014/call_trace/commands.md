# REQ-019 呼出し・トレース計測の再実行条件

対象ソースの HEAD は `8875be18f53ab460b55c8acb600d859afb8a7d47`、計測開始時の作業ツリーは clean である。測定結果は各条件を3つの独立プロセスで実行し、各プロセスで1回準備実行後に7回計時した。全プロセスを CPU 2 に固定した。各JSONには全サンプルとプロセスごとの中央値を保持する。

## A-1 / assert 有効

```bash
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
for run_idx in 1 2 3; do
  taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/interpreter/bench_call_dispatch.py \
    --variant native --iterations 10000 --repeats 7
  taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/interpreter/bench_call_dispatch.py \
    --variant native --iterations 3000 --repeats 7
done
```

コンパイルは Clang 21.1.8、`-std=c++23 -O3 -flto=thin -fuse-ld=lld` を使い、`-DNDEBUG` を付けない。

## A-2 / NDEBUG

```bash
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
for run_idx in 1 2 3; do
  taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/interpreter/bench_call_dispatch.py \
    --variant native --iterations 10000 --repeats 7
  taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/interpreter/bench_call_dispatch.py \
    --variant native --iterations 3000 --repeats 7
done
```

A-1 と同じフラグへ `-DNDEBUG` を追加する。

## C / WAMR

WAMR の固定ソースは `f5f57c09aee623436f5fb87a90798fdd2cdf39fd`。使用した既存 build の `CMakeCache.txt` を読み、Release、`-O3 -DNDEBUG -flto=thin`、Fast Interpreter 有効、AOT/JIT/Fast JIT 無効、WASI 有効を確認した。ライブラリは `build/wamr-benchmark/libiwasm.so`、SHA-256 は `2a288b7b4e74de2bc26693cb51c6e34b9160f45b4f037a5e9e4deaa8f708a960` である。

同じbuildを再構成する場合の設定は次のとおりである。今回は既存buildのrevision・cache・library hashを検査して再利用した。

```bash
cmake -S third_party/wasm-micro-runtime/product-mini/platforms/linux -B build/wamr-benchmark \
  -DCMAKE_C_COMPILER=clang -DCMAKE_BUILD_TYPE=Release \
  '-DCMAKE_C_FLAGS_RELEASE=-O3 -DNDEBUG -flto=thin' \
  '-DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=lld' \
  '-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=lld' \
  -DWAMR_BUILD_INTERP=1 -DWAMR_BUILD_FAST_INTERP=1 -DWAMR_BUILD_AOT=0 \
  -DWAMR_BUILD_JIT=0 -DWAMR_BUILD_FAST_JIT=0 -DWAMR_BUILD_LIBC_WASI=1 \
  -DWAMR_BUILD_LIBC_BUILTIN=0 -DWAMR_BUILD_SIMD=0 -DWAMR_BUILD_REF_TYPES=0 \
  -DBUILD_SHARED_LIBS=ON
cmake --build build/wamr-benchmark -j 4
```

```bash
.venv/bin/python -c 'import sys; sys.path.insert(0, "experiments/pysim/benchmarks/interpreter"); from bench_call_dispatch import WAT; open("/tmp/req019_guest.wat", "w", encoding="utf-8").write(WAT + "\n")'
wat2wasm /tmp/req019_guest.wat -o /tmp/req019_guest.wasm
for run_idx in 1 2 3; do
  taskset -c 2 .venv/bin/python /tmp/req019_wamr_call.py \
    build/wamr-benchmark/libiwasm.so /tmp/req019_guest.wasm 10000 7
  taskset -c 2 .venv/bin/python /tmp/req019_wamr_call.py \
    build/wamr-benchmark/libiwasm.so /tmp/req019_guest.wasm 3000 7
done
```

WAMRの各実行は初期化・ロード・instantiate・関数検索を計時外に置き、各関数を1回準備実行後、`wasm_runtime_call_wasm` のみを7回計時した。全準備・計時実行で返値を `n*(n+1)/2` と照合した。一時runnerのSHA-256は `7ce0504122add2a15ec4776d6f8512cefe6e1ee20c49b650d4fec6618c4c9aec`。

PySIM は Wasmtime、WAMR は WABT 1.0.36 で同じWATを変換した。WASMバイナリのSHA-256は異なるが、両バイナリの正規化WATと関数命令列は一致する。WAMR側の差はWasmtimeが付加するname custom sectionである。

## D / JIT 時間

```bash
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
```

JIT無効・有効の両方で、direct/indirect、10,000/3,000反復をそれぞれ3プロセス実行した。各プロセスはJIT無効で1回、JIT有効でトレース生成後の定常状態を1回準備し、その後7回計時した。タイマーは `NativeInterpreter.call()` のみに置き、JIT compile・WASM parse・module registrationは含めない。一時runnerのSHA-256は `36261db472abbf8fa9bf4ee7739657b62d339d8ac5c5b210d97a85b67e1143e9`。

## D / 診断カウンタ

```bash
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --qa --release
bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --qa --release
for run_idx in 1 2 3; do
  taskset -c 2 .venv/bin/python /tmp/req019_jit_diag.py 10000 direct
  taskset -c 2 .venv/bin/python /tmp/req019_jit_diag.py 10000 indirect
  taskset -c 2 .venv/bin/python /tmp/req019_jit_diag.py 3000 direct
  taskset -c 2 .venv/bin/python /tmp/req019_jit_diag.py 3000 indirect
done
```

QA専用 `RuntimeStatsEngine` のカウンタを使用し、時間は測っていない。各ケースで3回とも期待値とカウンタが一致した。一時runnerのSHA-256は `784764484e2f1ce505e890a19cc90755af15f687082457b144130de513bb802d`。

## 任意のuProf内訳

REQ-019 §4-E の関数別CPU内訳は任意項目のため実行していない。呼出し、WAMR、JIT診断の必須項目は完了している。
