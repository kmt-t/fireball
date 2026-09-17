# JITランタイム (キャッシュ・ホットスポット検出) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`jit_runtime.md`](docs/components/tier3_jit/jit_runtime.md)
関連正本: [`jit_compiler.md`](docs/components/tier3_jit/jit_compiler.md)（{JIT_LazyChaining}、{JIT_CopyAndPatch}はjit_runtimeと共同責務）
統合参考実装: [`runtime_engine_concept.py`](docs/components/tier2_runtime/concepts/runtime_engine_concept.py)。スタックキャッシュ固有の参考実装は [`stack_cache_concept.py`](docs/components/tier3_jit/concepts/stack_cache_concept.py) とする。
3面キャッシュ・ホットスポット・チェイニングの統合ケースは `runtime_engine_concept.py` および pysim のJITランタイムテストを参照し、スタックキャッシュ固有ケースは `stack_cache_concept.py` で検証する。

WASM PC→ネイティブコードの3段検索（カードマーキング→Folding XOR高速キャッシュ→ソート済みバンク内二分探索）、2-bitホットスポット検出、連続8KB JIT領域（非エビクション共通コード2KB + Active/Warm/Oldest各2KB）、Oldest-Only Promotion、局所チェイン解決、MPU W^X保護を検証する。

## 2. テストケース一覧

### 2-bitカードマーキング (jit_runtime.md (Card Marking), runtime_engine_concept.py)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-01 | カードは命令単位ではなくカード単位 | 同一64バイトカード内の2つの異なる命令オフセット | 一方をtouch | 他方も同じ状態を共有する（カード粒度） | jit_runtime.md , runtime_engine_concept.py `test_card_granularity_not_function_granularity` |
| TEST-JITR-02 | 状態遷移: UNEXECUTED→EXECUTED→HOT | 新規カード | 1回touch、2回目touch | 1回目でEXECUTED、2回目でHOT | jit_runtime.md (Card Marking) |
| TEST-JITR-03 | COMPILED後のtouchは状態を変えない | カードがCOMPILED | touch | 状態はCOMPILEDのまま | runtime_engine_concept.py `test_hotspot_bitmap_pure_2bit_state_transitions` |
| TEST-JITR-04 | 評価(Eviction)でUNEXECUTEDへ戻る（EXECUTEDではない） | カードがCOMPILED、対応トレースがキャッシュから追い出される | `mark_evicted` | 状態がUNEXECUTEDに戻る。**実装の勘所**: EXECUTEDへ戻すと、境界的にしかホットでないコードが「1回touchすればまたHOT」という状態になり、コンパイルとEvictを永久に繰り返す（スラッシング）。追い出された以上は`UNEXECUTED→EXECUTED→HOT`のウォームアップを最初からやり直させ、本当に持続的にホットなコードだけを再コンパイル対象にする | runtime_engine_concept.py `test_eviction_makes_the_card_recompilable`, `test_hotspot_bitmap_pure_2bit_state_transitions`, [`test_jit_runtime.py`](experiments/pysim/qa/tier3_jit/test_jit_runtime.py) `test_hotspot_05_3bank_cache_rotation_and_eviction_resets_card` |
| TEST-JITR-05 | ベーシックブロック先頭PCのみ履歴記録 | 命令数の多いベーシックブロック | 実行 | 履歴リングへのエントリはブロックにつき1件のみ | runtime_engine_concept.py `test_only_basic_block_heads_are_recorded` |
| TEST-JITR-06 | 最小トレース長未満のブロックはカード共有を起こさず永久に追跡されない | 同一カード内に2つの短いベーシックブロック（推定コンパイル後サイズがカード幅未満） | 両方のPCを繰り返し実行 | どちらのカード状態もUNEXECUTEDのまま変化せず、コンパイル待ち列にも一切追加されない。**実装の勘所**: カードの2-bit状態は本質的に「このカードに対応する1個のトレース」しか表現できない。2つの異なるベーシックブロックの先頭PCが同一カードを共有すると、一方をコンパイルした結果が他方にも「コンパイル済み」として誤って見え、あるいは一方のトレースをEvictした結果が他方の`COMPILED`状態を誤ってリセットしてしまう。追跡対象を「カード幅以上のブロックに限定する」ことで、追跡される任意の2ブロックの先頭PCは常に1カード分以上離れることが保証され（隣接ブロックの先頭は現在のブロックの終端以降にしか来ないため）、カードが複数ブロックに共有されることは構造的に発生しなくなる | jit_runtime.md 「最小トレース長フィルタ」, runtime_engine_concept.py `test_short_blocks_never_tracked_avoiding_card_aliasing`, [`test_jit_runtime.py`](experiments/pysim/qa/tier3_jit/test_jit_runtime.py) `test_hotspot_06_short_blocks_never_tracked_avoiding_card_aliasing` |
| TEST-JITR-07 | コンパイル待ち列処理時、既にキャッシュ常駐のPCは再コンパイルしない | PCがキャッシュに常駐済み（カード状態はまだCOMPILEDになっていない）だが同PCがコンパイル待ち列に積まれている | idle_hookを実行 | コンパイラは呼ばれず、カード状態のみCOMPILEDへ同期される。**実装の勘所**: カードの粗い状態ではなく、実際のキャッシュ常駐こそが「このPCがコンパイル済みか」の一次情報源である（例: 前回のコンパイルが`mark_compiled`を反映する前に同じPCが再度キューへ積まれた場合、カード状態だけを信じると二重コンパイルになる） | jit_runtime.md 「キュー処理時のキャッシュ再確認」, runtime_engine_concept.py `test_idle_hook_skips_recompiling_an_already_resident_trace`, [`test_jit_runtime.py`](experiments/pysim/qa/tier3_jit/test_jit_runtime.py) `test_hotspot_07_idle_hook_skips_recompiling_an_already_resident_trace` |
| TEST-JITR-08 | JITエントリテーブル（バンクのトレース一覧）は常にhead_pcでソートされ、削除は削除フラグ（トンビストーン）で行う | 複数のトレースをPC順不同で挿入し、1件削除後に同じPCを再挿入 | 挿入・削除・再挿入後にトレース一覧と該当PCの取得結果を確認 | 一覧は常にhead_pc昇順。削除直後は取得結果がNone。再挿入は既存tombstone枠を再利用し、取得結果は新しいトレースそのものになる。**実装の勘所**: JITはエントリ数が少ない疎なキー集合なのでRadix索引を持たず、ソート済み固定容量配列を二分探索する。新規キーの挿入時は配列をシフトし、削除時はtombstone化する。配列容量と挿入コストは2KBバンク上限により制限される | jit_runtime.md「JITエントリ表」, [`jit_cache.py`](experiments/pysim/tier3_jit/jit_cache.py), [`test_jit_runtime.py`](experiments/pysim/qa/tier3_jit/test_jit_runtime.py) |
| TEST-JITR-09 | OldestヒットによるPromotion時、被チェイン登録（inbound_sources）は昇格先バンクへ引き継がれる | トレースBがバンクXでチェイン元Aから被チェインされている状態で、Bのみが後にOldestからPromoteされる | Bをlookupで昇格させた後、AとBそれぞれの所属バンクを確認 | 昇格前のバンクXにはもうAの登録が残っておらず、Bの新しい所属バンクにAの登録が引き継がれている。**実装の勘所**: 被チェイン登録はトレースではなくバンクに紐づく。Promotionでトレースだけをバンク間で移動させ、被チェイン登録を移し忘れると、後で元のバンクがrotateでpurgeされた際に「Aの登録がどこにあるか」を見失い、Bが本当にpurgeされた時点でAのchain_nextが復帰スタブへアンパッチされず、ダングリング（存在しないアドレスへのジャンプ）になる | [`test_jit_runtime.py`](experiments/pysim/qa/tier3_jit/test_jit_runtime.py) `test_jitr_promote_transfers_inbound_sources_avoiding_dangling_chain` |
| TEST-JITR-09b | 実行ループはキャッシュ検索の前に必ずカードビットマップをO(1)確認する | 通常実行（大半のブロックは未コンパイル） | 多数回ブロックを実行 | `cache.lookup()`が呼ばれるのはカード状態が`COMPILED`である場合のみ。**実装の勘所**: 大半のブロックはコンパイルされないため、カードビットマップのO(1)チェックを先に行わずにキャッシュ検索（バンク横断探索）を毎回行うと、ミス時のペナルティがヒット時の利得を上回ってしまう。ビットマップが`COMPILED`と言わない限り、キャッシュ検索自体に触れてはならない | jit_runtime.md 「カードマーキング確認」, [`test_jit_runtime.py`](experiments/pysim/qa/tier3_jit/test_jit_runtime.py) `test_jitr_bitmap_checked_before_cache_lookup` |

### ホットスポット判定 (yield時) と バッチコンパイル

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-10 | 検出と通常コンパイルの遅延、および満杯時の即時drain | カードがHOTになる | 通常時とキュー満杯時を分けて実行 | 通常時はyield/idleまでコンパイルせず、キュー満杯時だけその場でLIFO drainしてキューを空ける。キュー容量を超えて保持しない | jit_compiler.md ADR_SafeQueuingOnHotMiss, `{JIT_ReverseCompilationOrder}` |
| TEST-JITR-11 | yield時に履歴を走査しHOTカードをキューへ | HOTなカードのPCが履歴に記録済み | `on_yield`相当を呼ぶ | 該当PCがコンパイル待ち列(LIFO)に追加される | jit_compiler.md 「ホットスポット判定」 |
| TEST-JITR-12 | LIFO順でのバッチコンパイル | キューに複数PC | idle_hookを実行 | 後入れのPCから先にコンパイルされる | `{JIT_ReverseCompilationOrder}`, runtime_engine_concept.py `test_lifo_compile_queue_order` |
| TEST-JITR-13 | 常駐トレース再確認とカード状態の整合 | コンパイル要求PCがキューに重複登録済み、またはキャッシュ常駐済み | idle_hookを実行 | 常駐済みなら再コンパイルせず`COMPILED`へ同期する。全キャッシュmissはコンパイル要求として扱い、eviction後のPCを`COMPILED`のまま放置しない | `{ADR_SafeQueuingOnHotMiss}` |
| TEST-JITR-14 | コンパイル失敗時の恒久的候補除外 | `compile_trace`が失敗を返す | idle_hookを実行 | Trackable Maskの対象ビットだけを解除し、カードを`COMPILED`へ設定しない。同一モジュール内で再履歴・再キュー・再コンパイルを行わない | `{TrackableBlockMask}` |
| TEST-JITR-15 | eviction／明示的flush後の再計測 | 常駐トレースがevictまたはJIT cache flushされる | そのPCを再実行 | キャッシュ参照はInterpreterへ戻り、カードは`UNEXECUTED`からhotnessを再計測する。候補性は維持される | `TEST-JITR-04`, `{TrackableBlockMask}` |

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
| 8 | (書き込み時) | **満杯** | - | - | 3面リングローテーション: Oldest を Purge して新 Active に、Active→Warm、Warm→Oldest。同時に `chain_next` のダングリング掃引を行う | TEST-JITR-25 |

#### 3段検索テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-20 | UNEXECUTED/EXECUTED/HOTは即座にインタープリタ継続 | カード状態がCOMPILED未満 | lookup | 事前フィルタで即終了、キャッシュ検索を行わない | 直交表 ケース1-3 |
| TEST-JITR-21 | Activeヒット | トレースがActiveに存在 | lookup | 昇格なしでネイティブコード実行 | 直交表 ケース4 |
| TEST-JITR-22 | Warmヒット（無償観測、昇格なし） | トレースがWarmに存在 | lookup | Warmのままコピーせず実行される。`promotions`カウンタは変化しない | 直交表 ケース5, runtime_engine_concept.py `test_warm_hit_does_not_promote_but_oldest_hit_does` |
| TEST-JITR-23 | Oldestヒットで即座にActiveへ昇格 | トレースがOldestに存在 | lookup | Active領域へコピーされてから実行、`promotions`が増加 | 直交表 ケース6, `{JIT_OldestOnly_Promote}` |
| TEST-JITR-24 | 全ミス後のInterpreter復帰と再計測 | Active/Warm/Oldestいずれにも存在しない | lookup | Interpreterへ戻り、eviction／flushでカードを`UNEXECUTED`へ戻してからhotnessを再計測する。キャッシュmissだけでカードを`COMPILED`に固定しない | 直交表 ケース7 |
| TEST-JITR-25 | キャッシュ満杯時の3面ローテーション | Active満杯 | 新規insert | Oldestをpurgeして新Activeにし、Active→Warm、Warm→Oldestへスライド。同時にchain_nextのダングリング掃引 | 直交表 ケース8 |
| TEST-JITR-26 | Direct-Mapped Folding XOR JIT Cache による O(1) 一発ヒット | トレースがキャッシュに存在 | lookup | 2-bit スロット選択を行う Folding XOR Hash で 4 スロットテーブルにヒットし、バンク二分探索を行わずに O(1) で即時返却される | `jit_runtime.md` , `{DirectMappedJIT4}` |

### トレース・チェイニング

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-30 | 新規コンパイル時のchain_next初期値はインタープリタ復帰 | 新規トレース作成 | chain_nextを確認 | `None`（インタープリタスタブ相当） | runtime_engine_concept.py `test_chain_next_defaults_to_interpreter_return` |
| TEST-JITR-31 | フォールスルー先がActive/Warmに存在すれば直結 | コンパイル対象の`next_pc`がWarmに常駐 | idle_hookでコンパイル | `chain_next`がそのアドレスに設定される（Warmも常駐コードとして直結対象） | jit_compiler.md 「新規コンパイル時」, runtime_engine_concept.py `test_idle_hook_chains_into_a_warm_resident_successor` |
| TEST-JITR-32 | Oldestへは直結しない | `next_pc`がOldestに存在 | idle_hookでコンパイル | `chain_next`は`None`のまま（Oldestは次のrotateで即purgeされ得るため） | runtime_engine_concept.py `test_idle_hook_never_chains_into_the_oldest_bank` |
| TEST-JITR-33 | ループ背進辺(loops_to)は連結対象外 | ループを含むトレース | コンパイル | 背進辺にはSafepointポーリングのみが埋め込まれ、無条件チェインしない（条件判定はスタックpopを伴う`_next_pc`に委ねる） | jit_compiler.md 「ループの背進辺」 |
| TEST-JITR-34 | 局所再チェイニング: 昇格した場合 | ターゲットがOldestからActiveへPromoteされた直後にrotateが発生 | rotate | 被チェインソースが昇格先アドレスへ再チェインされ、インタープリタへは落ちない | jit_compiler.md 「局所再チェイニングとアンリンク」, runtime_engine_concept.py `test_rotate_rechains_when_target_was_promoted_to_active` |
| TEST-JITR-35 | 局所アンリンク: 完全にEvictされた場合 | ターゲットがOldestで昇格されないままpurge | rotate | 被チェインソースの`chain_next`が`None`にアンパッチされる | runtime_engine_concept.py `test_rotate_unlinks_chains_when_oldest_is_purged` |
| TEST-JITR-36 | Warm→Oldest遷移だけではアンリンクしない | ターゲットがWarmからOldestへ移動（まだ生存） | rotate | チェインは維持される（Oldestでもまだ実行可能なコードとして常駐） | runtime_engine_concept.py `test_rotate_unlinks_chains_when_oldest_is_purged`の中間アサーション |
| TEST-JITR-37 | bank purgeとchain unlinkの処理量 | bank内nスロット、被チェイン元k件 | PySimのrotate処理を計測/確認 | `clear()`がnスロットを走査し、被チェイン元はbank別二分探索を行う。処理量は`O(n + k log n)`で、固定容量により上限がある。ターゲット実装は別途計算量を定義する | `jit_runtime.md` 世代交代ローテーション |

### MPU W^X保護

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-40 | RO_X状態での書き込みは違反 | `mpu_attr == RO_X`（既定） | insert等の書き込み操作を試みる | `MPUFault("W^X VIOLATION")` | runtime_engine_concept.py `test_mpu_wx_is_enforced_in_both_directions` |
| TEST-JITR-41 | RW_XN状態での実行は違反 | `begin_patch()`後 | `require_executable()` | `MPUFault("W^X VIOLATION")` | 同上 |
| TEST-JITR-42 | commit_patchでのバリア発行 | `begin_patch`→書き込み→`commit_patch` | 実行 | `__DSB();__ISB();`相当のバリアが発行され(`barrier_flushes`増加)、状態がRO_Xに戻る | jit_compiler.md 「MPU W^X 保護」 |
| TEST-JITR-43 | 書き込みと実行の同時許可(RWX)の排除 | 任意の状態 | 状態機械を確認 | RO_XとRW_XN以外の状態(RWX)が存在しない | jit_compiler.md |

### 関数戻り値とInterpreter境界

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITR-44 | JIT終了ブロックからInterpreterのreturnハンドラへ復帰 | JITトレースが`return`直前で終了する関数 | トレース後のInterpreter PCと共有スタックを確認する | JITはC/AAPCS戻り値レジスタを使わず共有オペランド領域へ結果を書き、バイトコード上の`return`命令位置へ戻る。Interpreterのreturnハンドラだけがsentinelを生成する | `runtime_interpreter.md` 関数復帰の番兵, pysim `test_jitr_terminal_trace_returns_to_interpreter_return_handler` |
| TEST-JITR-45 | JIT実行calleeの戻り値をcallerへ引き継ぐ | callerが反復してcalleeを呼び、calleeのブロックがJITコンパイル済み | `RuntimeEngine.run()`でcallerを実行 | calleeの結果は共有オペランド領域に残り、InterpreterがRETURN sentinelを消費して関数呼出し記述子を取り除き、callerがその結果を利用する | `runtime_interpreter.md` 関数復帰の番兵, pysim `test_jitr_nested_wasm_call_keeps_callee_result_on_shared_operand_stack` |
| TEST-JITR-46 | JIT有効時のWASMスカラー結果型維持 | i32/i64/f32/f64のトップレベル関数 | JIT有効の`RuntimeEngine.run()`を各関数に対して反復実行 | JITコンパイル可能な結果はJIT経由、未対応型はInterpreterへ委譲し、全結果の値を保持する。f32/f64を整数へ変換しない | `runtime_interpreter.md` 関数復帰, pysim `test_jitr_runtime_engine_preserves_typed_top_level_results` |
| TEST-JITR-47 | import/host callをInterpreter境界で実行 | ゲスト関数がWASM importを呼び、戻り値を後続命令で使う | 呼び出しブロックのコンパイルとRuntimeEngine実行を確認する | callを含むトレースはコンパイルされず、Interpreterがhost関数を呼ぶ。戻り値は共有オペランド領域に積まれ、callerの後続命令が消費する | `runtime_interpreter.md` 関数呼び出し境界, pysim `test_jitr_host_import_stays_on_interpreter_runtime_boundary` |
| TEST-JITR-48 | RuntimeEngine同期境界でTrapを正常結果と混同しない | ゲスト関数が整数除算ゼロTrapを発生させる | `RuntimeEngine.run()`で関数を実行する | Trap codeを呼び出し側へ伝え、`None`を正常な結果として返さない | `runtime_interpreter.md` 命令実行中のTrap, pysim `test_jitr_runtime_engine_surfaces_guest_trap_at_sync_boundary` |
| TEST-JITR-49 | JIT有効時のif/else両経路とループ脱出 | ループ内にthen/elseと`br_if`を持つ関数 | 複数入力をInterpreter専用とJIT有効RuntimeEngineで実行し比較する | then/else両方を通る入力を含め、全結果が一致し、JITトレースが実行される | `{JIT_RuntimeAPI_Fallback}`, pysim `test_jitr_if_else_loop_matches_interpreter_after_jit_compilation` |
| TEST-JITR-50 | `br_table`全分岐先のInterpreter境界 | 既定先を含む3分岐先を持つ関数 | 各selectorでInterpreter専用とJIT有効RuntimeEngineを実行する | 直前のJITトレースは`br_table`命令PCで終了し、分岐命令自体はInterpreterが解決する。case 0/1/defaultの全結果が一致する | `{JIT_RuntimeAPI_Fallback}`, pysim `test_jitr_br_table_falls_back_and_preserves_every_target` |
| TEST-JITR-51 | 異種型混在時のDROP幅とJIT安全フォールバック | 共有operand stack上にi32/f32/i64/f64値を積み、広幅値を`drop`する関数 | JITコンパイル可否とJIT有効RuntimeEngineの結果を検証する | i64/f64のDROPが2 raw wordを除去する。未対応の混在トレースはコンパイルされずInterpreterへ委譲し、後続i32演算とreturn値を保つ | `{ADR_TosCacheAsymmetry}`, pysim `test_jitr_mixed_typed_stack_declines_jit_without_losing_drop_widths` |
| TEST-JITR-52 | 混在型callee戻り値とJIT/Interpreter共有stack境界 | i32/f32/i64/f64を返すcalleeを順に呼び、i32 calleeをJIT hotにするcaller | callerをJIT有効RuntimeEngineで反復実行する | 各callee結果は正しいraw幅で共有stackへ戻り、i64/f64 drop後のi32計算結果が保たれる。途中のJIT callee復帰で関数末尾PCをreturn sentinelへ正規化する | `runtime_interpreter.md` 関数復帰, `{JIT_RuntimeAPI_Fallback}`, pysim `test_jitr_mixed_typed_callee_returns_keep_the_shared_stack_synchronized` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-JITR-01 | キャッシュ常駐性の一次情報源（二重コンパイル防止） | カード状態は未COMPILEDだがキャッシュ内に同一PCが常駐済み | `idle_hook` のキュー処理を実行 | コンパイラは起動されず、カード状態のみ `COMPILED` へ同期される。**実装の勘所**: カードの粗い2-bit状態のみを信じてキューを盲目的に処理すると、同一PCが短期間に複数回キューイングされた場合に二重コンパイルが発生し、メモリを浪費する | `jit_runtime.md` 「キュー処理時のキャッシュ再確認」 |
| GOTCHA-JITR-02 | Oldest昇格時の被チェイン登録移管とダングリング防止 | トレースB（Oldest所属）が別トレースAから被チェインされている状態でBをActiveへ昇格 | Bを昇格後にOldestバンクをローテーション破棄 | 昇格先バンクへAの被チェイン登録が移管されており、Oldest破棄時にAのチェイン先が誤ってアンパッチ（NULL化）されず、直結が維持される。**実装の勘所**: 被チェイン逆引きテーブル（`inbound_sources`）はバンク管理であるため、トレース本体のみを移動して登録の移管を怠ると、バンク消滅時にダングリングジャンプまたは誤ったアンパッチが発生する | `jit_runtime.md` 「局所再チェイニングとアンリンク」 |
| GOTCHA-JITR-03 | LIFO逆順コンパイルによる即時チェイニング最大化 | 連続するブロック（A→B→C）が順にキューへ投入された状態 | `idle_hook` を実行 | 後入れの C、次いで B、最後に A の順（LIFO）でコンパイルされ、先行ブロックがコンパイルされた時点で後続ブロックが既にキャッシュ常駐しているため、即時チェイニングが100%成功する。**実装の勘所**: FIFOでコンパイルすると、先行ブロックコンパイル時に後続がまだ存在しないためチェインが成立せず、余計なインタープリタフォールバックを招く | `jit_runtime.md` , `{JIT_ReverseCompilationOrder}` |
| GOTCHA-JITR-05 | 3面ローテーション・フラッシュ時の Folding XOR スロット無効化 | rotate() または flush_all() 実行時 | rotate() を実行し直後に旧PCでlookup | 4スロットの高速キャッシュが不可分にクリアされ、古いバンクへのダングリング参照を起こさず、必要に応じて昇格（Promotion）処理が正しく発火する。**実装の勘所**: 高速スロットをクリアしないと、Oldest に遷移したトレースが高速スロット経由で返却されてしまい、昇格処理がスキップされ、次の rotate() で実体が消滅した後にダングリング状態へ陥る | `jit_runtime.md`  |
| GOTCHA-JITR-06 | JIT脱出後の制御フレーム内容不正利用防止 | ループがif文の中に入れ子になっており（例: ループの中でif文が成立した時だけ別のループを回す構造）、内側ループの脱出条件ブロックがコンパイル済み | 内側ループ・外側ループの双方が複数回実行されるまで駆動を継続する | 外側ループ自身の分岐がインタープリタで正しく外側ループの先頭へ解決され、結果値が期待通り一致する。**実装の勘所**: JITによる脱出は、ブロック・ループ・ifの開始や終了に対応するインタープリタ側のフレーム操作を一切経由しない。そのため、以前インタープリタが直接その構文を実行していた際に積まれた制御フレームが、JIT側でその構文を抜けた後も回収されずに残ってしまうことがある。この残留は深さが多すぎる方向にも少なすぎる方向にも起こり得るため、フレーム深さの切り詰めだけでは正しさを保証できない。よってインタープリタに実行が戻った際の `br`/`br_if`/`else` の飛び先解決は制御フレームの中身を一切参照せず、静的解析で解決済みのブロックの後続アドレス・分岐先アドレスを直接使う。深さの切り詰め自体は、JITがフレーム操作を代行し続けることでスタックが際限なく伸びるのを防ぐ安全策としてのみ行う | `{JIT_RuntimeAPI_Fallback}`, `GOTCHA-INTP-06` |
| GOTCHA-JITR-07 | 短小ブロック判定の符号（後方分岐ブロックの誤除外） | ループ本体の末尾が、自分自身の先頭よりも前のアドレスへ戻る後方分岐になっている | 短小ブロックの足切り判定を経て駆動を反復実行する | 後方分岐を持つブロックも足切り判定を正しく通過し、十分な命令数を持てばコンパイル対象になる。**実装の勘所**: 足切り判定を「後続アドレスと自分自身の先頭との差分」で代用すると、後方分岐ブロックでは差分が常に負値になり「短すぎる」と誤判定されて、記録・コンパイルのいずれの対象からも永久に外れてしまう。関数内で最も実行頻度の高いブロック（ループ本体そのもの）が二度とコンパイルされないまま、常にインタープリタ実行に取り残される | `jit_runtime.md` |
| GOTCHA-JITR-08 | return直前のJIT終了とInterpreter復帰 | `return`を含むWASM関数 | トレース実行とcallee復帰を確認 | JITは`return`や`RETURN sentinel`を生成せず、終了エピローグで共有オペランド領域／実行コンテキストを同期してInterpreterへ戻る。Interpreterがcalleeの関数呼出し記述子を取り除き、ネスト時はcall helperがsentinelを消費する | `jit_runtime.md`, `runtime_interpreter.md` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- [`stack_cache_concept.py`](docs/components/tier3_jit/concepts/stack_cache_concept.py): TOS/NOSキャッシュ、トレース境界、境界チェック、およびバックエッジSafepointのConcept実装。固有テスト8件を実行済み。
- [`jit_cache_model.py`](docs/components/tier3_jit/formal/jit_cache_model.py)による3面キャッシュ代謝・MPU W^X・2-bit FSMの形式検証そのもの。
- Cortex-M33実機でのPMSAv8 MPU切り替えの実際のレイテンシ。
