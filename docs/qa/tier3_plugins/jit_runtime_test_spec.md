# JITランタイム (キャッシュ・ホットスポット検出) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md)
関連正本: [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md)（{JIT_LazyChaining}、{JIT_CopyAndPatch}はjit_runtimeと共同責務）
本仕様は、WASM PCからネイティブコードを検索する3段階の経路、ホットスポット昇格、3面コードキャッシュ、chain dispatcher、および実行可能メモリの権限遷移を検証する。検索経路はカードマーキング、Folding XOR高速キャッシュ、ソート済みバンク内の二分探索である。x64参照構成を対象とし、ARMv8-Mの物理配置、ABI、保護方式は別途定義する。

## 2. テストケース一覧

### 2-bitカードマーキング (jit_runtime.md (Card Marking))
<!-- traceability: {WasmCodeSectionPC} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-01 | カードは命令単位ではなくカード単位 | 同一64バイトカード内の2つの異なる命令オフセット | 一方をtouch | 他方も同じ状態を共有する（カード粒度） | jit_runtime.md  |
| TEST-JITR-02 | 状態遷移: UNEXECUTED→EXECUTED→HOT | 新規カード | 1回touch、2回目touch | 1回目でEXECUTED、2回目でHOT | jit_runtime.md (Card Marking) |
| TEST-JITR-03 | COMPILED後のtouchは状態を変えない | カードがCOMPILED | touch | 状態はCOMPILEDのまま | |
| TEST-JITR-04 | 評価(Eviction)でUNEXECUTEDへ戻る（EXECUTEDではない） | カードがCOMPILED、対応トレースがキャッシュから追い出される | `mark_evicted` | 状態がUNEXECUTEDに戻る。 | [`test_jit_runtime.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py) `test_hotspot_05_3bank_cache_rotation_and_eviction_resets_card` |
| TEST-JITR-06 | 最小トレース長未満のブロックは共有カードを更新しない | 同一コード領域カード内に2つの短いベーシックブロック（推定コンパイル後サイズがカード幅未満） | 両方のPCを繰り返し実行 | 候補外PCは共有カード状態を更新せず、コンパイル待ち列にも追加されない。カード領域の共有有無にかかわらずカード状態は `UNEXECUTED` のままである。 | `jit_runtime.md`「最小トレース長フィルタ」、[`test_jit_runtime.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py) `test_hotspot_06_short_blocks_never_tracked_avoiding_card_aliasing` |
| TEST-JITR-07 | `COMPILED`カードの重複要求をC++ Runtimeが破棄する | トレースがキャッシュに常駐し、同PCのカードが`COMPILED`で、同PCが待ち列にも積まれている | idle_hookを実行 | C++ Runtimeはコンパイルせず要求を破棄し、カード状態と常駐トレースを保つ。 | `jit_runtime.md`「コンパイル待ち列」、[`test_jit_runtime.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py) `test_hotspot_07_idle_hook_skips_recompiling_an_already_resident_trace` |
| TEST-JITR-08 | JITエントリテーブル（バンクのトレース一覧）は常にhead_pcでソートされ、削除は削除フラグ（トンビストーン）で行う | 複数のトレースをPC順不同で挿入し、1件削除後に同じPCを再挿入 | 挿入・削除・再挿入後にトレース一覧と該当PCの取得結果を確認 | 一覧は常にhead_pc昇順。削除直後は取得結果がNone。再挿入は既存tombstone枠を再利用し、取得結果は新しいトレースそのものになる。 | jit_runtime.md「JITエントリ表」, [`jit_runtime.cxx`](experiments/pysim/native/tier3_plugins/jit/jit_runtime.cxx), [`jit_cache.py`](experiments/pysim/qa/shared/jit_cache.py), [`test_jit_runtime.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py) |
| TEST-JITR-09 | OldestヒットによるPromotion時、被チェイン登録（inbound_sources）は昇格先バンクへ引き継がれる | トレースBがバンクXでチェイン元Aから被チェインされている状態で、Bのみが後にOldestからPromoteされる | Bをlookupで昇格させた後、AとBそれぞれの所属バンクを確認 | 昇格前のバンクXからAの登録がなくなり、Bの新しい所属バンクへ引き継がれる。 | [`test_jit_runtime.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py) `test_jitr_promote_transfers_inbound_sources_avoiding_dangling_chain` |
| TEST-JITR-09b | 通常実行ではJIT拡張内部で常駐traceを検索する | 未コンパイルブロックが多く、実行中にtraceをコンパイルする関数 | 関数を実行し、QA計測でJIT実行件数、Python側`cache.lookup()`の呼出し、常駐状態とカードを確認する | 関数結果が正しく、JITを実行する。本体実行入口がJIT拡張内部でtraceを選び、Python側`cache.lookup()`を呼ばない。QA snapshotの各PCは`COMPILED`カードと常駐traceに対応する | `jit_runtime.md`「トレース実行時の分岐解決とインタープリタ復帰」、pysim `test_jitr_native_trace_lookup_uses_resident_snapshot` |
| TEST-JITR-71 | カード表と候補抑止表はコード領域単位 | 4バイトカード境界をまたぐ2関数が同じCode-sectionカードに属する | 一方をtouchし、候補カードをmark/unmarkする | 両関数のPCが同じホットスポット状態と候補抑止bitを共有する。各表はコード領域全体を覆う単一の密ストレージを持つ | `jit_runtime.md`「Card Marking」「Trackable Mask」、pysim `test_hotspot_and_trackable_bitmaps_share_one_code_region_card_space` |

### ホットスポット判定 (yield時) と バッチコンパイル
<!-- traceability: {GOTCHA-JITR-09} {JIT_CardAgingSweep} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-10 | 検出と通常コンパイルの遅延、および満杯時の即時drain | カードがHOTになる | 通常時とキュー満杯時を分けて実行 | 通常時はyield/idleまでコンパイルせず、キュー満杯時だけその場でLIFO drainしてキューを空ける。キュー容量を超えて保持しない | jit_compiler.md ADR_SafeQueuingOnHotMiss, `{JIT_ReverseCompilationOrder}` |
| TEST-JITR-12 | LIFO順でのバッチコンパイル | 直線後続がA→B→Cとなる適格なブロックをA、B、Cの順にキューへ投入済み | `idle_hook`を実行する | C、B、Aの順にコンパイルされる。Aのinstall時にはBが常駐済みで、共通chain dispatcherを経由する後続chainが成立する | {JIT_ReverseCompilationOrder} |
| TEST-JITR-13 | 常駐トレース再確認とカード状態の整合 | コンパイル要求PCがキューに重複登録済み、またはキャッシュ常駐済み | idle_hookを実行 | 常駐済みなら再コンパイルせず`COMPILED`へ同期する。全キャッシュmissはコンパイル要求として扱い、eviction後のPCを`COMPILED`のまま放置しない | `{ADR_SafeQueuingOnHotMiss}` |
| TEST-JITR-14 | コンパイル失敗時の恒久的候補除外 | C++コンパイラが失敗を返す | idle_hookを実行 | Trackable Maskの対象ビットだけを解除し、カードを`COMPILED`へ設定しない。同一モジュール内で再履歴・再キュー・再コンパイルを行わない | `{TrackableBlockMask}` |
| TEST-JITR-15 | eviction／明示的flush後の再計測 | 常駐トレースがevictまたはJIT cache flushされる | そのPCを再実行 | キャッシュ参照はInterpreterへ戻り、カードは`UNEXECUTED`からhotnessを再計測する。候補性は維持される | `TEST-JITR-04`, `{TrackableBlockMask}` |
| TEST-JITR-16 | エイジングスイープは`EXECUTED`カードだけを`UNEXECUTED`へ戻す | コード領域内に`EXECUTED`が複数枚、`HOT`が1枚、`COMPILED`が1枚のカードがある | dirtyカードを含む表を一周以上スイープする | `EXECUTED`カードだけが`UNEXECUTED`になる。`HOT`と`COMPILED`は変化しない。減衰後に1回touchしたカードは`EXECUTED`であり`HOT`にならない。 | `GOTCHA-JITR-09`, `JIT_CardAgingSweep`, [`test_jit_runtime.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py) `test_jitr_aging_decays_only_executed_cards` |
| TEST-JITR-17 | カード更新表の包含不変条件 | コード領域内のカードを複数関数からtouchし、touchとスイープを交互に行う | 各操作の後に観測済みカード状態とdirty bitを照合する | dirty bitが0のカードは`EXECUTED`状態ではない。`UNEXECUTED`から`EXECUTED`への遷移時だけ、そのカードのbitが立つ。スイープはbitを立てない | `JIT_CardAgingSweep`, [`test_jit_runtime.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py) `test_jitr_update_bitmap_covers_every_executed_card` |
| TEST-JITR-18 | エイジングは3面キャッシュのローテーション1回につき1ステップ進む | カード更新表にdirty bitがあり、待ち列が空・処理済み・コンパイル無効のいずれかである | 各状態でidle_hookを実行し、続けて`flush_all`と`rotate`を行う。さらにバンクが満杯になるまでトレースを挿入する。各段階でカーソルを確認する | idle_hookと`flush_all`ではカーソルは進まない。`rotate`の実行で1ステップ進む。バンク満杯による自動ローテーションでも1ステップ進む | `JIT_CardAgingSweep`, [`test_jit_runtime.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py) `test_jitr_aging_advances_once_per_rotation` |
| TEST-JITR-19 | カードdirty表の巡回、走査上限、有限減衰 | コード領域カード数が9以上で複数バイトにわたりdirty bitが立つ | スイープを1ステップずつ実行する | 1ステップで処理する非ゼロバイト数は設定値以下。0バイトは処理数に数えず読み飛ばす。走査バイト数が上限に達した場合も終了する。カーソルは表末尾から先頭へ戻る。1バイトに複数カードbitが立つ場合はその全カードを同じステップで処理する。全dirtyカードは有限回のローテーション内に減衰する | `JIT_CardAgingSweep`, [`test_jit_runtime.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py) `test_jitr_aging_cursor_wraps_and_bounds_each_step`, `test_jitr_aging_scans_region_card_bytes_and_ignores_import_code` |

### JIT拡張ホットスポット履歴

<!-- traceability: {HistoryBuffer} {RuntimeHotspotProfiler} {LowLatencyJIT} -->

Interpreter境界で記録する履歴と、Tier 3 JIT拡張による実行区間終了時の一括分析を検証する。形式モデルは[`jit_hotspot_model.py`](docs/components/tier3_plugins/formal/jit_hotspot_model.py)を参照する。

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 |
| :--- | :--- | :--- | :--- | :--- |
| TEST-HOTSPOT-01 | 履歴レコードとInterpreter記録順 | JIT拡張有効で、複数の適格ブロックを通る関数がある | Interpreter区間を実行し、JIT拡張の履歴を読む | 各レコードがCode section payload相対PCを含む`(module_id, unified_pc)`を保持し、実行順と一致する。非適格ブロックは含まれない |
| TEST-HOTSPOT-02 | 基本ブロック実行経路の処理制約 | Runtime Event Sink、時計、Python APIを利用できる構成である | 適格ブロックを反復実行し、呼出し・時刻読出し・割当てを記録する | Interpreterの観測通知からJIT拡張の単一リングへ固定幅レコードを書くだけで、Event Sink、時計、Python API、動的確保を経由しない |
| TEST-HOTSPOT-03 | Interpreter区間終了時の一括分析 | 同じPCを複数回含む未分析履歴がある | yield、fallback、trap、関数完了の各終了理由でInterpreter区間を抜ける | Tier 3 JIT拡張が各終了境界で一度だけ履歴順にカードを更新し、必要なcompile requestを登録する |
| TEST-HOTSPOT-04 | JITのみを実行した区間 | 常駐traceとchainが実行可能で履歴が空である | Interpreterを通らないJIT trace/chain区間を実行する | 履歴を追加せず、ホットスポット分析を行わず、既存カードを変更しない |
| TEST-HOTSPOT-05 | 履歴容量超過と近似状態 | 容量Nの履歴へN+K件を記録する | 次の分析境界で履歴と分析結果を確認する | 直近N件を保持し、上書き数Kと履歴欠落状態を示す。容量超過を理由にyieldせず、分析済み範囲を消費する |
| TEST-HOTSPOT-06 | Runtime Event Sinkとの状態分離 | Event Sinkを有効または無効にしたJIT拡張構成がある | 基本ブロック列を実行し、両履歴の内容と容量超過を比較する | JIT履歴はRuntimeイベントリングへ入らず、Event Sinkの状態はJIT拡張の履歴記録・分析に影響しない |
| TEST-HOTSPOT-07 | JIT拡張無効構成の状態除去 | JIT拡張を接続しないRuntimeを用意する | 構成とメモリ量を比較する | Interpreterだけの構成にはJIT履歴領域、履歴書込み、分析処理が含まれず、実行結果・trap・yield・ゲスト状態はJIT有効構成と一致する |
| TEST-HOTSPOT-08 | Runtime寿命とモジュール状態 | Runtimeにモジュールを登録し、履歴とカード状態を作る | Runtimeを破棄し、新しいRuntimeを生成する | 旧Runtimeの履歴・カード状態はRuntime破棄で解放され、モジュール単位のunload APIや状態再利用を要求しない |

判定では履歴レコード列、カード遷移列、compile request、実行結果、上書き数、具象型構成、およびROM/RAM量を直接比較する。Runtime Event Sinkの有効・無効がHotspot履歴へ影響した場合、またはJIT-only区間で記録・分析が発生した場合は不合格とする。

### 3段検索・3面キャッシュ 直交表マトリクス
<!-- traceability: {JIT_MultiBuffer_Cache} {JIT_OldestOnly_Promote} {ADR_SafeQueuingOnHotMiss} -->

JITトレース検索時の内部状態と期待される挙動を検証する組み合わせ直交表マトリクス。3面バンク（Active / Warm / Oldest）を独立した列として扱う。

| ケース | カードマーキング状態 | Active | Warm | Oldest | 期待される動作 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | UNEXECUTED (0) | - | - | - | 事前フィルタで即時終了、インタープリタ実行継続 | TEST-JITR-20 |
| 2 | EXECUTED (1) | - | - | - | 事前フィルタで即時終了、インタープリタ実行継続 | TEST-JITR-20 |
| 3 | HOT (2) | - | - | - | インタープリタ継続（コンパイル待ち列に投入済み） | TEST-JITR-20 |
| 4 | COMPILED (3) | **hit** | - | - | **JITコード実行**（昇格なし） | TEST-JITR-21 |
| 5 | COMPILED (3) | miss | **hit** | - | **昇格せず** Warm 上のコードをそのまま実行（無償観測期間） | TEST-JITR-22 |
| 6 | COMPILED (3) | miss | miss | **hit** | **新 Active へ昇格 (Copy)** してから JIT 実行 | TEST-JITR-23 |
| 7 | COMPILED (3) | miss | miss | miss | キャッシュ不在を検出してInterpreterへ戻し、eviction／flushでカードを`UNEXECUTED`へ戻した上で再hotness計測する | TEST-JITR-24 |
| 8 | (書き込み時) | **満杯** | - | - | 3面リングローテーション: Oldest を Purge して新 Active に、Active→Warm、Warm→Oldest。同時に前方チェインとLOOPリンクのダングリング掃引を行う | TEST-JITR-25 |

#### 3段検索テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-20 | UNEXECUTED/EXECUTED/HOTは即座にインタープリタ継続 | カード状態がCOMPILED未満 | lookup | 事前フィルタで即終了、キャッシュ検索を行わない | 直交表 ケース1-3 |
| TEST-JITR-21 | Activeヒット | トレースがActiveに存在 | lookup | 昇格なしでネイティブコード実行 | 直交表 ケース4 |
| TEST-JITR-22 | Warmヒット（無償観測、昇格なし） | トレースがWarmに存在 | lookup | Warmのままコピーせず実行される。`promotions`カウンタは変化しない | 直交表 ケース5 |
| TEST-JITR-23 | Oldestヒットで即座にActiveへ昇格 | トレースがOldestに存在 | lookup | Active領域へコピーされてから実行、`promotions`が増加 | 直交表 ケース6, `{JIT_OldestOnly_Promote}` |
| TEST-JITR-24 | 全ミス後のInterpreter復帰と再計測 | Active/Warm/Oldestいずれにも存在しない | lookup | Interpreterへ戻り、eviction／flushでカードを`UNEXECUTED`へ戻してからhotnessを再計測する。キャッシュmissだけでカードを`COMPILED`に固定しない | 直交表 ケース7 |
| TEST-JITR-25 | キャッシュ満杯時の3面ローテーション | Activeのコード領域または記述枠が満杯 | 新規insert | Oldestをpurgeして新Activeにし、Active→Warm、Warm→Oldestへスライド。同時にchain targetのダングリング参照を無効化 | 直交表 ケース8、`test_jitr_entry_limit_rotates_before_code_region_is_full` |
| TEST-JITR-27 | 既存記述枠の再利用と満杯時のローテーション | 短いトレースでコード領域より先に記述枠を満杯にする | 既存PCを置換した後、新規PCを挿入する | 既存PCの置換はコード位置と使用バイト数を維持する。新規PCの挿入はローテーションし、既存PCをWarmに保持する。Warm・Oldestへの重複挿入は状態を変更せず拒否する | `test_jitr_bank_entry_limit_preserves_state_and_reuses_existing_slots`, `test_jitr_insert_preserves_one_resident_trace_per_pc` |
| TEST-JITR-28 | QA snapshot領域の容量と再利用 | QA専用ハーネスへ小さいモジュールを登録し、ホットスポット検出の有効・無効構成を用意する | QA snapshot生成、trace追加、候補除外、flushを順に行う | QA snapshotのtrace表容量は常駐枠上限と基本ブロック数の小さい方となる。候補マスクの観測は既存ストレージを借用する。更新でQA snapshot領域を追加確保しない。製品のdispatchへsnapshotを渡す要求とはしない | QA専用fixtureの検査、`test_jitr_native_dispatch_snapshot_is_cached_per_hotspot_configuration` |
| TEST-JITR-29 | 制御命令だけの区間の検索・観測抑止 | 構造命令と通常命令の開始PCが同じカードに入り、制御命令が連続する | native dispatcherで実行し、QAハーネスでJIT拡張の履歴とLoader索引の検索を観測する | 通常命令のあるブロックだけを開始PCで通知し、JIT拡張の単一リングへ直接記録する。制御命令だけの区間と記録通知でLoader索引を再検索しない。NativeStepからdispatchへ切り替えても、メモリ処理の再開PCを先頭として扱わない。分岐・呼出しの結果を維持する | `test_jitr_mask_card_collision_does_not_profile_structural_pc`, `test_jitr_memory_boundary_continuation_does_not_become_a_block_head`, `test_jitr_native_step_consumes_pending_head_before_memory_fallback`, `test_jitr_memory_helper_trap_clears_block_continuation` |
| TEST-JITR-26 | Direct-Mapped Folding XOR JIT Cache による O(1) 一発ヒット | トレースがキャッシュに存在 | lookup | 4-bit スロット選択を行う Folding XOR Hash で 16 スロットテーブルにヒットし、バンク二分探索を行わずに O(1) で即時返却される | `jit_runtime.md` , `{DirectMappedJIT16}` |

| TEST-LOAD-49 | int4_t スコアリングによる JIT 候補ビットマップ生成 | JIT プラグインへモジュール登録 | `test_plugin_candidate_gate_uses_specification_threshold`で8・9・10点と負の便益を含む2点の有効ブロックを実登録する | 128B BitView<4> テーブルから命令ごとの機械語短縮スコア（int4_t）を積算し、合計9点以上のブロックの head_pc カードビット（1bit）が正確に 1 にセットされる | `{JIT_StaticBenefitScoring}`, `{JIT_CandidateBitmap}` |
| TEST-LOAD-50 | JITCandidateBitmap 非候補ブロックの touch/履歴バイパス | 非候補ブロック（カードビット 0）の実行 | `eng.run(cold_pc, ctx)` | インタープリタ実行は行われるが、HotspotBitmap.touch() および履歴リングへの記録が完全にバイパスされ、カード状態が UNEXECUTED のまま維持される | `{JIT_CandidateBitmap}` |

### トレース・チェイニング
<!-- traceability: {GOTCHA-INTP-06} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-30 | 新規traceの未接続chain target | 新規x64 trace | headerとtrace末尾を確認する | `chain_target_addr == 0`。trace末尾は共通コード領域のchain dispatcherへ進み、targetなしなら共通epilogueへ戻る | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) `{JIT_LazyChaining}` |
| TEST-JITR-31 | 常駐後続traceへの共通dispatcher chain | 互換な直線後続traceがActiveまたはWarmに常駐 | source traceを実行する | source traceは共通コード領域のchain dispatcherを経由し、headerのtarget bodyへtail-jumpする。opcode handlerの判定・呼出しは行わない | `test_jitr_native_header_chain_executes_successor_body_once`, `{JIT_LazyChaining}` |
| TEST-JITR-32 | Oldest traceをchain targetにしない | 直線後続traceがOldestにのみ存在 | source traceを登録・実行する | chain targetは0のままで、共通chain dispatcherから共通epilogueへ戻る。Oldestは次回rotateで破棄される可能性がある | [`jit_runtime.cxx`](experiments/pysim/native/tier3_plugins/jit/jit_runtime.cxx) `try_link`, `{JIT_LazyChaining}` |
| TEST-JITR-33 | 制御終端をC++ Interpreter handlerへ委譲 | `BR`、`BR_IF`、`BR_TABLE`または構造終端を持つブロック | JIT実行後の終端PC、条件値、制御frame更新を確認 | 一般の制御終端はトレース内で実行・chainせず、対応するC++ handlerを一度通す。handler後はC++ dispatcherが同じ実行内で次PCをlookupし、後方分岐しきい値まで続ける。条件値とframe操作はhandlerが処理する | `{JIT_RuntimeAPI_Fallback}`, `GOTCHA-INTP-06` |
| TEST-JITR-34 | target promote後の共通dispatcher chain維持 | chain targetがOldestからActiveへpromoteされる | promoteとrotateの前後でheaderを確認する | 追跡metadataとheader targetがresident target bodyを指し続ける。実行は常に共通chain dispatcherを通る | `test_jitr_promote_transfers_inbound_sources_avoiding_dangling_chain` |
| TEST-JITR-35 | target eviction後のchain無効化 | target traceがpurgeされる | rotate後にsource headerを確認する | `chain_target_addr`を0にし、次のchain dispatcher実行はepilogueへ戻る。LOOP専用linkは設けない | `test_jitr_31_to_35_trace_chaining_and_ok_unlinking` |
| TEST-JITR-36 | WarmからOldestへ移動したresident target | target traceがWarmからOldestへ移動する | rotate直後のsource headerを確認する | targetがWarmからOldestへ移っても既存chainを維持する。Oldestだけに存在する後続へ新たにリンクしない。targetがpurgeされた時に既存chainを解除する | `test_jitr_31_to_35_trace_chaining_and_ok_unlinking` |
| TEST-JITR-37 | bank purgeとchain unlinkの処理量 | bank内n trace、被chain元k件 | cache rotateを確認する | 破棄bankの全n entryを処理し、被chain元を索引から取り出して更新する。参照実装の上限付き処理量は`O(n + k log n)`である | `jit_runtime.md`「世代交代ローテーション」 |

TEST-JITR-33のIFと内側loopの回帰試験は、対象の制御終端直前のtraceだけを配置する。TEST-JITR-44の終了block回帰試験も同じ構成を使う。対象PCのコンパイル成功、単独常駐、Native実行件数と意味結果を確認する。他のtraceが実行されたことだけで対象の到達を代用しない。hotnessからの自動生成は別の試験で扱う。

### x64実行可能バッファのW^X保護 (ARMv8-M: TBD)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-40 | パッチ中だけ実行可能バッファへ書き込める | x64 `ExecutableBuffer` が初期化済み | `begin_jit_patch()`前後で`write()`する | パッチ開始前の書込みはassertで拒否され、開始後は境界内の書込みが成功する | [`exec_memory.py`](experiments/pysim/qa/shared/exec_memory.py) |
| TEST-JITR-41 | 実行可能状態へ戻した後は書込みを拒否する | x64 `ExecutableBuffer` にコードを書込み済み | `commit_jit_patch()`後に`write()`する | 書込みはassertで拒否され、バッファは実行可能状態になる | 同上 |
| TEST-JITR-42 | x64の実行可能バッファ権限遷移 | `begin_jit_patch()`→書込み→`commit_jit_patch()` | OS が公開する実権限と生成コードの実行結果を確認する | 実権限は RW から RX へ遷移し、再パッチ中だけ RW へ戻る。生成コードは指定した値を返す。ARMv8-Mの命令や保護機構はこの期待値に含めない | [`exec_memory.py`](experiments/pysim/qa/shared/exec_memory.py) |
| TEST-JITR-43 | x64の書込み・実行同時許可(RWX)排除 | x64 `ExecutableBuffer` が初期化済み | 各権限遷移後にOS が公開する実権限を確認する | 全状態で書込みと実行が同時に許可されない | [`exec_memory.py`](experiments/pysim/qa/shared/exec_memory.py) |

### 関数戻り値とInterpreter境界
<!-- traceability: {TraceBoundaryInvariant} {GOTCHA-INTP-06} {GOTCHA-JITR-02} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-44 | JIT終了ブロックからC++ Interpreterのreturnハンドラへ復帰 | JITトレースが`return`直前で終了する関数 | トレース後のInterpreter PCと共有スタックを確認する | JITは戻り値を共有オペランド領域へ残し、C++ return handlerを一度呼び出す。handlerはreturn sentinelを設定し、次のInterpreter処理が関数復帰を完了する。x64のホスト関数ABIは [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) に従い、ARMv8-Mの物理ABIはTBDとする | `interpreter.md` 関数復帰の番兵、[`test_jit_runtime.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py) `test_jitr_return_terminated_block_jit_result_correct`、`test_jitr_return_handler_publishes_sentinel_once` |
| TEST-JITR-45 | JIT実行calleeの戻り値をcallerへ引き継ぐ | callerが反復してcalleeを呼び、calleeのブロックがJITコンパイル済み | `RuntimeEngine.call()`でcallerを実行 | calleeの結果は共有オペランド領域に残り、InterpreterがRETURN sentinelを消費して関数呼出し記述子を取り除き、callerがその結果を利用する | `interpreter.md` 関数復帰の番兵, pysim `test_jitr_nested_wasm_call_keeps_callee_result_on_shared_operand_stack` |
| TEST-JITR-46 | JIT有効時のWASMスカラー結果型維持 | i32/i64/f32/f64のトップレベル関数 | JIT有効の`RuntimeEngine.call()`を各関数に対して反復実行 | JITコンパイル可能な結果はJIT経由、未対応型はInterpreterへ委譲し、全結果の値を保持する。f32/f64を整数へ変換しない | `interpreter.md` 関数復帰, pysim `test_jitr_runtime_engine_preserves_typed_top_level_results` |
| TEST-JITR-47 | import/host callをInterpreter境界で実行 | ゲスト関数がWASM importを呼び、戻り値を後続命令で使う | 呼び出しブロックのコンパイルとRuntimeEngine実行を確認する | callを含むトレースはコンパイルされず、Interpreterがhost関数を呼ぶ。戻り値は共有オペランド領域に積まれ、callerの後続命令が消費する | `interpreter.md` 関数呼び出し境界, pysim `test_jitr_host_import_stays_on_interpreter_runtime_boundary` |
| TEST-JITR-48 | RuntimeEngine同期境界でTrapを正常結果と混同しない | ゲスト関数が整数除算ゼロTrapを発生させる | `RuntimeEngine.call()`で関数を実行する | Trap codeを呼び出し側へ伝え、`None`を正常な結果として返さない | `interpreter.md` 命令実行中のTrap, pysim `test_jitr_runtime_engine_surfaces_guest_trap_at_sync_boundary` |
| TEST-JITR-49 | JIT有効時のif/else両経路とループ脱出 | ループ内にthen/elseと`br_if`を持つ関数 | 複数入力をInterpreter専用とJIT有効RuntimeEngineで実行し比較する | then/else両方を通る入力を含め、全結果が一致し、JITトレースが実行される | `{JIT_RuntimeAPI_Fallback}`, pysim `test_jitr_if_else_loop_matches_interpreter_after_jit_compilation` |
| TEST-JITR-50 | `br_table`のC++ handler復帰と全分岐先 | 既定先を含む3分岐先を持つ関数 | 各selectorでInterpreter専用とJIT有効RuntimeEngineを実行する | 直前のJITトレース後にC++ `br_table` handlerが選択先を処理する。case 0/1/defaultの全結果が一致する | `{JIT_RuntimeAPI_Fallback}`, pysim `test_jitr_br_table_uses_native_handler_and_preserves_every_target` |
| TEST-JITR-51 | 異種型混在時のDROP幅とJIT安全フォールバック | 共有operand stack上にi32/f32/i64/f64値を積み、広幅値を`drop`する関数 | JITコンパイル可否とJIT有効RuntimeEngineの結果を検証する | i64/f64のDROPが2 raw wordを除去する。未対応の混在トレースはコンパイルされずInterpreterへ委譲し、後続i32演算とreturn値を保つ | `{ADR_TosCacheAsymmetry}`, pysim `test_jitr_mixed_typed_stack_declines_jit_without_losing_drop_widths` |
| TEST-JITR-52 | 混在型callee戻り値とJIT/Interpreter共有stack境界 | i32/f32/i64/f64を返すcalleeを順に呼び、i32 calleeをJIT hotにするcaller | callerをJIT有効RuntimeEngineで反復実行する | 各callee結果は正しいraw幅で共有stackへ戻り、i64/f64 drop後のi32計算結果が保たれる。途中のJIT callee復帰で関数末尾PCをreturn sentinelへ正規化する | `interpreter.md` 関数復帰, `{JIT_RuntimeAPI_Fallback}`, pysim `test_jitr_mixed_typed_callee_returns_keep_the_shared_stack_synchronized` |
| TEST-JITR-53 | `block`で終わるコンパイル済みブロックがC++ handlerを通る | 先頭ブロックだけをコンパイルし、続くループの分岐はInterpreterが実行する関数 | 関数を繰り返し呼び、wasmtime、Tier 2、Tier 3の結果を比較する | 3実行系の結果が一致し、終端`block`命令はC++ handlerが一度処理し、制御frameを積む | TraceBoundaryInvariant, `GOTCHA-INTP-06` |
| TEST-JITR-54 | ブロック先頭でない再開位置がコンパイルキューへ入らない | 短い`br_if`ブロックの不成立経路が`end`へ落ち、その`end`が後続の長いブロックと同じ4バイトカードに入る。コード配置を4通りにずらす | 各配置で3実行系の結果を比較する | 全配置で結果が一致し、ブロックが存在しないPCのコンパイル要求で停止しない | TraceBoundaryInvariant |
| TEST-JITR-55 | 再開位置で古い制御フレームを切り詰める | ネストした`br`の脱出と、ループを抜けた直後の`br 0`（ブロック先頭でも構造命令でもない位置）を持つ関数 | 3実行系の結果を比較する | 結果が一致し、ループへ戻る無限ループや誤った分岐先が発生しない | `GOTCHA-INTP-06` |
| TEST-JITR-56 | 広い型のローカルを持つフレームでのJIT実行 | i32専用のホットループと、同一関数内のf64ローカルを持つ関数 | 3実行系の結果を比較する | ローカルは型に関係なく固定スロットを持つため、f64ローカルの存在でトレース実行が停止しない | TraceBoundaryInvariant |
| TEST-JITR-57 | `if`で終わるホットブロック | 条件式で終わるブロックを持つホットループ | 3実行系の結果を比較する | `if`をインタープリタが実行し、条件値がオペランドスタックに残る。結果が一致する | TraceBoundaryInvariant |
| TEST-JITR-58 | 生成した構造化制御フローの3実行系差分 | シード固定で生成した60本のblock/loop/if/br入れ子 | 3実行系の結果を比較し、JIT実行回数とC++制御ハンドラ回数を確認する | 全プログラムの結果が一致し、JITトレースとC++ Interpreterの分岐ハンドラが実際に使われる | TraceBoundaryInvariant, `{JIT_RuntimeAPI_Fallback}` |
| TEST-JITR-59 | clang生成カーネルスイートの3実行系差分 | `suite.wasm`の16カーネル（呼び出し、`br_table`、f64、i64、サブワードメモリを含む） | 各カーネルの結果を3実行系で比較する | 全カーネルの結果が一致する | `{JIT_CopyAndPatch}`, `{ThreadedInterpreter}` |
| TEST-JITR-60 | スロット幅の異なるフレームの混在実行 | 4バイトスロットの関数と、i64ローカルを含む8バイトスロットの関数が、同一のホットループから交互に呼ばれる | 3実行系の結果を比較し、両関数のトレースが存在することを確認する | 結果が一致する。2つの関数のトレースが同時に実行される | `{ContextPointerRegister}`, TraceBoundaryInvariant |
| TEST-JITR-61 | オペランドスタックの容量を超える押し出しの拒否 | 深さ40の加算列を持つ関数を、30個の値が保留中の状態から呼ぶ。合計が容量（64ワード）を超える | Tier 2とJIT有効のエンジンの両方で実行する | どちらも容量超過の `assert` で停止する。トレースは、容量に収まる呼び出しでは実行される。収まらない呼び出しは、インタープリタが停止する。JITは容量外へ書き込まない | TraceBoundaryInvariant |
| TEST-JITR-62 | 退避・昇格・ローテーション後のチェイン再リンク | 14本の連鎖トレースと、容量の小さい3面キャッシュ。乱数で、挿入・参照（昇格）・ローテーションを繰り返す（40シード、各90手） | 各手の後にリンクを検査し、参照したトレースをネイティブ実行する | すべてのチェインポインタが、常駐トレースの有効な入口を指す。論理的な後続PCと機械語ヘッダのターゲットが一致する。同一トレースが複数の面に常駐しない。各リンク先は生成入力のindexとstrideから導いた直後のtraceである。Native実行の副作用と最終PCも生成入力から導いた期待値に一致する | `{JIT_MultiBuffer_Cache}`, `GOTCHA-JITR-02` |
| TEST-JITR-63 | C++ディスパッチのLOOP後方辺回数制御とyield境界 | 同一関数・同一制御frameのLOOP後方`BR_IF`と常駐トレースを持つ関数 | 共通C++ディスパッチ内のJIT実行、C++ `br_if` handler回数、RuntimeEngineのyield要求を観測する | しきい値未満はC++ディスパッチが後続常駐トレースを検索して実行する。しきい値到達後はC++ handlerが分岐とframe状態を更新し、RuntimeEngineがカウンタを0へ戻してyield要求を返す | `interpreter.md`「トレース境界での協調的Yield」、`runtime_vsoc_test_spec.md` TEST-VSOC-22, pysim `test_jitr_loop_backedge_stays_in_cpp_until_coos_yield` |
| TEST-JITR-64 | Interpreter warm-upからC++ディスパッチを使うJITへ移行 | 未コンパイルの数値ループを実行し、idle時に対象トレースをコンパイルする | RuntimeEngineで関数を完了し、返値・実行統計・キャッシュ常駐を確認する | 結果は15で、Interpreter warm-up後にLOOPトレースが常駐する。C++ dispatcherは設定された後方辺数までC++ handlerとトレースを実行してからRuntimeEngineへyieldを返す | `jit_runtime.md`「トレース実行時の分岐解決とインタープリタ復帰」、pysim `test_hybrid_interpreter_to_jit_trace_elevation` |
| TEST-JITR-65 | QA snapshotの常駐状態への追従 | 未コンパイル候補blockと常駐traceを持つQA専用JITハーネス | trace挿入前後のQA snapshotとnative実行を確認する | QA snapshotは常駐状態の変更を反映する。製品実行はJIT拡張の本体実行入口を使い、snapshotを供給しない。候補マスクのQA観測は既存ストレージを借用する | QA専用fixtureの検査、pysim `test_jitr_native_dispatch_snapshot_is_cached_per_hotspot_configuration` |
| TEST-JITR-66 | 診断カウンタを無効にしたRuntimeの意味保存 | 診断カウンタなしで後方分岐yieldを行うC++ Interpreter構成 | 関数を実行し、結果・yieldと統計値を確認する | 結果とyield境界は変わらず、製品Runtimeは検査用APIと累積カウンタを持たない。QA専用ハーネスでJIT body、handler、trace遷移を計測する | `jit_runtime.md`「トレース実行時の分岐解決とインタープリタ復帰」 |
| TEST-JITR-67 | フレーム深度をまたぐ合法なLOOP分岐はchain targetを作らない | 内側`block`から同一関数の外側`loop`へ戻る合法な`br_if` | 対象・分岐blockのframe深度、コンパイル結果、C++ handler経由の実行結果を確認する | トレース本体はコンパイル可能でも、後方分岐にchain targetは設定しない。分岐はC++ Interpreter handlerが処理し、Interpreterのみの結果と一致する | `runtime_vsoc.md`「後方分岐とyield回数」、`jit_runtime.md`「トレース実行時の分岐解決とインタープリタ復帰」、pysim `test_jitr_cross_frame_loop_branch_skips_special_link_but_keeps_trace_body` |
| TEST-JITR-68 | ホットスポット収集中の定義済み関数呼出しをC++内で継続 | 適格なcalleeを繰り返し呼ぶcallerと、ホットスポット収集有効のRuntime | 同じ入力を初回とcalleeコンパイル後に実行し、結果、calleeの常駐、JIT実行件数、Runtime復帰件数を確認する | 両実行の結果が一致する。calleeの適格ブロックは履歴へ記録されてコンパイルされる。定義済み関数の各呼出しでPythonへ戻らず、コンパイル後はcalleeのJIT traceが実行される | `{RuntimeHotspotProfiler}`、`{LowLatencyJIT}`、pysim `test_jitr_hotspot_collection_continues_defined_calls_in_cpp` |
| TEST-JITR-70 | 空のyield境界でのJIT制御処理省略 | コンパイル待ち作業と候補履歴がなく、後方分岐yieldを繰り返す関数 | 実行結果、yield回数、コンパイル処理・履歴引渡し・Python側trace検索の呼出しを確認する | 関数結果とyield条件を保つ。空キューのコンパイル処理と空履歴の引渡しを行わず、通常経路でtraceをPython側で検索しない | `{LowLatencyJIT}`、`{RuntimeHotspotProfiler}`、pysim `test_jitr_empty_yield_skips_python_control_work` |
| TEST-JITR-72 | LIFOキューから元WASMコードをコンパイルする | 2つ以上の適格blockが`HOT`になり、元のWASMコードがLoader領域にある | Tier 3 JIT拡張のidle hookで逆順に処理する。Python opcodeの再構築経路を禁止する | C++ compilerは元のWASMコードを直接読み、trace本体を生成する。成功したtraceだけがcacheへ入る | `jit_runtime.md`「オンデマンドコンパイルキュー」、pysim `test_jitr_native_compiler_scans_queued_traces_without_python_opcode_marshalling` |
| TEST-JITR-73 | JIT拡張の本体実行入口で常駐traceを選ぶ | 常駐traceがあり、実行拡張が初期化時に接続済みである | Interpreter dispatcherから実行し、QA計測でJIT実行とPython cache lookupを観測する | 結果とJIT実行が正しい。trace選択はJIT拡張内部で完了し、Interpreterへsnapshotを渡さない。通常経路でPython cache lookupを呼ばない | `jit_runtime.md`「ネイティブ実行拡張の接続」、pysim `test_jitr_native_trace_lookup_uses_resident_snapshot` |

### 実装上の注意点に対応する検証
<!-- traceability: {GOTCHA-INTP-06} {GOTCHA-JITR-01} {GOTCHA-JITR-02} {GOTCHA-JITR-03} {GOTCHA-JITR-05} {GOTCHA-JITR-06} {GOTCHA-JITR-07} {GOTCHA-JITR-08} {GOTCHA-JITR-09} -->

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-JITR-01 | `COMPILED`カードによる重複要求の除外 | トレースがキャッシュに常駐し、カード状態が`COMPILED`のPCが待ち列にもある | `idle_hook` のキュー処理を実行 | C++ Runtimeは再コンパイルせず要求を破棄し、カード状態と常駐トレースを保つ。 | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md)「コンパイル待ち列」 |
| GOTCHA-JITR-02 | Oldest昇格時の被チェイン登録移管 | BはOldest所属で、別トレースAから被チェインされている | BをActiveへ昇格し、Oldestバンクを破棄する | Aの登録を昇格先バンクへ移管する。Oldest破棄後もAのchain targetと常駐bodyのchainを維持する | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) 「局所再チェイニングとアンリンク」 |
| GOTCHA-JITR-03 | 3面ローテーション時の被チェイン更新と局所アンリンク | Oldest内のトレースが他バンクのトレースからchainされている | 昇格済みの場合と完全破棄の場合で、Active満杯によるローテーションを実行する | Oldestが新Activeへ再利用される。被チェイン元のtargetは昇格先bodyまたは復帰スタブへ更新され、破棄先へ跳ばない。更新対象は逆引き登録された被チェイン元に限られる | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) |
| GOTCHA-JITR-05 | 3面ローテーション・フラッシュ時の Folding XOR スロット無効化 | rotate() または flush_all() 実行時 | rotate() を実行し、直後に旧PCでlookup | 16スロットの高速キャッシュを不可分にクリアする。古いバンクへのダングリング参照を防ぎ、必要に応じて昇格（Promotion）を発火する。 | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) |
| GOTCHA-JITR-06 | JIT脱出後のC++制御handlerと次PC lookup | ループがif文の中に入れ子になっており、内側ループの脱出条件ブロックがコンパイル済み | JIT実行後の終端命令PC、条件値、Native制御フレーム更新、次のlookupを観測する | 対応済み終端命令はC++ Interpreter handlerを一度通る。handlerが条件と制御フレームを更新し、通常経路では同じC++ dispatcherが後方分岐しきい値まで継続して遷移先PCをlookupする。結果はInterpreter専用実行と一致する。 | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md)、`{JIT_RuntimeAPI_Fallback}`, `GOTCHA-INTP-06`, `test_jitr_br_if_loop_exit_jit_result_correct` |
| GOTCHA-JITR-07 | 短小ブロック判定の符号（後方分岐ブロックの誤除外） | ループ本体の末尾が、自分自身の先頭より前へ戻る後方分岐になっている | 短小ブロックの足切り判定を経て実行を反復する | 後方分岐ブロックも足切り判定を通過し、命令数が十分ならコンパイル対象になる。 | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) |
| GOTCHA-JITR-08 | return直前のJIT終了とInterpreter復帰 | `return`を含むWASM関数 | トレース実行とcallee復帰を確認 | JITは`return`や`RETURN sentinel`を生成せず、終了エピローグで共有オペランド領域／実行コンテキストを同期してInterpreterへ戻る。Interpreterがcalleeの関数呼出し記述子を取り除き、ネスト時はcall helperがsentinelを消費する | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md), `interpreter.md` |
| GOTCHA-JITR-09 | エイジングスイープと常駐状態・待ち列の分離 | カードが`COMPILED`（キャッシュに常駐）、別のカードが`HOT`（コンパイル待ち列に登録済み）、別のカードが`EXECUTED` | 全ブロックを覆うまでエイジングスイープを実行し、常駐トレースをlookup | `COMPILED`のカードは`COMPILED`のままで、lookupは常駐トレースを返す。`HOT`のカードは`HOT`のままで、待ち列の要求と対応する。`EXECUTED`のカードだけが`UNEXECUTED`になる。 | [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) 「エイジングスイープ」, `TEST-JITR-16`, [`test_jit_runtime.py`](experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py) `test_gotcha_jitr_09_aging_never_drops_compiled_or_hot` |

### QA専用計測構成のトレース実行回数

<!-- traceability: {JIT_MultiBuffer_Cache} {JIT_OldestOnly_Promote} {Challenge_JITCacheEfficiency} -->

この節はQA専用の計測型を合成した構成だけに適用する。計測契約の正本は[`jit_runtime_bench_spec.md`](docs/components/tier3_plugins/benchmarks/jit_runtime_bench_spec.md)とする。製品構成は`void`を選び、計測状態、時計読取り、実行後のchain走査、および統計リセットAPIを持たない。

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-76 | 反復実行、飽和、昇格、測定区間のリセット | 実行回数が既知の常駐loop traceがある | QA計測構成で通常統計とホットスポット検出の有効・無効を組み合わせて反復実行し、Oldest昇格、QA snapshot再生成、32ビット上限、QA resetを確認する | body実行ごとに1増える。lookupでは増えない。昇格とQA snapshot更新では値とカウンタの所在を保持する。最大値で飽和し、reset後は0から数える | `test_jitr_trace_execution_counts_loop_bodies_and_survives_promotion` |
| TEST-JITR-77 | 直接chainの後続bodyの実行回数 | 2 traceの直線chainがある | QA計測構成で通常統計の有効・無効を組み合わせ、Runtimeからchainを実行する | 先頭と後続の回数がともに1増え、ゲストの結果が一致する | `test_jitr_trace_execution_counts_include_direct_chain_successors` |
| TEST-JITR-78 | QAの退役記録と新traceの計数寿命 | 実行済みと未実行のtrace、および収集先がある | QAハーネスで昇格、rotate、置換、flush前後の常駐状態を比較する | 昇格では記録せず値を保持する。脱落したtraceのPCと操作前に保存した回数を一度通知する。未実行の0も通知する。同じPCの新traceは0から始まる | `test_jitr_trace_execution_retirement_records_zero_and_used_traces` |
| TEST-JITR-79 | 容量不足で実行されないtraceの除外 | 常駐traceの必要スタック量が空き容量を越える | Runtimeから関数を実行する | Interpreterで正しい結果を得て、traceの回数は0のままとなる | `test_jitr_trace_execution_count_excludes_pre_entry_stack_fallback` |

`test_jitr_measurements_are_formatted_in_cpp_through_shared_printk`はC++が計測値を整形し、共有printkへ出力することを検査する。

形式モデルは[`jit_trace_execution_model.py`](docs/components/tier3_plugins/formal/jit_trace_execution_model.py)を参照する。dispatcher外の低水準直接呼出しは計数対象外である。製品の計測状態の有無をこのモデルから要求しない。

## 3. テスト検証実績と網羅状況

2026-10-05、Linux x86_64、Clang 21.1.8の参照環境で、QA計測構成のTEST-JITR-76〜79を8ケース実行した。通常統計とホットスポット計測の構成を含めて8件成功した。関連する`test_jit_runtime.py`と`test_x64_jit.py`の回帰は109件成功した。AddressSanitizerとUndefinedBehaviorSanitizerを有効にしたC++ dispatcherでも追加8ケースが成功した。実行回数モデルの通常系4特性と`guards=False`の4特性の反証を確認した。

実行コマンドは次のとおりである。

```bash
uv run python -m pytest experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py experiments/pysim/qa/tier3_plugins/jit/test_x64_jit.py -q
uv run python docs/components/tier3_plugins/formal/jit_trace_execution_model.py
```

TEST-JITR-61は`test_jitr_61_a_trace_that_would_overflow_the_operand_stack_runs_on_the_interpreter`で検査する。Native InterpreterとJIT有効RuntimeEngineの両方が`OPERAND_STACK_CAPACITY`を報告することを確認する。任意の`AssertionError`を容量超過の証拠として受理しない。先行する容量内の呼出しでは実JIT実行も確認する。この試験だけから物理メモリの全容量外書込みがないことを証明しない。

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- TEST-JITR-44は対象traceの実行と最終結果を確認する。独立した終了blockの試験ではNative trace実行1回とC++ return handler呼出し1回を診断値で確認する。最初のRuntime境界で論理PCの`-1`、Native contextの`0xffffffff`、共有stackの戻り値60、未完了のcall frameを直接観測する。次のRuntime境界でframe除去と関数完了を確認し、traceとhandlerの件数が増えないことを検査する。

- ARMv8-MのTOS/NOS物理レジスタ割当とtrace境界仕様はTBD。
- [`jit_cache_model.py`](docs/components/tier3_plugins/formal/jit_cache_model.py)による抽象的なW^X不変条件、3面キャッシュ代謝、2-bit FSMの形式検証そのもの。
- ARMv8-Mの物理メモリ保護機構、JIT配置、命令同期、実機レイテンシ（すべてTBD）。

### コンパイル候補の処理予算
<!-- traceability: {ADR_JitCompileScheduling} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-80 | 通常予算は候補処理数に適用する | 固定キューに成功、失敗、既存traceによるスキップ候補がある | 協調境界のidle hookへ0、1、キュー件数未満の予算を渡す | 成否によらず候補処理で1件消費する。処理数は予算以下であり、未処理候補を保持する | `jit_runtime.md`「コンパイル時期と候補処理予算」 |
| TEST-JITR-81 | 満杯時の全件処理 | 固定キューが容量に達する。通常予算は容量未満である | 満杯契機の候補処理を実行する | 通常予算の例外として固定容量ぶんをその場で全件処理する。失敗・スキップを含めてキューが空となる | `jit_runtime.md`「コンパイル時期と候補処理予算」 |

TEST-JITR-80の候補数予算は現行の成功数予算へ未追従である。TEST-JITR-81は現行の全件処理を採用契約として直接検証するためのケースである。対応する実行テストと形式モデルの追加・更新は未完了とする。
