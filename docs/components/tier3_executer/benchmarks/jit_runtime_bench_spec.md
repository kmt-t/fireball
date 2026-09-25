# JIT コンパイラ & ランタイム ベンチマーク仕様書 (JIT Runtime Benchmark Specification)

## 1. 目的と対象範囲
<!-- traceability: {JIT_CopyAndPatch} {JIT_ZeroCompileCostTheorem} {LowLatencyJIT} {META_AccessDictionary} {META_BinarySearch} {ThreadedInterpreter} -->

正本: [`jit_compiler.md`](docs/components/tier3_executer/jit_compiler.md), [`jit_runtime.md`](docs/components/tier3_executer/jit_runtime.md)
参考実装: [`bench_jit.py`](experiments/pysim/benchmarks/jit/bench_jit.py)

Copy-and-Patch方式によるJITコンパイル速度（トレース結合＋リロケーションパッチ）、2-bitカードマーキング表（`bit_view<2>`）による$O(1)$ホットスポット事前判定、少数の疎なJITエントリをソート配列から二分探索するlookup時間、およびインタープリタ対JITネイティブ実行のスループット比を計測する。

## 2. ベンチマーク測定項目一覧
<!-- traceability: {JIT_ZeroCompileCostTheorem} {META_BinarySearch} -->

| ベンチマーク ID | 測定項目 | 前提条件 / 設定 | 計測指標 | 目標性能 / 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BENCHMARK-JIT-01** | Copy-and-Patch コンパイル速度 | 基本ブロック (BasicBlock) 4命令 | Traces/sec, µs/trace | 高速なステンシル結合（ゼロ最適化コスト） | `{JIT_CopyAndPatch}`, `JIT_ZeroCompileCostTheorem` |
| **BENCHMARK-JIT-02** | 1命令あたりコンパイル時間 | 各 WASM オプコードのパッチ時間 | ns/opcode | 線形スケール（$O(N)$）でパッチ完了 | `{LowLatencyJIT}` |
| **BENCHMARK-JIT-03** | 2-Bit カードマーキング状態判定 ($O(1)$) | `HotspotBitmap` / `bit_view<2>` | ns/check, M ops/sec | インタープリタ実行ループを阻害しない極低コスト | `jit_runtime.md` |
| **BENCHMARK-JIT-04** | 少数JITエントリのソート配列二分探索 | 3バンクに収まる疎なエントリ数（上限は設定容量から算出） | ns/lookup, M ops/sec | 正確なキー検索。JIT用Radix索引は使用しない | `META_BinarySearch` |
| **BENCHMARK-JIT-05** | ループ演算スループット比 (Interp vs JIT) | 100,000回算術ループ実行 | 実行時間 (ms), Speedup比 | 差分結果が一致し、JITトレースの実行カウンタが正であること | `jit_compiler.md` |
| **BENCHMARK-JIT-06** | 共通コード領域のCヘルパー呼出し | トレースヘッダの委譲先関数アドレス、対象ABIの共通呼出し入口 | ns/dispatch, M dispatch/sec, 副作用回数, 呼出しコードのバイト数 | 委譲先アドレスをトレース本体へ埋め込まず、共通コード領域の呼出しコードを経由して同じヘルパーへ到達すること | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) `{PositionIndependentCode}` |

## 3. 測定手順

### 3.1 共通測定契約

BENCHMARK-JIT-05 は、同一の `heavy_loop` WASM バイナリと100,000回の入力で3経路を比較する。各経路の結果は符号付きi32値 `704,982,704` と一致しなければならない。

Pythonハンドラ経路は `_step(..., stop_at_boundary=False)` を直接呼ぶ。C++インタープリタ経路はビルド済み拡張を通る `Interpreter.call()` を使う。Hybrid JIT経路は `JITInterpreter.call()` を使う。C++インタープリタとHybrid JITは、共通設定 `FB_CONF_RUNTIME_YIELD_THRESHOLD` の取得済み後方分岐数に達した時点でC++ディスパッチを止め、Python呼出し側へ戻る。RuntimeEngineをCOOSから駆動する場合、このstatusが協調yield境界となる。未対応命令、外部呼出し、トラップ、関数完了などの意味上必要な境界は回数条件より先に戻る。ベンチマークの両経路は同じ設定値を使用し、実行出力にも記録する。

計測区間には関数実行と実行状態の初期化を含める。WASM生成・ロード、実行器の準備、JITトレースの事前コンパイルは区間から除外する。通常の速度値はプロファイラを通さず、各経路3回の中央値で報告する。

実行診断カウンタ`FB_CONF_RUNTIME_PROFILE_STATS`は既定で無効にし、速度・cyclesの計測中も無効にする。通常のC++拡張ビルドではカウンタ収集コードを生成しない。QAまたは別計測でカウンタが要る場合は`FIREBALL_BUILD_RUNTIME_PROFILE_STATS=1`を指定して拡張を再ビルドする。RuntimeEngineのカウンタ収集は別途 opt-in とし、計時後の診断実行だけで有効にする。chainはtrace末尾から共通コード領域のchain dispatcherへ入り、そこで次trace bodyへtail-jumpする経路を指す。`native_dispatch_trace_transitions`はC++ handlerからJIT traceへのdispatcher遷移を数える別指標であり、chain回数には使わない。共通コードchain dispatcherの実行回数を数える専用指標は現状ないため、未取得の値として扱う。動的hotspot profilingの負荷比較では、同じtraceを事前準備して`--hotspot-profiling enabled`と`disabled`を実行する。OFF構成ではC++拡張を再ビルドし、ホットネス観測コード自体を除外する。

Intel CPUではVTune Hotspotsを使い、3経路を個別に収集する。AMD CPUではuProfのHotspotsとIBSを使い、Hybrid JIT経路を個別に収集する。各反復で期待結果を照合し、Hybrid JITでは常駐traceが事前に準備され、実行結果が一致することを確認する。C++インタープリタとHybrid JITの共通LOOP後方分岐yieldしきい値は出力された `loop_backedge_yield_threshold` と照合する。プロファイラ収集中の所要時間は通常の速度比較に使わない。

#### Linux ホストサイクル数 / 動的 WASM 命令

Linux `perf stat` の `cycles:u` を単独イベントで収集し、カウンタ値をスクリプトが出力する動的 WASM 命令数で割る。計測制御FIFOにより、モジュール生成・ロード・実行器準備・JITウォームアップを除き、反復実行中だけカウンタを有効にする。`taskset` で論理CPUを固定し、各経路を別プロセスで3回実行して中央値を報告する。

```bash
UV=${UV:-/home/t-matsu/.local/bin/uv}
export UV_CACHE_DIR=/tmp/fireball-uv-cache
export UV_OFFLINE=true
export UV_NO_SYNC=true

run_cycles() {
  local path="$1" repetitions="$2" hotspot_mode="${3:-enabled}"
  local work_dir control_fd ack_fd
  work_dir="$(mktemp -d /tmp/pysim-cycles.XXXXXX)"
  mkfifo "$work_dir/control" "$work_dir/ack"
  exec {control_fd}<>"$work_dir/control"
  exec {ack_fd}<>"$work_dir/ack"
  local status=0
  if perf stat -D -1 -e cycles:u \
    --control="fd:${control_fd},${ack_fd}" -- \
    taskset -c 2 "$UV" run --offline --no-sync python -u \
    experiments/pysim/benchmarks/jit/profile_arithmetic_path.py \
    --path "$path" --repetitions "$repetitions" \
    --hotspot-profiling "$hotspot_mode" \
    --perf-control-fifo "$work_dir/control" \
    --perf-ack-fifo "$work_dir/ack"; then
    status=0
  else
    status=$?
  fi
  exec {control_fd}>&-
  exec {ack_fd}>&-
  rm -rf "$work_dir"
  return "$status"
}

for trial in 1 2 3; do run_cycles python-handler 1; done
for trial in 1 2 3; do run_cycles native-interpreter 128; done
for trial in 1 2 3; do run_cycles hybrid-jit 10; done
for trial in 1 2 3; do run_cycles hybrid-jit 10 disabled; done
```

cycles収集コマンドでは実行統計を有効にしない。経路カウンタが必要な場合は、計時を伴わない診断実行を別に行う。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
  uv run --offline --no-sync python experiments/pysim/benchmarks/jit/profile_arithmetic_path.py \
  --path hybrid-jit --repetitions 10 --hotspot-profiling enabled --collect-runtime-stats
```

各試行の `perf stat` が出すサイクル数を、プログラム出力 `wasm_dynamic_instructions` で割る。`heavy_loop(100000)` の分母は1回につき1,300,012命令であり、反復回数を掛けた値が表示される。CPU番号 `2` はオンラインの論理CPUへ置き換えてよい。Intel・AMDとも同じ手順を使えるが、CPUモデルをまたいだ値は直接比較しない。`perf`でイベントが利用できない環境では、後述のVTune / uProf手順でホットスポットを調べ、サイクル数は未計測として記録する。

#### Intel VTune Hotspots

```bash
VTUNE=${VTUNE:-/opt/intel/oneapi/vtune/2026.4/bin64/vtune}
UV=${UV:-/home/t-matsu/.local/bin/uv}
export UV_CACHE_DIR=/tmp/fireball-uv-cache
export UV_OFFLINE=true
export UV_NO_SYNC=true
run_id="$(date +%Y%m%d-%H%M%S)-$$"
bash experiments/pysim/tier3_executer/interpreter/build_native.sh
bash experiments/pysim/tier3_executer/jit/build_native.sh
for path in python-handler native-interpreter hybrid-jit; do
  result_dir="/tmp/pysim-vtune-${run_id}-${path}"
  "$VTUNE" -collect hotspots -knob sampling-mode=sw \
    -knob enable-stack-collection=true -result-dir "$result_dir" -- \
    "$UV" run --offline --no-sync python -u \
    experiments/pysim/benchmarks/jit/profile_arithmetic_path.py --path "$path"
  "$VTUNE" -report hotspots -result-dir "$result_dir" \
    -report-output "/tmp/pysim-vtune-${run_id}-${path}-hotspots.txt"
done
```

#### AMD uProf Hotspots / IBS

```bash
UPROF=${UPROF:-/opt/AMDuProf_5.3-521/bin/AMDuProfCLI}
UV=${UV:-/home/t-matsu/.local/bin/uv}
export UV_CACHE_DIR=/tmp/fireball-uv-cache
export UV_OFFLINE=true
export UV_NO_SYNC=true
run_id="$(date +%Y%m%d-%H%M%S)-$$"
bash experiments/pysim/tier3_executer/jit/build_native.sh
"$UPROF" profile --config hotspots -g --detail \
  -o "/tmp/pysim-uprof-${run_id}-hotspots" \
  "$UV" run --offline --no-sync python -u \
  experiments/pysim/benchmarks/jit/profile_arithmetic_path.py \
  --path hybrid-jit --repetitions 12 --hotspot-profiling enabled
"$UPROF" profile --config ibs -g --detail \
  -o "/tmp/pysim-uprof-${run_id}-ibs" \
  "$UV" run --offline --no-sync python -u \
  experiments/pysim/benchmarks/jit/profile_arithmetic_path.py \
  --path hybrid-jit --repetitions 12 --hotspot-profiling enabled
```

コマンドはリポジトリルートから実行する。uvはプロジェクトの`.python-version`と`.venv`を使う。`UV_CACHE_DIR`はキャッシュを一時領域へ向け、`UV_OFFLINE`はuvによるネットワークアクセスを止める。uProfの結果はHotspotsとIBSで別々に一時領域へ保存する。

VTuneは各経路を別々に収集し、ソフトウェアサンプリングを使う。VTuneの収集・レポートとuProfのプロファイルは一時領域に保存する。

サイクル数はホストCPUのユーザーモード実行を対象にしたハードウェアイベントである。pysimのPython呼出し・ディスパッチ・実行制御も測定値に含まれる。JIT経路は複数のWASM命令をネイティブコードでまとめて処理するため、値は「動的WASM命令あたりのホスト実行コスト」であり、個々のWASM命令の単体レイテンシではない。

### 3.2 標準ベンチマーク

1. **コンパイル速度測定**:
   - `TraceCompiler.compile_trace()` に対し、算術基本ブロックを $N=10,000$ 回コンパイルし、1トレースあたりの平均所要時間を算出。
2. **カードマーキング & JITエントリ二分探索測定**:
   - `HotspotBitmap.get_state()` と3バンク内のソート済みエントリ配列に対するbinary lookupの単体スループットを $N=100,000$ 回計測。
3. **実行速度比較 (Differential Execution)**:
   - 同一の WASM 算術ループモジュールをPythonハンドラ、C++ネイティブハンドラ、Hybrid JITの3経路で実行し、計算結果の等価性と実行所要時間を比較する。各経路はTier 3 Executerの実装である。`Interpreter.call()` はビルド済みC++拡張を使うため、Pythonハンドラ経路の計測ではPythonハンドラを直接実行する。
   - ビルド済みC++インタープリタ拡張の実行時間は別の基準値として測定し、Pythonハンドラの速度比と混同しない。各経路の結果は直接比較して一致を検証する。
   - C++インタープリタとHybrid JITは同じ `FB_CONF_RUNTIME_YIELD_THRESHOLD` を使い、取得済みLOOP後方分岐数が設定値へ達した場合にPython/COOS境界へ戻る。C++インタープリタはJIT managerのホットスポットyieldを持たないため、計測対象のHybrid JITループを事前コンパイルし、計測中の追加ホットスポットyieldを避ける。
   - 各実行経路を同じ反復回数で3回計測し、中央値を報告する。JIT経路は計測前にトレースをコンパイルし、計測区間のJIT実行回数が0より大きいことを確認する。
4. **複雑処理の委譲測定**:
   - 対象ABIの関数契約に一致する関数アドレスをトレースヘッダへ設定し、ヘルパー契約別の共通コード入口を選択して実行する。ARMv8-Mのhelper入口と計測条件はTBDとする。x64整数ヘルパー入口は1入口あたり32バイトとして計測する。
   - 同一のトレースバイナリを別の実行可能バッファへコピーして呼び出し、共通コード領域の呼出しコードを経由することと副作用を直接 `assert` する。
   - pysimでは `ctypes` コールバックのPython遷移コストを含むため、組込みCの性能値とは分離して報告する。
