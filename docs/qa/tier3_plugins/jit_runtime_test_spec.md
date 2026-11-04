# JITランタイム (キャッシュ・ホットスポット検出) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md)
関連正本: [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md)（{JIT_LazyChaining}、{JIT_AssemblyStencilCodegen}はjit_runtimeと共同責務）
本仕様は、実行コンテキストのBasicBlock indexとPC照合、カード状態によるJIT検索抑止、BasicBlock内JIT参照、ホットスポット昇格、3面コードキャッシュ、chain dispatcher、および実行可能メモリの初期権限を検証する。製品RuntimeはLoaderのBasicBlock参照を使い、`FB_CONF_JIT_CACHE_TOTAL_ENTRY_CAPACITY`件以下のソート済みPC索引はキャッシュ更新とchain metadataの更新に使う。x64参照構成を対象とし、ARMv8-Mの物理配置、ABI、保護方式は別途定義する。

## 2. テストケース一覧

### 2-bitカードマーキング (jit_runtime.md (Card Marking))
<!-- traceability: {WasmCodeSectionPC} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-01 | カードは命令単位ではなくカード単位 | 同一64バイトカード内の2つの異なる命令オフセット | 一方をtouch | 他方も同じ状態を共有する（カード粒度） | jit_runtime.md |
| TEST-JITR-02 | 状態遷移: UNEXECUTED→EXECUTED→HOT | 新規カード | 1回touch、2回目touch | 1回目でEXECUTED、2回目でHOT | jit_runtime.md (Card Marking) |
| TEST-JITR-03 | COMPILED後のtouchは状態を変えない | カードがCOMPILED | touch | 状態はCOMPILEDのまま | |
| TEST-JITR-04 | 評価(Eviction)でUNEXECUTEDへ戻る（EXECUTEDではない） | カードがCOMPILED、対応トレースがキャッシュから追い出される | `mark_evicted` | 状態がUNEXECUTEDに戻る。 | `` |
| TEST-JITR-05 | 履歴リング上書きとyield時の一度限りの分析 | 履歴容量Nの JITランタイムに N+K 個（K=2）の判定点（追跡可能なblock head）がある | N+K 件を判定点で記録してyieldを2回実行する | 上書き件数はKとなり分析は近似扱いとなる。古いK件のカードは`UNEXECUTED`へ戻り、残りN件は`EXECUTED`となる。2回目のyieldで状態を再度消費しない | `test_jit_runtime.py::test_hotspot_02_history_ring_buffered_yield_drain` |
| TEST-JITR-06 | 最小トレース長未満のブロックは共有カードを更新しない | 同一コード領域カード内に2つの短いベーシックブロック（推定コンパイル後サイズがカード幅未満） | 両方のPCを繰り返し実行 | 候補外PCは共有カード状態を更新せず、コンパイル待ち列にも追加されない。カード領域の共有有無にかかわらずカード状態は `UNEXECUTED` のままである。 | `jit_runtime.md`「最小トレース長フィルタ」、 `` |
| TEST-JITR-07 | `COMPILED`カードの重複要求をC++ Runtimeが破棄する | トレースがキャッシュに常駐し、同PCのカードが`COMPILED`で、同PCが待ち列にも積まれている | idle_hookを実行 | C++ Runtimeはコンパイルせず要求を破棄し、カード状態と常駐トレースを保つ。 | `jit_runtime.md`「コンパイル待ち列」、 `` |
| TEST-JITR-08 | JITエントリテーブル（バンクのトレース一覧）は常にhead_pcでソートされ、削除は削除フラグ（トンビストーン）で行う | 複数のトレースをPC順不同で挿入し、1件削除後に同じPCを再挿入 | 挿入・削除・再挿入後にトレース一覧と該当PCの取得結果を確認 | 一覧は常にhead_pc昇順。削除直後は取得結果がNone。再挿入は既存tombstone枠を再利用し、取得結果は新しいトレースそのものになる。 | jit_runtime.md「JITエントリ表」, [`jit_runtime.cxx`](experiments/pysim/native/tier3_plugins/jit/jit_runtime.cxx),|
| TEST-JITR-09 | Oldestのその場実行と既存chain維持 | Warmのtarget Bへsource Aがchain済み | BをOldestへ移しlookupする。新source CからBへの接続を試み、次のrotateを実行する | BのアドレスとAの既存chainはlookupで変わらない。CはBへ新規chainしない。Bの退役前にAのtargetを0へ戻す | `` |
| TEST-JITR-09b | 通常実行ではJIT拡張内部で常駐traceを検索する | 未コンパイルブロックが多く、実行中にtraceをコンパイルする関数 | 関数を実行し、QA計測でJIT実行件数、Python側`cache.lookup()`の呼出し、常駐状態とカードを確認する | 関数結果が正しく、JITを実行する。本体実行入口がJIT拡張内部でtraceを選び、Python側`cache.lookup()`を呼ばない。QA snapshotの各PCは`COMPILED`カードと常駐traceに対応する | `jit_runtime.md`「トレース実行時の分岐解決とインタープリタ復帰」、pysim `` |
| TEST-JITR-71 | カード表と候補抑止表はコード領域単位 | 4バイトカード境界をまたぐ2関数が同じCode-sectionカードに属する | 一方をtouchし、候補カードをmark/unmarkする | 両関数のPCが同じホットスポット状態と候補抑止bitを共有する。各表はコード領域全体を覆う単一の密ストレージを持つ | `jit_runtime.md`「Card Marking」「Trackable Mask」、pysim `` |

### ホットスポット判定 (yield時) と バッチコンパイル
<!-- traceability: {GOTCHA-JITR-09} {JIT_CardAgingSweep} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-10 | 検出と通常コンパイルの遅延、および満杯時の即時drain | カードがHOTになる | 通常時とキュー満杯時を分けて実行 | 通常時はyield/idleまでコンパイルせず、キュー満杯時だけその場でLIFO drainしてキューを空ける。キュー容量を超えて保持しない | jit_compiler.md ADR_SafeQueuingOnHotMiss, `{JIT_ReverseCompilationOrder}` |
| TEST-JITR-12 | LIFO順でのバッチコンパイル | 直線後続がA→B→Cとなる適格なブロックをA、B、Cの順にキューへ投入済み | `idle_hook`を実行する | C、B、Aの順にコンパイルされる。Aのinstall時にはBが常駐済みで、共通chain dispatcherを経由する後続chainが成立する | {JIT_ReverseCompilationOrder} |
| TEST-JITR-13 | 常駐トレース再確認とカード状態の整合 | コンパイル要求PCがキューに重複登録済み、またはキャッシュ常駐済み | idle_hookを実行 | 常駐済みなら再コンパイルせず`COMPILED`へ同期する。全キャッシュmissはコンパイル要求として扱い、eviction後のPCを`COMPILED`のまま放置しない | `{ADR_SafeQueuingOnHotMiss}` |
| TEST-JITR-14 | コンパイル失敗時の恒久的候補除外 | C++コンパイラが失敗を返す | idle_hookを実行 | Trackable Maskの対象ビットだけを解除し、カードを`COMPILED`へ設定しない。同一モジュール内で再履歴・再キュー・再コンパイルを行わない | `{TrackableBlockMask}` |
| TEST-JITR-15 | eviction／明示的flush後の再計測 | 常駐トレースがevictまたはJIT cache flushされる | そのPCを再実行 | キャッシュ参照はInterpreterへ戻り、カードは`UNEXECUTED`からhotnessを再計測する。候補性は維持される | `TEST-JITR-04`, `{TrackableBlockMask}` |
| TEST-JITR-16 | エイジングスイープは`EXECUTED`カードだけを`UNEXECUTED`へ戻す | コード領域内に`EXECUTED`が複数枚、`HOT`が1枚、`COMPILED`が1枚のカードがある | dirtyカードを含む表を一周以上スイープする | `EXECUTED`カードだけが`UNEXECUTED`になる。`HOT`と`COMPILED`は変化しない。減衰後に1回touchしたカードは`EXECUTED`であり`HOT`にならない。 | `GOTCHA-JITR-09`, `JIT_CardAgingSweep`, `` |
| TEST-JITR-17 | カード更新表の包含不変条件 | コード領域内のカードを複数関数からtouchし、touchとスイープを交互に行う | 各操作の後に観測済みカード状態とdirty bitを照合する | dirty bitが0のカードは`EXECUTED`状態ではない。`UNEXECUTED`から`EXECUTED`への遷移時だけ、そのカードのbitが立つ。スイープはbitを立てない | `JIT_CardAgingSweep`, `` |
| TEST-JITR-18 | エイジングは3面キャッシュのローテーション1回につき1ステップ進む | カード更新表にdirty bitがあり、待ち列が空・処理済み・コンパイル無効のいずれかである | 各状態でidle_hookを実行し、続けて`flush_all`と`rotate`を行う。さらにバンクが満杯になるまでトレースを挿入する。各段階でカーソルを確認する | idle_hookと`flush_all`ではカーソルは進まない。`rotate`の実行で1ステップ進む。バンク満杯による自動ローテーションでも1ステップ進む | `JIT_CardAgingSweep` |
| TEST-JITR-19 | カードdirty表の巡回、走査上限、有限減衰 | コード領域カード数が9以上で複数バイトにわたりdirty bitが立つ | スイープを1ステップずつ実行する | 1ステップで処理する非ゼロバイト数は設定値（`FB_CONF_JIT_AGING_STEP_UNITS`）以下。0バイトは処理数に数えず読み飛ばす。走査バイト数が上限（`FB_CONF_JIT_AGING_STEP_SCAN_BYTES`）に達した場合も終了する。カーソルは表末尾から先頭へ戻る。1バイトに複数カードbitが立つ場合はその全カードを同じステップで処理する。全dirtyカードは有限回のローテーション内に減衰する | `JIT_CardAgingSweep` |

### JIT拡張ホットスポット履歴

<!-- traceability: {HistoryBuffer} {RuntimeHotspotProfiler} {LowLatencyJIT} -->

Interpreter境界で記録する履歴と、Tier 3 JIT拡張による実行区間終了時の一括分析を検証する。形式モデルは[`jit_hotspot_model.py`](docs/components/tier3_plugins/formal/jit_hotspot_model.py)を参照する。

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 |
| :--- | :--- | :--- | :--- | :--- |
| TEST-HOTSPOT-01 | 履歴レコードとInterpreter記録順 | JIT拡張有効で、複数の適格ブロックを通る関数がある | Interpreter区間を実行し、JIT拡張の履歴を読む | 各レコードがInterpreterの解決した`block_index`とCode section payload相対`unified_pc`を保持し、観測されたPC列が実行順と一致する。非適格ブロックは含まれない |
| TEST-HOTSPOT-02 | 基本ブロック実行経路の処理制約 | Runtime Event Sink、時計、Python APIを利用できる構成である | 適格ブロックを反復実行し、呼出し・時刻読出し・割当てを記録する | Interpreterの観測通知からJIT拡張の単一リングへ固定幅レコードを書くだけで、Event Sink、時計、Python API、動的確保を経由しない |
| TEST-HOTSPOT-03 | Interpreter区間終了時の一括分析 | 同じPCを複数回含む未分析履歴がある | yield、Runtime API境界、trap、関数完了の各終了理由でInterpreter区間を抜ける | Tier 3 JIT拡張が各終了境界で一度だけ履歴順にカードを更新し、必要なcompile requestを登録する |
| TEST-HOTSPOT-04 | JITのみを実行した区間 | 常駐traceとchainが実行可能で履歴が空である | Interpreterを通らないJIT trace/chain区間を実行する | 履歴を追加せず、ホットスポット分析を行わず、既存カードを変更しない |
| TEST-HOTSPOT-05 | 履歴容量超過と近似状態 | 容量Nの履歴へN+K件を記録する | 次の分析境界で履歴と分析結果を確認する | 直近N件を保持し、上書き数Kと履歴欠落状態を示す。容量超過を理由にyieldせず、分析済み範囲を消費する |
| TEST-HOTSPOT-06 | Runtime Event Sinkとの状態分離 | Event Sinkを有効または無効にしたJIT拡張構成がある | 基本ブロック列を実行し、両履歴の内容と容量超過を比較する | JIT履歴はRuntimeイベントリングへ入らず、Event Sinkの状態はJIT拡張の履歴記録・分析に影響しない |
| TEST-HOTSPOT-07 | JIT拡張無効構成の状態除去 | JIT拡張を接続しないRuntimeを用意する | 構成とメモリ量を比較する | Interpreterだけの構成にはJIT履歴領域、履歴書込み、分析処理が含まれず、実行結果・trap・yield・ゲスト状態はJIT有効構成と一致する |
| TEST-HOTSPOT-08 | Runtime寿命とモジュール状態 | Runtimeにモジュールを登録し、履歴とカード状態を作る | Runtimeを破棄し、新しいRuntimeを生成する | 旧Runtimeの履歴・カード状態はRuntime破棄で解放され、モジュール単位のunload APIや状態再利用を要求しない |

判定では履歴レコード列、カード遷移列、compile request、実行結果、上書き数、具象型構成、およびROM/RAM量を直接比較する。Runtime Event Sinkの有効・無効がHotspot履歴へ影響した場合、またはJIT-only区間で記録・分析が発生した場合は不合格とする。

### BasicBlock先行検索・3面キャッシュ 直交表マトリクス
<!-- traceability: {JIT_MultiBuffer_Cache} {JIT_OldestExecuteOnly} {ADR_SafeQueuingOnHotMiss} -->

JITトレース検索時の内部状態と期待される挙動を検証する組み合わせ直交表マトリクス。3面バンク（Active / Warm / Oldest）を独立した列として扱う。

| ケース | カードマーキング状態 | Active | Warm | Oldest | 期待される動作 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | UNEXECUTED (0) | - | - | - | 事前フィルタで即時終了、インタープリタ実行継続 | TEST-JITR-20 |
| 2 | EXECUTED (1) | - | - | - | 事前フィルタで即時終了、インタープリタ実行継続 | TEST-JITR-20 |
| 3 | HOT (2) | - | - | - | インタープリタ継続（コンパイル待ち列に投入済み） | TEST-JITR-20 |
| 4 | COMPILED (3) | **hit** | - | - | **JITコード実行**（昇格なし） | TEST-JITR-21 |
| 5 | COMPILED (3) | miss | **hit** | - | **昇格せず** Warm 上のコードをそのまま実行（無償観測期間） | TEST-JITR-22 |
| 6 | COMPILED (3) | miss | miss | **hit** | **所在を変えず** Oldestのコードを実行 | TEST-JITR-23 |
| 7 | COMPILED (3) | miss | miss | miss | キャッシュ不在を検出してInterpreterへ戻し、eviction／flushでカードを`UNEXECUTED`へ戻した上で再hotness計測する | TEST-JITR-24 |
| 8 | (書き込み時) | **満杯** | - | - | 3面リングローテーション: Oldest を Purge して新 Active に、Active→Warm、Warm→Oldest。同時に前方チェインとLOOPリンクのダングリング掃引を行う | TEST-JITR-25 |

#### 3段検索テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-20 | UNEXECUTED/EXECUTED/HOTは即座にインタープリタ継続 | カード状態がCOMPILED未満 | lookup | 事前フィルタで即終了、キャッシュ検索を行わない | 直交表 ケース1-3 |
| TEST-JITR-21 | Activeヒット | トレースがActiveに存在 | lookup | 昇格なしでネイティブコード実行 | 直交表 ケース4 |
| TEST-JITR-22 | Warmヒット（無償観測、昇格なし） | トレースがWarmに存在 | lookup | Warmのままコピーせず実行される。コードアドレスと常駐バイト数は変化しない | 直交表 ケース5 |
| TEST-JITR-23 | Oldestヒットのその場実行 | トレースがOldestに存在 | lookup前後のアドレス、所属バンク、常駐バイト数、世代を確認する | 同じOldest領域から実行する。コピーとキャッシュ書換えを行わない | 直交表 ケース6, `{JIT_OldestExecuteOnly}` |
| TEST-JITR-24 | 全ミス後のInterpreter復帰と再計測 | Active/Warm/Oldestいずれにも存在しない | lookup | Interpreterへ戻り、eviction／flushでカードを`UNEXECUTED`へ戻してからhotnessを再計測する。キャッシュmissだけでカードを`COMPILED`に固定しない | 直交表 ケース7 |
| TEST-JITR-25 | キャッシュ満杯時の3面ローテーション | Activeのコード領域または記述枠が満杯 | 新規insert | Oldestをpurgeして新Activeにし、Active→Warm、Warm→Oldestへスライド。同時にchain targetのダングリング参照を無効化 | 直交表 ケース8、`` |
| TEST-JITR-27 | 既存記述枠の再利用と満杯時のローテーション | 短いトレースでコード領域より先に記述枠を満杯にする | 既存PCを置換した後、新規PCを挿入する | 既存PCの置換はコード位置と使用バイト数を維持する。新規PCの挿入はローテーションし、既存PCをWarmに保持する。Warm・Oldestへの重複挿入は状態を変更せず拒否する | ``, `` |
| TEST-JITR-28 | QA snapshot領域の容量と再利用 | QA専用ハーネスへ小さいモジュールを登録し、ホットスポット検出の有効・無効構成を用意する | QA snapshot生成、trace追加、候補除外、flushを順に行う | QA snapshotのtrace表容量は常駐枠上限と基本ブロック数の小さい方となる。候補マスクの観測は既存ストレージを借用する。更新でQA snapshot領域を追加確保しない。製品のdispatchへsnapshotを渡す要求とはしない | QA専用fixtureの検査、`` |
| TEST-JITR-29 | 制御命令だけの区間の検索・観測抑止 | 構造命令と通常命令の開始PCが同じカードに入り、制御命令が連続する | native dispatcherで実行し、QAハーネスでJIT拡張の履歴とLoader索引の検索を観測する | 構造制御境界を除く命令bodyのブロックを開始PCで通知し、Runtime API lowering対象の命令もJIT拡張の単一リングへ直接記録する。制御命令だけの区間と記録通知でLoader索引を再検索しない。NativeStepからdispatchへ切り替えても、メモリ処理の再開PCを先頭として扱わない。分岐・呼出しの結果を維持する | ``, ``, ``, `` |
| TEST-JITR-26 | BasicBlock内JITキャッシュ参照の直接ヒット | 常駐traceを指す有効な`jit_cache_reference`を持つBasicBlock | `COMPILED`カードで同じブロック入口を繰り返しdispatchする | JIT索引の二分探索を行わず、8bit参照から該当traceを返す。参照先traceの`head_pc`がBasicBlockのPCと一致し、正しい本体を実行する | `jit_runtime.md`「JITエントリ表と全バンク索引」、`{JIT_MultiBuffer_Cache}` |
| TEST-JITR-83 | current_block_indexとcard-stateによるJIT lookup抑止 | Interpreterのproviderに状態0〜3のカードを設定し、BasicBlock参照を未設定または既設定にする | 各状態のブロックをnative dispatcherから実行し、context index、PC照合、JIT provider callback、cache参照を確認する | dispatcherは`current_block_index`からBasicBlockを直接参照し、`head_pc`とPCの一致を検証する。状態0〜2ではJIT providerを呼ばずJIT参照も変更しない。状態3だけJIT providerへ進み、BasicBlock参照を使う。通常dispatchにLoader Radix・二分検索・block検索cacheはない | `jit_runtime.md`「BasicBlock indexによるJIT検索手順」、pysim ``, `` |
| TEST-JITR-84 | 3面キャッシュ変更後のBasicBlock参照整合 | 有効なBasicBlock参照を持つtraceがActive/Warm/Oldestの移動・削除対象となる | insert、rotation、flushを各実行し、関連BasicBlockの参照とlookupを確認する | 移動した常駐traceの参照は新しいbank/entry位置へ更新され、退役traceの参照は`0xFF`へ戻る。参照から別PCのtraceへ到達しない | `jit_runtime.md`「JITエントリ表と全バンク索引」、`{GOTCHA-JITR-05}` |
| TEST-JITR-87 | successor block indexによるchain lookup | Loader metadataを持つsourceとresident successor traceがある | sourceの`next_block_index`と`next_pc`でsuccessorを受け入れ、PC照合とchain targetを確認する | chain linkと候補受入れはBasicBlock indexから参照を引き、indexとPCを両方照合する。製品経路は`find_cached_trace_by_pc`を呼ばない。| `jit_runtime.md`「BasicBlock indexによるJIT検索手順」、``, `` |
| TEST-JITR-88 | trace headerからLoader blockを直接参照する | moduleのLoader blockからtraceをコンパイルしてcacheへ挿入する | trace headerのblock indexとLoader配列の位置を比較し、JIT実行後の結果も検証する | headerのblock indexはLoader配列内の対象block indexと一致し、JIT実行結果もInterpreter実行結果と一致する | `jit_runtime.md`「trace headerとLoader block metadata」、`test_jit_runtime.py::test_jitr_trace_header_resolves_loader_block_by_index` |
| TEST-JITR-85 | 内部契約違反の発生境界での停止 | 範囲外PC、不正chain target、Warmの重複PC、確定後のメモリ書込みを個別に与える | 各操作を子プロセスで実行する | 不正な値を使う前に責務境界のassertで停止する。既定値、cache miss、書込み拒否の通常結果へ変換しない | `` |
| TEST-JITR-86 | Runtime API表の読み取り専用借用 | 独立した二つのJIT共通コード領域 | 共通領域の表参照とLinux実マッピングを確認する | 同じ読み取り専用表を借用する。表本体はどちらの可変キャッシュ領域にもない | ``、`{GLOBAL_Policy_Memory}` |

| TEST-LOAD-49 | int4_t 便益スコアによる JIT 候補ビットマップ生成 | JIT プラグインへモジュール登録 | ``で8・9・10点の有効ブロックと、`unreachable`を含むtrap-onlyブロックを実登録する | 128B BitView<4> テーブルから命令ごとの便益スコア（int4_t）を積算し、合計9点以上のブロックのhead_pcカードビット（1bit）を1にする。Runtime API経路の`i32.popcnt`は正のスコアを持つ | `{JIT_StaticBenefitScoring}`, `{JIT_CandidateBitmap}` |
| TEST-LOAD-50 | JITCandidateBitmap 非候補ブロックの touch/履歴バイパス | 非候補ブロック（カードビット 0）の実行 | `eng.run(cold_pc, ctx)` | インタープリタ実行は行われるが、HotspotBitmap.touch() および履歴リングへの記録が完全にバイパスされ、カード状態が UNEXECUTED のまま維持される | `{JIT_CandidateBitmap}` |

### トレース・チェイニング
<!-- traceability: {GOTCHA-INTP-06} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-30 | 新規traceの未接続chain target | 新規x64 trace | headerとtrace末尾を確認する | `chain_target_addr == 0`。trace末尾は共通コード領域のchain dispatcherへ進み、targetなしなら終端dispatcherだけが`body_end_ip`を`context.ip`に設定して`true`で呼出し元へ戻る | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) `{JIT_LazyChaining}` |
| TEST-JITR-31 | 常駐後続traceへの共通dispatcher chainと通常dispatch復帰 | 互換な直線後続traceがActiveまたはWarmに常駐し、chain終端traceに制御命令が続く。各traceは定数領域値を参照する | source traceを実行し、JIT entry callback、trace body、bool結果、終端metadata handoff、C++ dispatcherの次処理を観測する | source traceは共通コード領域のchain dispatcherからtarget entry prologue後のcall slotへtail-jumpする。chain先で`r12`とstack frameを積み重ねない。targetの`rel`はtarget定数領域先頭から始まる。終端traceにchain targetがない場合に限り、終端dispatcherはmetadata pointerを`execution_context.rel`へ一時保存し、`body_end_ip`を`execution_context.ip`へ設定して`true`を返す。`false`ではC++ dispatcherがtrap/CPS exitを処理する。`true`ではC++ dispatcherがmetadataからstack・Interpreter `rel`を更新し、終端control opcodeを処理する。次BasicBlock indexはboundary metadataから取得し、PCとの一致を検証する。JIT lookupはchain終端を走査せず、終端descriptorを組み立てない。 | ``, `{JIT_LazyChaining}`, [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITR-38 | リロケーション定数の逐次消費 | 直線traceに複数のリロケーション値を含む | traceを実行し、定数領域と`rel`の開始・終了位置を確認する | 各生成箇所は命令列へ個別アドレスやslot offsetをパッチせず、`rel`がtrace内の出現順に定数を読み進める。終端時のカーソルは消費件数分だけ進み、trace入口ごとに先頭へ戻る | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md), [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md) |
| TEST-JITR-32 | Oldest traceをchain targetにしない | 直線後続traceがOldestにのみ存在 | source traceを登録・実行する | chain targetは0のままで、共通chain dispatcherから呼出し元へ戻る。Oldestは次回rotateで破棄される可能性がある | [`jit_runtime.cxx`](experiments/pysim/native/tier3_plugins/jit/jit_runtime.cxx) `link_resident_successor`, `{JIT_LazyChaining}` |
| TEST-JITR-33 | 制御命令の状態整合と二重実行防止 | `br`、`br_if`、`br_table`または構造命令を持つブロック | 対応するJIT経路とInterpreter単独実行の条件値、stack、frame、PCを比較する | 条件値と制御状態が一致する。JITで処理済みの制御命令をhandlerで再実行しない。未実行命令をInterpreterへ渡した場合はhandlerが一度だけ処理する | `{JIT_RuntimeAPI}`, `GOTCHA-INTP-06` |
| TEST-JITR-34 | targetがOldestへ移った後の既存chain維持 | Warmのtargetにsourceがchain済み | rotateとOldest lookupの前後でheaderを確認し、新sourceを挿入する | 既存target addressは維持する。新sourceはOldestへchainしない。target破棄前に既存リンクを解除する | `` |
| TEST-JITR-35 | target eviction後のchain無効化 | 直線後続のtarget traceがpurgeされる | rotate後にsource headerを確認する | `chain_target_addr`を0にし、次のchain dispatcher実行は呼出し元へ戻る。分岐opcodeからのchainは作成しない | `` |
| TEST-JITR-36 | WarmからOldestへ移動したresident target | target traceがWarmからOldestへ移動する | rotate直後のsource headerを確認する | targetがWarmからOldestへ移っても既存chainを維持する。Oldestだけに存在する後続へ新たにリンクしない。targetがpurgeされた時に既存chainを解除する | `` |
| TEST-JITR-37 | bank purgeとchain unlinkの処理量 | bank内n trace、被chain元k件 | cache rotateを確認する | 破棄bankの全n entryを処理し、被chain元を索引から取り出して更新する。参照実装の上限付き処理量は`O(n + k log n)`である | `jit_runtime.md`「世代交代ローテーション」 |

現行handler経路に対するTEST-JITR-33のIFと内側loopの回帰試験は、対象の制御終端直前のtraceだけを配置する。TEST-JITR-44の終了block回帰試験も同じ構成を使う。対象PCのコンパイル成功、単独常駐、Native実行件数と意味結果を確認する。他のtraceが実行されたことだけで対象の到達を代用しない。hotnessからの自動生成は別の試験で扱う。

### x64実行可能バッファの初期権限とパッチ区間 (ARMv8-M: TBD)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-40 | パッチ中だけ実行可能バッファへ書き込める | x64 `ExecutableBuffer` が初期化済み | `begin_jit_patch()`前後で`write()`する | パッチ開始前の書込みはassertで拒否され、開始後は境界内の書込みが成功する | |
| TEST-JITR-41 | 実行可能状態へ戻した後は書込みを拒否する | x64 `ExecutableBuffer` にコードを書込み済み | `commit_jit_patch()`後に`write()`する | 書込みはassertで拒否され、バッファは実行可能状態になる | 同上 |
| TEST-JITR-42 | x64の実行可能バッファ初期権限 | `begin_jit_patch()`→書込み→`commit_jit_patch()` | OS が公開する実権限と生成コードの実行結果を確認する | 実権限は初期化からRWXであり、再パッチと再確定でも変わらない。生成コードは指定した値を返す。ARMv8-Mの命令や保護機構はこの期待値に含めない | |
| TEST-JITR-43 | x64のパッチ区間とOS権限の分離 | x64 `ExecutableBuffer` が初期化済み | OSの実権限とパッチ区間外の書込みAPIを確認する | OS実権限はRWXであり、パッチ区間外のAPI書込みは拒否される | |

### 関数戻り値とInterpreter境界
<!-- traceability: {TraceBoundaryInvariant} {GOTCHA-INTP-06} {GOTCHA-JITR-02} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-44 | JIT終了ブロックからInterpreter return処理へ委譲 | JITトレースが`return`直前で終了する関数 | トレース後のInterpreter PCと共有スタックを確認する | JIT traceは`return`命令を生成しない。return処理はInterpreter仕様に従う | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)、TEST-INTP-16, TEST-INTP-17 |
| TEST-JITR-45 | terminal call traceからTier 2 CPS handlerへの接続 | callerが定義済みcalleeを反復して呼び、callerのcall traceをJIT常駐させる | QAの`RuntimeStatsEngine.call()`でJIT拡張を接続した`NativeInterpreter`を実行し、Interpreter単独結果、stack、frame、`ip`、driver復帰数を比較する | call operationはtraceの最後に置く。x64 stencilは共通コード領域の借用function pointer tableのhandler slotからTier 2 CPS handlerへtail-callする。traceに関数の絶対アドレスを埋め込まない。呼出し意味論は[`interpreter.md`](docs/components/tier2_runtime/interpreter.md)を正本とする | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md)、[`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| TEST-JITR-46 | JIT有効時のWASMスカラー結果型維持 | i32/i64/f32/f64のトップレベル関数 | QAの`RuntimeStatsEngine.call()`でJIT拡張を接続した`NativeInterpreter`を各関数に対して反復実行する | JITコンパイル可能な結果はJIT経由、未対応型はInterpreterへ委譲し、全結果の値を保持する。f32/f64を整数へ変換しない | `interpreter.md` 関数復帰, pysim `` |
| TEST-JITR-47 | terminal direct callからCPS handlerへの直接tail-call | ゲスト関数がWASM importを呼び、戻り値をmemoryと後続命令で使う | caller traceとsuccessor traceを常駐させ、Interpreter単独とJIT有効の結果、memory、stack、復帰`ip`、trace exit数を比較する | call opcodeはtraceの最後のoperationである。共通コード領域のborrowed table pointerとslotを介してTier 2 CPS handlerへtail-callする。traceに関数の絶対アドレスを埋め込まない。最終状態はInterpreter単独実行と一致する | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md)、[`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| TEST-JITR-48 | Runtime APIのbool結果によるtrap中断と正常継続 | `i32.div_s`を含む基本ブロックと、`i32.clz`を含む基本ブロックがある | `JITRuntimeManager`を接続した`NativeInterpreter`で各基本ブロックをコンパイルして実行する | 除算Runtime APIはtrap条件を`execution_context.trap_code`へ記録して`false`を返す。stencilはabort slotへ分岐し、後続storeを実行しない。trace entryは`execution_context.trap_code`の非0を読んで`false`を返す（戻り値の内部表現は使わない）。正常な`i32.clz` APIは`true`を返し、trace後続のJIT命令へ継続する | `jit_abi.md`、`interpreter.md` 命令実行中のTrap, pysim ``, `` |
| TEST-JITR-49 | JIT有効時のif/else両経路とループ脱出 | ループ内にthen/elseと`br_if`を持つ関数 | 複数入力をInterpreter単独構成とJIT拡張接続構成で実行し比較する | then/else両方を通る入力を含め、全結果が一致し、JITトレースが実行される | `{JIT_RuntimeAPI}`, pysim `` |
| TEST-JITR-50 | `br_table`の全分岐先と状態整合 | 既定先を含む3分岐先を持つ関数 | 各selectorでInterpreter単独構成と対応するJIT構成を実行する | case 0/1/defaultの結果、stack、frame、PCが一致する。selectorと分岐命令を二重に処理しない | `{JIT_RuntimeAPI}`。既存``はhandler経路を確認する |
| TEST-JITR-51 | 異種型混在時のRuntime API実行と後続JIT | 共有operand stack上にi32/f32/i64/f64値を積み、広幅値を`drop`する関数 | JIT compile結果、trace内Runtime API実行、後続JIT命令と最終結果を検証する | i64/f64のDROPが2 raw wordを除去する。必要な操作はtrace内Runtime API tableから実行し、通常継続後のi32演算もJIT trace内で完了する | `{CPS_3Args}`, pysim `` |
| TEST-JITR-52 | 混在型関数呼出しを含むterminal call trace | i32/f32/i64/f64を返すcalleeを順に呼び、i32 calleeをJIT hotにするcaller | JIT拡張を接続した`NativeInterpreter`でcallerを反復実行し、resident trace、結果、stackを確認する | call operationはtraceの最後に置く。終端stencilはTier 2 CPS handlerへtail-callする。関数結果と呼出し状態の意味論はInterpreter仕様に従う | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md)、[`interpreter.md`](docs/components/tier2_runtime/interpreter.md)、`{JIT_RuntimeAPI}` |
| TEST-JITR-53 | `block`命令後の静的分岐metadata整合 | `block`を含むコンパイル対象と後続loopを持つ関数 | 関数を繰り返し呼び、wasmtime、Tier 2、Tier 3の結果と遷移先を比較する | 3実行系の結果が一致する。Loaderの分岐metadataを使い、実行時制御フレームの二重更新やリークがない | TraceBoundaryInvariant, `GOTCHA-INTP-06`, `` |
| TEST-JITR-54 | ブロック先頭でない再開位置がコンパイルキューへ入らない | 短い`br_if`ブロックの不成立経路が`end`へ落ち、その`end`が後続の長いブロックと同じ4バイトカードに入る。コード配置を4通りにずらす | 各配置で3実行系の結果を比較する | 全配置で結果が一致し、判定点でない再開位置は履歴に記録されず、ブロックが存在しないPCのコンパイル要求で停止しない | TraceBoundaryInvariant |
| TEST-JITR-55 | 再開位置で静的branch edgeを使う | ネストした`br`の脱出と、ループを抜けた直後の`br 0`（ブロック先頭でも構造命令でもない位置）を持つ関数 | 3実行系の結果を比較する | 結果が一致し、注釈済みbranch edgeが正しいPCとstackを復元する。実行時制御フレームを保持しない。判定点でない`br 0`の再開位置は履歴に記録しない | `GOTCHA-INTP-06`, `` |
| TEST-JITR-56 | 混在幅ローカルを持つフレームでのJIT実行 | i32専用のホットループと、同一関数内のf64ローカルを持つ関数 | 3実行系の結果を比較する | Loaderのオフセット・サイズ表に従ってローカルが連続配置され、f64ローカルの存在でi32トレース実行が停止しない | TraceBoundaryInvariant |
| TEST-JITR-57 | `if`で終わるホットブロック | 条件式で終わるブロックを持つホットループ | 3実行系の結果を比較する | `if`をインタープリタが実行し、条件値がオペランドスタックに残る。結果が一致する | TraceBoundaryInvariant |
| TEST-JITR-58 | 生成した構造化制御フローの3実行系差分 | シード固定で生成した60本のblock/loop/if/br入れ子 | 3実行系の結果を比較し、JIT実行回数とC++制御ハンドラ回数を確認する | 全プログラムの結果が一致し、JITトレースとC++ Interpreterの分岐ハンドラが実際に使われる | TraceBoundaryInvariant, `{JIT_RuntimeAPI}` |
| TEST-JITR-59 | assembly stencilから生成したカーネルスイートの3実行系差分 | `suite.wasm`の16カーネル（呼び出し、`br_table`、f64、i64、サブワードメモリを含む） | 各カーネルの結果を3実行系で比較する | 全結果が一致し、JIT traceはassembly stencilと必要なRuntime APIを使って実行される | `{JIT_AssemblyStencilCodegen}`, `{ThreadedInterpreter}` |
| TEST-JITR-60 | 広幅ローカルを含む異なるレイアウトの混在実行 | i32だけの関数とi64ローカルを含む関数が、同一のホットループから交互に呼ばれる | 3実行系の結果を比較し、両関数のトレースが存在することを確認する | 結果が一致し、2つの関数のトレースが同時に実行される。各関数はロード時のオフセット・サイズ表に従う | `{ContextPointerRegister}`, TraceBoundaryInvariant |
| TEST-JITR-61 | terminal call trace実行時のオペランドスタックoverflow | JIT適格callerがterminal `call`を持つ。callee `pad`は62個のi64値（124ワード）を保留した状態で18段のi32関数`work`を呼び、容量128ワードを超える | Tier 2とJIT有効のエンジンで実行し、caller traceの実行とtrap codeを比較する | callerのterminal call traceが実行される。共有stackの容量超過はInterpreterの`OPERAND_STACK_CAPACITY`として検出され、JIT callerとInterpreter単独のtrap codeは一致し、範囲外stack書込みを行わない。callとoverflowの意味論は[`interpreter.md`](docs/components/tier2_runtime/interpreter.md)およびTEST-INTP-80に従う | TraceBoundaryInvariant、[`interpreter.md`](docs/components/tier2_runtime/interpreter.md)、TEST-INTP-80 |
| TEST-JITR-62 | 参照・ローテーション後のチェイン整合 | 1面のentry上限を超える本数の連鎖トレースと、3面キャッシュ（本数は entry 上限 + 2）。乱数で、挿入・参照・ローテーションを繰り返す（40シード、各90手） | 各手の後にリンクを検査し、参照したトレースをネイティブ実行する | すべてのチェインポインタが、常駐トレースの有効な入口を指す。論理的な後続PCと機械語ヘッダのターゲットが一致する。同一トレースが複数の面に常駐しない。各リンク先は生成入力のindexとstrideから導いた直後のtraceである。Native実行の副作用と最終PCが生成入力から導いた期待値に一致する。trace entryは`bool`を返す。通常完了時は未接続chain targetに到達した終端dispatcherが`ip`を設定して復帰する。Interpreter dispatcherがmetadataから実行状態を確定する | `{JIT_MultiBuffer_Cache}`, `GOTCHA-JITR-02` |
| TEST-JITR-63 | LOOP後方辺回数とyield境界 | Interpreterに有限のyieldしきい値を設定し、自己後方辺へ戻る`br_if`を持つJIT候補を用意する | Interpreter CPS経路で分岐条件、共有カウンタ、trace実行数、yield statusを観測する | 条件不成立では取得辺数を増やさない。成立した辺を判定点で一度だけ数えて履歴に記録し、traceを終了して次の反復をInterpreter CPS経由で処理する。しきい値到達時はC++ dispatcherからyieldする。RuntimeEngineはカウンタとしきい値を変更しない | `{JIT_BackedgeYield}`、pysim `` |
| TEST-JITR-64 | Interpreter warm-upからJIT実行へ移行 | 未コンパイルの数値ループを実行し、idle時に対象トレースをコンパイルする | QAの`RuntimeStatsEngine`でJIT拡張接続済みInterpreterを駆動し、返値・実行統計・キャッシュ常駐を確認する | 結果は15で、Interpreter warm-up後にLOOP traceが常駐する。各反復の`br_if`はInterpreter CPS handlerで処理し、後方辺のしきい値到達時はC++ dispatcherからRuntimeEngineへyieldを返す | `jit_runtime.md`「トレース実行時の分岐解決とインタープリタ復帰」 |
| TEST-JITR-65 | QA snapshotの常駐状態への追従 | 未コンパイル候補blockと常駐traceを持つQA専用JITハーネス | trace挿入前後のQA snapshotとnative実行を確認する | QA snapshotは常駐状態の変更を反映する。製品実行はJIT拡張の本体実行入口を使い、snapshotを供給しない。候補マスクのQA観測は既存ストレージを借用する | QA専用fixtureの検査、pysim `` |
| TEST-JITR-66 | 診断カウンタを無効にしたRuntimeの意味保存 | 診断カウンタなしで後方分岐yieldを行うC++ Interpreter構成 | 関数を実行し、結果・yieldと統計値を確認する | 結果とyield境界は変わらず、製品Runtimeは検査用APIと累積カウンタを持たない。QA専用ハーネスでJIT body、handler、trace遷移を計測する | `jit_runtime.md`「トレース実行時の分岐解決とインタープリタ復帰」 |
| TEST-JITR-67 | frame深度をまたぐLOOP後方分岐のCPS処理 | 内側`block`から外側`loop`へ戻る合法な`br_if` sourceと、常駐target traceを用意する | 分岐成立・不成立時の条件値、stack、frame、PC、取得辺数とyieldを確認する | source/targetのframe深度に関わらず分岐opcodeはtraceに含めず、Interpreter CPS handlerが遷移を処理する。Interpreter単独実行と同じ結果になり、取得辺を一度だけ数える。判定点で取得辺を履歴に記録する。しきい値到達時にyieldする | `{JIT_BackedgeYield}`、pysim `` |
| TEST-JITR-68 | JIT有効時の関数呼出しとhotspot観測 | 定義済み関数の呼出しを含むJIT適格関数とhotspot収集 | 同じ入力を実行し、結果、JIT trace実行件数、実行境界復帰数を確認する | JIT固有のtrace観測はJIT Runtime契約に従う。関数呼出しとyieldの意味論はInterpreter仕様を正本とする | `{RuntimeHotspotProfiler}`、`{LowLatencyJIT}`、[`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| TEST-JITR-69 | 静的前方`BR`後のnesting metadata整合 | 40回繰り返すLOOP内で、内側BLOCKから値を残さず前方`BR`で抜ける関数 | 3実行系の結果、source/target headerの静的frame depth、native chainがないことを確認する | 結果が一致する。分岐traceからtargetへnative chainせず、Interpreter CPS handlerがtarget PCとblock indexを決める。後方辺の計数とyield契約を守る。実行時制御スタックは持たない | `{JIT_BackedgeYield}`、`{PositionIndependentCode}`、pysim `` |
| TEST-JITR-70 | 空のyield境界でのJIT制御処理省略 | コンパイル待ち作業と候補履歴がなく、後方分岐yieldを繰り返す関数 | 実行結果、yield回数、コンパイル処理・履歴引渡し・Python側trace検索の呼出しを確認する | 関数結果とyield条件を保つ。空キューのコンパイル処理と空履歴の引渡しを行わず、通常経路でtraceをPython側で検索しない | `{LowLatencyJIT}`、`{RuntimeHotspotProfiler}`、pysim `` |
| TEST-JITR-72 | LIFOキューから元WASMコードをコンパイルする | 2つ以上の適格blockが`HOT`になり、元のWASMコードがLoader領域にある | Tier 3 JIT拡張のidle hookで逆順に処理する。Python opcodeの再構築経路を禁止する | C++ compilerは元のWASMコードを直接読み、trace本体を生成する。成功したtraceだけがcacheへ入る | `jit_runtime.md`「オンデマンドコンパイルキュー」、pysim `` |
| TEST-JITR-73 | trace終端の`if`／`br`／`br_if`のInterpreter CPS処理 | 条件成立・不成立の両方を通る`br_if`とresident source traceを用意する | Interpreter dispatcherから実行し、結果、source traceのchain target、CPS handler回数とtrace exitを確認する | 分岐opcodeをRuntime APIで実行せず、traceを完了してからInterpreter CPS handlerで一度だけ処理する。source traceを未接続のまま保持し、結果をInterpreterと一致させる | `jit_runtime.md`「トレース実行時の分岐解決とインタープリタ復帰」、pysim `` |
| TEST-JITR-74 | terminal `call_indirect` stencilからCPS handlerへの接続 | 一致する型、不一致の型、table範囲外を含むtyped table fixture | Interpreter単独とJIT有効を比較し、結果、trap、memory、副作用、stack、復帰`ip`、trace metadataを確認する | `call_indirect`はtraceの最後のoperationであり、専用slotからTier 2 CPS handlerへtail-callする。traceに関数の絶対アドレスを埋め込まない。関数選択とtrapの意味論はInterpreter仕様に従う | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md)、[`interpreter.md`](docs/components/tier2_runtime/interpreter.md)、TEST-INTP-78 |
| TEST-JITR-75 | 無効な静的frame depthのtrace実行辞退 | 常駐前traceのnative frame depth metadataが範囲外 | metadata providerが`0xFFFF`を返すtraceをcacheへ挿入して関数を実行する | headerのframe depthは`0xFF`へ制限され、Interpreter実行の結果は`[5]`となる。trace実行回数とJIT invocation数はともに0となる | `test_jit_runtime.py::test_jitr_trace_execution_count_excludes_invalid_static_depth` |
| TEST-JITR-90 | recursive call traceのterminal handler接続 | 再帰関数のcall traceを常駐させる | Interpreter単独とJIT有効の結果、active frame数、stack offset、復帰`ip`、chain状態を比較する | terminal call stencilはTier 2 CPS handlerへ直接tail-callする。結果と観測状態はInterpreter単独実行と一致する。呼出し意味論は[`interpreter.md`](docs/components/tier2_runtime/interpreter.md)に従う | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md)、[`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| TEST-JITR-91 | terminal direct call traceとtrap結果の一致 | direct call先が`unreachable`を実行し、caller suffixにmemory書込みがある | Interpreter単独とJIT有効を比較し、trap、memory、frame、stack、trace chain状態を確認する | terminal call stencilはTier 2 CPS handlerへtail-callする。JIT有効時のtrapと観測状態はInterpreter単独実行と一致する。trap伝播はInterpreter仕様に従う | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md)、[`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| TEST-JITR-92 | vMMIO後のterminal callでstack prefixを保持しcaller suffixを一度実行 | 関数index 0に未使用import、index 1にcalleeを置く。callerはvMMIO load前にstore addressをstackへ積み、vMMIO load後に注釈付き算術命令を実行してからcallする。call後のsuffixは結果をstoreし、mutable global counterを増やしてからmemoryをloadする | terminal call traceを常駐させ、Interpreter単独とJIT有効の結果、全linear memory、global counter、完了時のIP・frame/local stack・operand stack depthを比較する。terminal call後にsource traceのchainがないこととJIT trace実行を確認する | 両実行の結果、memory、global counter、完了時状態が一致する。結果は43、global counterは1、memory[0]は43であり、call直前のstore addressを保持してstoreする。global counterが1なのでcaller suffixは一度だけ実行された。完了時にframe、local stack、operand stackは残らずIPも一致する。terminal call stencilは借用function pointer tableからTier 2 CPS handlerへtail-callし、annotation cursorはJIT ABIに従う | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md)、[`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| TEST-JITR-93 | ジャンプの判定点でのJIT入口 | 常駐traceを持つブロックへ、`if`、`else`、`br`、`br_if`、`br_table`のいずれかで飛ぶ関数。飛び先のカードは`COMPILED` | 飛び先への遷移を実行する | 判定はジャンプのハンドラ内で行い、飛び先のカードが`COMPILED`のときだけCPSを終えてdispatcherからtraceを実行する。カードが`UNEXECUTED`または`EXECUTED`のときはCPSを続ける | `{Interpreter_LazyJITSwitch}`、`jit_runtime.md`「分岐境界の整合」 |
| TEST-JITR-94 | 呼出しの入口でのJIT判定 | 直線の関数（分岐もループもない）を、呼出し先の入口のカードが`COMPILED`の状態で呼ぶ | 呼出しを実行する | 呼出し先の入口の判定はcallハンドラ内で行い、`COMPILED`のときだけCPSを終えてtraceを実行する。`call`自体はCPSを出さない | `interpreter.md`「関数呼出し」、`jit_runtime.md`「呼出」 |
| TEST-JITR-95 | sorted indexの高位PCビットの畳み込み | loaderのblock索引（`fold_mix32`のキーで並べたradix storage）に2つのPCを入れる | 各PCのキーで索引を引く。存在しないPCのキーも引く | 各PCは自身の索引を返し、存在しないキーはNoneを返す。試験は合成traceを使わない | `jit_runtime.md`「JITエントリ表と全バンク索引」、`test_jit_runtime.py::test_jitr_26_sorted_index_folds_high_pc_bits` |
| TEST-JITR-96 | nopは操作を追加しない | `nop`だけからなるblockと、`nop`を7個または26個含むblock。`i32.const 7`、`local.set 0`、`nop`を8個含むblock | 各blockをtraceへコンパイルし、混在blockのtraceを実行する | `nop`だけのblockは非空のtraceを返し、そのサイズは`(block nop)`のtraceと等しい。混在blockのtraceを実行するとlocal 0は7になる | `jit_compiler.md`「命令経路」の`nop`行、`test_jit_runtime.py::test_jitr_96_nop_adds_no_operation_so_nop_blocks_compile` |
| TEST-JITR-97 | i32.rotl・i32.rotrのRuntime API経路 | 循環シフトを含むループを60回回す（`ROTATION_WAT`） | Tier 2、Tier 3、wasmtimeの結果を比較し、循環シフトを含むblockに常駐traceがあるか確認する | 3者の結果が一致する。循環シフトを含むblockのうち少なくとも1つに常駐traceがある | `jit_compiler.md`「命令経路」、`test_jit_differential.py::test_jitr_97_i32_rotations_run_through_the_runtime_api_on_the_jit` |
| TEST-JITR-98 | f64メモリ操作の2 word幅 | f64のload・storeを含むループを20回回す（`F64_MEMORY_WAT`） | Tier 2、Tier 3、wasmtimeの結果を比較し、f64のload・storeを含むblockに常駐traceがあるか確認する | 3者の結果が一致する（30）。f64のload・storeを含むblockのうち少なくとも1つに常駐traceがある | `jit_compiler.md`「命令経路」、`test_jit_differential.py::test_jitr_98_f64_memory_operations_compile_with_two_word_values` |
| TEST-JITR-99 | スイートのblockがコンパイル失敗の要因に該当しない | `benchmarks/profile/guest/suite.wasm`の全block。命令数が1以上64以下で、推定本体サイズが1000 byte以下のblock（推定は直接stencil30 byte、Runtime API操作240 byte、基底80 byteの測定値による） | 各blockをtraceへコンパイルする | 対象blockは全てtraceを返す。対象は300個を超える。命令数64超と命令数0は対象外とする | `jit_compiler.md`「コンパイル失敗の要因」、`test_jit_differential.py::test_jitr_99_suite_blocks_within_the_body_budget_compile` |
| TEST-JITR-100 | 処理数0（aging を行わない構成）のエイジング | 処理数0の構成でQA probeライブラリを作り、非ゼロバイトを持つdirty表がある | ローテーションを3回実行する | 各ローテーションは1ステップと数える。処理する非ゼロバイトと走査バイトは0である。カーソルは動かず、カード状態は`EXECUTED`のまま残る | `JIT_CardAgingSweep` |
| TEST-JITR-101 | 呼出し後継続blockのentry stack words | 呼出し結果の前に演算数が残るloop本体があり、継続blockがその演算数を消費する | 結果を比較し、継続blockが常駐traceになることを確認する | 結果は`n(n+1)/2`と一致する。継続blockはentry stack words 2で、JITにコンパイルされる。entry stack wordsを0と読むと継続blockはコンパイルされない | `runtime_loader.md`「`module.blocks`と`block_storage`」の`entry_stack_words` |
| TEST-JITR-102 | 入口語数を超えるpopは契約違反 | 呼出し後継続blockの入口語数を0と読ませたloader metadataがある | 別プロセスでloadし、トレースを実行する | プロセスはassertで停止する。入口語数の不足を0として黙って失敗を返さない | `jit_compiler.md`「コンパイル失敗の要因」 |
| TEST-JITR-104 | loaderが受理するopcodeのruntime API slot | loaderが受理する単一byte opcodeと0xFCの subopcode 0–7、10、11 | 公開されたslot表で、stencilと終端の経路を除く各opcodeの slot を引く | 全opcodeにslotがある。欠けると trace compiler は assert で停止する | `jit_compiler.md`「コンパイル失敗の要因」 |
| TEST-JITR-105 | operand値の上限に達するblockのコンパイル | 64個のi64定数を積み、呼出しで消費するblockがあり、その語数は値スタック容量に等しい | blockをコンパイルする | traceが得られる。深さの検査はこの境界で拒否しない | `jit_compiler.md`「1トレースは」の共有stack深さ |
| TEST-JITR-106 | 到達不能blockは同じcardが HOT でも待ち列に入らない | `unreachable` 相当の `br` の後ろに短い到達不能blockがあり、実行されるblockと同じcardを共有する | 実行を3回行い、idle hookを呼ぶ | 実行されたblockだけがコンパイルされ、到達不能blockのtraceは存在しない | `jit_compiler.md`「コンパイラが受け取るのは実行された block だけである」 |
| TEST-JITR-107 | 受理されるopcodeの1命令blockのコンパイルと実行 | Loaderの受理集合（dispatch表）から、制御・構造・call命令を除いたopcodeを1つずつ、その命令を含む関数にする | 実行を3回行い、idle hookでコンパイルし、同じ引数で再実行する | blockがコンパイル済みで、trace の実行結果が interpreter と一致する | `jit_compiler.md`「コンパイラが受け取るのは実行された block だけである」 |
| TEST-JITR-108 | 採否記録の初期値と根の入口の計数 | 設置後の block を、根の lookup で4回実行する | 計数が、4回の lookup の分だけ増える。ストライクは履歴なし（5）のまま | 計数は根の入口と同じ数え方で、1 lookup に 1 を加える | `jit_runtime.md`「採否記録」 |
| TEST-JITR-109 | 破棄時のストライク更新 | 寿命が K 未満の trace を破棄し、次に K 以上の寿命の trace を破棄する | 1回目はストライク 1 で寿命を保つ。2回目はストライク 0 に戻る | 破棄時の寿命でストライクを更新し、寿命は次の設置まで残る | `jit_runtime.md`「ストライク」 |
| TEST-JITR-110 | 8 回転の区切りの破棄数 | 区切りの先頭から、常駐 trace 1 件を含めて 8 回転する | 8 回転目に、直前の区切りの破棄数が 1 になり、現在の区切りは 0 に戻る | 段階は直前に終わった区切りの破棄数で決まる | `jit_runtime.md`「段階」 |
| TEST-JITR-111 | 段階の上限に達した block の却下 | 4回の短命な破棄で、ストライクを段階の上限 4 に達させる | 次の採否で却下され、コンパイルされず、トラッカブルマスクが解除される | 却下は採否の判定で行う（ルール（iii））。ストライクは段階の却下の値 6 になる | `jit_runtime.md`「採否」 |
| TEST-JITR-112 | 点数が閾値未満の候補の却下 | 制御命令と nop だけの block が、全 card 観測の下で HOT になる。対照の block は、点数が閾値以上の命令を持つ | 実行を5回行い、idle hookを呼ぶ | 閾値未満の block は、コンパイルされず、トラッカブルマスクが解除される。対照は、コンパイルされる | `jit_runtime.md`「採否」（ルール（i）） |
| TEST-JITR-113 | 段階が緩くなったときの却下の回復 | 20 個の破棄で段階を厳しくし、対象の block を段階の上限で却下させ、空の区切りで段階を緩める | 対象の候補マスクが戻り、ストライクが 0 になり、次の採否でコンパイルされる | 回復は段階が緩くなった区切りだけで起きる（ルール「回復」） | `jit_runtime.md`「回復」 |
| TEST-JITR-114 | 点数不足の却下は段階の緩和で戻らない | 113 の段階の変化の中で、点数が閾値未満の block を観測させる | 点数による却下の block は、ストライク 5 のままで、トラッカブルマスクが戻らない | 回復はストライク 6 の block だけに行う（ルール「回復」） | `jit_runtime.md`「回復」 |

### 実装上の注意点に対応する検証
<!-- traceability: {GOTCHA-INTP-06} {GOTCHA-JITR-01} {GOTCHA-JITR-02} {GOTCHA-JITR-03} {GOTCHA-JITR-05} {GOTCHA-JITR-06} {GOTCHA-JITR-07} {GOTCHA-JITR-08} {GOTCHA-JITR-09} -->

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-JITR-01 | `COMPILED`カードによる重複要求の除外 | トレースがキャッシュに常駐し、カード状態が`COMPILED`のPCが待ち列にもある | `idle_hook` のキュー処理を実行 | C++ Runtimeは再コンパイルせず要求を破棄し、カード状態と常駐トレースを保つ。 | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md)「コンパイル待ち列」 |
| GOTCHA-JITR-02 | Oldestのその場実行と新規chain抑止 | 試験1: BはOldest所属で、Aから既存chainされている（連結元は1つ）。試験2: BだけがOldestにあり、新しい連結元Aを挿入する（連結元は1つ） | 試験1: Bをlookupし、Bの番地を変えずに実行する。試験2: Aを挿入する | 試験1: Bの所在とAの既存chainは変わらず、Bの局所の副作用が出る。試験2: 新しい連結元Aの連結先がNoneであり、Oldestへの新規chainを作らない | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) 「Oldestの実行と既存chainの維持」 |
| GOTCHA-JITR-03 | 3面ローテーション時の局所アンリンク | Oldest内のtraceが他バンクのtraceからchainされている | Active満杯によるローテーションを実行する | Oldestを新Activeへ再利用する前に、逆引き表から候補sourceを照合して退役targetへのリンクを解除する | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) |
| GOTCHA-JITR-05 | 3面ローテーション・フラッシュ時のBasicBlock参照更新 | rotate() または flush_all() 実行時 | 有効な`jit_cache_reference`を持つtraceを移動または退役させ、直後に旧PCをlookup | 移動したtraceは新しいbank/entry位置からlookupできる。退役したtraceのBasicBlock参照は`0xFF`へ戻り、別PCのtraceへ誤接続しない。 | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) |
| GOTCHA-JITR-06 | JIT境界の制御状態と二重実行防止 | if内のloop脱出条件ブロックがコンパイル済み | JIT実行後の条件値、stack、frame、再開PCと結果を観測する | 制御命令を実行した経路が条件値とframeを一度だけ更新する。Interpreter復帰後に実行済み命令を再実行しない。結果はInterpreter単独実行と一致する | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md), `{JIT_RuntimeAPI}`, `GOTCHA-INTP-06` |
| GOTCHA-JITR-07 | 短小ブロック判定の符号（後方分岐ブロックの誤除外） | ループ本体の末尾が、自分自身の先頭より前へ戻る後方分岐になっている | 短小ブロックの足切り判定を経て実行を反復する | 後方分岐ブロックも足切り判定を通過し、命令数が十分ならコンパイル対象になる。 | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) |
| GOTCHA-JITR-08 | return直前のJIT終了とInterpreter復帰 | `return`を含むWASM関数 | トレース実行後の結果をInterpreter単独実行と比較する | JIT traceは`return`命令を生成せず、return処理はInterpreter仕様に従う | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md)、[`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| GOTCHA-JITR-09 | エイジングスイープと常駐状態・待ち列の分離 | カードが`COMPILED`（キャッシュに常駐）、別のカードが`HOT`（コンパイル待ち列に登録済み）、別のカードが`EXECUTED` | 全ブロックを覆うまでエイジングスイープを実行し、常駐トレースをlookup | `COMPILED`のカードは`COMPILED`のままで、lookupは常駐トレースを返す。`HOT`のカードは`HOT`のままで、待ち列の要求と対応する。`EXECUTED`のカードだけが`UNEXECUTED`になる。 | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) 「エイジングスイープ」, `TEST-JITR-16` |

### QA専用計測構成のトレース実行回数

<!-- traceability: {JIT_MultiBuffer_Cache} {JIT_OldestExecuteOnly} {Challenge_JITCacheEfficiency} -->

この節はQA専用の計測型を合成した構成だけに適用する。計測契約の正本は[`jit_runtime_bench_spec.md`](docs/components/tier3_plugins/benchmarks/jit_runtime_bench_spec.md)とする。製品構成は`void`を選び、計測状態、時計読取り、実行後のchain走査、および統計リセットAPIを持たない。

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-76 | 反復実行、飽和、Oldest実行、測定区間のリセット | 実行回数が既知の常駐loop traceがある | QA計測構成で通常統計とホットスポット検出の有効・無効を組み合わせて反復実行し、Oldest実行、QA snapshot再生成、32ビット上限、QA resetを確認する | trace bodyを実行するたび、実行カウンタ領域がある構成では1回数える。カウンタ領域がない構成ではNULL pointerを維持し、QA viewも0として読む。lookupでは増えない。世代交代とsnapshot更新で値・所在を保ち、有効なカウンタは最大値で飽和しreset後は0から数える | `` |
| TEST-JITR-77 | 直接chainの後続bodyの実行回数 | 2 traceの直線chainがある | QA計測構成で通常統計の有効・無効を組み合わせ、Runtimeからchainを実行する | 先頭と後続の回数がともに1増え、ゲストの結果が一致する | `` |
| TEST-JITR-78 | QAの退役記録と新traceの計数寿命 | 実行済みと未実行のtrace、および収集先がある | QAハーネスでlookup、rotate、置換、flush前後の常駐状態を比較する | lookupでは記録せず値を保持する。脱落したtraceのPCと操作前に保存した回数を一度通知する。未実行の0も通知する。同じPCの新traceは0から始まる | `` |
| TEST-JITR-79 | 無効な静的metadataで実行されないtraceの除外 | 常駐traceの`frame_depth` metadataが無効値である | Runtimeから関数を実行する | Interpreterで正しい結果を得て、metadata検査によりtrace実行を辞退し、traceの回数は0のままとなる | `` |


形式モデルは[`jit_trace_execution_model.py`](docs/components/tier3_plugins/formal/jit_trace_execution_model.py)を参照する。dispatcher外の低水準直接呼出しは計数対象外である。製品の計測状態の有無をこのモデルから要求しない。

### コンパイル候補の待ち列処理
<!-- traceability: {ADR_JitCompileScheduling} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-80 | idle hookで候補待ち列の全件を処理する | 固定キューに複数のコンパイル可能な候補がある | 協調境界でidle hookを呼ぶ | 待ち列の全候補をLIFO順にコンパイルし、待ち列を空にする | `jit_runtime.md`「コンパイル時期と待ち列処理」 |
| TEST-JITR-81 | キュー満杯時に待ち列を同期処理する | 固定キューが容量に達し、その後さらに候補が1件到着する | 満杯契機の候補処理を実行し、続けて idle hook を実行する | 満杯時の待ち列の候補（容量分）は失敗・スキップを含めて同期処理され、待ち列を空にする。以後の候補は待ち列に残り、次の idle hook で処理する | `jit_runtime.md`「コンパイル時期と待ち列処理」 |

## 3. テスト検証実績と網羅状況


実行コマンドは次のとおりである。



- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

`br_table`、structured control、call/returnなどの一般制御経路は各Interpreter復帰契約に従う。後方・自己chainの循環集計、しきい値yieldと退役時unlinkはTEST-JITR-63, TEST-JITR-67および[`jit_loop_chain_model.py`](docs/components/tier3_plugins/formal/jit_loop_chain_model.py)で検証する。TEST-JITR-44, TEST-JITR-73などのInterpreter復帰試験は、未実行命令をhandlerへ渡す経路の証拠に限定する。

- TEST-JITR-44は対象traceの実行と最終結果を確認する。独立した終了blockの試験ではNative trace実行1回とC++ return handler呼出し1回を診断値で確認する。最初のRuntime境界で論理PCの`-1`、Native contextの`0xffffffff`、共有stackの戻り値60、未完了のcall frameを直接観測する。次のRuntime境界でframe除去と関数完了を確認し、traceとhandlerの件数が増えないことを検査する。

- ARMv8-Mでは共有スタックを値の正本とする。ABI引数の物理配置と命令内スクラッチレジスタの割当はTBD。
- [`jit_cache_model.py`](docs/components/tier3_plugins/formal/jit_cache_model.py)による抽象的なW^X不変条件、3面キャッシュ代謝、2-bit FSMの形式検証そのもの。
- ARMv8-Mの物理メモリ保護機構、JIT配置、命令同期、実機レイテンシ（すべてTBD）。
