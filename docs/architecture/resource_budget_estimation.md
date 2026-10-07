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

計測日は2026-10-07である。
計測対象の製品コードはcommit `f02f4bb34c428e133863af8873e525b3109e6749`にある。
結果JSONには計測時のworktree状態と対象ファイルのハッシュを保存する。
資源一覧の計測入口には、現行JIT設定を渡すための未コミット修正を含む。
現行製品側のJIT入口とネイティブライブラリを使う。
QA専用のJIT管理クラスと検査用ライブラリは、製品RAMとROMへ計上しない。

| 対象 | 計測入口 | 結果 |
| :--- | :--- | :--- |
| アリーナと製品JITの要求領域 | [`bench_runtime_memory.py`](experiments/pysim/benchmarks/memory/bench_runtime_memory.py) | [`runtime_memory_current_20261007.json`](experiments/pysim/benchmarks/results/runtime_memory_current_20261007.json) |
| ソース規模、ホストELF、JIT管理構造体、コンパイル時stack | [`bench_resource_inventory.py`](experiments/pysim/benchmarks/memory/bench_resource_inventory.py) | [`resource_inventory_current_20261007.json`](experiments/pysim/benchmarks/results/resource_inventory_current_20261007.json) |

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
| 関数型索引、局所変数型、局所変数幅、基本ブロックと検索索引 | Loaderが解析したモジュールに対応する領域を確保する |
| 実体のグローバル変数と関数テーブル | インスタンス生成時に確保する |
| ネイティブ実行用のモジュール、関数、型、テーブル、ブロックの借用記述 | 初回実行時にホストABIの構造体サイズから計上する |
| 実行コンテキスト、値スタック、局所変数スタック、呼出し情報、制御情報 | 呼出しワークスペースを確保し、後続呼出しで再利用する |
| JITコンパイルの一時領域 | 最大552バイトのtrace出力を実行コード領域へ直接生成し、32バイトの作業stack位置配列をコンパイル関数のstack frameに置く |

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
| 算術ループ（1,000反復） | 1 / 4 | 280 | 280 | 5,624 | 5,624 | 0 | 12,288 |
| 間接呼出し（100反復） | 4 / 12 | 740 | 744 | 6,600 | 6,600 | 0 | 12,288 |
| 複合カーネル（`k_crc32(1)`） | 28 / 519 | 19,428 | 19,476 | 43,504 | 43,504 | 65,536 | 12,288 |
| AO-Bench（32×16） | 7 / 88 | 3,432 | 3,432 | 12,008 | 12,008 | 65,536 | 12,288 |

表の「ゲスト論理リニアメモリ」はWASMの論理サイズである。PySIMではホストbytearrayがbackingとなるが、Cortex-M33の物理RAM量ではない。対象のCortex-M33構成では論理アドレス窓10KB、物理RAM全体32KBを別々に扱う。このホスト測定で64KBと表示される値を物理RAMへ加算してはならない。

Hybrid JITのアリーナ使用量はInterpreter単独と同じだった。コンパイラは最大552バイトのtrace出力を実行コード領域へ直接書き、最大32バイトの作業stack位置配列はC++ stack frameに置く。追加の240バイトarena scratchは確保しない。

現行x64製品JIT runtimeの固定管理構造体は`sizeof`で1,584バイトである。
3バンク分のentryは840バイトである。
fast lookupは128バイトである。
profile historyは256バイトである。
compile queueは16バイトである。
native execution extensionは24バイトである。
残りはbank状態、profile、executable-memory view、pointer、alignment等を含む。

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

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} {JIT_CopyAndPatch} -->
1トレースの命令数上限は64命令である。
生成するtrace blobは最大552バイトで、40バイトheaderと最大512バイトのコード領域を含む。
コンパイル中の作業スタック深さの上限は16スロットである。
WASM命令は逐次復号する。復号した全命令を格納する配列は持たない。
容量超過や非対応命令ではコンパイルを辞退し、C++ Interpreter handlerからruntimeへ戻る。

| コンパイル中の領域 | 容量と配置 | 計上場所 |
| :--- | ---: | :--- |
| 出力blob（header 40バイト + body領域512バイト） | 最大552バイト | x64実行コードregion内 |
| 作業スタック位置 | 16件×`int16_t` 2バイト = 32バイト | `compile_pending`のC++ stack frame内 |

一時領域を`BumpAllocator`から貸与しない。トレース生成用の240バイトscratch allocationはなく、成功、辞退、エラー時にarena offsetを復元する処理もない。出力を実行コードregionへ直接生成する。

現行WASMのコンパイルは、次の順に主要関数を呼ぶ。

1. `fireball::jit_runtime<void>::compile_pending`
2. `fb_jit_compile_block`
3. `fireball::compile_wasm_trace`

Clang 21.1.8 / x86_64で、製品JITビルド設定へ`-fstack-usage`を加えて計測した。
設定は`-O2 -g -fPIC -fvisibility=hidden -fno-exceptions -fno-rtti`である。

| 測定対象 | スタックフレームのバイト数 |
| :--- | ---: |
| `fireball::jit_runtime<void>::compile_pending` | 216 |
| `fb_jit_compile_block` | 88 |
| `fireball::compile_wasm_trace` | 216 |
| **主要3関数の既知部分** | **520** |

`compile_instruction_body`は明示命令入力を扱うQA経路で、136バイトのframeをWASM compile chainへ加えない。
32バイトの作業stack位置配列は`compile_pending`の216バイトframeに含まれる。
最大552バイトの出力はx64実行コードregionへ直接生成する。
主要3関数のframe合計520バイトは既知部分であり、命令処理の補助関数、呼出し元、ABI frame、OS/割込みstackを含む経路全体の上限ではない。
ARMv8-Mのコンパイル時stack上限は対象ABIの全呼出し経路で確定する。

## 6. ホストROM関連セクションとWASM入力

<!-- traceability: {Resource_Estimation_Model} {ROMParsing} {JIT_CopyAndPatch} -->
最小構成のROM上限96KBは98,304バイト、想定構成の128KBは131,072バイトとする。
現行ホストライブラリを製品ビルドスクリプトで再生成する。
GNU `size -A` でELFセクションを測定する。
QA用の `libjit_probe.so` と `libinterpreter_probe.so` はこの表へ含めない。

| x64参照ライブラリ | `.text` | `.rodata` | `.text` + `.rodata` | `.data.rel.ro` | `.data` + `.bss` | unwind情報 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| printk | 872 | 65 | 937 | 0 | 16 | 296 |
| C++ Interpreter | 117,949 | 3,961 | 121,910 | 4,288 | 16 | 25,708 |
| x64 JIT | 38,773 | 3,000 | 41,773 | 1,904 | 16 | 5,380 |
| **合計** | **157,594** | **7,026** | **164,620** | **6,192** | **48** | **31,384** |

`.text` と `.rodata` の合計は約160.76KiBである。
Interpreterの通常実行とデバッグの入口を含む。
独立したprintkライブラリも集計する。
Python側に残るLoader、Runtime、共有機能のC++移植分は含まない。
`.data.rel.ro` 6,192バイトはInterpreterとJITのライブラリにあり、`.text` + `.rodata` と `.data` + `.bss` の各合計へ含めていない。
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
| Tier 1 Core | 10 | 3,100 | 2,325 |
| Tier 1 Interface | 3 | 851 | 587 |
| Tier 2 Runtime | 31 | 7,635 | 5,898 |
| Tier 3 Plugins | 11 | 1,206 | 945 |
| Tier 3 Platform | 15 | 1,564 | 1,145 |
| 共通入口 | 2 | 841 | 677 |
| **PySIM Python合計** | **72** | **15,197** | **11,577** |

| 別計上するPythonの対象 | ファイル数 | 物理行数 | SLOC |
| :--- | ---: | ---: | ---: |
| Python Interpreter（参照実装とホスト接続コード） | 5 | 6,239 | 5,150 |

| C/C++の対象 | ファイル数 | 物理行数 | SLOC |
| :--- | ---: | ---: | ---: |
| 製品側の`src/`と`inc/` | 7 | 6,280 | 3,704 |
| PySIM native参照実装とネイティブABI | 22 | 8,373 | 7,841 |

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
