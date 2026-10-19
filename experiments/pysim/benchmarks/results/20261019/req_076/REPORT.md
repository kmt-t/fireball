# REQ-076 runtime_end 関数復帰経路の最適化計測

計測日: 2026-10-19

基準: main `eabbfaa8`。対象: `req-074-block-split-points`、コード変更コミット `9f9d7df3`。REQ-074の命令数比較はコミット `750db32c`。

push、PR、LLM監査は実施していない。

## 実装と Release 命令数

`runtime_end` は END の命令位置を1バイト進める処理に変えた。END に即値はない。次の block index が現在の block と一致する場合の同一 block 継続判定は維持した。コードをReleaseで再ビルドし、逆アセンブルから経路を数えた。

経路数は REQ-075 §3 と同じく、JIT無効、debugger/single-control-step無効、annotationあり、native continuation無効の実行経路を対象とし、共通の外側C++ dispatch loopの戻り後処理を含めない。END handler、インライン処理、および経路が継続する場合の後続dispatchを含む。`return` は `finish_function_boundary` を通るcaller復帰とroot完了を別記した。

| WASM経路 | main | REQ-074 | REQ-076 | main比 |
|---|---:|---:|---:|---:|
| end: callerへ復帰 | 50 | 53 | 48 | -2 |
| end: main/root完了 | 29 | 30 | 25 | -4 |
| end: block/ifから次block | 43 | 44 | 39 | -4 |
| end: loop内同一block継続 | 43 | 20 | 14 | -29 |
| block | 34 | 9 | 9 | -25 |
| loop | 34 | 35 | 35 | +1 |
| return: callerへ復帰 | 42 | 42 | 42 | +0 |
| return: root完了 | 21 | 21 | 21 | +0 |

W-2の動的回数は、REQ-075で同じWASM入力を使って得たopcode/PC別カウントを用いた。入力・frame hashは今回の全測定でも一致した。回数はblock 1,827、loop 1,827、block/if end 5,975、関数末尾ENDからcallerへ復帰41,079、root完了END 1。loop end delimiterはdispatchされないため0回である。`return` 命令の動的回数は独立計数していない。

| WASM経路 | W-2回数 | main | REQ-074 | REQ-076 | REQ-076−main 加重差 |
|---|---:|---:|---:|---:|---:|
| block | 1,827 | 34 | 9 | 9 | -45,675 |
| loop | 1,827 | 34 | 35 | 35 | +1,827 |
| end: loop同一block継続 | 0 | 43 | 20 | 14 | 0 |
| end: block/ifから次block | 5,975 | 43 | 44 | 39 | -23,900 |
| end: callerへ復帰 | 41,079 | 50 | 53 | 48 | -82,158 |
| end: root完了 | 1 | 29 | 30 | 25 | -4 |
| return caller/root | 未計数 | 42 / 21 | 42 / 21 | 42 / 21 | — |
| **計（returnを除く）** |  |  |  |  | **-149,910** |

ENDのcaller復帰はmainの50命令に対し48命令となり、要件の上限を満たす。REQ-074からは5命令減った。root完了は25、block/if endから次blockは39、loop end同一block継続は14命令となる。blockとloopのハンドラ数はREQ-074と同じである。`return` の2経路は全版で同じ命令列だった。

## 4 workload 計測

各 workload・各版で7プロセスを実行し、版の順序を交互にした。各版・各 workload を計測前に1回ずつ無計測で起動した。3つの呼出しworkloadは10,000 iteration・プロセス内7反復、W-2はJIT無効・warmup 3・プロセス内7反復・yield threshold 4096。

計数コマンド: `perf stat -x, -e instructions:u,cycles:u -- taskset -c 2 <python> <benchmark>`。呼出しbenchmarkは `<root>/.venv/bin/python <root>/experiments/pysim/benchmarks/interpreter/bench_call_dispatch.py --iterations 10000 --variant native --workload <indirect|direct|baseline> --repeats 7`。AO-Benchは `<root>/.venv/bin/python /tmp/REQ-039/aobench_driver.py --root <root> --warmup 3 --repeats 7 --yield-threshold 4096`。

| workload | 版 | instructions median [min,max] | cycles median [min,max] | workload時間 median [min,max] ms |
|---|---|---:|---:|---:|
| 間接呼び出し | main | 673,257,865 [672,555,256, 674,611,146] | 430,309,701 [423,808,528, 444,947,189] | 2.217347 [2.153057, 2.380291] |
| 間接呼び出し | REQ-076 | 673,129,310 [672,220,655, 674,030,638] | 436,289,500 [418,488,877, 440,229,055] | 2.238547 [2.220834, 2.444691] |
| 直接呼び出し | main | 668,556,976 [668,030,383, 670,371,099] | 440,419,181 [417,506,048, 456,410,184] | 2.174577 [2.129864, 2.258514] |
| 直接呼び出し | REQ-076 | 668,418,445 [667,261,623, 669,724,084] | 431,937,654 [424,857,494, 448,669,818] | 2.226064 [2.161162, 2.334085] |
| W-1 baseline | main | 648,624,432 [647,563,028, 649,742,719] | 420,561,263 [407,185,679, 426,543,912] | 1.962381 [1.941593, 2.020019] |
| W-1 baseline | REQ-076 | 648,618,561 [647,598,481, 649,540,226] | 426,422,788 [415,431,215, 451,030,391] | 1.975496 [1.962071, 1.987107] |
| W-2 AO-Bench 32×16 | main | 938,610,192 [937,900,406, 938,676,902] | 533,711,369 [527,604,876, 536,205,140] | 3.598204 [3.508647, 3.832170] |
| W-2 AO-Bench 32×16 | REQ-076 | 938,614,789 [937,855,927, 939,033,114] | 542,913,952 [523,405,757, 544,864,698] | 3.627508 [3.457711, 3.866334] |

| workload | paired instructions difference median [min,max] (REQ-076 − main) | paired時間差 median [min,max] ms | hash |
|---|---:|---:|---|
| 間接呼び出し | -829,156 [-1,979,420, +1,001,595] | +0.021540 [-0.141744, +0.227344] | 一致 |
| 直接呼び出し | -467,262 [-2,752,587, +1,584,904] | +0.015729 [-0.058880, +0.204221] | 一致 |
| W-1 baseline | -104,446 [-2,092,296, +1,977,198] | +0.017402 [-0.046377, +0.033903] | 一致 |
| W-2 AO-Bench 32×16 | +356,212 [-754,265, +714,383] | +0.001302 [-0.286134, +0.285733] | 一致 |

全56プロセスが終了コード0。call結果hashは `06352ea15d65c7112ff2ad0282d4ff5e14111d532acb521a19e9922c870d8e2e`、W-2入力hashは `9f5dc300e96641bf040551f9f56cb6e5bda084e98bde83a3fb1cff9a719d49e9`、frame hashは `5cd38f158314486141e032776845b8eabba32e3be1699b3b382ba92c80bba47e`で一致した。

## W-2 反復1回/11回による実行1回あたりの命令数

各版10組、各組で `repeats=1` と `repeats=11` を1回ずつ実行し、組内で `(instructions(11) - instructions(1)) / 10` を求めた。計40 process。各ペアで反復数の実行順も交互にした。計数コマンドは上記 `perf stat` の後に `taskset -c 2 <python> /tmp/REQ-039/aobench_driver.py --root <root> --warmup 0 --repeats <1|11> --yield-threshold 4096`。

| 版 | repeats=1 instructions median [min,max] | repeats=11 instructions median [min,max] | 推定 instructions/実行 median [min,max] |
|---|---:|---:|---:|
| main | 579,192,710 [578,598,060, 580,412,830] | 978,053,970 [977,046,153, 979,049,045] | 39,882,732 [39,763,666, 39,919,886] |
| REQ-076 | 581,028,532 [580,511,537, 581,945,266] | 978,595,110 [977,638,965, 978,962,944] | 39,717,160 [39,570,665, 39,810,512] |

組ごとの差（REQ-076 − main）の中央値は **-145,014 instructions/実行**、範囲は -333,486〜+29,311。版別中央値の差は -165,572 instructions/実行。10組中9組でREQ-076の推定値が小さかった。

## テスト、文書、環境

関連するInterpreter、CPS Interpreter、JIT Runtime、JIT differentialの4ファイルをpytestで実行し、256件すべて通過した。C++ source checkは対象ファイル1件、0 warning。変更行に対するclang-format検査も通過した。

Loader仕様では関数命令本体の9走査を順に記し、各走査の入力、確定/出力、一時領域上限を記録した。ソースとの対応は次のとおり。走査1はreaderのsection prevalidation、走査2はstack type analysis、走査3-4はcontrol mapの容量計数/構築、走査5はmodule-wide block capacity count、走査6はfunction block capacity recount、走査7はblock range extraction、走査8-9はannotation pool capacity count/encodingに対応する。仕様本文には実装関数名を書いていない。ローカル宣言は走査1の事前計数で1回、関数code登録時にローカル数計数と型展開で各1回読む。

Release buildは `bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release`。Compiler: Ubuntu clang version 21.1.8 (6ubuntu1). CPUは AMD Ryzen 5 5500GT with Radeon Graphics、CPU 2固定、governor powersave、kernel 7.0.0-38-generic, perf version 7.0.14。共有ライブラリSHA-256はmain `adab47e3c125a78deea89aed6d5a2046c74c220edb29c7c38b2d3d8e185362cf`、REQ-076 `8097d1d9010186899bd8d2e1dce32b900876745e9500c61888ebca16c7eb8440`。

Document formatter CLIは `spec_integrator.cli format-doc --config spec-integrator.yaml docs/components/tier2_runtime/runtime_loader.md` で実行し、0/1ファイルを正規化した。公式 `format-doc.sh` / `format-src.sh` ラッパーは未キャッシュの `ruff==0.16.8` を要求した。パッケージ取得は行わず、既存Spec-Integrator venvのCLI、変更行clang-formatで検証した。Full `check-doc --config spec-integrator.yaml --report /tmp/REQ076-check-doc-final.md` は0 errors、20 warningsで全品質ゲートを通過した。警告は既存の文章可読性・綴り候補と、リスク評価/VERIFY_LLM判定の未実施に限られる。LLM監査は指示どおり実施していない。

## コミット

| 区分 | コミット | 内容 |
|---|---|---|
| code | `9f9d7df3` | runtime_end のEND位置を1 byte加算へ変更 |
| benchmark | この記録の独立コミット | main / REQ-076の計測記録 |
| specification | `ba0f83e2` | Loader 走査仕様 |

## REQ-076 項目別確認表

| 指示項目 | 実施内容 | 根拠・結果 | 状態 |
|---|---|---|---|
| 1. caller復帰の命令数をmain以下にし、loop endの同一block動作を維持 | END位置を1 byte加算にし、同一block index判定を維持 | caller end: main 50、REQ-074 53、REQ-076 48命令。Interpreter/JIT test 256 passed | 完了 |
| 2. 経路別 Release 命令数を3版で比較 | end 4経路、block、loop、return caller/rootを逆アセンブルで比較 | 上表。Release共有ライブラリを各版から採取 | 完了 |
| 3. caller復帰がmain以下か確認 | 3版を比較 | REQ-076は48でmainの50以下 | 完了 |
| 4. Interpreter/JIT関連試験 | 4 test modules実行 | 256 passed | 完了 |
| 5. REQ-072 4 workloadとW-2反復1/11の比較 | 7 paired processes、W-2 run estimate 10 pairs | 56 + 40 process。各workloadで出力hash一致。実行1回命令数差中央値 -145,014、範囲 -333,486〜+29,311 | 完了 |
| 6. codeとbench recordを別commit | code commit済み、bench commitを作成 | code `9f9d7df3`; benchは別コミット | 完了 |
| 7. Loader走査仕様を追加しformat/check | `runtime_loader.md`に9走査と領域上限を追記 | format-doc CLI完了。Full check-doc: 0 errors, 20 warnings, quality gates passed | 完了 |
