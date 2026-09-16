# PySIM の計測と最適化判断

## 判断の土台

Fireball の pysim は、最小構成 SRAM 32KB / Flash 96KB を対象とする参照モデルである。予算の正本・内訳は docs/architecture/resource_budget_estimation.md と docs/components/tier1_core/system_config.md から読み直す。過去の集計値をそのまま「空き」とみなさず、追加領域が既存プール内かプール外か、最大容量・整列・メタデータを含むかを示す。資料間の算術・前提が一致しなければ、合意済みの空き容量として扱わず不一致を報告する。

VTune が測るのは計測ホスト上の Python プロセスである。Python オブジェクトの割当て量は、コンパイル後の Cortex-M の SRAM 配置量ではない。x86 のサンプル数、キャッシュミス、命令時間も、Cortex-M のサイクル数や Flash サイズと同値ではない。VTune は「どの処理が、どの入力で、どの頻度で動くか」「Python 上でどの呼び出し経路が高価か」を探す道具として使う。C++ 側の見積りは、移植先の型と固定容量から別に算出し、可能ならターゲットの map ファイル、セクションサイズ、スタック高水位、実機または同一 ABI のベンチマークで確かめる。

最適化の順序は、仕様・不変条件 → 実測されたボトルネック → 計算量・参照局所性 → RAM/ROM と最悪時時間 → 変更の広さと保守負荷とする。ホットであることは仕様変更やメモリ追加の免罪符にならない。現在の設計で十分な処理を、測定根拠なしに別コンテナや新しいキャッシュへ置き換えない。

## VTune 分析の選択

VTune の Python 対応と分析タイプは版・OS・CPU に依存する。まずインストール済み版の CLI help と公式ユーザーガイドを確認し、コレクタやオプションを推測しない。

| 疑問 | まず選ぶ分析 | 判断時の注意 |
| --- | --- | --- |
| どの pysim 関数／呼び出し経路が CPU 時間を使うか | Hotspots | Python managed-code 表示と Cython/native extension の双方を確認する。相対時間とサンプルの偏りを見る。 |
| どの割当て箇所が Python のメモリ増減に寄与するか | Memory Consumption | Python では Linux 対象に限る。ターゲット SRAM、静的バッファ、Flash/ROM の証拠にはしない。 |
| CPU キャッシュ階層やメモリ帯域がホスト実行を制限するか | Memory Access または Microarchitecture Exploration | 対象 CPU、ドライバ／Perf、収集モードの対応を確認する。ハードウェアイベントの結果を Cortex-M へ外挿しない。 |
| シナリオ全体の実行時間・段階ごとの配分 | 既存ベンチマークと Hotspots | 既存ベンチマークを先に読み、起動・ロード・検証・後処理を含むか記録する。関数単体値と総時間を混同しない。 |

CLI の基本形は次のとおり。プレースホルダーは実際の Python 実行ファイル、絶対パスのベンチマーク、必要な引数に置き換え、使える knob は vtune -help で確かめる。

    vtune -collect hotspots -result-dir <unique-result-dir> -- <python> <absolute-benchmark.py> [args]
    vtune -report hotspots -result-dir <unique-result-dir> -report-output <report.txt>
    vtune -collect memory-consumption -result-dir <unique-result-dir> -- <python> <absolute-benchmark.py> [args]

Memory Consumption は Python プロファイルでは Linux のみ対応する。Windows で使えない分析を代替の数値であるかのように推定しない。VTune が存在しない・権限や PMU 制約で分析できない場合は、導入や権限変更を勝手に行わず、利用可能な結果・既存ベンチマークでできる範囲と未測定項目を示す。

一回の測定結果だけで採否を決めない。Python バージョン、VTune 版、OS/CPU、作業ツリー、入力、コマンド、ウォームアップ、反復回数、計測時間、結果の正当性チェックを記録する。基準と候補を同じ条件で複数回測り、中央値とばらつきを比べる。VTune を通した時間と非計測ベンチマーク時間を分ける。差がばらつきより小さい場合は「改善を確認」と言わない。

このリポジトリの experiments/pysim/benchmarks/run_all.py は線形メモリ、vMMIO、JIT、AO-Bench などをまとめて走らせる。原因調査ではまずボトルネックに対応する個別ベンチマークを選び、総合スイートはマクロな回帰確認に使う。ワークロードの出力や意味的結果をベンチマークが直接検査しているかも読む。

## コンテナ選択

問いの形、更新頻度、上限、読取り回数、実測されたコストで選ぶ。実装は docs/components/tier1_core/system_containers.md と pysim の import/Tier 正本に適合させる。製品 pysim で組み込み dict / set / list を使ってはならない。容量不明の伸縮配列を tuple 再生成で偽装しない。

| 用途とアクセス形 | 既存語彙の候補 | 選択条件と費用 |
| --- | --- | --- |
| 密な整数添字から小さい状態値を取得・更新 | BitView / MutableBitStorage または固定長配列 | 直接添字が問いそのものなら検索索引を足さない。値域が 1/2/4 bit で収まる場合だけビット詰めのサイズと読み書き命令を比較する。 |
| 小規模・静的な疎キーの存在確認 | ReadOnlyFlatSetView | 値列が不要で、ソート済みキーに対する二分探索が十分な場合。 |
| 小規模・静的な疎キーから値を検索 | ReadOnlyFlatMapView | ソート済みペア列を持つ。検索頻度・キー数に対して追加の索引が不要な場合。 |
| 大きな読取り中心テーブルの検索 | ReadOnlyRadixBinaryTreeView | 実測で全域探索のコストが問題となり、Radix Table の ROM/RAM と構築・更新費が予算内の場合。小さな表には付けない。 |
| 実行中に更新される固定容量のキー集合／マップ | MutableFlatSetStorage / MutableFlatMapStorage。仕様が求めるときだけ Radix 版 | 最大件数、キー/値の幅、更新コストを先に確定する。所有ストレージと同じ実体を指す View を所有側で重複保持しない。 |
| 上限付き LIFO 列 | StaticVector | 実測上の push/pop 列が必要で、容量上限を仕様から導けるとき。 |
| 上限付き FIFO | RingBuffer | 順序付きキューが必要なとき。LIFO 用途へ流用しない。 |
| 頻繁に再利用する同一検索結果 | 既存仕様のキャッシュまたは追加の固定容量キャッシュ案 | VTune/カウンタ等で時間局所性とヒット率を確かめる。キー、値、valid bit、衝突、更新時無効化を含む RAM コストを計上し、必要な検索器より総コストが小さいときだけ候補にする。 |

この表は語彙を追加・変更する許可ではない。既存コンポーネント仕様がデータ構造を定めている場合はそれに従う。例えば小規模表に Radix 索引を足す、所有側で storage と view を二重管理する、テーブルと値を別配列にして意味のない複製を作る案は、ホストで少し速くても提案から除く。コンテナ案には、要素上限と sizeof ベースの概算（要素、整列、容量、カウンタ、索引、アロケータがあればその分）を必ず添える。

## 事前計算と遅延計算

各候補について次を埋めて比較する。

- 計算値はビルド時に確定する不変定数か、モジュール入力に依存する値か、実行中に変化する状態か。
- 値が使われる回数・比率、初回アクセスの期限、ライフタイム、再ロード／無効化条件は何か。
- 事前計算はどこで行い、結果はどこに置くか。Flash の .rodata に置けるのか、各モジュールの SRAM/guest pool に置くのか、起動時に RAM で作るのか。
- 遅延計算はアクセスごとに何サイクル相当か。遅延初回処理が許されるか。メモ化するなら追加状態と競合・無効化費用はいくらか。

概算では、事前処理コストを C_pre、保存値の検索コストを C_lookup、遅延で一度計算するコストを C_eval、実際に値が要求される回数を N とし、C_pre + N × C_lookup と N × C_eval を同じ単位で比較する。事前生成の表サイズ・起動遅延・コードサイズを別軸で予算評価する。N が小さい、使われない入力が多い、前計算で SRAM を圧迫する、または起動時レイテンシ制約があるなら、遅延計算が妥当なことがある。頻繁に使う値なら事前計算が有利なことがあるが、推定頻度ではなく代表シナリオの頻度と最悪時制約を根拠にする。

ビルド時に定まる不変表は .rodata 化できるかを検討する。モジュールごとに入力から導く索引は一般に静的 ROM テーブルとは別物であり、最大モジュール数分の RAM を数える。値が不変でもローダが実行時に生成するなら、Flash 消費がゼロとは限らない。遅延計算に変えても、その値が必ず使われる経路では総計算量が減らず、初回の worst-case latency を悪化させる場合がある。メモ化は「計算回数を減らす」代わりに、保存領域・初期化・更新整合性を買う案として扱う。

## 案の順位付けと報告

候補ごとに以下を区別する。

1. **観測**: VTune の分析名、関数/呼び出し経路、サンプル割合・時間・カウンタ、ワークロードと実行条件。
2. **解釈**: 総時間への寄与と測定の限界。ホスト Python だけで分かる事実と、ターゲットへ移植する推論を分ける。
3. **変更案**: 変更するアルゴリズム／コンテナ／計算タイミングと、既存仕様のどの条件に適合するか。
4. **コスト**: RAM bytes と配置先、ROM bytes、初期化処理、実行時コスト、worst-case latency、Tier 依存やテストの広がり。数値にできなければ未見積もりと明記する。
5. **確かめ方**: 関係する意味テスト、代表ベンチマーク、VTune の再計測、対象ビルドの map/section/stack データ。

最優先は、仕様を保ちながら、繰り返しホットな経路の計算量を落とし、既存の固定容量語彙で予算内に収まる案である。わずかなホスト時間短縮と引き換えに新しい常駐状態、重複インデックス、初期化経路、Tier 越境、要件の緩和を持ち込む場合は採用を保留し、その費用と代案を並べる。効果が測定ノイズ以下・未確認なら最適化を勧めない。

## 公式 VTune 参照

版によって画面、オプション、対応ハードウェアは変わるため、実行時にはインストール済み版のガイドを優先する。

- [Python Code Analysis](https://www.intel.com/content/www/us/en/docs/vtune-profiler/user-guide/2025-4/python-code-analysis.html): Python の Hotspots/Threading と Linux の Memory Consumption 対応、および制限。
- [Analysis Types](https://www.intel.com/content/www/us/en/docs/vtune-profiler/user-guide/2025-4/analysis-types.html): 各分析タイプの用途とターゲット制限。
- [Memory Consumption CLI](https://www.intel.com/content/www/us/en/docs/vtune-profiler/user-guide/2025-4/run-memory-consumption-analysis-command-line.html): Python プロセスの割当てと解放を収集する CLI。
- [Memory Usage View](https://www.intel.com/content/www/us/en/docs/vtune-profiler/user-guide/2025-4/memory-usage-view.html): ホスト CPU のキャッシュ、ロード/ストア、帯域分析。
- [VTune Command Syntax](https://www.intel.com/content/www/us/en/docs/vtune-profiler/user-guide/2025-4/command-syntax.html): CLI の基本構文。
