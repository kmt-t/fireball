# JIT ランタイム管理 コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} {VERIFY_BENCHMARK}
<!-- evidence:
     formal: formal/jit_cache_model.py
     benchmark: benchmarks/jit_zero_compile_cost_bench.py
     concept: ../tier2_runtime/concepts/runtime_engine_concept.py
     test: docs/qa/tier3_executer/jit_runtime_test_spec.md
-->

## 1. コンセプト
<!-- traceability: {SimpleJITArchitecture} {JIT_MultiBuffer_Cache} {JIT_OldestOnly_Promote} {META_AccessDictionary} {META_BinarySearch} {LowLatencyJIT} {LowOverhead} {HistoryBuffer} {GLOBAL_PeriodicTask} {DirectMappedJIT16} {Runtime_BumpAllocator} -->
JIT ランタイム管理は、WASM PC とネイティブコードの紐付け検索を担当する。3面世代交代コードキャッシュのローテーションも担当する。ホットスポット検出も一括して担う。

実行入口は Tier 3 `Interpreter` のテンプレートメソッド契約と共有する。`JITInterpreter` は `Interpreter.call()` の呼出状態・完了・トラップ・結果検証を継承し、実行ドライバだけを `RuntimeEngine` のJIT／Interpreter統合経路へ差し替える。これにより、Interpreter と JIT の実行経路は同じ公開 `call()` 境界で比較できる。

インタープリタ実行ループ内の検索は3段で構成する。第1段はカードマーキング表 (`bit_view<2>`) による $O(1)$ 事前判定、第2段はDirect-Mapped Folding XORキャッシュ（16スロット）による $O(1)$ 検索、第3段は各バンクのソート済みJITエントリ配列に対する二分探索である。エントリ数が少ないためRadix表は設けず、補助索引のメモリと更新処理を持たない。

3面コードキャッシュはデータ用バンプアロケータとは異なる。データRAM（`RW + XN`）とは分離されている。MPU W^X 制御された専用実行可能セクションから確保される。専用コードアロケータによりハードウェア保護境界が厳格に保たれる。

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} {SimpleJITArchitecture} -->
本コンポーネントは **Tier 3 (詳細リーフコンポーネント: Leaf Component)** に属する。JIT サブシステムのうち実行時検索、3面コードキャッシュ管理、局所アンリンク、ホットスポット検出を担当する。コード生成コアは [`jit_compiler.md`](docs/components/tier3_executer/jit_compiler.md) が担当する。

### 2.1 JIT サブシステムのデコンポジション
<!-- traceability: {JIT_Encoder} {JIT_CopyAndPatch} -->
JITサブシステムは、以下の2つの独立した設計書に責務を分離して構成される。
- **[jit_compiler.md](docs/components/tier3_executer/jit_compiler.md)**: 命令テンプレートを用いたネイティブコード生成および静的命令エンコードを担当する。
- **[jit_runtime.md](docs/components/tier3_executer/jit_runtime.md)**: 実行履歴監視、ホットスポット判定、PC-アドレス変換検索、3面キャッシュローテーションを担当する。 `{SimpleJITArchitecture}` `{JIT_MultiBuffer_Cache}`

### 2.2 実装責務と依存方向
<!-- traceability: {META_ContractImplSplit} {META_StaticDI} {META_3TierSeparation} -->
Tier 2 の `RuntimeEngine` は、JITの内部状態を所有しない。[`runtime_engine.py`](experiments/pysim/tier2_runtime/runtime_engine.py) の `JITRuntime` 契約を介して、モジュール登録、基本ブロック解決、ホットスポット記録、イールド処理、トレース検索、チェイン解決、およびキャッシュ無効化を呼び出す。Tier 2 はカード表、履歴リング、コンパイル待ち列、キャッシュバンク、直接マップ索引の型へ依存しない。

Tier 3 の実装は次の責務に分ける。

| 実装 | 所有する責務 | 依存先 |
| :--- | :--- | :--- |
| [`jit_manager.py`](experiments/pysim/tier3_executer/jit/jit_manager.py) の `JITRuntimeManager` | ホットスポットカード、候補マスク、履歴リング、関数更新表、コンパイル待ち列、ブロック索引、コンパイル起動、トレース検索、チェイン解決 | Tier 2 `JITRuntime` の呼出し形、Loaderの`Module`/`BasicBlock`情報 |
| [`jit_cache.py`](experiments/pysim/tier3_executer/jit/jit_cache.py) | 3面コードキャッシュ、バンク回転、Oldest昇格、局所アンリンク、エントリ索引 | `JITRuntimeManager` からの所有・通知 |
| [`x64_jit.py`](experiments/pysim/tier3_executer/jit/x64_jit.py) | トレースのコード生成とコンパイラ実装 | `JITCompiler` 契約、JIT ABI |
| [`runtime_engine.py`](experiments/pysim/tier2_runtime/runtime_engine.py) | Interpreter/JITの実行境界、vIRQ、実行統計、Tier 3契約の呼出し | `JITRuntime` 契約のみ |

`RuntimeEngine` の生成時に `JITRuntimeManager` を静的に注入する。実行中に実装を差し替える動的プラグイン機構は持たず、構成に選んだTier 3実装だけを実行経路へ合成する。JITを無効にする場合は`JITRuntime`を注入せず、Interpreter経路だけを使用する。

なお、evidenceに記載する [`runtime_engine_concept.py`](docs/components/tier2_runtime/concepts/runtime_engine_concept.py) は、複数コンポーネントを一つの実行可能モデルで検証するための統合概念コードである。製品実装の責務配置は本節の `JITRuntimeManager` と `RuntimeEngine` の分離を正本とする。

## 3. 静的モデル

### 3.1 データ構造
- **統一プログラムカウンタ (`UnifiedPC` / `wasm_pc_t`)**: モジュール全体の全関数・全命令を一意に識別する 32ビット整数である。
  - **構造**: `(func_index << 16) | (bytecode_offset & 0xFFFF)`
    - **上位 16ビット (`func_index`)**: モジュール内の関数インデックス（0 〜 65,535）。
    - **下位 16ビット (`bytecode_offset`)**: 当該関数のバイトコード内オフセット（0 〜 65,535 バイト）。
  - **役割**: 複数関数を含む WASM モジュールにおいて、関数間の PC 衝突を防止する。一意な追跡とディスパッチを保証する。
- **`JitEntryIndex`**: WASMオフセットとネイティブコードの対応付け、および 4 段高速検索ロジックをカプセル化した主要クラスである。
- **カードマーキング表 (Card Marking Table)**: 関数ごとのコード領域を 4 バイト単位のカードで分割管理する 2 ビット状態表である。密ビュー `fireball::bit_view<2>` として参照する。
  - `0: UNEXECUTED` (未実行)
  - `1: EXECUTED` (実行済み)
  - `2: HOT` (コンパイル要求中)
  - `3: COMPILED` (コンパイル済み / オンデマンド許可)
- **コンパイル対象可否マスク (Trackable Mask)**: ブロックの静的適格性を管理する 1 ビット状態表である。ロード時に一度だけマークされる。`next_pc` を持ち、かつバイト長が `min_trace_bytes` 以上のブロックを対象とする。密ビュー `fireball::bit_view<1>` として独立バッファで参照する。実行時のディスパッチはこの 1 ビットのみを参照する。ブロックの静的メタデータを再走査する必要がない。 `{TrackableBlockMask}`
- **関数更新表 (Function Update Bitmap)**: 関数添字ごとの 1 ビット状態表である。前回の巡回以降に、その関数のカードが `UNEXECUTED` から `EXECUTED` へ遷移した関数だけに 1 を立てる。密ビュー `fireball::bit_view<1>` として独立バッファで参照する。
  - サイズは、モジュールの関数数と同じビット数である。
  - 関数添字は、touch した PC の上位 16 ビットから直接決まる。カードや基本ブロックの解決は要らない。
  - 巡回は 8 関数を 1 バイトとして行う。値が 0 のバイトは 1 回の比較で読み飛ばす。
  - コードを持たない関数（import 関数）のビットは立たない。
- **エイジングカーソル**: 関数更新表のバイト位置を保持する整数である。モジュール登録時に 0 で初期化し、表の末尾に達したら先頭へ戻る。
- **JITエントリ表**: 各バンクの `head_pc` 順に並ぶ固定容量配列である。検索は二分探索（$O(\log n)$）とし、削除済み枠は無効項目として扱う。エントリが少ないためRadix索引を設けない。
- **JITコード領域 (8KB)**: 4KBページ2枚分の連続領域である。先頭2KBは開始処理、終了処理、対象ABIのヘルパー呼出しコード、および絶対アドレスプールを置く非エビクション領域とし、残る2KBずつを`Bank 0 (Active)`, `Bank 1 (Warm)`, `Bank 2 (Oldest)`に割り当てる。トレースヘッダのサイズと物理欄は対象ABIで定義し、共通領域オフセットとエントリ・終了ジャンプ先を保持する。MPUの`W^X`制御対象であり、共通コード領域はflushやバンクローテーションでも維持する。
  共通領域内の固定オフセットは次のとおりである。オフセットはコード領域先頭からの値であり、トレースヘッダのフィールド位置とは別の値である。

  | 共通領域オフセット | 配置物 | 容量・用途 |
  | :--- | :--- | :--- |
  | `0x000` | 対象ABIの開始処理 | 4つの論理引数を受けてトレース本体へ移行する |
  | `0x020` | 対象ABIの終了処理 | 共有状態を同期して呼び出し元へ復帰する |
  | `0x030` | コンテキスト型ヘルパー入口 | トレースヘッダの関数アドレスを共通呼出しコードへ渡す |
  | `0x050` | 絶対アドレスプール | 256バイトの共通アドレス領域 |
  | `0x160` | x64 i32ヘルパー入口群 | 32バイト単位で4入口。2個の32ビット整数引数と結果領域ポインタで関数を呼び出し、終了処理へ戻る |
  | `0x200` | x64 wideヘルパー入口群 | 32バイト単位で11入口。ヘルパー契約ごとの引数を設定して関数を呼び出し、終了処理へ戻る |

  共通領域の参照値は各トレースヘッダへ格納する。ARMv8-MのAAPCS呼出し入口もヘルパー契約ごとに共通領域へ置く。トレースの入口・出口・ヘルパー遷移は、この参照値を用いて相対分岐先を決定する。
- **オンデマンドコンパイルキュー (On-demand Compile Queue)**: `HOT` に達した命令オフセットを保持する固定容量 LIFO キューである。容量到達時にバッチコンパイルが即座に実行される。固定容量を上回ることはない。 `{JIT_ReverseCompilationOrder}` `{GLOBAL_Policy_Memory}`
- **バンク別被チェイン逆引きテーブル (Inbound Chain Index Table)**: 各キャッシュバンクへ向けた直接チェインリンク元の JIT エントリインデックスを保持する固定長配列である。
- **実行履歴バッファ**: 短期間の実行履歴を一時的に保持するリングバッファである。 `{HistoryBuffer}`

### 3.2 内部ブロック図
```mermaid
flowchart TD
    Search[Search Request WASM PC] --> Stage1[Stage 1: Card Marking bit_view check O1]
    Stage1 -->|COMPILED| Stage2[Stage 2: Direct-Mapped Folding XOR Cache 16 slots O1]
    Stage1 -->|NOT COMPILED| Interp[Interpreter Fast-Exit]
    Stage2 -->|Hit| Exec[exec_trace native code]
    Stage2 -->|Miss| Stage3[Stage 3: Binary Search in sorted bank entries O log n]
    Stage3 -->|Hit| Exec
    Stage3 -->|Miss| Interp
```

### 3.3 主要なクラス・構造体・配列・定数

#### JITエントリインデックス（JitEntryIndex）クラス
| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 高速スロット配列 | 4-bit スロット選択を行う Folding XOR Hash による Direct-Mapped キャッシュ | 固定長配列 | 16スロット (`{DirectMappedJIT16}`) |
| エントリ配列 | `head_pc` 昇順のJITエントリを保持する | 固定長ソート配列 | 二分探索 $O(\log n)$。Radix索引なし |
| カードマーキング表 | カードごとの 2-bit 状態表 | 密ビュー | `fireball::bit_view<2>` |
| 関数更新表 | 前回の巡回以降に `EXECUTED` のカードが生じた関数の印 | 密ビュー | `fireball::bit_view<1>`、関数数ビット |
| 被チェイン逆引きテーブル | バンクごとの被チェイン元 JIT エントリインデックス配列 | 固定長配列の配列 | `FB_CONF_JIT_MAX_INBOUND_CHAINS_PER_BANK` |
| 履歴バッファ | 判定契機までの一時的な実行記録 | リングバッファ | `offset` の配列 `{HistoryBuffer}` |

## 4. 動的モデル

### 4.1 アルゴリズム
1. **カードマーキング確認 ($O(1)$)**: カードマーキング表 (`bit_view<2>`) を $O(1)$ で確認する。状態が `COMPILED` でなければ即座に終了する。
2. **Direct-Mapped Folding XOR キャッシュ確認 ($O(1)$, `{DirectMappedJIT16}`)**:
   - `UnifiedPC` を 32→16→8→4 ビットと3回の XOR で折りたたむ。
   - `slot = temp & 0x0F` を計算して16スロットの高速テーブルを照合する。
   - スロットのタグが `head_pc` と一致（Hit）した場合、バンク検索をバイパスして $O(1)$ でトレースを返す。
3. **バンク内二分探索 ($O(\log n)$)**:
   - キャッシュミス時、Active / Warm / Oldest の順に各バンクの `head_pc` 昇順配列を二分探索する。JITエントリ数が少ないためRadix表は持たない。
   - 固定容量配列内のキーに一致するlive entryがあればトレースを得る。tombstoneまたはキー不一致なら次のバンクを検索する。
   - ヒットした場合はネイティブコードアドレス（`exec_trace`）を返す。
   - 次回用として高速スロットへ格納する。
5. **ホットスポット昇格判定**:
   - yield 時等に履歴バッファを走査する。
   - 実行頻度が閾値に達したカードを`HOT`のままコンパイル待ち列へ登録する。コンパイルとcache挿入の両方が成功した場合にのみ`COMPILED`へ遷移する。
   - コンパイル失敗時は対象bitを解除し、そのブロックを再履歴・再コンパイル対象にしない。cache evictionまたは明示flushでは、対象bitを維持したままカードを`UNEXECUTED`へ戻し、次の閾値までhotnessを再計測する。
6. **最小トレース長フィルタ**:
   - 推定サイズが 1 カード分未満のブロックは、履歴記録やコンパイル登録の対象外とする（TEST-JITR-06）。
7. **3面世代交代ローテーションと局所アンリンク (`{GOTCHA-JITR-03}`, `{JIT_MultiBuffer_Cache}`, `{JIT_OldestOnly_Promote}`)**:
   - Active バンク満杯時、`Oldest` バンクをパージして新 `Active` に再利用する。
   - パージ直前に、被チェイン逆引きテーブルに登録されたソースエントリ（$k$ 件）のみを参照する。
   - 昇格済みなら再チェイニングし、完全破棄なら復帰スタブへアンパッチする。全件走査は行わない。
   - `rotate()` および `flush_all()` 実行時には Folding XOR 高速キャッシュを無効化する。古いバンクへの誤参照を防止する（`{GOTCHA-JITR-05}`）。
   - ローテーションのたびに、エイジングスイープを 1 ステップ実行する（手順10）。`flush_all()` では実行しない。
   - 参照実装のバンク破棄は、破棄対象の$n$項目の消去と被チェイン元$k$件の照合を伴うため、実際の処理量は$O(n + k\log n)$である。固定容量$n_max$と被チェイン件数上限$k_max$により停止量は上限化されるが、$O(k)$とは表現しない。ターゲット実装はバンク破棄の方法を別途定義する。
8. **トレース昇格時のインバウンドソース付け替え (`{GOTCHA-JITR-02}`)**:
   - Oldest バンクのトレースが再実行されて新 Active バンクへ昇格した際、チェイン先アドレスを新バンクへ更新する。
   - 逆引きテーブルの登録先も新バンクへ付け替える。古いバンクがパージされた後のダングリングジャンプを防止する。
9. **キュー処理時のキャッシュ再確認と二重コンパイル抑止 (`{GOTCHA-JITR-01}`)**:
   - コンパイル待ち列から取り出した PC が、既に 3 面キャッシュに常駐済みであれば再コンパイルを行わない。カード状態のみ `COMPILED` へ同期する。
   - **設計理由**: 二重コンパイルによるキャッシュ容量の浪費と CPU 時間の損失を完全に防止する。
10. **エイジングスイープ (`{JIT_CardAgingSweep}`)**:
   - 目的は、`EXECUTED` のまま長く残ったカードを `UNEXECUTED` へ戻すことである。時間的に離れた2回の実行が `HOT` を成立させる状況を防ぐ。
   - 契機は、3 面キャッシュのローテーション（`rotate()`）である。ローテーション 1 回につき 1 ステップを実行する。バンク満杯による自動ローテーションも含む。コンパイルの成否や `flush_all()` では実行しない。
   - ローテーションは、キャッシュ圧迫の直接の指標である。追い出しが起きない間は、エイジングも止めてよい。
   - 1 回の実行では、エイジングカーソルから 8 関数（1 バイト）単位で関数更新表を進める。
   - 値が 0 でないバイトを `FB_CONF_JIT_AGING_STEP_UNITS` 個処理した時点で、1 回の実行を終える。
   - 走査したバイトが `FB_CONF_JIT_AGING_STEP_SCAN_BYTES` 個に達した時点でも、1 回の実行を終える。値が 0 のバイトも走査数に数える。
   - 値が 0 のバイトは処理数に数えず、読み飛ばす。表を 1 周しても終了条件に達しない場合は、1 周した時点で終える。
   - 処理するバイトでは、ビットが立った関数だけを扱う。
   - 処理する関数では、その関数のカードマーキング表を先頭から末尾まで走査し、`EXECUTED` のカードをすべて `UNEXECUTED` へ戻す。その後、当該関数のビットを 0 にする。
   - `HOT` と `COMPILED` のカードには触れない。`HOT` はコンパイル待ち列と対応し、`COMPILED` は常駐トレースと対応するためである。
   - 走査後にカーソルを進める。表の末尾に達したら先頭へ戻る。
   - **ビットを立てる契機**: カードが `UNEXECUTED` から `EXECUTED` へ遷移した時点で、そのカードの関数のビットを立てる。この遷移はカード状態の更新（touch）の中だけで起きる。遷移が起きた場合を除き、ホットパスへ処理を追加しない。
   - **設計理由**: 前回の巡回以降に `EXECUTED` のカードが生じていない関数は、`EXECUTED` のカードを持たない。更新表を持つことで、その関数のカード表の走査を省く。
   - **上限**: 1 回の実行で走査するバイトは最大 $\min(O, \lceil F / 8 \rceil)$ 個である。処理する関数は最大 $8 \times U$ 個である（$F$: 関数数、$U$: `FB_CONF_JIT_AGING_STEP_UNITS`、$O$: `FB_CONF_JIT_AGING_STEP_SCAN_BYTES`）。
   - **処理量**: 1 関数の処理量は、その関数のカードマーキング表の大きさに比例する。1 回の実行の最大処理量は、$8 \times U$ 個の関数のカード表の合計である。
   - **有限性**: ビットが立った関数は、高々 $\lceil \lceil F / 8 \rceil / O \rceil + \lceil \lceil F / 8 \rceil / U \rceil$ 回のローテーションの内に処理される。1 周に要するローテーションは、少なくとも $\lceil \lceil F / 8 \rceil / O \rceil$ 回である。
   - **保持期間**: カーソルが通過する直前に `EXECUTED` になったカードは、ほぼ直後に減衰する。1 周に要するローテーション数の下限は走査上限が決め、値が 0 でないバイトが多いほど 1 周は長くなる。減衰は関数単位なので、同じ関数の `EXECUTED` のカードは同時に減衰する。`HOT` の閾値（2回の実行）はこの範囲で満たす必要がある。

#### エイジングスイープ手順（手順アクティビティ図）
<!-- traceability: {JIT_CardAgingSweep} {TrackableBlockMask} {GOTCHA-JITR-09} -->
ローテーションごとに実行する巡回手順を示す。

```mermaid
flowchart TD
    Start(["Bank rotation finished"]) --> Init["Set units_done = 0, scanned = 0"]
    Init --> More{"units_done < FB_CONF_JIT_AGING_STEP_UNITS and scanned < min(FB_CONF_JIT_AGING_STEP_SCAN_BYTES, ceil(F / 8))?"}
    More -- "No" --> Done(["Return to rotation"])
    More -- "Yes" --> Read["Read function update bitmap byte at cursor"]
    Read --> Zero{"Byte == 0?"}
    Zero -- "Yes" --> Advance["Advance cursor (wrap at end) and scanned = scanned + 1"]
    Zero -- "No" --> Bit["Take next set bit: function f"]
    Bit --> Scan["Scan card marking table of function f"]
    Scan --> Decay["Set every EXECUTED card to UNEXECUTED (HOT / COMPILED unchanged)"]
    Decay --> Clear["Clear bit f"]
    Clear --> Rest{"Set bits remain in byte?"}
    Rest -- "Yes" --> Bit
    Rest -- "No" --> Count["units_done = units_done + 1"]
    Count --> Advance
    Advance --> More
```

#### 3段高速検索パイプライン手順（手順アクティビティ図）
<!-- traceability: {JIT_MultiBuffer_Cache} {LowLatencyJIT} {DirectMappedJIT16} {META_BinarySearch} -->
実行時 PC からネイティブ `exec_trace` アドレスを特定する3段探索パイプラインを示す。

```mermaid
flowchart TD
    Start(["Input UnifiedPC: (func_index << 16) | bytecode_offset"]) --> Stage1["[Stage 1] Card Marking: Check bit_view<2>[pc >> card_shift] (O(1))"]
    Stage1 --> CheckCompiled{"Card State == COMPILED?"}

    CheckCompiled -- "No" --> ExitInterp(["Fast Exit: Dispatch to Interpreter Handler"])
    CheckCompiled -- "Yes" --> StageFast["[Stage 2] Direct-Mapped Folding XOR JIT Cache[4] (O(1))"]

    StageFast --> FastHit{"Cache Tag == head_pc ?"}
    FastHit -- "HIT" --> ReturnTrace(["Return Native Code Entry: exec_trace (O(1) Direct)"])

    FastHit -- "MISS" --> StageBinary["[Stage 3] Binary Search in sorted bank entries (O(log n))"]

    StageBinary --> Hit{"JIT Entry found?"}
    Hit -- "Yes" --> FillSlot["Fill Folding XOR Cache Slot"] --> ReturnTrace
    Hit -- "No (False Positive / Evicted)" --> ExitInterp
```

#### 3面世代交代ローテーションと被チェイン局所アンリンク（責務シーケンス図）
<!-- traceability: {GOTCHA-JITR-02} {GOTCHA-JITR-03} {JIT_MultiBuffer_Cache} {JIT_LazyChaining} {JIT_CardAgingSweep} -->
Active バンク満杯時の世代交代において、局所アンリンクと再チェイニングの連携シーケンスを示す。

```mermaid
sequenceDiagram
    autonumber
    participant Active as Active Cache Bank (Full)
    participant Mgr as JIT Cache Manager
    participant Inbound as Inbound Chain Table (Oldest Bank)
    participant Source as Preceding JIT Traces (Source)
    participant Oldest as Oldest Cache Bank (Purged)

    Active->>Mgr: Allocation request exceeds bank capacity
    Note over Mgr: GOTCHA-JITR-03: Trigger 3-Bank Rotation
    Note over Mgr: Shift roles: Oldest -> New Active, Warm -> Oldest, Active -> Warm

    Mgr->>Mgr: Invalidate Direct-Mapped Folding XOR Cache (16 slots)
    Note over Mgr: GOTCHA-JITR-05: Clear fast cache to prevent stale/dangling references

    Mgr->>Inbound: Inspect registered inbound source traces (k entries)
    loop For each source trace index in Inbound Table
        Inbound->>Source: Inspect target trace residency
        alt Target was Promoted to Active/Warm (GOTCHA-JITR-02)
            Source->>Source: Re-chain: Update chain_target_addr to Promoted Address
            Source->>Mgr: Transfer inbound registration to new Bank
            Note over Source: Direct native jump maintained!
        else Target was Evicted (Not Promoted)
            Source->>Source: Unlink: Patch chain_target_addr to Interpreter Fallback Stub
            Note over Source: Safely reverts to interpreter on next branch
        end
    end

    Mgr->>Oldest: Clear metadata & wipe allocation offset = 0
    Note over Oldest: O(n + k log n), bounded by configured bank and inbound capacities

    Mgr->>Mgr: Run one aging sweep step (JIT_CardAgingSweep)
    Note over Mgr: Card states only. HOT and COMPILED cards are never modified (GOTCHA-JITR-09)
```

### 4.2 状態遷移図
```mermaid
stateDiagram-v2
    [*] --> UNEXECUTED
    UNEXECUTED --> EXECUTED: First execution
    EXECUTED --> HOT: Threshold reached
    HOT --> COMPILED: Compilation done
    COMPILED --> UNEXECUTED: Cache evicted
    EXECUTED --> UNEXECUTED: Aging sweep
```

キャッシュ破棄（Eviction）時は `EXECUTED` ではなく `UNEXECUTED` へリセットする（TEST-JITR-04）。

エイジングスイープが `UNEXECUTED` へ戻せる状態は `EXECUTED` だけである。`HOT` と `COMPILED` は変更しない（`{GOTCHA-JITR-09}`）。

コンパイル失敗時はカードを`COMPILED`にせず、独立したTrackable Maskの対象bitを解除する。これはカード状態の遷移ではなく再試行対象の除外であり、eviction後に候補性を保ったままhotnessを取り直す動作とは異なる。

### 4.3 トレース実行時の分岐解決とインタープリタ復帰
<!-- traceability: {JIT_RuntimeAPI_Fallback} {DirectBytecodeExecution} -->
制御フロー・コール境界のインタープリタ委譲不変条件を示す。ロード時の静的解析で各基本ブロックに付帯情報を持たせる。これにより実行時の分岐解決を定数時間で行う。

| 付帯情報 | 意味 |
| :--- | :--- |
| 後続アドレス | 分岐条件が不成立だった場合に続くブロックの先頭アドレス |
| 分岐先アドレス | 分岐条件が成立した場合に実際にジャンプする先のアドレス |
| フレーム深さ | 当該ブロック到達時に積まれているべき制御フレームの深さ |
| 命令列長 | 当該ブロック自身が占める命令バイト数（足切り判定用） |

命令列長は、後続アドレスから自分自身の先頭アドレスを引いて求めてはならない。後方分岐ブロックでは差分が負になる。その結果「短すぎる」と誤判定される。命令列長はブロック自身の命令バイト数から直接求める。

#### コンパイル済みトレース実行後の遷移手順（アクティビティ図）
```mermaid
flowchart TD
    Start(["コンパイル済みトレースを一つ実行する"]) --> HasCond{"分岐条件を持つ終端命令か？<br/>(BR_IF または IF で終わるブロック)"}
    HasCond -- "はい" --> CheckCond{"条件は成立したか？"}
    CheckCond -- "成立" --> TakeBranch["分岐先アドレスへ進む"]
    CheckCond -- "不成立" --> TakeFallthrough["後続アドレスへ進む（条件の計算結果は破棄）"]
    HasCond -- "いいえ<br/>(単純な素通り、またはRETURNで終端)" --> PushResult["戻り値があればオペランドスタックへ積む"] --> TakeFallthrough2["後続アドレスへ進む"]
    TakeBranch --> HasNext{"進む先のアドレスは存在するか？"}
    TakeFallthrough --> HasNext
    TakeFallthrough2 --> HasNext
    HasNext -- "いいえ（関数終了）" --> EndSentinel["コード末尾を指す番兵値へ進める"]
    HasNext -- "はい" --> NormalAdvance["そのアドレスへ進める"]
    EndSentinel --> Reconcile["制御フレームの深さを調整する"]
    NormalAdvance --> Reconcile
    Reconcile --> Dispatch["コンパイル済みならJITで続行、未済ならインタープリタへ委託"]
```

- **分岐条件の扱い**: 分岐条件を持つ終端命令（`BR_IF`, `IF`）の計算結果は、オペランドスタックへ積まない。積むと後続演算が誤って消費してしまう。
- **関数終了の定数時間解決 (`{GOTCHA-JITR-08}`)**: `RETURN` で終わるブロックでは命令列を再走査しない。実行時の命令デコードやオブジェクト生成は一切禁止されている（`{DirectBytecodeExecution}`）。コード長という単一の数値で関数の終わりを表す。復帰処理はインタープリタへ委ねる。
- **制御フレームの整合 (`{GOTCHA-JITR-06}`)**: JITトレース実行は制御フレーム操作を経由しない。そのためインタープリタ実行時に積まれたフレームが残留することがある。フレーム深さの巻き戻しだけでは正しさを保証できない。したがってインタープリタ復帰時の分岐解決は、フレームスタックの中身を参照しない。静的解析済みの後続アドレスや分岐先アドレスを直接使用する。フレーム深さの切り詰めはスタック肥大化防止の安全策としてのみ機能させる。
- **短小判定の符号 (`{GOTCHA-JITR-07}`)**: ブロックの足切り判定は自身の命令バイト数で行う。後続アドレスとの差分で代用すると、後方分岐ブロックで差分が負になり、高頻度ブロックが永久に除外されてしまう。
- **エイジングと常駐状態の分離 (`{GOTCHA-JITR-09}`)**: エイジングスイープは `EXECUTED` のカードだけを変更する。`COMPILED` まで戻すと、常駐トレースのカードが `UNEXECUTED` になり、lookup が常駐コードを見逃す。`HOT` まで戻すと、コンパイル待ち列の要求とカード状態が食い違う。常駐性の正本はキャッシュ、待ち列の正本は待ち列であり、スイープはどちらも書き換えない。
- **押し出し量の事前確認**: ランタイムは、トレースを呼ぶ前に、連鎖先を含む最大の `stack_words` が空き容量に収まることを確認する。収まらない場合はトレースを使わず、インタープリタが実行する。インタープリタは、容量超過を `assert` で停止する。JITだけが容量外へ書き込む状態を作らないためである。
- **連鎖の再リンク**: 昇格とローテーションの後も、すべての連鎖ポインタは、常駐トレースの有効な入口を指す。Pythonのヘッダとネイティブのヘッダは一致する。退避された後続へのポインタは、インタープリタへの復帰へ戻す（`{GOTCHA-JITR-02}`）。

## 5. インターフェース定義

### 5.1 公開API

#### Tier 2実行境界（JITRuntime）
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | Tier 2実行ループへ、Tier 3のブロック解決、履歴記録、トレース検索、チェイン情報、キャッシュ無効化を提供する。 |
| 実装 | `RuntimeEngine` の `JITRuntime` Protocol と `JITRuntimeManager` |
| 構成 | `RuntimeEngine(jit_runtime=JITRuntimeManager(...))` による静的依存性注入 |
| 不変条件 | Tier 2はTier 3のカード表・キュー・キャッシュ実装へ直接アクセスしない。 |

#### 検索（lookup）
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 命令オフセットに対応するネイティブコードアドレス（`exec_trace` 型）を返す。 |
| シグネチャ | `lookup(pc: wasm_pc_t) -> result<exec_trace, jit_lookup_result_t>` |
| 戻り値 | 成功時はネイティブ実行エントリ、失敗時はエラーコードを返す。 |

#### エントリ登録（register_entry）
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 命令オフセットとネイティブ実行コードアドレスを登録し、索引を更新する。 |
| シグネチャ | `register_entry(pc: wasm_pc_t, native_entry: exec_trace) -> void` |

## 6. 制約達成の方策

### 6.1 性能制約
- **方策**: カードマーキング表と16スロットFolding XORキャッシュの $O(1)$ 事前検索後、キャッシュミス時に少数のソート済みJITエントリを二分探索する（$O(\log n)$）。Radix索引は設けず、固定容量配列と二分探索を共通の検索契約とする。

### 6.2 メモリ制約
<!-- traceability: {JIT_MultiBuffer_Cache} {JIT_OldestOnly_Promote} -->
- **方策**: 3面循環バッファと Oldest ヒット限定昇格を採用する。Oldestのlookup hitは即時にActiveへ昇格し、Warmのhitは昇格させない。断片化を防ぎつつ、低頻度コードを自然に破棄させる。

## 7. 形式検証・テスト仕様との対応

### 7.1 検証対象の不変条件
- **3面キャッシュ代謝の有界性**: 循環ローテーションによる Oldest パージと新 Active 再利用を検証する。
- **局所アンリンク安全性**: 被チェインソース$k$件だけを逆引き表から処理する。バンク再利用は全$n$項目の消去とバンク検索を伴い、$O(n + k\log n)$である。固定容量により有界だが$O(k)$のみとは主張しない。
- **カード状態の遷移規則**: 昇格は `UNEXECUTED`、`EXECUTED`、`HOT`、`COMPILED` の順だけで進む。`UNEXECUTED` へ戻す経路は、パージ時のリセットとエイジングスイープの2つに限る。
- **エイジング安全性**: エイジングスイープは `HOT` と `COMPILED` のカードを変更しない。`COMPILED` のカードは常駐トレースと対応し続ける（`{GOTCHA-JITR-09}`）。
- **更新表の包含性**: 関数更新表のビットが 0 の関数は、`EXECUTED` のカードを持たない。`EXECUTED` になる経路は `UNEXECUTED` からの遷移だけである。
- **エイジングの有限性**: 関数更新表のビットが立った関数の `EXECUTED` のカードは、有限回のローテーションの内に、走査されて減衰するか `HOT` へ進む。

### 7.2 テスト仕様書との連携
本コンポーネントのテストケースおよび直交表は、[`jit_runtime_test_spec.md`](docs/qa/tier3_executer/jit_runtime_test_spec.md) を正本として定義する。形式検証モデルは `formal/jit_cache_model.py` を参照する。
