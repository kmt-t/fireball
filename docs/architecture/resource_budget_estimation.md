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

計測日は2026-10-06である。
基点は `889e406f` である。
計測時の未コミット変更を含め、対象ファイルのハッシュを結果へ保存する。
現行製品側のJIT入口とネイティブライブラリを使う。
QA専用のJIT管理クラスと検査用ライブラリは、製品RAMとROMへ計上しない。

| 対象 | 計測入口 | 結果 |
| :--- | :--- | :--- |
| アリーナと製品JITの要求領域 | [`bench_runtime_memory.py`](experiments/pysim/benchmarks/memory/bench_runtime_memory.py) | [`runtime_memory_current_20261006.json`](experiments/pysim/benchmarks/results/runtime_memory_current_20261006.json) |
| ソース規模、ホストELF、JIT管理構造体、コンパイル時stack | [`bench_resource_inventory.py`](experiments/pysim/benchmarks/memory/bench_resource_inventory.py) | [`resource_inventory_current_20261006.json`](experiments/pysim/benchmarks/results/resource_inventory_current_20261006.json) |

アリーナの値は32ビット参照レイアウトとx86_64のネイティブABIを組み合わせた計上値である。
JITの要求領域とELFはx86_64ホストの値である。
これらをPythonプロセスの実メモリ使用量やARMv8-Mの確定使用量として扱わない。
対象ABIの配置とリンク結果で、製品全体の容量適合を判定する。

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
| JITコンパイルの作業領域 | `JITRuntimeManager.bind_execution` が240バイトを貸与する |

メモリ計測は、製品側の `RuntimeEngine` と `JITRuntimeManager` で実行する。
QA用の実行カウンタと診断dispatcherは、この計測経路へ含めない。
供給側の `region_provider` で、JITが要求したサイズとアラインメントを記録する。
この領域にはJIT管理構造体、ビット表、配置時の空き、実行コード領域を含む。
アリーナとは別領域であるため、要求サイズを一度だけ足す。
構造体内の実行拡張と履歴を、アリーナ外の別領域として再度足してはならない。
実行コード8,192バイトも、要求領域に含むため再度足さない。

WASMブロックの借用記述は `NativeModuleExecution` が所有する。
JITはこの記述を参照し、別のブロック登録配列を所有しない。
1件24バイトの借用記述はアリーナへ計上済みである。
QA専用のブロック登録配列、結線レコード、dispatch snapshotは測定対象へ含めない。

各入力についてInterpreter単独とHybrid JITを別アリーナで3回ずつ実行する。
戻り値と出力バイト列を両方式で照合する。
WASIを使わない入力の戻り値はWasmtimeとも照合する。
AO-Benchでは出力長528バイトを検証する。
2回目と3回目のアリーナ使用量が同じであることを確認する。
確保量とアラインメントの空きの合計が、最終使用量と一致することを確認する。
同じ入力をQA側の `RuntimeStatsEngine` と診断dispatcherで別に実行する。
この別実行で、生成したtraceの実行回数が正であることを確認する。
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
Hybrid JITのアリーナ使用量には、240バイトのコンパイル作業領域を含む。

| 入力 | 関数数 / 基本ブロック数 | ロード後 | インスタンス生成後 | Interpreter単独のアリーナ最大使用量 | Hybridのアリーナ最大使用量 | 製品JITの要求領域 | 線形メモリ |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 算術ループ（1,000反復） | 1 / 4 | 268 | 268 | 5,736 | 5,976 | 28,672 | 0 |
| 間接呼出し（100反復） | 4 / 12 | 712 | 716 | 6,608 | 6,848 | 28,672 | 0 |
| 複合カーネル（`k_crc32(1)`） | 28 / 519 | 18,636 | 18,684 | 38,496 | 38,736 | 28,672 | 65,536 |
| AO-Bench（32×16） | 7 / 88 | 3,296 | 3,296 | 11,272 | 11,512 | 28,672 | 65,536 |

実行コンテキストのワークスペースは各入力とも5,180バイトである。
製品JIT管理構造体は、測定専用の `sizeof` 計測で16,904バイトである。
計測用コードは一時領域へ生成する。
製品APIへ検査入口を追加しない。

| JIT管理構造体内の主な構成要素 | バイト数 |
| :--- | ---: |
| trace本体（3面×32件 + 一時登録1件） | 13,192 |
| ネイティブ実行の拡張 | 32 |
| バンク索引と流入索引 | 2,784 |
| 高速検索表 | 384 |
| プロファイル履歴 | 256 |
| コンパイル待ち | 16 |
| コンパイル作業領域の借用参照と復元位置 | 40 |

これらの領域は管理構造体の16,904バイトへ含む。
管理フィールド、参照ポインタ、構造体内の空きも同じサイズへ含む。
常駐件数が少なくても、固定配列の全容量を計上する。
作業領域の実体240バイトは、管理構造体へ埋め込まずランタイムのアリーナから借用する。

ホストの実行メモリ保護は4,096バイトのページ境界を要求する。
管理構造体とビット表を合わせた領域は、全4入力で20,480バイトへ丸められる。
コード領域8,192バイトを加えた要求量は28,672バイトである。
配置時の空きは要求量に含む。

| 入力 | カード状態2bit | 更新索引1bit | 候補マスク1bit | データ領域の配置時の空き |
| :--- | ---: | ---: | ---: | ---: |
| 算術ループ | 3 | 2 | 2 | 3,569 |
| 間接呼出し | 8 | 4 | 4 | 3,560 |
| 複合カーネル | 912 | 456 | 456 | 1,752 |
| AO-Bench | 60 | 30 | 30 | 3,456 |

アロケータのワークスペース管理表は1,344バイトである。
64件のオフセット、サイズ、アラインメント、状態の配列から算出する。
次表の既知部分は、各方式のアリーナ、管理表、線形メモリ、製品JITの要求領域の合計である。
コンパイル時の機械スタックとシステム共有領域は含まない。

| 入力 | Interpreter単独の既知部分 | Hybrid JITの既知部分 | Hybridの既知部分を32,768バイトから引いた値 |
| :--- | ---: | ---: | ---: |
| 算術ループ | 7,080 | 35,992 | −3,224 |
| 間接呼出し | 7,952 | 36,864 | −4,096 |
| 複合カーネル | 105,376 | 134,288 | −101,520 |
| AO-Bench | 78,152 | 107,064 | −74,296 |

現行ホスト構成のHybrid JITは、全4入力で既知部分だけでも32,768バイトを超える。
これはホストABIとホストのページ保護を含む構成の判定である。
ARMv8-MのRAM適合性を、この値から直接判定しない。
線形メモリを1ページ持つ2入力は、その領域だけで最小構成のRAMを上回る。
この2入力を同じ線形メモリ容量のまま最小構成へ移す容量適合は成立しない。
静的合計21,504バイトの適合は、対象実装の静的予約量で別途判定する。

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
生成する本体の上限は96バイトである。
コンパイル中の作業スタック深さの上限は16スロットである。
WASM命令は逐次復号する。
復号した全命令を格納する配列は持たない。
容量超過や非対応命令ではコンパイルを辞退し、Interpreterで実行する。

| JITコンパイルの作業領域 | 容量と単価 | バイト数 |
| :--- | :--- | ---: |
| 出力コード（本体96バイト + x64ヘッダ16バイト） | 112バイト | 112 |
| 生成中のコード本体 | 96バイト | 96 |
| 作業スタックの位置 | 16件×`int16_t` 2バイト | 32 |
| **作業領域の実体合計** | | **240** |

作業領域はランタイムの `BumpAllocator` から貸与する。
JIT管理構造体は、借用参照と復元位置を40バイトで保持する。
コンパイル候補ごとに作業領域の使用位置を保存する。
成功、辞退、エラーの各経路で使用位置を復元する。
作業領域の実体240バイトは、第3節のHybridアリーナ最大使用量へ計上済みである。
Pythonの接続コードが持つ作業用配列は、この計上した領域に対応するホスト上の実体である。
Pythonプロセスの配列サイズを製品RAMへ再度足さない。

現行WASMのコンパイルは、次の順に主要関数を呼ぶ。

1. `fireball::jit_runtime<void>::compile_pending`
2. `fb_jit_compile_block`
3. `fireball::compile_wasm_trace`

Clang 21.1.8 / x86_64で、製品JITビルドの設定へ `-fstack-usage` を加えて計測する。
設定は `-O2 -g -fPIC -fvisibility=hidden -fno-exceptions -fno-rtti` である。

| 測定対象 | スタックフレームのバイト数 |
| :--- | ---: |
| `fireball::jit_runtime<void>::compile_pending` | 136 |
| `fb_jit_compile_block` | 120 |
| `fireball::compile_wasm_trace` | 216 |
| **主要3関数の既知部分** | **472** |

`compile_instruction_body` は明示命令入力を扱うQA経路に属する。
この関数のフレーム168バイトは、上記のWASM経路へ加えない。
出力コード112バイトは、スタックフレームではなく借用した作業領域に存在する。
作業領域240バイトと主要3関数のフレーム472バイトを合わせた既知部分は712バイトである。
これはコンパイル経路全体の上限ではない。
命令処理の補助関数、呼出し元、FFI、Python管理情報を含まない。
第3節の既知部分へ主要3関数のスタックを加える場合は、472バイトだけを足す。
アリーナへ計上済みの240バイトを再度足してはならない。
ARMv8-Mのコンパイル時スタック上限は、対象ABIの全呼出し経路で別途確定する。

## 6. ホストROM関連セクションとWASM入力

<!-- traceability: {Resource_Estimation_Model} {ROMParsing} {JIT_CopyAndPatch} -->
最小構成のROM上限96KBは98,304バイト、想定構成の128KBは131,072バイトとする。
現行ホストライブラリを製品ビルドスクリプトで再生成する。
GNU `size -A` でELFセクションを測定する。
QA用の `libjit_probe.so` と `libinterpreter_probe.so` はこの表へ含めない。

| x64参照ライブラリ | `.text` | `.rodata` | 両者の合計 | `.data` + `.bss` | unwind情報 |
| :--- | ---: | ---: | ---: | ---: | ---: |
| printk | 872 | 65 | 937 | 16 | 296 |
| C++ Interpreter | 142,994 | 3,056 | 146,050 | 16 | 31,360 |
| x64 JIT | 31,973 | 1,972 | 33,945 | 16 | 4,348 |
| **合計** | **175,839** | **5,093** | **180,932** | **48** | **36,004** |

`.text` と `.rodata` の合計は約176.69KiBである。
Interpreterの通常実行とデバッグの入口を含む。
独立したprintkライブラリも集計する。
Python側に残るLoader、Runtime、共有機能のC++移植分は含まない。
ホスト共有ライブラリの再配置、GOT、動的リンク情報、debug情報は別セクションである。
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
ファイルごとのハッシュと計測器の版を結果へ保存する。

Python Interpreter配下の4ファイルは、参照実装とホスト接続コードとして別計上する。
この別計上値は、製品側Python合計とC/C++合計へ加えない。
C++ Interpreterをネイティブ実装として計上し、Interpreterの二重計上を避ける。
QAへ移されたJIT検査・操作用コードも製品ソースへ含めない。
各TierのPythonと共通入口の `system.py`、`__init__.py` を集計する。
QA、ベンチマーク、シナリオ、`main.py`、`aobench.py` は含めない。

| Pythonの対象 | ファイル数 | 物理行数 | SLOC |
| :--- | ---: | ---: | ---: |
| Tier 1 Core | 10 | 3,101 | 2,326 |
| Tier 1 Interface | 3 | 851 | 587 |
| Tier 2 Runtime（Python Interpreter別計上） | 31 | 7,711 | 5,979 |
| Tier 3 Plugins | 11 | 1,255 | 994 |
| Tier 3 Platform | 15 | 1,564 | 1,145 |
| 共通入口 | 2 | 837 | 673 |
| **製品側Python合計** | **72** | **15,319** | **11,704** |

| 別計上するPythonの対象 | ファイル数 | 物理行数 | SLOC |
| :--- | ---: | ---: | ---: |
| Python Interpreter（参照実装とホスト接続コード） | 4 | 6,192 | 5,122 |

別計上範囲は `tier2_runtime/interpreter/` 配下の `.py` ファイルである。
C++ Interpreterは次のnative実装欄へ計上する。

| C/C++の対象 | ファイル数 | 物理行数 | SLOC |
| :--- | ---: | ---: | ---: |
| 製品側の `src/` と `inc/` | 7 | 6,279 | 3,704 |
| pysimのnative実装とネイティブABIヘッダ | 21 | 6,220 | 5,786 |
| **現存C/C++合計** | **28** | **12,499** | **9,490** |

製品側には [`malloc.c`](src/allocator/malloc.c) の3,581 SLOCを含む。
`third_party/`、QA、概念コード、形式モデルは含めない。
Pythonとnative C/C++の現存合計は17,490 SLOCである。
この合計を、完成時の組み込みC++規模とは扱わない。
現存C/C++をすべて採用する比較では、20,000 SLOCまでの増分枠は10,510 SLOCである。
この枠へ、C++未実装のLoader、Runtime、共有機能、ARMv8-M対応を収める必要がある。
完成時の規模適合は、対象構成を揃えた製品C++の実装と計測で判定する。
Pythonの移行係数やソース行数から、ROM/RAM容量を外挿しない。

```bash
bash experiments/pysim/native/tier1_core/printk/build_native.sh
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
bash experiments/pysim/native/tier3_plugins/jit/build_native.sh
uv run python experiments/pysim/benchmarks/memory/bench_resource_inventory.py \
  --output /tmp/fireball-resource-inventory.json
```
