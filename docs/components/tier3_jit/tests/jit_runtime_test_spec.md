# JITランタイム (キャッシュ・ホットスポット検出) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier3_jit/jit_runtime.md`
関連正本: `docs/components/tier3_jit/jit_compiler.md`（§4.1「トレース・チェイニング」「バッチコンパイル」はjit_runtimeと共同責務）
参考実装: `docs/components/tier2_runtime/concepts/runtime_engine_concept.py`（統合シミュレーション。`docs/components/tier3_jit/concepts/stack_cache_concept.py`は未読のため別途確認要）

WASM PC→ネイティブコードの3段検索（カードマーキング→Radix→二分探索）、2-bitホットスポット検出、3面世代交代キャッシュ（Active/Warm/Oldest）、Oldest-Only Promotion、局所チェイン解決(O(k))、MPU W^X保護を検証する。

## 2. テストケース一覧

### 2-bitカードマーキング (jit_compiler.md §3.1, runtime_engine_concept.py §2)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITR-01 | カードは命令単位ではなくカード単位 | 同一64バイトカード内の2つの異なる命令オフセット | 一方をtouch | 他方も同じ状態を共有する（カード粒度） | jit_runtime.md §3.1, runtime_engine_concept.py `test_card_granularity_not_function_granularity` |
| JITR-02 | 状態遷移: UNEXECUTED→EXECUTED→HOT | 新規カード | 1回touch、2回目touch | 1回目でEXECUTED、2回目でHOT | §4.2 状態遷移図 |
| JITR-03 | COMPILED後のtouchは状態を変えない | カードがCOMPILED | touch | 状態はCOMPILEDのまま | runtime_engine_concept.py `test_hotspot_bitmap_pure_2bit_state_transitions` |
| JITR-04 | 評価(Eviction)でEXECUTEDへ戻る | カードがCOMPILED、対応トレースがキャッシュから追い出される | `mark_evicted` | 状態がEXECUTEDに戻り、再コンパイル可能になる | §4.2, runtime_engine_concept.py `test_eviction_makes_the_card_recompilable` |
| JITR-05 | ベーシックブロック先頭PCのみ履歴記録 | 命令数の多いベーシックブロック | 実行 | 履歴リングへのエントリはブロックにつき1件のみ | runtime_engine_concept.py `test_only_basic_block_heads_are_recorded` |

### ホットスポット判定 (yield時) と バッチコンパイル (§4.1)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITR-10 | 検出とコンパイルは非同期（実行をブロックしない） | カードがHOTになる | 実行継続中にコンパイル回数を確認 | yield/idle前は`compilations == 0`（インライン同期コンパイルをしない） | jit_compiler.md ADR_SafeQueuingOnHotMiss, runtime_engine_concept.py `test_compilation_is_deferred_to_the_yield_and_idle_hook` |
| JITR-11 | yield時に履歴を走査しHOTカードをキューへ | HOTなカードのPCが履歴に記録済み | `on_yield`相当を呼ぶ | 該当PCがコンパイル待ち列(LIFO)に追加される | jit_compiler.md §4.1「ホットスポット判定」 |
| JITR-12 | LIFO順でのバッチコンパイル | キューに複数PC | idle_hookを実行 | 後入れのPCから先にコンパイルされる | `{JIT_ReverseCompilationOrder}`, runtime_engine_concept.py `test_lifo_compile_queue_order` |
| JITR-13 | コンパイル待ち列投入後もカード状態はCOMPILEDのまま変えない(検索ミス時) | COMPILED状態でActive/Warm/Oldestすべてmiss | 検索を実行 | NULL返却＋キュー投入されるが、カード状態はCOMPILEDから変化しない | jit_compiler.md §5.1 ケース7, `{ADR_SafeQueuingOnHotMiss}` |

### 3段検索・3面キャッシュ (jit_compiler.md §5.1直交表)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITR-20 | UNEXECUTED/EXECUTED/HOTは即座にインタープリタ継続 | カード状態がCOMPILED未満 | lookup | 事前フィルタで即終了、キャッシュ検索を行わない | §5.1 ケース1-3 |
| JITR-21 | Activeヒット | トレースがActiveに存在 | lookup | 昇格なしでネイティブコード実行 | §5.1 ケース4 |
| JITR-22 | Warmヒット（無償観測、昇格なし） | トレースがWarmに存在 | lookup | Warmのままコピーせず実行される。`promotions`カウンタは変化しない | §5.1 ケース5, runtime_engine_concept.py `test_warm_hit_does_not_promote_but_oldest_hit_does` |
| JITR-23 | Oldestヒットで即座にActiveへ昇格 | トレースがOldestに存在 | lookup | Active領域へコピーされてから実行、`promotions`が増加 | §5.1 ケース6, `{JIT_OldestOnly_Promote}` |
| JITR-24 | 全ミスでNULL返却＋キュー投入 | Active/Warm/Oldestいずれにも存在しない、カードはCOMPILED | lookup | NULLを返し、カード状態は変更しない | §5.1 ケース7 |
| JITR-25 | キャッシュ満杯時の3面ローテーション | Active満杯 | 新規insert | Oldestをpurgeして新Activeにし、Active→Warm、Warm→Oldestへスライド。同時にchain_nextのダングリング掃引 | §5.1 ケース8 |

### トレース・チェイニング (jit_compiler.md §4.1「トレース・チェイニング」)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITR-30 | 新規コンパイル時のchain_next初期値はインタープリタ復帰 | 新規トレース作成 | chain_nextを確認 | `None`（インタープリタスタブ相当） | runtime_engine_concept.py `test_chain_next_defaults_to_interpreter_return` |
| JITR-31 | フォールスルー先がActive/Warmに存在すれば直結 | コンパイル対象の`next_pc`がWarmに常駐 | idle_hookでコンパイル | `chain_next`がそのアドレスに設定される（Warmも常駐コードとして直結対象） | jit_compiler.md §4.1「新規コンパイル時」, runtime_engine_concept.py `test_idle_hook_chains_into_a_warm_resident_successor` |
| JITR-32 | Oldestへは直結しない | `next_pc`がOldestに存在 | idle_hookでコンパイル | `chain_next`は`None`のまま（Oldestは次のrotateで即purgeされ得るため） | runtime_engine_concept.py `test_idle_hook_never_chains_into_the_oldest_bank` |
| JITR-33 | ループ背進辺(loops_to)は連結対象外 | ループを含むトレース | コンパイル | 背進辺にはSafepointポーリングのみが埋め込まれ、無条件チェインしない（条件判定はスタックpopを伴う`_next_pc`に委ねる） | jit_compiler.md §4.1「ループの背進辺」 |
| JITR-34 | 局所再チェイニング: 昇格した場合 | ターゲットがOldestからActiveへPromoteされた直後にrotateが発生 | rotate | 被チェインソースが昇格先アドレスへ再チェインされ、インタープリタへは落ちない | jit_compiler.md §4.1「局所再チェイニングとアンリンク」, runtime_engine_concept.py `test_rotate_rechains_when_target_was_promoted_to_active` |
| JITR-35 | 局所アンリンク: 完全にEvictされた場合 | ターゲットがOldestで昇格されないままpurge | rotate | 被チェインソースの`chain_next`が`None`にアンパッチされる | runtime_engine_concept.py `test_rotate_unlinks_chains_when_oldest_is_purged` |
| JITR-36 | Warm→Oldest遷移だけではアンリンクしない | ターゲットがWarmからOldestへ移動（まだ生存） | rotate | チェインは維持される（Oldestでもまだ実行可能なコードとして常駐） | runtime_engine_concept.py `test_rotate_unlinks_chains_when_oldest_is_purged`の中間アサーション |
| JITR-37 | O(k)有界: 全走査をしない | 大量のトレースがキャッシュに存在 | rotate時の被チェイン解決を計測/確認 | purgeされるOldestバンクの`inbound_sources`に登録されたソース(k件)のみを参照する実装になっている | jit_compiler.md §4.1「全走査オーバーヘッドO(N)を完全排除」 |

### MPU W^X保護 (jit_compiler.md §7.2)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITR-40 | RO_X状態での書き込みは違反 | `mpu_attr == RO_X`（既定） | insert等の書き込み操作を試みる | `MPUFault("W^X VIOLATION")` | runtime_engine_concept.py `test_mpu_wx_is_enforced_in_both_directions` |
| JITR-41 | RW_XN状態での実行は違反 | `begin_patch()`後 | `require_executable()` | `MPUFault("W^X VIOLATION")` | 同上 |
| JITR-42 | commit_patchでのバリア発行 | `begin_patch`→書き込み→`commit_patch` | 実行 | `__DSB();__ISB();`相当のバリアが発行され(`barrier_flushes`増加)、状態がRO_Xに戻る | jit_compiler.md §7.2「MPU W^X 保護」 |
| JITR-43 | 書き込みと実行の同時許可(RWX)の排除 | 任意の状態 | 状態機械を確認 | RO_XとRW_XN以外の状態(RWX)が存在しない | jit_compiler.md §7.2 |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- `docs/components/tier3_jit/concepts/stack_cache_concept.py`（未読。TOS/NOSキャッシュのトレース境界での書き戻しに関するテストが含まれる可能性が高く、別途読了・反映が必要）。
- `../formal/jit_cache_model.py`による3面キャッシュ代謝・MPU W^X・2-bit FSMの形式検証そのもの。
- Cortex-M33実機でのPMSAv8 MPU切り替えの実際のレイテンシ。
