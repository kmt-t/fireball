# 物理リソース予算とC++実装規模の見積もり仕様書
<!-- traceability: {Resource_Estimation_Model} -->

## 1. 目的と評価範囲

<!-- traceability: {Resource_Estimation_Model} {Size_20KSLOC} {GLOBAL_StrictMemoryLimit} {ConsolidatedHeap} {ROMParsing} {META_ZeroCostAbstraction} -->
本書は、現行参照実装の資源計上値と、組み込みC++実装のROM / RAM予算を整理する。
容量条件の正本は [`requirement_list.md`](docs/requires/requirement_list.md) とする。
最小構成はCortex-M33 / RAM 32KB / ROM 96KBである。
想定構成はCortex-M33 / RAM 64KB / ROM 128KBである。
静的合計21KBと物理RAM 32KBを、最小構成の容量条件として確認する。
本書では21KBを21,504バイト、32KBを32,768バイトとして扱う。

最新計測日は2026-10-14（Asia/Tokyo）である。
計測対象のHEADはcommit `506d1213e2d3045fb2b7ccec9016ed2e0d1c0849`であり、計測開始時のworktreeはcleanである。
結果JSONにはHEAD、計測時のworktree状態、ビルド種別、CPU affinity、試行回数、対象ファイルのハッシュを保存する。
2026-10-07の計測結果は履歴として[`metadata.json`](experiments/pysim/benchmarks/results/20261014/pre_req017_latest/metadata.json)に保持する。
現行実装はbuild時にアセンブリからPIC raw stencilを生成し、実行時に固定長traceへ組み立てる。
このホスト用JIT経路のraw stencil ROM量とcompile stackを新たに計測した。値はx86_64ホストの結果であり、ARMv8-Mの適合を示さない。
現行製品側のJIT入口とネイティブライブラリを使う。
QA専用のJIT管理クラスと検査用ライブラリは、製品RAMとROMへ計上しない。

| 対象 | 計測入口 | 結果 |
| :--- | :--- | :--- |
| アリーナと製品JITの要求領域 | [`bench_runtime_memory.py`](experiments/pysim/benchmarks/memory/bench_runtime_memory.py) | [`runtime_memory.json`](experiments/pysim/benchmarks/results/latest/runtime_memory.json) |
| ソース規模、ホストELF、JIT管理構造体、コンパイル時stack | [`bench_resource_inventory.py`](experiments/pysim/benchmarks/memory/bench_resource_inventory.py) | [`resource_inventory.json`](experiments/pysim/benchmarks/results/latest/resource_inventory.json) |
| JIT stencil raw binary | [`generate_assembly.py`](tools/jit_stencil_gen/generate_assembly.py) | [`stencil_rom.json`](experiments/pysim/benchmarks/results/latest/stencil_rom.json) |
| 製品C/C++のSLOC | [`check_source_size.py`](tools/check_source_size.py) | [`sloc.json`](experiments/pysim/benchmarks/results/latest/sloc.json) |

アリーナの値は32ビット参照レイアウトとx86_64のネイティブABIを組み合わせたPySIM内の計上値である。
JITの要求領域とELFはx86_64ホストの値である。
PySIMのWASMリニアメモリは論理ゲストメモリであり、MCUの物理RAM量と同じ値ではない。
これらをPythonプロセスのRSSやARMv8-Mの確定使用量として扱わない。
対象ABI、物理配置、リンクmapがないため、本計測は物理RAM 32KBへの適合を証明しない。

## 2. 計測方法と計上範囲

<!-- traceability: {Runtime_BumpAllocator} {META_BumpAllocator} {Resource_Estimation_Model} -->
ランタイムが所有するアリーナをLoaderとInterpreterへ共有する。
ロード後、インスタンス生成後、各呼出し後に `BumpAllocator.offset` を取得する。
使用量には確保した領域とアラインメントによる空きを含める。
解放した呼出しワークスペースは、後続呼出しで再利用する。
解放後も予約量は減らないため、最終使用量を観測した最大値として計上する。

| アリーナへ計上する対象 | 計上方法 |
| :--- | :--- |
| WASMの型、関数、インポート、エクスポート、グローバル変数、テーブルの記述情報 | Loaderのレコード件数と参照レイアウトの単価から求める |
| 関数型索引、局所変数型、局所変数幅、基本ブロックと直接参照metadata | Loaderが解析したモジュールに対応する領域を確保する |
| 実体のグローバル変数と関数テーブル | インスタンス生成時に確保する |
| ネイティブ実行用のモジュール、関数、型、テーブル、ブロックの借用記述 | 初回実行時にホストABIの構造体サイズから計上する |
| 実行コンテキスト、値スタック、局所変数スタック、呼出し情報、制御情報 | 呼出しワークスペースを確保し、後続呼出しで再利用する |

メモリ計測は、製品側の `RuntimeEngine` と `JITRuntimeManager` で実行する。
QA用の実行カウンタと診断dispatcherは、この計測経路へ含めない。
供給側の `region_provider` で、JITが要求したサイズとアラインメントを記録する。
この領域にはJIT管理構造体、モジュール長に応じるprofile bitmap、ページ配置の空き、実行コード領域を含む。
アリーナとは別領域であるため、要求サイズを一度だけ足す。
構造体内の実行拡張と履歴を、要求領域外の別領域として再度足してはならない。
実行コード8,192バイトも要求領域に含む。4,096バイト単位のデータ・コード配置はx64実装の制約であり、Cortex-M33の物理配置へ外挿しない。

WASMブロックの借用記述は `NativeModuleExecution` が所有する。
JITはこの記述を参照し、別のブロック登録配列を所有しない。
ブロック記述と関数記述はネイティブ実行用借用ビューとしてアリーナへ計上済みである。
QA専用のブロック登録配列、結線レコード、dispatch snapshotは測定対象へ含めない。

各入力についてInterpreter単独とHybrid JITを別アリーナで3回ずつ実行した。
戻り値と出力バイト列を両方式で照合する。
WASIを使わない入力の戻り値はWasmtimeとも照合する。
AO-Benchでは出力長528バイトを検証する。
2回目と3回目のアリーナ使用量が同じであることを確認する。
確保量とアラインメントの空きの合計が、最終使用量と一致することを確認する。
同じ入力をQA側の `RuntimeStatsEngine` と診断dispatcherで別に実行する。
この別実行で、生成したtraceの実行回数が正であることを確認する。
今回のtrace実行数は、算術ループ5,875回、間接呼出し475回、複合カーネル4回、AO-Bench254,279回である。
戻り値と出力ハッシュを製品経路の結果と照合する。
QA側の領域と診断ライブラリは、製品RAMとROMへ計上しない。
領域要求は各入力の3呼出しを通じて1回である。

```bash
bash experiments/pysim/native/tier1_core/printk/build_native.sh
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
bash experiments/pysim/native/tier3_plugins/jit/build_native.sh
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --qa
uv run python experiments/pysim/benchmarks/memory/bench_runtime_memory.py \
  --calls 3 --output /tmp/fireball-runtime-memory.json
```

この測定は指定入力と単一実行コンテキストに対する結果である。
複数の中断コンテキストや全容量の組合せに対する上限証明は含まない。

## 3. ランタイムメモリの計上結果

<!-- traceability: {Resource_Estimation_Model} {Runtime_BumpAllocator} {JIT_MultiBuffer_Cache} {GLOBAL_StrictMemoryLimit} -->
測定環境はx86_64、ポインタ幅8バイトである。
表の数値はバイト単位である。

| 入力 | 関数数 / 基本ブロック数 | ロード後 | インスタンス生成後 | Interpreter単独のアリーナ最大使用量 | Hybridのアリーナ最大使用量 | ゲスト論理リニアメモリ | x64 JIT要求領域 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| arithmetic | 1 / 7 | 390 | 390 | 3,184 | 3,184 | 0 | 12,288 |
| indirect_calls | 4 / 24 | 1,263 | 1,268 | 4,296 | 4,296 | 0 | 12,288 |
| kernel_suite | 28 / 885 | 51,911 | 51,956 | 56,712 | 56,712 | 65,536 | 12,288 |
| AO-Bench（32×16） | 7 / 107 | 5,741 | 5,741 | 8,968 | 8,968 | 65,536 | 12,288 |

表の「ゲスト論理リニアメモリ」はWASMの論理サイズである。PySIMではホストbytearrayがbackingとなるが、Cortex-M33の物理RAM量ではない。対象のCortex-M33構成では論理アドレス窓10KB、物理RAM全体32KBを別々に扱う。このホスト測定で64KBと表示される値を物理RAMへ加算してはならない。

Hybrid JITのアリーナ使用量はInterpreter単独と同じだった。各入力を3回呼び出した後の値は、同一入力内ですべて同じだった。この計測はJIT管理領域とアリーナ使用量を対象とし、native compilerのC++ stackとは分けて記録する。現行trace compilerは最大1,568バイトの出力を実行コードregion内に構成する。

現行x64製品JIT runtimeの固定管理構造体は`sizeof`で1,656バイトである。
この内側にcache banks 936バイト、PC index 72バイト、inbound chain heads 64バイト、inbound chain next 72バイト、profile history 256バイト、compile queue 16バイト、execution extension 48バイトを含む。
各内訳はruntime構造体の一部であり、構造体全体へ重ねて加算しない。
compile scratch 1,588バイトはruntime構造体外のcompiler stack frame内にあり、stack計測値へ含める。

別にCode sectionの長さに比例するcard state・dirty・candidate bitmapを持つ。Code section payload長を`L`、card幅を4バイト、`C = ceil(L / 4)`とすると、profile領域は`ceil(C / 4) + 2 × ceil(C / 8)`バイトである。複合カーネルのCode sectionは14,578バイトでprofile bitmapは1,824バイト、固定構造体と合わせて3,408バイトとなる。AO-BenchのCode sectionは960バイトでbitmapは120バイト、合計1,704バイトとなる。この可変profile領域はモジュール規模に応じて増え、各traceのdescriptorを別配列へ保持するものではない。

x64ホストの実行コード領域は8,192バイトである。x64のJIT region providerは管理領域と実行領域を4,096バイト単位に配置し、今回の4入力では合計12,288バイトを要求した。ページ丸めと実行コード領域は要求値に含まれる。M33の物理JIT配置は別設計であり、このホスト要求値をそのまま加算しない。

アロケータのPythonワークスペース管理表は1,344バイトである。
これはPython参照シミュレータのホスト管理値である。
Cortex-M33製品RAMへ移行係数で加算しない。
本計測は指定入力、単一実行コンテキスト、ホストABIを対象とする。
32KB物理RAM全体への適合は証明しない。
Cortex-M33の実機構成で管理構造体と10KB論理アドレス窓のbackingを確定する。
実行領域、Interpreter stack、kernel/driver領域も確定する。
これらを合計した後、32,768バイトと比較する。

## 4. 対象実装の配置で確定する領域

<!-- traceability: {Resource_Estimation_Model} {ConsolidatedHeap} {GLOBAL_StrictMemoryLimit} {ROMParsing} -->
全体RAMは、各ランタイムの予約量、線形メモリ、JIT領域、その他の専有領域に共有領域を加えて求める。
第3節へ含めた領域を再度足してはならない。
対象実装では次の項目を確定する。

- ARMv8-Mの管理構造体、ポインタ幅、アラインメント、静的容量を計上する。
- JIT実行領域の保護方式とMPU境界による空きを計上する。
- 第5節のコンパイル時stackを、同時に生存する実行時領域へ加える。
- TCB、IPC、共有ヒープ、ドライバ、ロガー、デバッガを構成ごとに計上する。
- ネイティブの機械スタック、OSと割込みのstackを計上する。
- WASMバイナリをROMから参照する場合、その容量をROM側へ計上する。

`FB_CONF_RUNTIME_BUMP_ARENA_BYTES` の既定値131,072バイトはシミュレータの計上上限である。
この値を、実機の予約容量や各入力の実使用量として扱わない。
同容量を実機で予約する構成は、アリーナだけで最小構成のRAMを上回る。
実機の予約容量は対象入力、同時実行数、構成容量と対象ABIに基づいて決める。
対象ボード、ARMv8-M ABI、JIT命令生成、メモリ保護方式、リンカ配置は未確定である。
Python管理オブジェクトとFFIの一時領域はホスト専用であり、製品RAMへ移行係数で加算しない。

## 5. JITコンパイル時のRAM

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} {JIT_AssemblyStencilCodegen} {JIT_RuntimeAPI} -->
1トレースのWASM命令数上限は64命令である。
生成するtrace blobは最大1,568バイトで、32バイトheaderと最大1,536バイトのassembly機械語本体・alignment padding・定数領域を含む。
コンパイル中の共有operand stack深さの上限は128 wordである。
C++フロントエンドは復号済み命令列とoperation symbol列を固定長配列へ一時保持する。
build時に`operations.S`、`trace.S`、`common.S`をアセンブルし、生成したraw binaryとmetadataをC++ byte array headerへ出力する。trace compile時はC++が選択済みoperationを固定長出力へ合成し、成功接続slotを後続stencilで置き換える。命令列内の条件分岐は合成後の位置に合わせて再配置する。
Loaderが受理したopcodeに直接assembly stencilがない場合はJIT Runtime API stencilから処理する。APIが通常継続を返すと、後続operationも同じtrace内で実行する。trapと制御境界はその命令のRuntime APIまたはInterpreter境界で処理する。

| コンパイル中の領域 | 容量と配置 | 計上場所 |
| :--- | ---: | :--- |
| 出力blob（header 32バイト + body・定数領域1,536バイト） | 最大1,568バイト | x64実行コードregion内 |
| WASM decode staging | 64件×16バイト = 1,024バイト | `compile_wasm_trace`のC++ stack frame内 |
| operation view列 | x64 ABIで64件×96バイト = 6,144バイト | `trace_builder`のC++ stack frame内 |
| 定数pool staging | 最大321件×16バイト = 5,136バイト | `trace_builder`のC++ stack frame内 |
| 作業スタックモデル | 128個のword幅、`size_t`、2個の32bit値 = 144バイト | `stack_model`のC++ stack frame内 |
| abort slot位置列 | x64 ABIで64件×8バイト = 512バイト | `trace_builder::finish`のC++ stack frame内 |
| stencil・scaffold scratch | 3配列×512バイト = 1,536バイト | `trace_builder`のC++ stack frame内 |

実行時trace compileではassemblerやlinkerを起動せず、C++の固定配列を使う。表はx64型レイアウトから見積もった領域であり、最適化後のC++ stack値は別途`-fstack-usage`で計測した。

WASM traceのコンパイル経路は、次の順に処理する。

1. `fireball::jit_runtime<void>::compile_pending`
2. `fb_jit_compile_block`
3. `fireball::compile_wasm_trace`
4. C++ trace builderがbuild時生成のassembly stencilを選び、成功接続slotを後続operationで置換してfall-through列を構成する

現行のtrace compilerを別の一時build directoryで`-O2 -g -fstack-usage`付きでコンパイルし、次の関数frameを得た。

| コンパイル経路 | frame bytes |
| :--- | ---: |
| `compile_queued_candidates` | 56 |
| `compile_one_candidate` | 1,944 |
| `fb_jit_compile_block` | 120 |
| `compile_wasm_trace` | 20,664 |
| 既知のWASM compile call chain合計 | 22,784 |

22,784バイトは既知関数frameの合計であり、ABI、呼出し元、追加callee frameを含む経路全体のstack上限ではない。scratch 1,588バイトは`compile_one_candidate` frame内に含まれ、stack width 128バイトもcompiler frame内に含まれるため重ねて加算しない。計測はx86_64ホスト用shared libraryの構築経路であり、ARMv8-Mのstack使用量を示さない。

shared libraryにはbuild時生成のassembly raw binaryを保持するC++ byte arrayが入る。JIT compile latencyとARMv8-M向けstack使用量は別途確定する。
以前の`Copy-and-Patch`実装で計測したC++ frame合計は現行のstencil composition pathへ転用しない。
ARMv8-Mの実装は未確定であり、対象ABI向けのbuild時LIRとnative stack使用量を個別に確定する。

## 6. ホストROM関連セクションとWASM入力

<!-- traceability: {Resource_Estimation_Model} {ROMParsing} {JIT_AssemblyStencilCodegen} -->
最小構成のROM上限96KBは98,304バイト、想定構成の128KBは131,072バイトとする。
現行x64 trace compilerはassembly raw binaryをC++ byte arrayとして共有libraryへ含め、実行時code generatorを持たない。
raw binaryはtrace 665バイト、operations 1,438バイト、common 69バイト、合計2,172バイトである。
これらは`libtrace_compiler.so`へ含まれており、ELF section sizeへ重ねて加算しない。
通常buildのx86_64 shared libraryは、`.text + .rodata`がprintk 921バイト、interpreter 370,992バイト、JIT 185,597バイトである。
3 libraryの`.text + .rodata`合計は557,510バイトであり、JIT raw stencil 2,172バイトはこの合計の内側にある。
これらはdebugや再配置情報を含むARM firmware全体のROM sizeではない。
このx64ホスト用JIT compilerは現行MCU向け最小RAM/ROM見積りへ加算できる対象ではない。
組み込み向けコード生成方式を確定する際に、対象ABIのbuild時LIRと実行時native stack予算を個別に計測する。
再配置、GOT、動的リンク情報、debug情報も別セクションである。
unwind情報は `.eh_frame` と `.eh_frame_hdr` の合計を別欄へ示す。
`.data` の初期値は、対象firmwareのROM load imageにも計上する。
`.bss` はRAMへ計上し、ROM payloadには足さない。
ホストの機械語サイズをARMv8-Mへ換算しない。
ARMv8-M firmwareの全体適合は、対象構成のmapとセクションで判定する。

| 入力 | ROMへ配置するWASMのバイト長 |
| :--- | ---: |
| 算術ループ | 83 |
| 間接呼出し | 337 |
| 複合カーネル | 15,316 |
| AO-Bench | 1,441 |

複数モジュールを同時にROMへ置く場合は、その全容量を加える。
現行入力バイト列のPython所有は、物理的なROM配置の証明ではない。

## 7. 実装規模

<!-- traceability: {Size_20KSLOC} {Resource_Estimation_Model} -->
SLOCは空行、コメント、Python docstringを除いた物理コード行数とする。
PythonはASTでdocstringを識別し、tokenizeでコメントを除外する。
C/C++はPygmentsの字句解析でコメントを除外する。
プリプロセッサ指令と文字列中のコメント記号はコードとして数える。
ファイルごとのhashと計測器の版を結果へ保存する。

| PySIMのPython参照実装 | ファイル数 | 物理行数 | SLOC |
| :--- | ---: | ---: | ---: |
| Tier 1 Core | 10 | 3,235 | 2,419 |
| Tier 1 Interface | 3 | 851 | 587 |
| Tier 2 Runtime | 33 | 7,966 | 6,212 |
| Tier 3 Plugins | 12 | 1,157 | 896 |
| Tier 3 Platform | 15 | 1,570 | 1,151 |
| 共通入口 | 2 | 873 | 709 |
| **PySIM Python合計** | **75** | **15,652** | **11,974** |

| 別計上するPythonの対象 | ファイル数 | 物理行数 | SLOC |
| :--- | ---: | ---: | ---: |
| Python Interpreter（参照実装とホスト接続コード） | 8 | 6,527 | 5,427 |

| C/C++の対象 | ファイル数 | 物理行数 | SLOC |
| :--- | ---: | ---: | ---: |
| 製品側の`src/`と`inc/` | 7 | 6,280 | 3,704 |
| PySIM native参照実装とネイティブABI | 26 | 10,348 | 9,274 |

`third_party/`、QA、概念コード、形式モデル、benchmark、scenarioは上のSLOC値に含めない。
PySIM PythonとPySIM native共有ライブラリはx64参照実装であり、Cortex-M33 firmwareへリンクする対象コードではない。
現在の`src/`と`inc/`は3,704 SLOCである。
20,000 SLOC制約への最終適合は、対象firmwareへ実際に含めるファイル一覧とビルド構成を固定して判定する。
Pythonの移行係数や参照実装の行数から、ROM/RAM容量を外挿しない。

```bash
bash experiments/pysim/native/tier1_core/printk/build_native.sh
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
bash experiments/pysim/native/tier3_plugins/jit/build_native.sh
uv run python experiments/pysim/benchmarks/memory/bench_resource_inventory.py \
  --output /tmp/fireball-resource-inventory.json
```

## 8. 現時点の概算と適合判定

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} {Size_20KSLOC} {ROMParsing} -->
最小構成のRAM予算には、要件で定める静的合計21,504バイトを置く。
RAM 32,768バイトとの差は11,264バイトである。
静的合計21,504バイトの構成別内訳は、要件と現行計測に記録されていない。
この差分は名目上の余裕であり、未計上領域を含む実行時空き容量の確定値ではない。

| 項目 | 概算値 | 根拠と適用範囲 |
| :--- | ---: | :--- |
| 最小構成の物理RAM | 32,768バイト | 要件値 |
| 静的合計 | 21,504バイト | 要件値。構成別の内訳は未確定 |
| 静的合計との差分 | 11,264バイト | 物理RAMから静的合計を引いた算術値 |
| 最大アリーナ使用量 | 43,504バイト | x86_64ホストの参照計上。ARMv8-M物理RAMではない |
| Hybrid JITの要求領域 | 12,288バイト | x64 region providerの値。実行コード8,192バイトを含む |
| 4つのWASM入力の合計 | 17,177バイト | 4モジュールをすべてROMへ置く場合 |
| 最小構成ROMとの差分 | 81,127バイト | 98,304バイトから4つのWASM入力合計を引いた値 |
| 製品側`src/`と`inc/` | 3,704 SLOC | 現行計測値。20,000 SLOC制約まで16,296 SLOC |

最大アリーナ使用量43,504バイトは複合カーネルの参照計測値である。
この値には参照レイアウトとx86_64 ABIの借用記述が含まれる。
ゲスト論理メモリ65,536バイトは別に計上し、物理RAMへ加算しない。
JIT要求領域はアリーナとは別に計上するが、ARMv8-Mの物理配置には外挿しない。

4つのWASM入力をROMへ常駐させる場合、その入力だけで17,177バイトを使う。
残る81,127バイトにはファームウェア本体、初期値付きデータ、その他のROM資産を置く。
x64参照ライブラリの`.text`と`.rodata`は合計164,620バイトである。
この値はARMv8-Mのコードサイズを表さず、ROM差分の算出には使わない。

現行の製品側C/C++は3,704 SLOCであり、20,000 SLOCの18.52%である。
最終判定には、製品firmwareへ含めるファイル一覧を確定する。
Python参照実装とPySIM native参照実装のSLOCは製品firmwareへ加えない。

ARMv8-MのRAMおよびROM適合は未判定である。
対象ボード、ARM ABI、JIT配置とMPU境界、リンクmap、全体のstack・IPC・driver構成が未確定である。
これらを確定した後に、同時生存領域とROM load imageを重複なく計上する。

## 9. Interpreter BasicBlock index metadataの増分（2026-10-13）

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} -->
AO-Bench WASMをLoaderで読み、基本ブロックindexを注釈プールとBasicBlock metadataへ保存する変更の増分を計測した。計測入口とコマンド、ソース識別子は[`REPORT.md`](experiments/pysim/benchmarks/results/block_index_dispatch_20261013/REPORT.md)に記録した。比較対象はu16命令幅を含む変更前Step 0の測定値である。

| 項目 | 変更前 | 変更後 | 増加量 | 増加率 |
| :--- | ---: | ---: | ---: | ---: |
| Interpreter注釈プール | 2,456 byte | 2,944 byte | 488 byte | 19.87% |
| BasicBlock記述 | 2,996 byte（107 × 28） | 3,852 byte（107 × 36） | 856 byte | 28.57% |
| Loader arena合計 | 25,756 byte | 27,100 byte | 1,344 byte | 5.22% |
| x64 JIT境界metadata | 24 byte | 24 byte | 0 byte | 0% |

Loader arenaの増加1,344 byteは、注釈プールの488 byteとBasicBlock記述の856 byteの合計と一致する。既定容量131,072 byteに対する使用量は25,756 byteから27,100 byteへ増え、20.68%を使う。差分は容量の約1.03%である。x64 JIT境界metadataの`block_index`は24-byte recordの予約欄を利用するため、record自体のサイズは増えない。

Loader arenaとは別に、x64 runtime ABIではExecutionContextが160 byteから168 byte（+8 byte/実行コンテキスト）、CallFrameが40 byteから48 byte（+8 byte/frame）へ増える。32枠のCallStackは1,288 byteから1,544 byte（+256 byte）となる。FunctionExecutionViewは104 byteから112 byte（+8 byte/function）であり、AO-Benchの6定義関数分では48 byte増える。これらを対象実装のworkspace計上へ加える際は、同時に生存する個数を確定してから加算し、Loader arena増分と混同しない。

この確認はx86_64 PySIM Loader arena上のAO-Bench入力に限る。ExecutionContextやCallFrameなど固定runtime ABI構造体のサイズ、およびARMv8-Mの物理RAM配置はこのarena測定に含めない。物理RAM 32KBへの適合を証明しない。

## 10. 注釈poolと座標型の縮小（2026-10-13）

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} {Loader_BasicBlockIndex} -->
Interpreter注釈を実行時に必要な導出値へ絞り、BasicBlock番号とframe word位置をu16にした。固定長命令の幅recordを除去し、可変LEB命令だけu8幅を保持する。PC索引行はu32 folded PCとu16 block番号を6 byteのbyte列で格納し、構造体paddingを避ける。入力WASMとLoader計測値、コマンドは[`REPORT.md`](experiments/pysim/benchmarks/results/annotation_types_20261013/REPORT.md)に記録した。

| 対象 | AO-Bench変更前 | AO-Bench変更後 | 増減 | 増減率 |
| :--- | ---: | ---: | ---: | ---: |
| Interpreter注釈pool | 2,944 byte | 1,712 byte | −1,232 byte | −41.85% |
| BasicBlock記述 | 3,852 byte（107 × 36） | 3,424 byte（107 × 32） | −428 byte | −11.11% |
| Block PC索引 | 856 byte（107 × 8） | 642 byte（107 × 6） | −214 byte | −25.00% |
| Radix表 | 68 byte（17 × 4） | 34 byte（17 × 2） | −34 byte | −50.00% |
| Loader arena実測 | 27,100 byte | 21,444 byte | −5,656 byte | −20.87% |

| 対象 | profile suite変更前 | profile suite変更後 | 増減 | 増減率 |
| :--- | ---: | ---: | ---: | ---: |
| Interpreter注釈pool | 36,090 byte | 20,177 byte | −15,913 byte | −44.09% |
| BasicBlock記述 | 31,860 byte（885 × 36） | 28,320 byte（885 × 32） | −3,540 byte | −11.11% |
| Block PC索引 | 7,080 byte（885 × 8） | 5,310 byte（885 × 6） | −1,770 byte | −25.00% |
| Radix表 | 68 byte（17 × 4） | 34 byte（17 × 2） | −34 byte | −50.00% |
| Loader arena実測 | 366,520 byte | 287,586 byte | −78,934 byte | −21.54% |

Loader arena全体の減少には、4項目に列挙した記録幅以外の差がAO-Benchで−3,748 byte、profile suiteで−57,677 byte含まれる。前回記録の基準snapshotと今回のLoader割当全体を再比較した残差であり、この依頼の4項目の削減効果へ帰属させない。AO-Benchの現在容量131,072 byteに対する計測使用量は21,444 byte（16.36%）である。profile suiteの現在容量2,000,000 byteに対する計測使用量は287,586 byte（14.38%）である。profile suiteの注釈poolとarenaは、`br_table`にu8制御深さを加えた後の再計測値である。保存済みIJ測定の20,121 byteは以前の注釈形式を測った値であり、現行形式との直接比較には用いない。

JITのx64 trace headerは40 byteから32 byteへ8 byte（20.00%）減り、trace exit metadataは24 byteから20 byteへ4 byte（16.67%）減った。`jit_cache_entry`は32bit `code_offset_t`を保持するため8 byteから12 byteとなる。設定済み72 entry全てを使う場合、descriptor領域は576 byteから864 byteへ288 byte（50%）増える。AO-Benchの6定義関数ではFunctionExecutionViewが112 byteから88 byteへ縮み、合計144 byte減る。現在のABIサイズはloader計測JSONに記録した。JIT管理recordとFunctionExecutionViewはLoader arena外のruntime metadataであり、相互の差分を相殺してLoader arenaの値に混ぜない。これらはx64 host構造の実測であり、ARMv8-Mの配置や物理RAM適合を示さない。

## 11. JIT定数poolの注釈複製削除（2026-10-13）

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} -->
JIT traceのconstant poolからInterpreter annotation bytesの複製を除き、trace単位header相対参照と終端record内のannotation byte offsetへ置き換えた。各Runtime API recordはslot/count/spanをu8化し、source IPは`code_ip_t`、immediateは値の型幅で保持する。計測コマンド、WASM・native libraryのSHA-256、項目別の診断値は[`REPORT.md`](experiments/pysim/benchmarks/results/jit_const_pool_20261013/REPORT.md)に記録した。

| 対象 | AO-Bench変更前 | AO-Bench変更後 | 増減 | 増減率 |
| :--- | ---: | ---: | ---: | ---: |
| JIT constant pool累積生成量 | 165,493 byte | 129,234 byte | −36,259 byte | −21.91% |
| Interpreter注釈複製 | 23,077 byte | 0 byte | −23,077 byte | −100.00% |
| profile suite constant pool累積生成量 | 8,963 byte | 6,431 byte | −2,532 byte | −28.25% |
| profile suite Interpreter注釈複製 | 867 byte | 0 byte | −867 byte | −100.00% |

AO-Bench 320×180では、header参照は69,120 byteから33,368 byte、slot/countは35,256 byteから17,368 byte、source IPは35,256 byteから34,736 byte、Runtime API immediateは2,784 byteから1,710 byteへ減った。変更後にrecord span 8,684 byteとtrace annotation offset 33,368 byteを加えた。profile suiteでは同じ項目順に3,052/1,864/1,864/1,316/867 byteから1,188/932/1,864/809/0 byteとなり、span 466 byte、annotation offset 1,172 byteを加えた。

このpool値は一回のAO-Benchまたはcompile-all診断中に生成したtrace bytesの累積であり、同時常駐RAMの実測値ではない。個々のtraceは固定容量cacheへ配置され、容量予約とcompiler staging配列のサイズはこの変更で変わらない。pool縮小はtraceごとの配置量を下げ、固定cache内に保持できるtrace数へ影響する。ピーク同時常駐量をRAM見積もりへ加算する場合は、cache occupancyとevictionを別途計測する。x86_64 PySIMの結果であり、ARMv8-MのRAM/ROM適合は示さない。

## 12. `br_table`注釈と容量計算（2026-10-13）

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} {Loader_BasicBlockIndex} -->
`br_table`の注釈をu16 label countと、defaultを含む各targetのu8制御深さ・u16 BasicBlock番号へ統一した。書き込み量は`2 + 3 × (label_count + 1)` byteであり、Loaderは容量計算値と実書込量の一致をassertする。WASMごとのlabel数、bytecodeから再構成した旧二重書き形式との比較、計測ファイルは[`br_table_measurement.json`](experiments/pysim/benchmarks/results/jit_const_pool_20261013/br_table_measurement.json)と[`REPORT.md`](experiments/pysim/benchmarks/results/jit_const_pool_20261013/REPORT.md)にある。

profile suiteには11個の`br_table`があり、defaultを含むtarget recordは56個である。旧形式のcount・深さをu32で保持する領域とtarget recordを合わせると436 byte、現行形式は190 byteであり、再構成値で246 byte（56.42%）を削減する。AO-Benchには`br_table`がないため、注釈poolは変わらない。前回のIJ測定snapshot 20,121 byteは、旧二重書きlayoutの実測値ではなく、source SHAも異なる。現行sourceで再計測したprofile suite注釈poolは20,177 byteであり、この差分は現在のtarget recordに制御深さを追加した56 byteである。異なるlayoutのsnapshot同士を同じbaselineとして扱わない。

## 13. Interpreter実行metadataとruntime frameの削除（2026-10-13）

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} {Loader_BasicBlockIndex} -->
AO-BenchのNative Interpreterから密な制御表、PC→BasicBlock索引、重複stack/frame metadataを除いた。Native control mapは937 code byte × 16 byteで14,992 byte、Native PC indexとradix tableは676 byte、local size tableは50 byte、実行時ControlStackは520 byteだった。これらの配列領域を解放した。Python `block_storage`と`Module.get_block()`はPython lookup契約のため維持する。

| 対象 | 変更前 | 変更後 | 削減量 |
| :--- | ---: | ---: | ---: |
| Native dense control map | 14,992 B | 0 B | 14,992 B |
| Native PC indexとradix table | 676 B | 0 B | 676 B |
| local size table | 50 B | 0 B | 50 B |
| runtime ControlStack | 520 B | 0 B | 520 B |
| `ExecutionContextABI` | 168 B | 144 B | 24 B |
| `FunctionExecutionViewNative` | 88 B × 7 | 72 B × 7 | 112 B |
| `ModuleExecutionViewNative` | 128 B | 104 B | 24 B |
| 32枠の`CallStackNative` | 1,544 B | 1,288 B | 256 B |

`CallFrameNative`は48 Bから40 Bへ8 B/frame縮小した。32枠分の256 Bは`CallStackNative`行に含む。function view 7件にはAO-Benchの1 importを含む。viewから消した`control_map`と`local_sizes`の2 pointerはfunction viewの−16 B/件に含む。重複計上を避けた構造体・配列の合計は16,654 Bである。`stack_checkpoint`、Native search表、runtime ControlStackは対応するC++/Python ABIから除いた。`current_block_index`は実行中のblock metadataを直接参照するために維持し、通常dispatchはPC検索を行わない。`execution_context.ip`はJIT call/return/resumeに用いるため保持する。

Loader arenaは前回の2-byte annotation layout測定21,444 Bから現在5,741 Bへ15,703 B（73.23%）減った。131,072 B capacityの使用率は16.36%から4.38%になった。前後snapshotのannotation poolは1,712 Bから1,729 Bへ17 B増え、arena allocator alignmentで2 Bの差がある。Native map/index/local size表のgross 15,718 Bとarenaのnet減少との差は、これらの同時差分を含む。削除項目以外の残差を個別の削減効果へ帰属させない。

AO-Benchの前後計測では、320×180のNativeInterpreterは425.064 msから394.635 ms（−7.16%）、Hybrid JITは501.539 msから461.494 ms（−7.98%）になった。全5解像度のNativeInterpreterとJITで遅延増加はなく、出力hashは一致した。前後のsource SHAは異なる複数ファイルを含むため、時間差全体を単一のstorage削除へ帰属させない。x86_64 PySIMのlogical allocator・host ABIの結果であり、ARMv8-Mの物理RAM適合を示さない。実行条件、全hash、build SHA-256、コマンド、全計時値は[`REPORT.md`](experiments/pysim/benchmarks/results/storage_cleanup_20261013/REPORT.md)、生データは[`aobench_after.json`](experiments/pysim/benchmarks/results/storage_cleanup_20261013/aobench_after.json)と[`storage_after.json`](experiments/pysim/benchmarks/results/storage_cleanup_20261013/storage_after.json)に記録した。
