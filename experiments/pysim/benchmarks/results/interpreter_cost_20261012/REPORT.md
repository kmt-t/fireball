# Interpreter の基本ブロック検索とフレーム構築の計測結果

<!-- traceability: {ThreadedInterpreter} {ExecutionContext_Layout} {ADR_LoopBackedgeYield} {GLOBAL_StrictMemoryLimit} -->

計測日は2026-10-12である。対象は作業開始時点の未コミット変更を含む作業ツリーである。
対象コンポーネントの実装は変更しない。診断差分は復元済みである。
仕様書と `BENCHMARK_REPORT.md` は更新していない。コミットは行っていない。

次の作業は基本ブロック検索の回数削減を第一候補とする。
320×180 の検索は1呼び出しあたり18,821,697回である。
検索の時間帰属は呼び出し全体の21.40%である。
呼び出しフレーム準備の時間帰属は5.77%である。
これらの割合は、計数なしの同じ機械語に対するサンプリングから得た概算である。

## 計測条件

<!-- traceability: {ThreadedInterpreter} {ADR_LoopBackedgeYield} -->

- 計時ビルド入口は `native/tier2_runtime/interpreter/build_native.sh --release` である。
- JIT のビルド入口は `native/tier3_plugins/jit/build_native.sh --release` である。
- Clang 21.1.8、C++23、`-O3 -DNDEBUG -flto=thin -fuse-ld=lld` を使う。
- `-fomit-frame-pointer -fno-exceptions -fno-rtti` は既存ビルド入口の設定に従う。
- CPU は AMD Ryzen 5 5500GT、OS は Ubuntu 26.04.1、Python は3.13.15である。
- 各プロセスを `taskset -c 2` で固定する。実行中の affinity が `[2]` であることを検査する。
- 解像度は64×36、128×72、192×108、256×144、320×180である。
- 各条件は3独立プロセス、各3 warmup・7計測呼び出し、yield しきい値4096である。
- Interpreter と JIT を別プロセスで順次実行する。同時に計時するプロセスは一つである。
- 各プロセスの7値の中央値を求め、その3プロセスの中央値を代表値とする。

通常計時、サンプリング、診断計数、復元後の通常計時を順に実行した。
採用した通常計時は60プロセス・420呼び出しである。
診断とサンプリングは各30プロセス・210呼び出しである。
両者の経過時間は通常計時へ混ぜない。
全120プロセスの全 warmup と計測呼び出しで、各解像度の出力 hash が一致した。
保存済み WAMR 比較結果の出力 hash とも照合した。
今回 WAMR の時間は再計測していない。

ビルド識別子、入力と出力の SHA-256、実行コマンドは同じディレクトリの JSON に保存する。
`source_manifest_before.json` は対象105ファイルの作業開始時の SHA-256 を持つ。
`restoration_verification.json` は105ファイルの復元と両ライブラリの SHA-256 一致を記録する。
HEAD は `git_head.txt`、既存差分は `git_status_before.txt` に記録する。

## 計数の定義と実測値

<!-- traceability: {ThreadedInterpreter} {ExecutionContext_Layout} -->

既存の `native_dispatch_stats` は cache hit/miss を持つ。
ただし `CollectStats` は制御境界の停止フラグを変える。
通常経路の反復を直接測るため、今回は `CollectStats=false` を保った専用計数を使った。
`FB_CONF_JIT_BLOCK_COUNTERS` も制御境界の設定を変えるため使わなかった。
現在の `block_boundary()` は通常経路でも dispatcher へ戻る。
通常経路が多くの制御境界を CPS 内で通過するとは仮定しない。

専用計数は `FB_COST_DIAGNOSTIC` のビルド分岐にだけ存在する。
カウンタは固定長の64-bit整数12個である。
カウンタは呼び出し直前にリセットし、呼び出し直後に読む。
実行 ABI、文脈のレイアウト、停止フラグ、yield 条件は変更しない。
JIT の Runtime API は命令意味論を別ライブラリへ複製するため、両ライブラリの値を合算する。

| 計数 | 計測位置 |
| :--- | :--- |
| 基本ブロック検索 | `find_cached_block_index` の入口 |
| cache hit / miss | 同手続きの hit 復帰と backing index 検索直前 |
| 制御フレーム構築 | `runtime_block` と `runtime_if` の実際のフレーム確保位置 |
| `runtime_call` | 直接・間接呼び出しの手続き入口。import も含む |
| 呼び出しフレーム構築 | 非 import の `callee` 構築位置。Python が作る最上位フレームは含まない |
| 外側ループ | `run_native_dispatch_abi` の `while (true)` の入口 |
| JIT lookup | `jit_provider->lookup` の呼び出し直前 |

以下は1回の AO-Bench 呼び出しあたりの値である。
JIT lookup 以外の主要計数は、各条件の21計測呼び出しすべてで一致した。
検索、hit/miss、制御構築、呼び出し構築は Interpreter と JIT で同じ値だった。

| 解像度 | 検索 | hit | miss | 制御構築 | `runtime_call` | 呼び出し構築 | 外側ループ |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 64×36 | 745,491 | 284,672 | 460,819 | 82,729 | 210,578 | 210,577 | 745,491 |
| 128×72 | 3,005,146 | 1,146,574 | 1,858,572 | 332,570 | 852,748 | 852,747 | 3,005,146 |
| 192×108 | 6,756,885 | 2,576,769 | 4,180,116 | 746,726 | 1,922,018 | 1,922,017 | 6,756,885 |
| 256×144 | 12,036,303 | 4,591,553 | 7,444,750 | 1,330,174 | 3,421,799 | 3,421,798 | 12,036,303 |
| 320×180 | 18,821,697 | 7,178,464 | 11,643,233 | 2,079,358 | 5,354,466 | 5,354,465 | 18,821,697 |

制御フレーム構築は `block`・`loop` と `if` の合計である。
320×180 では前者が395,790回、後者が1,683,568回である。
`runtime_if` の実行は3,139,878回である。
フレームを積まない `if` があるため、実行数と構築数を分ける。
`runtime_call` と呼び出しフレーム構築の差は各フレーム1回である。
import 呼び出しはネイティブのゲストフレームを構築しない。
最上位関数のフレームを全構築数に含める場合は、ネイティブ構築数へ1を加える。

JIT の外側ループは Interpreter より各呼び出し1回多い。
JIT lookup はフレームごとに変動するため、中央値と最小・最大を示す。
この変動の原因は今回分離していない。

| 解像度 | JIT外側ループ | JIT lookup 中央値 | JIT lookup 最小–最大 |
| :--- | ---: | ---: | :--- |
| 64×36 | 745,492 | 221,493 | 210,066–246,895 |
| 128×72 | 3,005,147 | 900,192 | 854,180–968,006 |
| 192×108 | 6,756,886 | 2,084,062 | 1,975,101–2,201,181 |
| 256×144 | 12,036,304 | 3,690,696 | 3,630,540–3,846,023 |
| 320×180 | 18,821,698 | 5,958,848 | 5,794,137–6,127,342 |

全解像度で cache hit率は約38.1%だった。
320×180 では38.14%であり、約61.86%が backing index 検索へ進む。
既存の8エントリのキャッシュは検索の大半を吸収していない。
容量を増やした場合の効果や、衝突の内訳は今回測っていない。

## 処理別の時間と割合

<!-- traceability: {ThreadedInterpreter} {ExecutionContext_Layout} -->

AMD uProf 5.3.521 の Hotspots を1 ms間隔で使った。
`--start-paused` と Profile Controller API を使い、warmup 後の7呼び出しだけを収集した。
ロード、warmup、出力の取り出し、出力 hash 計算は収集区間へ含めない。
Interpreter はデバッグ情報だけを追加した release ビルドを使った。
通常ビルドと追加ビルドの `.text` は同じ SHA-256 である。
JIT は元からデバッグ情報付きの release ビルドである。

`.text` の SHA-256 は `f54fdadb24595e0da99a6f92a8ee3ef2a807bda743dd752613f3cc2fa59cf35f` である。
標本の ELF アドレスを `llvm-symbolizer-21 --inlines` で解決した。
inline chain に該当する手続きがある標本を一度だけ数える。
同じ標本を入れ子の別項目へ帰属させる場合がある。
別項目の時間や割合を合算しない。

算式は次のとおりである。

```text
処理割合 = 3プロセスの該当標本合計 / 3プロセスの全標本合計
1呼び出しの概算時間 = 通常計時の代表値 × 処理割合
1回あたりの概算時間 = 1呼び出しの概算時間 / 診断で得た1呼び出しの平均計数
```

分母には呼び出し中の全モジュールの標本を使う。
Interpreter ライブラリ内だけの割合にはしない。
サンプリング秒数を21呼び出しで割った未補正値も `costs.csv` へ保存する。
各プロセスの割合は `summary.json` に保存する。
数値はホスト CPU 上の時間帰属であり、命令のレイテンシや MCU のサイクル数ではない。
inline 化されない別手続きへの呼び出しは、呼び出し元へ遡って帰属させない。

| 区分 | 時間へ帰属させる範囲 | 1回あたりの分母 |
| :--- | :--- | :--- |
| 基本ブロック検索 | `find_cached_block_index` とその inline chain。backing 検索を含む | 全検索回数 |
| backing 検索 | `fb_native_find_block_index_folded` | cache miss 回数 |
| 制御処理 | `runtime_block` と `runtime_if`。フレーム構築前の処理も含む | 両手続きの全実行数 |
| 制御フレーム格納 | `native_wasm_operations.cxx:1044–1046,1890–1892` | 実際の制御構築数 |
| 呼び出し処理 | `runtime_call` の inline chain 全体 | import を含む入口回数 |
| 呼び出しフレーム準備 | `native_wasm_operations.cxx:1573–1618`。ローカル初期化、引数展開、フレーム保存、文脈切り替えを含む | 実際のゲスト呼び出し構築数 |
| 外側 dispatcher | `run_native_dispatch_abi` の inline chain。検索、出口処理、JIT 入口を含む | 外側ループ反復数 |
| JIT lookup | `lookup_runtime_trace` の inline chain | lookup 回数の21呼び出し平均 |

**interpreter の時間帰属**

| 解像度 | 処理 | 概算 ms/呼び出し | 割合 | 概算 ns/回 | 標本数 |
| :--- | :--- | ---: | ---: | ---: | ---: |
| 64×36 | 基本ブロック検索 | 3.339 | 16.91% | 4.48 | 58 |
| 64×36 | backing 検索（内数） | 1.669 | 8.45% | 3.62 | 29 |
| 64×36 | 制御処理全体 | 0.863 | 4.37% | 6.15 | 15 |
| 64×36 | 呼び出し処理全体 | 1.669 | 8.45% | 7.93 | 29 |
| 64×36 | 呼び出しフレーム準備（内数） | 0.921 | 4.66% | 4.37 | 16 |
| 64×36 | 外側 dispatcher（検索を含む） | 6.102 | 30.90% | 8.18 | 106 |
| 128×72 | 基本ブロック検索 | 16.741 | 21.79% | 5.57 | 305 |
| 128×72 | backing 検索（内数） | 8.727 | 11.36% | 4.70 | 159 |
| 128×72 | 制御処理全体 | 2.141 | 2.79% | 3.79 | 39 |
| 128×72 | 呼び出し処理全体 | 6.367 | 8.29% | 7.47 | 116 |
| 128×72 | 呼び出しフレーム準備（内数） | 3.787 | 4.93% | 4.44 | 69 |
| 128×72 | 外側 dispatcher（検索を含む） | 28.268 | 36.79% | 9.41 | 515 |
| 192×108 | 基本ブロック検索 | 35.281 | 20.53% | 5.22 | 609 |
| 192×108 | backing 検索（内数） | 16.916 | 9.84% | 4.05 | 292 |
| 192×108 | 制御処理全体 | 3.824 | 2.22% | 3.01 | 66 |
| 192×108 | 呼び出し処理全体 | 15.526 | 9.03% | 8.08 | 268 |
| 192×108 | 呼び出しフレーム準備（内数） | 9.038 | 5.26% | 4.70 | 156 |
| 192×108 | 外側 dispatcher（検索を含む） | 61.119 | 35.56% | 9.05 | 1,055 |
| 256×144 | 基本ブロック検索 | 63.017 | 20.98% | 5.24 | 1,284 |
| 256×144 | backing 検索（内数） | 28.319 | 9.43% | 3.80 | 577 |
| 256×144 | 制御処理全体 | 6.528 | 2.17% | 2.89 | 133 |
| 256×144 | 呼び出し処理全体 | 29.938 | 9.97% | 8.75 | 610 |
| 256×144 | 呼び出しフレーム準備（内数） | 18.405 | 6.13% | 5.38 | 375 |
| 256×144 | 外側 dispatcher（検索を含む） | 104.931 | 34.93% | 8.72 | 2,138 |
| 320×180 | 基本ブロック検索 | 101.337 | 21.40% | 5.38 | 2,033 |
| 320×180 | backing 検索（内数） | 48.949 | 10.34% | 4.20 | 982 |
| 320×180 | 制御処理全体 | 11.913 | 2.52% | 3.37 | 239 |
| 320×180 | 呼び出し処理全体 | 45.908 | 9.70% | 8.57 | 921 |
| 320×180 | 呼び出しフレーム準備（内数） | 27.316 | 5.77% | 5.10 | 548 |
| 320×180 | 外側 dispatcher（検索を含む） | 170.275 | 35.96% | 9.05 | 3,416 |

**jit の時間帰属**

| 解像度 | 処理 | 概算 ms/呼び出し | 割合 | 概算 ns/回 | 標本数 |
| :--- | :--- | ---: | ---: | ---: | ---: |
| 64×36 | 基本ブロック検索 | 1.926 | 8.49% | 2.58 | 22 |
| 64×36 | backing 検索（内数） | 1.576 | 6.95% | 3.42 | 18 |
| 64×36 | 制御処理全体 | 0.613 | 2.70% | 4.37 | 7 |
| 64×36 | 呼び出し処理全体 | 2.101 | 9.27% | 9.98 | 24 |
| 64×36 | 呼び出しフレーム準備（内数） | 0.875 | 3.86% | 4.16 | 10 |
| 64×36 | 外側 dispatcher（検索を含む） | 7.266 | 32.05% | 9.75 | 83 |
| 64×36 | JIT lookup | 1.838 | 8.11% | 8.25 | 21 |
| 128×72 | 基本ブロック検索 | 10.125 | 11.15% | 3.37 | 184 |
| 128×72 | backing 検索（内数） | 6.934 | 7.64% | 3.73 | 126 |
| 128×72 | 制御処理全体 | 1.596 | 1.76% | 2.82 | 29 |
| 128×72 | 呼び出し処理全体 | 7.814 | 8.61% | 9.16 | 142 |
| 128×72 | 呼び出しフレーム準備（内数） | 4.512 | 4.97% | 5.29 | 82 |
| 128×72 | 外側 dispatcher（検索を含む） | 31.917 | 35.15% | 10.62 | 580 |
| 128×72 | JIT lookup | 6.053 | 6.67% | 6.70 | 110 |
| 192×108 | 基本ブロック検索 | 22.142 | 10.88% | 3.28 | 421 |
| 192×108 | backing 検索（内数） | 16.041 | 7.89% | 3.84 | 305 |
| 192×108 | 制御処理全体 | 3.944 | 1.94% | 3.11 | 75 |
| 192×108 | 呼び出し処理全体 | 18.565 | 9.13% | 9.66 | 353 |
| 192×108 | 呼び出しフレーム準備（内数） | 10.098 | 4.96% | 5.25 | 192 |
| 192×108 | 外側 dispatcher（検索を含む） | 69.107 | 33.97% | 10.23 | 1,314 |
| 192×108 | JIT lookup | 14.726 | 7.24% | 7.09 | 280 |
| 256×144 | 基本ブロック検索 | 37.463 | 10.46% | 3.11 | 766 |
| 256×144 | backing 検索（内数） | 25.481 | 7.11% | 3.42 | 521 |
| 256×144 | 制御処理全体 | 6.603 | 1.84% | 2.92 | 135 |
| 256×144 | 呼び出し処理全体 | 28.220 | 7.88% | 8.25 | 577 |
| 256×144 | 呼び出しフレーム準備（内数） | 15.553 | 4.34% | 4.55 | 318 |
| 256×144 | 外側 dispatcher（検索を含む） | 123.834 | 34.56% | 10.29 | 2,532 |
| 256×144 | JIT lookup | 27.339 | 7.63% | 7.32 | 559 |
| 320×180 | 基本ブロック検索 | 61.043 | 10.87% | 3.24 | 1,235 |
| 320×180 | backing 検索（内数） | 44.139 | 7.86% | 3.79 | 893 |
| 320×180 | 制御処理全体 | 11.961 | 2.13% | 3.38 | 242 |
| 320×180 | 呼び出し処理全体 | 48.439 | 8.62% | 9.05 | 980 |
| 320×180 | 呼び出しフレーム準備（内数） | 27.877 | 4.96% | 5.21 | 564 |
| 320×180 | 外側 dispatcher（検索を含む） | 196.376 | 34.96% | 10.43 | 3,973 |
| 320×180 | JIT lookup | 41.420 | 7.37% | 6.94 | 838 |


320×180 の Interpreter では全9,499標本、JIT では全11,363標本を得た。
Interpreter の検索割合はプロセス別に20.86–22.43%だった。
フレーム準備の割合は5.58–5.88%だった。
小さい解像度の結果は標本数が少なく、ばらつきが大きい。

制御フレームの格納行は Interpreter で9標本、JIT で11標本だけである。
時間帰属はそれぞれ0.095%、0.097%だった。
単独構築の1回あたり時間を確定する根拠として採用しない。
参考計算値は `costs.csv` に保存する。
制御処理全体は Interpreter で11.91 ms、2.52%だった。
これは実行1回あたり3.37 nsに相当する。
構築1回へ全時間を償却すると5.73 nsになるが、非構築の `if` も含む。
この値をフレーム格納だけの費用とは呼ばない。

容量検査の該当行は Interpreter で2標本、JIT で0標本だった。
最適化による命令配置と行番号帰属の影響を分離できない。
容量検査の費用がゼロであるとは判定しない。
cache hit だけの時間も、共通処理を実行経路別に分離していないため未確定である。

このサンプリングの意味と制約は AMD の説明に従う。
標本間隔を各関数へ帰属した値は、各実行を個別にストップウォッチで測った値ではない。
[AMD の標本時間の説明](https://docs.amd.com/r/en-US/68658-uProf-getting-started-guide/Understanding-Hotspots-Data)を参照する。
[AMD の Hotspots と inline 解析の説明](https://docs.amd.com/r/en-US/68658-uProf-getting-started-guide/AMD-uProf-Hotspots-Analysis)を参照する。

## 計数追加の影響

<!-- traceability: {ThreadedInterpreter} -->

診断計数の時間は採用する性能値へ含めない。
比較の通常基準は、追加前と復元後の6プロセスの中央値である。
診断値は3プロセスの中央値である。
診断差は、カウンタの命令だけでなく、最適化とコード配置の変化も含む。
純粋な increment の費用だけを示すものではない。

| 経路・解像度 | 追加前 ms | 診断 ms | 復元後 ms | 通常基準 ms | 診断差 | 通常中央値の範囲 ms | 診断中央値の範囲 ms |
| :--- | ---: | ---: | ---: | ---: | ---: | :--- | :--- |
| interpreter 64×36 | 19.745 | 19.283 | 19.290 | 19.406 | -0.64% | 18.959–20.975 | 19.114–19.471 |
| jit 64×36 | 22.673 | 22.749 | 23.004 | 22.839 | -0.39% | 22.287–23.925 | 22.489–22.993 |
| interpreter 128×72 | 76.846 | 76.300 | 75.837 | 75.923 | +0.50% | 75.279–76.861 | 75.131–76.511 |
| jit 128×72 | 90.798 | 89.754 | 89.657 | 90.228 | -0.52% | 89.322–93.361 | 88.080–90.653 |
| interpreter 192×108 | 171.887 | 170.317 | 170.149 | 171.224 | -0.53% | 168.337–172.825 | 167.276–171.329 |
| jit 192×108 | 203.430 | 198.078 | 200.021 | 200.883 | -1.40% | 199.410–205.917 | 196.398–200.319 |
| interpreter 256×144 | 300.413 | 300.340 | 300.935 | 300.674 | -0.11% | 296.770–307.247 | 296.944–302.453 |
| jit 256×144 | 358.297 | 350.834 | 356.118 | 357.208 | -1.78% | 352.021–365.051 | 349.988–354.413 |
| interpreter 320×180 | 473.489 | 465.731 | 472.398 | 473.110 | -1.56% | 470.978–481.925 | 465.321–477.443 |
| jit 320×180 | 561.646 | 556.094 | 559.233 | 560.440 | -0.78% | 554.625–565.925 | 551.627–576.552 |

全解像度の診断差は Interpreter で−1.56–+0.50%、JIT で−1.78–−0.39%だった。
診断ビルドによる明確な正のオーバーヘッドは確認できなかった。
負の差を高速化の効果として扱わない。
診断と通常のプロセス中央値の範囲は320×180で重なる。
正常計時のばらつきと計数によるコード配置の影響を分離していない。
計数の費用がゼロであるとも主張しない。

## 次の作業の提案

<!-- traceability: {ThreadedInterpreter} {GLOBAL_StrictMemoryLimit} -->

次の優先度は、時間帰属と回数の実測を根拠とする。
改善量は今回実装して検証した値ではない。

1. **基本ブロック検索の回数削減を第一候補とする。** 320×180 で約1,882万回、約101 msを占める。call、return、静的な制御遷移で確定した検索結果を再利用できるか調べる。既存の Loader メタデータやフレームの継続情報を先に確認する。単にキャッシュ容量を増やす案は、衝突と局所性を別途計測してから比較する。
2. **関数単位のフレーム準備の再利用を第二候補とする。** 約535万回の構築で、準備範囲は約27 msを占める。現在も `local_slot_count`、`param_packed_slot_count`、`local_offsets`、`local_sizes` は関数側に保持する。何を追加で事前計算すると実行時のどの読み出し・展開を省けるかを具体化する。引数値、ローカルのゼロ初期化、戻り先、実行文脈は動的であり、時間の全量を除去できるとは仮定しない。
3. **容量検査の関数入口への移動は別の依頼とする。** 今回の行別標本では費用を分離できなかった。制御フレームやオペランド領域の高水位、呼び出し間の容量契約、trap 条件を先に確定する。今回の結果だけでは移動による改善率を提示できない。

制御フレーム格納だけが主要な遅延原因という仮説は支持されなかった。
構築を含む制御処理全体でも時間帰属は約2.52%だった。
一方で、検索がホットという仮説と呼び出し準備に費用があるという仮説は支持された。
フレーム準備の関数単位再利用で、どの割合を実際に除去できるかは未検証である。

検索の21.40%を仮に全部除けても、理想的な速度向上は約1.27倍である。
これは他の時間が固定の場合の上限計算である。
外側ループそのものも減らす変更は別の効果を持ち得る。
外側 dispatcher の35.96%には検索が含まれるため、両割合は足さない。
今回の候補だけで WAMR との差全体を説明したとは判定しない。

JIT でも主要な検索・フレーム構築回数は減らなかった。
JIT lookup は追加で約41.42 ms、7.37%の時間帰属を持つ。
このワークロードでは JIT の通常計時も Interpreter より遅かった。
この観測を、別ワークロードや別ターゲットでも JIT が遅いという一般論へ広げない。

提案は新しい RAM キャッシュを前提としない。
既存の検索キャッシュは8×16=128 bytesである。
容量変更時は増加分、フレームごとの増加、初期化と無効化の費用を別に算出する。
関数メタデータの追加が必要なら、所有権と ROM 配置を先に決める。
最小構成 RAM 32 KB・ROM 96 KBへの物理適合性は本ホスト計測では確認しない。
参照元は `docs/architecture/resource_budget_estimation.md` である。

## 検証と除外した試行

<!-- traceability: {ThreadedInterpreter} {ExecutionContext_Layout} -->

関係するフレーム、call/return、制御ブロック、block-search cache、yield、JIT lookup の11テストは通過した。
163テストは指定範囲外として deselect した。
詳細は `tests.log` に保存する。
QA ビルドは通常のアサーション付き入口で作成した。
計時用の製品ライブラリの SHA-256 は QA ビルド後も同じである。

標準のソースゲートは失敗した。ファイル指定にもかかわらず、入口が全30単体スイートを自動実行した。
内訳は28スイート通過、2スイート失敗である。指定した11テストの通過とは別に記録する。
今回追加したスクリプトの `zip(strict=...)` 不足は修正した。
修正後の6スクリプトの Ruff 検査は通過した。記録は `ruff.log` に置く。
残る失敗は、製品の入口アサーションと QA の JIT 実行回数の試験である。
入口アサーションの試験は SIGABRT を期待したが、`NDEBUG` ビルドでは SIGSEGV になった。
JIT 実行回数の6試験は、今回 QA ビルドで `--block-counters` を指定しなかったため0を読んだ。
この結果を release 計測の出力不一致とは扱わない。
関係するテストだけを実行する条件に従い、全スイートの再実行は行わない。
標準ソースゲート全体が通過したとは報告しない。

文書ゲートは `docs/` 外の結果ファイルを受け付けず、パス検査の ValueError で終了した。
報告ファイルのために文書の正本やゲートを変更しない。
記録は `check_doc.log` に置く。文書ゲート全体が通過したとは報告しない。
表の列数、見出し、未展開の置換文字、計数恒等式、出力 hash は結果用の検査で確認した。

復元後の予備試行は除外した。
`llvm-objcopy --dump-section` に出力 ELF を指定しなかった操作が、入力 ELF のメタデータを書き換えた。
ロード済みライブラリを触った試行で SIGBUS が発生した。
ライブラリを入口から再構築し、開始時の SHA-256 に戻してから全30プロセスを再実行した。
採用した30 JSON はすべて正常終了したプロセスの結果である。
`restored_matrix_attempts.log` は先行試行の出力混入を含む記録である。
`restored_matrix.log` は採用した JSON から再構成した一覧である。
再実行例では、抽出用に別の出力 ELF を指定する。

## 再実行コマンドと保存ファイル

<!-- traceability: {ThreadedInterpreter} -->

以下はリポジトリルートから実行する。
現在の対象ソースが `source_manifest_before.json` と同じ場合に診断パッチを再適用できる。
`instrument.py install` は元ファイルを一時領域へ保存する。
`restore` は現在のファイルが診断差分と一致することを確認してから戻す。
計測の途中にソースやライブラリを変更しない。
同じ名前の結果を更新するため、保存済み結果を保持する場合は先に退避する。

```bash
TASK=experiments/pysim/benchmarks/results/interpreter_cost_20261012
TEMP_TASK=/tmp/fireball-interpreter-cost-20261012
mkdir -p "$TEMP_TASK"

bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
.venv/bin/python "$TASK/run_matrix.py" --mode baseline

cp experiments/pysim/tier2_runtime/interpreter/libnative_interpreter.so "$TEMP_TASK/baseline_interpreter.so"
cp experiments/pysim/tier3_plugins/jit/libtrace_compiler.so "$TEMP_TASK/baseline_jit.so"
cp experiments/pysim/native/tier2_runtime/interpreter/build_native.sh "$TEMP_TASK/build_native.sh"
sed -i 's/clang++ -std=c++23/clang++ -g -std=c++23/' experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
cp experiments/pysim/tier2_runtime/interpreter/libnative_interpreter.so "$TEMP_TASK/symbols_interpreter.so"
cp "$TEMP_TASK/build_native.sh" experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
llvm-objcopy-21 --dump-section ".text=$TEMP_TASK/baseline.text" "$TEMP_TASK/baseline_interpreter.so" "$TEMP_TASK/inspect_baseline.so"
llvm-objcopy-21 --dump-section ".text=$TEMP_TASK/symbols.text" "$TEMP_TASK/symbols_interpreter.so" "$TEMP_TASK/inspect_symbols.so"
sha256sum "$TEMP_TASK/baseline.text" "$TEMP_TASK/symbols.text"
.venv/bin/python "$TASK/run_profiles.py"
.venv/bin/python "$TASK/analyze_profiles.py"

.venv/bin/python "$TASK/instrument.py" install
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
.venv/bin/python "$TASK/run_matrix.py" --mode diagnostic
.venv/bin/python "$TASK/instrument.py" restore
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
.venv/bin/python "$TASK/run_matrix.py" --mode restored
.venv/bin/python "$TASK/summarize.py"
```

個別の通常計時は既存入口でも再実行できる。

```bash
taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/aobench/bench_native_build.py \
  --engine interpreter --width 320 --warmup 3 --repeats 7 --yield-threshold 4096
```

実際の診断・プロファイルの起動コマンドは各 JSON に配列として保存する。
`run_profiles.py` は `AMDuProfCLI collect --config hotspots --timer-interval 1 --start-paused` を使う。
report は `--inline --cutoff 0 --db sqlite` で生成する。
生のプロファイルと SQLite DB は `/tmp/fireball-interpreter-cost-20261012/profile_*` に置く。
保存する結果には全アドレス標本の集計と inline chain の対応を含める。

| ファイル | 内容 |
| :--- | :--- |
| `counts.csv` | 全解像度・両経路の計数、中央値、平均、最小、最大 |
| `costs.csv` | 全処理の標本数、割合、補正した時間、標本時間、1回あたり時間 |
| `timing.csv` | 追加前、診断、復元後、プロファイル中の比較と出力 hash |
| `summary.json` | 上記の全値とプロセス別の割合 |
| `baseline_*.json`, `diagnostic_*.json`, `restored_*.json` | 各プロセスの7計時値、SHA-256、コマンド、計数 |
| `profile_*_commands.json`, `profile_*.log` | 採取・report コマンドとプロファイル中の出力 hash |
| `profile_*_samples.csv`, `symbol_mapping.json` | アドレスごとの標本数、分類、全 inline chain |
| `profile_*_report.csv`, `profile_summary.json` | uProf の元 report と処理別の標本合計 |
| `environment.json`, `symbol_builds.json`, `diagnostic_builds.json` | 環境、ビルド識別情報と SHA-256 |
| `*.patch`, `instrument.py` | 復元済みの診断差分と再適用・復元手順 |
| `source_manifest_before.json`, `restoration_verification.json` | 作業開始時と復元後の一致検査 |
| `tests.log`, `check_src.log`, `check_doc.log` | 関連テストと対象を絞った品質ゲートの記録 |
| `ruff.log`, `artifact_validation.json` | 計測スクリプトの規約検査と結果ファイルの検査 |
| `SHA256SUMS` | 保存成果物の SHA-256 一覧 |
