# vSoC (統合実行エンジン) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`runtime_vsoc.md`](docs/components/tier2_runtime/runtime_vsoc.md)
本書はvSoCの統合責務、割り込みとデバッガの協調、およびマルチモジュールリンクを対象とする。

## 2. テストケース一覧

### ハーネス統合 (runtime_vsoc.md (Harness))
<!-- traceability: {CPS_4Args} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-01 | vSoCはTier3実装の内部ヘッダに依存しない | - | 依存関係と`vsoc_harness`のメンバ型を確認 | 具象依存型はハーネスのメンバ型としてコンパイル時に固定する。依存注入に仮想呼出しや実行時型探索を使わない。C ABIコールバック境界は対応するABI契約に従う | `{META_StaticDI}` |
| TEST-VSOC-02 | `exec_trace`の統一論理引数契約 | インタープリタ実行/JIT実行の双方 | 実行入口を確認する | 呼び出し側は実行エンジンの種別を意識しない（同一の4論理引数 `(ctx, sp, local_base, tos)`）。x64物理配置はABI定義に従い、ARMv8-MはTBD | 「実行エンジン委譲」 |
| TEST-VSOC-03 | `register-hook`はvMMIOへの薄い転送 | - | `register-hook`を呼ぶ | `harness.vmmio`経由で`runtime_vmmio.md`の同名APIへそのまま転送され、事前/事後条件はvmmio層が正本 | register-hook |

### LOOP後方分岐yieldとJITキャッシュ協調
<!-- traceability: {JIT_BackedgeYield} {ADR_LoopBackedgeYield} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-10 | LOOP後方分岐は有限回数で協調境界へ戻る | Interpreterの有限な`FB_CONF_INTERPRETER_YIELD_THRESHOLD` | しきい値前後の分岐回数とyield境界を確認する | 各取得済み後方分岐で実行状態を更新する。しきい値到達までは命令実行を続け、Interpreter dispatcherがyield statusを返す。RuntimeEngineはstatusをSystemへ伝える。Interpreter単独実行とHybrid JITで同じ条件を使う | `{ADR_LoopBackedgeYield}`, `runtime_vsoc.md` |
| TEST-VSOC-11 | 保留interrupt-eventの構成 | - | 保留イベント構造を確認 | `vector_id`、`source_id`、`cause_code`、`payload0`、`payload1`の固定5ワードで保持される | `{GLOBAL_InterruptWakeup}` |
| TEST-VSOC-13 | IRQ/JITレース不在 | JIT実行中に割り込みイベントが待機 | 形式検証プロパティを確認 | JITコード実行中は割り込みハンドラを開始せず、COOS協調境界から配送する(`AG(Not(handling_irq & jit_mode))`) | `irq_jit_race_freedom_proof` |
| TEST-VSOC-14 | 抽象モデル上のflush完了性 | dirty状態になったキャッシュ | 形式検証プロパティを確認 | `AG(dirty -> AF(flushed))`（抽象遷移モデルでdirty状態からflush完了状態へ到達する。実時間の期限は対象外） | [`vsoc_cache_coherency_model.py`](docs/components/tier2_runtime/formal/vsoc_cache_coherency_model.py) `dirty_cache_eventually_flushes` |
| TEST-VSOC-15 | 世代の逆行不在 | 3面ローテーション | 各バンクのgeneration cookieを確認 | 全バンク一括更新され、逆行・不一致が生じない | [`vsoc_cache_coherency_model.py`](docs/components/tier2_runtime/formal/vsoc_cache_coherency_model.py) `generation_monotonicity_across_banks` |
| TEST-VSOC-16 | Purgeと回収の不可分性 | ローテーション時 | Oldestバンクのpurge処理を確認 | Purgeとエントリ表スロット回収が同一トランザクションで行われ、未回収スロットが蓄積しない | [`vsoc_cache_coherency_model.py`](docs/components/tier2_runtime/formal/vsoc_cache_coherency_model.py) `bounded_cache_rotation_memory` |
| TEST-VSOC-17 | 形式検証の変異反証 | 通常モデルと`guards=False`モデル | `vsoc_state_model.py`を実行 | 通常モデルでは2つの性質が成立し、ガードを無効化した変異モデルでは両方の性質が失敗する | [`vsoc_state_model.py`](docs/components/tier2_runtime/formal/vsoc_state_model.py) |

### vDMAとBulk Memoryの転送契約
<!-- traceability: {WasmFCSubset} {VDMA} {MemoryBoundaryCheck} {OwnershipTransfer} {GLOBAL_InterruptWakeup} -->

本節は転送の振る舞いを検査し、専用importのABIはTEST-SYS-20〜22へ対応付ける。
転送範囲の正本は[`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md)である。
内部の同期・非同期は転送対象と操作に応じる。
成功復帰には、いずれの方式でも転送完了とCPU可視性を要する。
保留とengine占有の検査は、それぞれを伴う転送対象と操作に適用する。
外部依存は、その契約を表す制御可能な境界doubleへ置換してよい。
製品ドライバや実機の確認予定を、モックによるソフトウェア検査の省略・延期の根拠にしない。

#### 入口別の契約と観測

| 入口 | 根拠 | 成功の観測 | 失敗の観測 | 入口固有の条件 |
| :--- | :--- | :--- | :--- | :--- |
| WASM `memory.copy` | `runtime_vsoc.md`の内部ゲストメモリコピーサービス、`wasm_instruction_set.md` | trapなく完了し、直後のguest loadが転送結果を読む | 開始前の不正端点はWASM memory trapとなり、全バック領域を保存する | memmove意味論を保つ。ゼロ長でも端点を検査する。リニアメモリ間はCPUで処理する |
| 専用 `vdma.start` import | `runtime_vmmio.md`の仮想DMAとの境界、`runtime_syscall.md`の専用ホストコール | WASI errno互換の0を返し、直後のguest loadが転送結果を読む | 定義された拒否は非0を返す。guestのエラー処理分岐で結果を記録する | 3つのu32引数を専用ハンドラへ渡す。汎用trapやVDMA制御レジスタを経由しない |

専用importのゼロ長と重複転送には、Bulk Memoryの追加保証を流用しない。
これらの期待結果は、専用APIまたは転送対象の契約で定義された範囲に限る。
開始後の失敗には、開始前の拒否と同じロールバック保証を課さない。
失敗を成功と誤認せず、停止の確認前にバッファを再利用しないことを観測する。

#### 因子、水準、組合せ

| 因子 | 水準 | 適用条件 |
| :--- | :--- | :--- |
| 入口 | 内部 `memory.copy`、専用import | 同じ転送データを別々の実guestで実行する |
| 転送元と転送先 | リニアメモリ、DYNAMIC、SHM、PASSTHROUGH | 全16方向を両入口で計画する。各端点に読出し・書込み可能な実マッピングを用意する |
| 長さ | 1、複数byte、端点の残容量、残容量+1 | 大小による実行経路の切替を期待しない。ゼロ長はBulk Memoryの独立ケースとする |
| 境界 | 内部、末尾に一致、実容量越え、ページ越え、u32加算の桁あふれ | vMMIOは1ページと実マッピング範囲の両方へ収める。隣接ページが有効でも自動分割を期待しない |
| アラインメント | 整列、非整列 | RAM型バック領域を使う。実デバイスはそのアクセス契約に従う |
| 拒否箇所 | 転送元、転送先、両端 | 単独違反では他方を有効にする。両端違反ではエラーの優先順位を仮定しない |
| アクセス許可 | 許可、転送元の読出し拒否、転送先の書込み拒否、非所有者 | 方向ごとの共通アクセスゲートを検査する |
| TLB | 未充填、許可アクセス後 | SHMとPASSTHROUGHへ適用する。リニアメモリとDYNAMICをTLB因子へ入れない |
| 物理領域の関係 | 別領域、同一範囲、前方重複、後方重複 | 重複の期待値は `memory.copy` のmemmove契約から導く。仕様が許すマッピングだけを組み合わせる |
| 完了方式 | 同期、非同期 | 実際の転送対象と操作で決まる。FCから方式を生成しない |

正常転送の入口・転送元・転送先の3因子は、32構成すべてを検査対象とする。
次表は `memory.copy` の経路を示す。
専用importは各構成で専用ハンドラへ接続する。

| 転送元＼転送先 | リニアメモリ | DYNAMIC | SHM | PASSTHROUGH |
| :--- | :--- | :--- | :--- | :--- |
| リニアメモリ | CPU memmove | 内部vDMA | 内部vDMA | 内部vDMA |
| DYNAMIC | 内部vDMA | 内部vDMA | 内部vDMA | 内部vDMA |
| SHM | 内部vDMA | 内部vDMA | 内部vDMA | 内部vDMA |
| PASSTHROUGH | 内部vDMA | 内部vDMA | 内部vDMA | 内部vDMA |

正常系は、次項の7因子で制約付きpairwise行を構成する。
範囲・権限の拒否、ゼロ長、alias、履歴、完了方式は独立したケースで検査する。
32構成の正常転送、両端の独立した拒否、次の高リスク条件は固定例にも置く。

- DYNAMICの256byte実容量に対するoffset 255・長さ2を、転送元と転送先で試す。
- SHMの `mapping_size` 末尾を1byte越える要求を、転送元と転送先で試す。
- 有効な隣接PTEを用意し、単一要求のページ越えを拒否する。
- 所有者がTLBを充填した後、非所有者の読出しと書込みを拒否する。
- 有効な転送元と不正な転送先を用意し、転送開始と部分更新がないことを確認する。
- 異なる仮想端点が同じ物理領域を指す重複copyを、前後両方向で試す。
- 拒否の直後に同じ有効マッピングで転送し、正常系が維持されることを確認する。

正常系の因子行は次項へ記載する。
因子行の網羅性確認と、転送試験の実行結果を分けて記録する。
既存の横断pairwise suiteは保持し、本節の転送方向や境界の証拠へ読み替えない。

#### 正常転送の構成行

次表はTEST-VSOC-67の実行入力である。
[`test_vdma.py`](experiments/pysim/qa/tier2_runtime/test_vdma.py)で全32行を実行する。
`copy`は内部 `memory.copy`、`import`は専用 `vdma.start` を表す。
領域名のLはリニアメモリ、DはDYNAMIC、SはSHM、PはPASSTHROUGHである。
offsetは各マッピングの先頭からのbyte数である。

各領域には、少なくとも128byteの読書き可能なRAM型バック領域を用意する。
転送元と転送先が同じ領域種別なら、一つのマッピング内の非重複範囲を使う。
DYNAMIC同士にも一つのHALスロットを使い、二重mapを要求しない。
異なる種別には別の物理領域を用意し、aliasの試験はTEST-VSOC-69へ分ける。
guestの結果記録とmarkerは、この表の転送範囲外へ配置する。

`cold`は対象PTEのTLB未充填、`warm`は正当なアクセスで充填後の状態である。
warmではS/Pの対象エントリを保持できるVPNを選び、実際のキャッシュ状態を確認する。
`n/a`は両端がL/Dの場合である。
全行で転送元の読出しと転送先の書込みを許可し、有効所有者から実行する。

7因子の候補を全列挙すると、有効構成は672通りとなる。
その構成から得る有効水準対165組と、入口・元・先の32構成を、次の32行で含む。
この網羅性は有限因子の列挙と集合比較で確認する。
実行コードと本表の行ID・入力の一致も独立したテストで検査する。
拒否、実容量末尾、ゼロ長、alias、非同期状態の網羅はこの集計に含めない。

| 構成行 | 入口 | 元 | 先 | 長さ | 元offset | 先offset | TLB |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| N01 | copy | D | D | 1 | 4 | 68 | n/a |
| N02 | copy | D | L | 1 | 4 | 68 | n/a |
| N03 | copy | D | P | 1 | 4 | 68 | warm |
| N04 | copy | D | S | 4 | 5 | 69 | cold |
| N05 | copy | L | D | 1 | 5 | 69 | n/a |
| N06 | copy | L | L | 1 | 4 | 68 | n/a |
| N07 | copy | L | P | 4 | 4 | 68 | cold |
| N08 | copy | L | S | 16 | 5 | 68 | warm |
| N09 | copy | P | D | 16 | 4 | 68 | cold |
| N10 | copy | P | L | 4 | 4 | 68 | warm |
| N11 | copy | P | P | 4 | 4 | 69 | warm |
| N12 | copy | P | S | 1 | 4 | 68 | cold |
| N13 | copy | S | D | 1 | 4 | 68 | cold |
| N14 | copy | S | L | 1 | 5 | 69 | cold |
| N15 | copy | S | P | 16 | 4 | 68 | cold |
| N16 | copy | S | S | 4 | 4 | 68 | cold |
| N17 | import | D | D | 4 | 4 | 68 | n/a |
| N18 | import | D | L | 16 | 4 | 68 | n/a |
| N19 | import | D | P | 1 | 4 | 68 | cold |
| N20 | import | D | S | 1 | 4 | 68 | cold |
| N21 | import | L | D | 1 | 4 | 68 | n/a |
| N22 | import | L | L | 1 | 4 | 68 | n/a |
| N23 | import | L | P | 1 | 4 | 68 | cold |
| N24 | import | L | S | 1 | 4 | 68 | cold |
| N25 | import | P | D | 1 | 4 | 68 | cold |
| N26 | import | P | L | 1 | 4 | 68 | cold |
| N27 | import | P | P | 1 | 5 | 68 | cold |
| N28 | import | P | S | 1 | 4 | 68 | cold |
| N29 | import | S | D | 16 | 4 | 69 | warm |
| N30 | import | S | L | 1 | 4 | 68 | cold |
| N31 | import | S | P | 1 | 4 | 68 | cold |
| N32 | import | S | S | 1 | 4 | 68 | cold |

#### テストケース

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-60 | リニアメモリ間のCPU copy | 両範囲が有効で非重複 | 大小・アラインメントの異なるcopyを実guestで実行する | CPU memmoveで全バイトが一致し、vDMA callbackを呼ばない | `{WasmFCSubset}`, `test_linear_memory_copy_uses_cpu_memmove` |
| TEST-VSOC-61 | 重複・同一・ゼロ長copy | 有効なリニアメモリ端点 | 両方向の重複、同一範囲、ゼロ長を実行する | 独立した入力snapshotから導いた全メモリと一致し、vDMAを起動しない | `{WasmFCSubset}`, `test_linear_memory_copy_uses_cpu_memmove` |
| TEST-VSOC-62 | DMA完了前のguest再開禁止 | 非同期転送を伴う対象と操作を選び、境界double等で完了を保留できる。別のREADYタスクを用意する | 両入口で転送後にguest進捗markerとloadを置き、保留・完了後のguestとTCBの状態を観測する | pending中は次WASM命令へ進まず、既存COOS待機へ戻る。別READYタスクが実行される。転送完了とCPU可視性の確認後に成功復帰する。pending中の転送先不変や外部応答の無条件な有限完了は要求しない | `{VDMA}`, TEST-SYS-22 |
| TEST-VSOC-63 | 開始後失敗の成功誤認防止 | 転送対象が開始後失敗とその結果を定義する | 実対象または外部境界doubleから失敗を返し、両入口の結果と成功markerを確認する | 定義された失敗を返し、成功markerを実行しない。専用importはエラー分岐へ進める。部分転送のロールバックやタイムアウトによる停止・再利用を仮定しない | `{VDMA}` |
| TEST-VSOC-64 | 両端の全範囲検査と拒否時の保存 | sourceかdestinationの一方を範囲外にし、他方を有効にする | 両入口で、末尾一致・1byte越え・実容量越え・ページ越え・u32桁あふれを試す | 有効範囲だけ転送する。不正範囲は開始前に入口別の失敗を返す。全バック領域、所有者、マッピングを保存し、同じ有効領域を再利用できる | `{MemoryBoundaryCheck}`, `runtime_vmmio.md` |
| TEST-VSOC-65 | DMA書込みのCPU可視性 | CPU側とDMA側の可視性を分離できる対象または境界double。対象が許す非重複範囲を使う | destinationの旧値をCPU側へ残し、sourceと隣接byteへCPU書込みする。デバイス完了とCPU可視化を別々に制御する。復帰後にguestからdestinationと隣接範囲を読む | DMA前のsource clean/write-backとDMA後のdestination invalidateまたは同等処理がmemory barrierで順序化される。guestは可視化前に成功復帰せず、復帰後に転送結果を読み、隣接byteを保存する。実cache命令の効果は別途確認する | `{VDMA}` |
| TEST-VSOC-66 | engine占有による開始拒否の副作用なし | 対象driverの契約がengine占有時の開始拒否を定め、別の転送が同じengineを保持する | 両入口で新規要求を発行し、先行転送の状態も照合する | 定義された開始拒否を返す。拒否した要求の転送先を変更せず、先行転送の状態を破壊しない。解放後の有効要求は転送できる | `{VDMA}` |
| TEST-VSOC-67 | 全端点方向での同期完了 | 読出し可能なsourceと書込み可能なdestinationを別のRAM型領域に用意する | 32構成で異なるbyte列を転送し、成功直後にguest loadと成功markerを実行する | 入口別の成功結果、guest読出し、実バック領域が独立snapshotと一致する。転送元と転送先の隣接byte、所有者、マッピングを保存する | `{VDMA}`, `{WasmFCSubset}`, TEST-SYS-20 |
| TEST-VSOC-68 | 転送方向ごとの権限・所有権検査 | 許可されたマッピングで、読出し権限・書込み権限・所有者の一つだけを不適合にする | 両入口、source側とdestination側、TLB未充填と許可アクセス後で試す | 転送元は読出し、転送先は書込みの許可を要する。非所有者を拒否する。拒否時に全byteと所有権を保存し、許可された要求は成功する | `{OwnershipTransfer}`, `runtime_vmmio.md`, TEST-SYS-21 |
| TEST-VSOC-69 | 仮想端点が異なる物理aliasのmemmove | 仕様が許すRAM型マッピングが同じ物理領域を指す | `memory.copy`で同一範囲・前方重複・後方重複を実行する | 仮想アドレスの大小や異なるFCに依存せず、転送前snapshotから導いた物理領域全体と一致する | `{WasmFCSubset}` |
| TEST-VSOC-70 | ゼロ長copyの端点検査 | 有効端点、リニアメモリ末尾と末尾+1、未マップ端点を用意する | `memory.copy`の長さ0を各端点へ指定する | リニアメモリ末尾は有効である。不正な端点はゼロ長でもtrapとなる。全byteを保存し、有効時だけ後続markerを実行する | `{WasmFCSubset}`, `{MemoryBoundaryCheck}` |
| TEST-VSOC-71 | 不適格なvMMIO端点の開始前拒否 | `memory.copy`にFC=12・予約FC・未マップのFC=13/14/15を指定する | source側とdestination側を独立に不適格にし、長さ1で実guestを実行する | WASM memory trapとなる。転送、制御レジスタ書込み、後続markerは発生せず、全バック領域を保存する | `{WasmFCSubset}`, `runtime_vsoc.md` |
| TEST-VSOC-72 | マッピング・所有権変更後のアクセス契約 | 既存APIでDYNAMICをmap/unmapするか、SHMをrevoke・移譲できる | 許可転送、変更、旧所有者の拒否、新しい有効所有者の転送を実行する | 各操作後の実byteと所有権が独立モデルに一致する。失効した許可をTLBから再利用せず、有効なマッピングで転送を継続できる | `{OwnershipTransfer}`, `runtime_vmmio.md` |
| TEST-VSOC-73 | 非同期完了通知の協調境界 | 非同期対象の待機先が既存COOS経路に登録済みである | 完了を固定FIFOへ投函し、協調境界の前後でguestとタスク状態を比較する | ISR投函だけではタスク状態を変更しない。既存の境界配送で対象を再開し、完了とCPU可視性の確認後に成功復帰する | `{VDMA}`, `{GLOBAL_InterruptWakeup}`, TEST-SYS-22 |

#### 独立oracleと探索方法

固定例と生成例は、実NativeInterpreter・System・COOS・vMMIO・HALバッファを使う。
振る舞いを単位とする古典学派を採用し、実際の状態と副作用を検査する。
通常のRuntimeEngine境界で観測し、Interpreterの命令単位実行経路を追加しない。
外部の完了時刻や失敗を制御するdoubleは、実対象の既存境界へ接続する。
doubleによる結果を実機DMAやCPU cacheの証拠に数えない。
期待するtrapまたはerrnoを具体型で照合する。
任意の例外やテスト自身の `AssertionError` を捕捉して成功扱いにしない。

期待値は転送前に保存したbyte列とfixtureの既知の物理配置から導く。
製品のアドレス解決やコピー関数をoracleから呼ばない。
aliasでは物理領域ごとにsnapshotを一つ保存し、元byte列から期待転送先を作る。
領域全体を照合し、同じ物理byteへ複数の仮想端点が対応することも反映する。
非alias時だけ、転送元全体の不変を独立に要求する。
guest用の結果記録領域とmarkerは転送領域から分離する。
全メモリの期待値には、この記録処理の書込みも明示する。

| 技法 | 対象と生成範囲 | 各例で照合する結果 |
| :--- | :--- | :--- |
| 固定例・パラメータ化 | TEST-VSOC-60/61と64、67〜71。32構成と高リスク例を明示する | 入口別の結果、guest直後load、全バック領域、隣接byte、所有者、マッピング |
| 制約付きpairwise | 正常転送の構成行N01〜N32。両入口、両端種別、長さ1/4/16、元offset 4/5、先offset 68/69、TLB状態を組み合わせる | 全行でTEST-VSOC-67の独立oracleを使う。入力設計の165有効ペアと32方向が、実行した行で覆われたかを記録する |
| Hypothesisのbyte転送 | TEST-VSOC-67/69。RAM型領域の任意byte列、source/destination offset、残容量内の長さを生成する | 独立snapshotから導いた全領域との一致、隣接byte保存、完了直後のguest可視性 |
| Hypothesisの拒否入力 | TEST-VSOC-64/68。一方を有効に保ち、もう一方の範囲か権限だけを違反させる | 開始前の拒否、全byte・所有者・マッピング保存、拒否後の有効転送成功 |
| Hypothesisの状態履歴 | TEST-VSOC-72。既存APIの許すmap、転送、unmap、revoke、所有権移譲を生成する | 各操作後の単純な独立モデルと実状態の一致。所有権喪失後に古いviewを使用しない |
| 形式検証 | [`wasm_bulk_memory_model.py`](docs/specs/formal/wasm_bulk_memory_model.py) の範囲外更新禁止、CPU経路、保留中再開禁止、可視化前再開禁止 | 通常モデルの性質成立と `guards=False` の反証。抽象状態の成立を実driverの証拠へ読み替えない |

生成する長さとoffsetはfixtureの実容量に制約する。
専用importの生成長は1以上とし、重複転送の生成は `memory.copy` に限定する。
リニアメモリの実末尾、DYNAMIC実容量、SHM実サイズ、ページ境界は固定例で補う。
生成件数、実行時間、Hypothesisの再現情報は実装時の検証実績へ記録する。
縮小された反例が固有の違反を示した場合は、読める固定回帰例へ保存する。

#### 検出する違反と実装順序

| 想定する違反 | 対応ケースと検出結果 |
| :--- | :--- |
| source/destinationの入替え、長さ・offsetの誤り、隣接byte破壊 | TEST-VSOC-67。異なる入力byte列と全領域oracleが不一致となる |
| 開始アドレスだけ検査する、4KB PTEを実容量と誤認する、加算をラップする | TEST-VSOC-64。開始前拒否または全領域保存が失敗する |
| sourceに書込み権限、destinationに読出し権限を使う、TLBで所有者検査を省く | TEST-VSOC-68/72。方向別の許可結果または拒否後の状態が不一致となる |
| 仮想アドレスだけで重複を判定する、前向きコピーで元byteを破壊する | TEST-VSOC-69。独立した物理snapshotと一致しない |
| 長さ0で検査を省く、FC=12や予約FCへ転送する | TEST-VSOC-70/71。必要なtrapがなく、後続markerが実行される |
| 開始受理を成功と扱う、可視性確認を省く、開始後失敗を成功へ変換する | TEST-VSOC-62/63/65。guest進捗または直後loadが契約に違反する |
| 拒否要求が先行転送を破壊する、ISRでタスクを直接READY化する | 適用対象のTEST-VSOC-66/73。先行状態または協調境界前の状態が変化する |

最初に、現行pysimの同期対象で64、67〜72と生成試験を実装する。
TEST-VSOC-60/61には、現在の生成範囲外にある実メモリ末尾の固定例を補う。
32構成の正常転送と両端の拒否を、実guestの両入口から検査する。
62、63、66、73は、外部転送サービスのモックへ接続して検査する。
65は、CPU側とDMA側の可視性を分離したモックで成功復帰の契約を検査する。
製品ドライバの操作順序は、その下位境界をモック化して検査する。
実機の適合確認は、これらのモック試験と独立して実施する。

設計した全ケースを実行済みとは扱わない。
受入れの証拠は、ケースと因子行の対応、独立oracle、直接観測、違反への検出感度である。
line/branch coverageは、抜けた経路を探す診断情報として記録する。

### vSoC Engineライフサイクル
<!-- traceability: {VSOC_Lifecycle} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-20 | ロード失敗でError状態 | 不正なWASM | `prepare(module)` | `Loading→Error`に遷移 | `VSOC_Lifecycle` |
| TEST-VSOC-21 | LOOP後方分岐しきい値到達でscheduler境界へ復帰 | C++ InterpreterまたはHybrid JIT実行中 | Interpreter設定の回数しきい値まで取得後方辺を実行する | しきい値到達後にC++ dispatchがyield statusを返す。JIT有効時のホットスポット記録とcompile queue処理はInterpreter拡張が境界処理として行い、RuntimeEngineは結果をSystemへ渡す | `{JIT_BackedgeYield}` |
| TEST-VSOC-22 | LOOPしきい値到達時にC++ handlerを通ってCOOSへ戻る | 同一制御フレームのLOOP後方分岐を実行するJITトレース | Interpreterの`FB_CONF_INTERPRETER_YIELD_THRESHOLD`へ`loop_jump_count`が達するまで実行する | C++ dispatcherは取得済み後方辺ごとにC++ Interpreter handlerを実行し、Interpreter設定のしきい値に達した後にyield statusを返す。RuntimeEngineは`yield_requested`をSystemへ渡し、`System.run_guest()`は`on_yield()`後にCOOSへ制御を返す。イベント配送はCOOS境界の別処理として行う | TEST-VSOC-10, `{ADR_LoopBackedgeYield}` |
| TEST-VSOC-23 | デバッガとInterpreter拡張の同時接続拒否 | JITネイティブ実行拡張を接続したNativeInterpreter | native dispatch前にDebuggerをattachする | 接続時 `assert` で拒否され、DebuggerとJIT拡張は同じInterpreterに存在しない | `{DebuggerInterpreterComposition}` |
| TEST-VSOC-24 | アタッチ中のインタープリタ専用実行 | `Interpreter + Debugger` 構成 | デバッガをアタッチして `step()` または `continue` を実行する | PCを保持したままインタープリタだけが実行され、JITの動的切替とキャッシュ操作は発生しない | `{DebuggerInterpreterComposition}` |

### vIRQ登録と原因付き階層配送

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-50 | 静的vIRQノードの登録 | root・4分類・デバイスの固定ノード | `fireball:host/virq.register(node_id, function_index)` を実行 | 静的ノードだけが受け付けられ、親子関係と原因源表は変更されない | `runtime_vsoc.md` `register-virq-dispatcher` |
| TEST-VSOC-51 | WASM関数シグネチャ拒否 | 登録対象に不一致シグネチャの関数 | `fireball:host/virq.register` 経由で登録 | `(u32,u32,u32,u32,u32) -> u32` 以外は拒否され、有効登録を上書きしない | `runtime_vsoc.md` `register-virq-dispatcher` |
| TEST-VSOC-52 | COOS協調境界前の登録変更不可視性 | 有効登録A、保留登録B | C++のyield statusを受け取る前後に同じイベントを配送 | yield前はA、次のCOOS協調境界で変更反映後はBだけが観測され、途中状態は観測されない | `{GLOBAL_InterruptWakeup}` |
| TEST-VSOC-53 | 原因レコードの階層伝播 | root/category/deviceに登録済み関数 | `PASS_THROUGH`を返すイベントを配送 | `root → 分類 → デバイス → ゲスト関数`の順に1回ずつ呼ばれる | `runtime_vsoc.md` `dispatch-interrupt-event` |
| TEST-VSOC-54 | HANDLEDとREJECTの終端 | root・分類・deviceの3階層それぞれが終端結果を返す | 各階層で`HANDLED`と`REJECT`の計6組を実行する | 呼出し列とlast_pathが終端階層で止まる。HANDLEDは診断なしで終わり、REJECTはその階層の診断原因で終了する。FAULTへ再帰配送しない | `runtime_vsoc.md` `dispatch-interrupt-event` |
| TEST-VSOC-55 | WASIポーリングとの分離 | vIRQイベントとHALポーリングハンドルが同時に存在 | 両経路を独立して処理 | vIRQ配送が`poll-check`/`poll-wait`を起動せず、ポーリングがvIRQ登録を変更しない | [`interface_wit.md`](docs/components/tier3_platform/interface_wit.md) のポーリング契約 |
| TEST-VSOC-56 | 再スケジュール世代境界でのCOOS再開可能実行 | C++ dispatch中にCOOSの再スケジュール世代が更新され、LOOP後方分岐yieldしきい値にも達する | `System.run_guest()`をCOOSタスクとして実行し、別タスクの実行後にゲストを再開する | 再スケジュール世代はhandlerごとに読まず、後方分岐しきい値でC++ dispatchが戻った後にCOOS境界で観測する。同じ実行コンテキストから再開して結果を保持し、別タスクはゲスト完了前に実行される | `{ADR_InterruptRescheduleGeneration}` `{ADR_LoopBackedgeYield}` |
| TEST-VSOC-25 | ブレークポイントヒットでDebugging状態へ | 任意の実行状態 | ブレークポイント到達 | `(any)→Debugging` | - |
| TEST-VSOC-26 | resume(interp)でインタープリタ実行を継続 | Debugging状態 | `resume(interp)`を呼ぶ | PCを保持したままInterpreterRunへ遷移し、JITキャッシュ操作を行わない | `{VSOC_Lifecycle}` |

### マルチモジュール動的リンク
<!-- traceability: {MultiModule_Support} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-30 | インポートセクションからのシンボル解決 | 複数モジュールロード済み | `resolve_symbol(module_name, func_name)` | Module Registryを介して正しく解決される | `MultiModule_Support` |
| TEST-VSOC-31 | インタープリタテーブルへのパッチ | シンボル解決成功 | `patch_interp_table(func_addr)` | 呼び出し先アドレスが正しくパッチされる | `interpreter.md` |

### `fireball_call`シグネチャの整合性

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VSOC-40 | `fireball_call` host-call の引数個数とパッキング | 汎用システムコール発行 | `fireball:host/trap` import の受け渡しを検証 | `fireball_call(id, arg0..arg5)`（計7引数）として統一され、6つの汎用引数がvMMIOレジスタを経由せずホストハンドラへ直接渡る。vIRQ/vDMA専用host callはこのABIに含めない | `{Syscall_Mapping}` |

### 実装上の注意点に対応する検証
<!-- traceability: {GOTCHA-VSOC-01} {GOTCHA-VSOC-02} {GOTCHA-VSOC-03} {Interpreter_LazyJITSwitch} {ADR_LoopBackedgeYield} {ExecutionContext_Layout} {EnvironmentPointer} {VsocRuntime_Layout} {CPS_4Args} -->

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-VSOC-01 | 命令handler後のディスパッチ | handlerが分岐後のPCを確定した状態 | ディスパッチの制御遷移を確認する | ディスパッチャは同じ実行区間内で次PCのtraceまたはhandlerを実行し、命令ごとにはRuntimeEngineへ戻らない | `{JIT_BackedgeYield}`, [`runtime_vsoc.md`](docs/components/tier2_runtime/runtime_vsoc.md) |
| GOTCHA-VSOC-02 | Interpreter設定によるLOOP後方分岐yield条件 | Interpreter単独またはHybrid JIT経路 | 分岐回数と実行状態を確認する | 実行contextのLOOP後方分岐数がInterpreter設定のしきい値に達した時だけInterpreterがyield statusをRuntimeEngineへ返す。両経路はInterpreter設定の同じ条件で復帰する | [`runtime_vsoc.md`](docs/components/tier2_runtime/runtime_vsoc.md)、`{ADR_LoopBackedgeYield}` |
| GOTCHA-VSOC-03 | `execution_context` レイアウトと委譲シグネチャ | WASMスタック初期化 | コンテキストオフセットを確認する | 確認済みx86-64 ABIでは`execution_context`は96バイトであり、リニアメモリのホスト基点は`+0x48`、64bit有効サイズは`+0x50`に置く。グローバル値と幅はモジュール実行情報から参照する。`exec_trace`は`(ctx, sp, local_base, tos)`の4引数で呼び出す | [`runtime_vsoc.md`](docs/components/tier2_runtime/runtime_vsoc.md)、`{ExecutionContext_Layout}`, `{EnvironmentPointer}`, `{VsocRuntime_Layout}` |

## 3. テスト検証実績と網羅状況

### 同期copy経路の確認範囲

| 実行入口 | 実際に観測する条件 | 設計との対応と限界 |
| :--- | :--- | :--- |
| `test_linear_memory_copy_uses_cpu_memmove` | 実NativeInterpreter・既存COOS経路。offset 0〜192、長さ0〜64のHypothesisと重複・同一・非重複・ゼロ長の固定例。全65536byteと独立snapshotを比較し、vDMA呼出し0を確認する | TEST-VSOC-60/61。実メモリ末尾、vMMIO端点、非同期対象を含まない |
| `test_syscall_04_vdma_host_call_transfer` | 実guestの専用importと `memory.copy`。RAM→DYNAMIC、RAM→PASSTHROUGHの4構成で、4byteを転送し、直後loadと転送先全バッファ・隣接byteを確認する | TEST-SYS-20、TEST-VSOC-67の一部。32構成中4構成の実行に対応する。逆方向、SHM、vMMIO同士、物理cacheを含まない |
| `test_vdma_dynamic_bounds_rejection_preserves_real_buffer` | ホストハンドラの直接呼出し。DYNAMIC転送先のoffset/長さを255/2、256/1、0/257にし、ゲストRAM全体、実バッファ全体、PTEとownerを保存する | TEST-VSOC-64の一部。実guestの入口、転送元側の違反、拒否後の有効転送は本ケースで検査しない |
| `test_syscall_21_vdma_rejects_non_owner_shm_without_mutation` | 専用importへ解決したハンドラを実タスクコンテキストから直接呼ぶ。TLB未充填・所有者アクセス後の2条件で、全ゲストRAM・物理領域・owner・mapping_sizeを保存し、その後の所有者転送を確認する | TEST-SYS-21、TEST-VSOC-68/72の一部。転送先SHMの非所有者拒否を検査する。実guest、転送元拒否、実所有権移譲を含まない |
| `wasm_bulk_memory_model.py` | 同期完了、非同期保留の自己ループ、完了、可視化、guest復帰を抽象状態で表す。通常モデルとガード無効化変異を検査する | TEST-VSOC-62などの安全性モデル。実対象の開始・待機・通知・cache処理の結線を証明しない |

### vDMA転送の検証
<!-- traceability: {VDMA} {WasmFCSubset} {MemoryBoundaryCheck} {OwnershipTransfer} -->

[`test_vdma.py`](experiments/pysim/qa/tier2_runtime/test_vdma.py)は、実NativeInterpreter、既存RuntimeEngineとCOOS、HAL固定バッファ、実MemoryManagerを使う。
通常転送は実guestから `System.run_guest` を通す。
bulkの拒否は同じRuntimeEngineの既存実行境界で構造化trapを直接観測する。
内部の `AssertionError` は成功扱いにしない。
期待byte列は転送前snapshotとfixtureで定めた領域配置から導く。
製品の転送処理や端点解決処理を期待値の計算へ使わない。
全guest RAM、HALバッファ、SHM実体、物理配列、IPCRレジスタ、PTEの所有者・範囲・許可を比較する。
HALのunmap後も保存を検査する際は、試験の信頼された観測側から固定スロットのbyteをコピーする。
guestの借用ビューをunmap越しに保持しない。

| 対象契約 | 実行入口と条件 | 直接観測する状態と限界 |
| :--- | :--- | :--- |
| TEST-VSOC-60/61 | `test_linear_memory_copy_uses_cpu_memmove` | 既存の生成範囲に、65536byteメモリの最後の1byte、末尾同士のゼロ長、32768byteの重複copyを固定例で加えた。全byteとvDMA呼出し0を検査する |
| TEST-VSOC-67、TEST-SYS-20 | `test_all_endpoint_directions_complete_before_guest_load`のN01〜N32 | 両入口とL/D/S/Pの全32構成で、成功結果、直後guest load、marker、全バック領域を照合する。7因子の有効672構成に現れる165水準対を含む。実機cacheの証拠にはしない |
| TEST-VSOC-67/72 | `test_guest_store_and_dma_use_same_managed_shm`の両入口 | guestの非整列32bit store/load、実SharedBlockのbyte、SHMからguestへのDMAが同じ実体を使うことを照合する |
| TEST-VSOC-64 | `test_full_endpoint_bounds_and_reuse`の88条件、両端不正の32構成、`test_u32_overflow_rejected_before_mutation`の4条件 | 元と先を別々に検査する。末尾一致と有効隣接PTEから始まる要求は成功する。実容量越え、単一要求のページ越え、u32桁あふれは拒否し、全状態を保存する。拒否後に同じ端点種別を再利用する |
| TEST-VSOC-68、TEST-SYS-21 | `test_permissions_follow_transfer_direction`の24条件、`test_non_owner_rejection_preserves_storage_and_owner_access`の18条件 | PASSTHROUGHの方向別read/write許可をcold/warmで検査する。実DYNAMICとSHMの非所有者を、元と先、両入口で拒否する。SHMはTLB充填後も検査する。所有者からの再転送は成功する。bulkはゼロ長でも同じゲートを検査する |
| TEST-VSOC-69 | `test_physical_alias_copy_has_memmove_semantics` | 手動SHMマッピングとPASSTHROUGHが同じ物理RAMを指す。仮想順と物理順の逆転、同一範囲、前後重複を、転送前byte列から導く物理配列全体と比較する。専用importの重複保証へ拡大しない |
| TEST-VSOC-70/71 | `test_copy_rejects_unsupported_and_unmapped_endpoints`の20条件、ゼロ長の12条件 | 元と先でFC=12、予約FC、未マップD/S/Pを拒否する。全byteと制御レジスタを保存する。有効L/D/S/Pのゼロ長とリニア末尾は成功し、末尾+1はtrapとなる |
| TEST-VSOC-72 | `test_dynamic_mapping_histories_preserve_bytes_and_access` | 必須のunmap→拒否→map→転送を含む履歴を生成する。各操作後のbyte、PTE、所有者を独立した状態と比較する。冗長なmap/unmapは発行しない |
| TEST-VSOC-72 | `test_real_shm_release_grant_claim_preserves_data_and_rejects_old_owner`の両入口 | 実SharedBlockのrelease→grant→claimを使う。TLB充填後の旧マッピングを撤去し、旧所有者を拒否する。データ保存と新所有者からの転送を照合する。所有権を失ったSharedBlockのビューは使わない |

Hypothesisは任意byte列の転送を64例、物理aliasを48例、境界拒否と再利用を48例、DYNAMIC履歴を32例まで生成する。
本実行ではそれぞれ64、48、48、32例が成功した。
aliasには3つの固定例も置く。
履歴の追加生成部分は最大12操作とし、各操作後に状態を検査する。
固定入力の境界試験と生成試験を両方実行する。
失敗時は縮小入力と再現用blobを表示する。
有限探索から、全byte列と全履歴の網羅を主張しない。

Linux x86-64・CPython 3.14.6で次を実行した。

```bash
.venv/bin/python -m pytest -q experiments/pysim/qa/tier2_runtime/test_vdma.py --hypothesis-show-statistics
```

結果は240件成功、失敗0件、skip 0件である。
隔離したPythonプロセスで、転送元と先の取り違え、末尾1byteの転送漏れ、read/write許可ゲートの欠落を個別に注入した。
3種類とも対応する状態・拒否assertが失敗した。
製品ファイルは変異検査で変更していない。
関連9ファイルの回帰428件も成功した。
### 外部vDMAサービスのモックによる検証
<!-- traceability: {VDMA} {WasmFCSubset} {MemoryBoundaryCheck} {OwnershipTransfer} {GLOBAL_InterruptWakeup} -->

実機がないことは、外部依存の状態を制御したテストを省略する理由にならない。
[`vdma_mock.py`](experiments/pysim/qa/private/tier2_runtime/vdma_mock.py)に制御可能な外部転送サービスを置く。
既存の同期 `VdmaTransfer` callableへ差し込み、実NativeInterpreterと実RuntimeHostCallGatewayを使う。
専用importと内部copyへ同じ境界契約を別々に注入する。
COOSの待機登録、割り込みFIFO、配送、TCB、別READYタスクは実実装を使う。
モックが持つ待機アダプタは、既存COOSを使ってデバイス役タスクを進め、同じnative呼出しスタックへ復帰する。
製品のSystem転送サービス、実ドライバ、cache操作の実装をこのモックで検証したとは扱わない。

| 対象契約 | 実行入口と因子 | 独立した期待値と実観測 |
| :--- | :--- | :--- |
| TEST-VSOC-62/65/73、TEST-SYS-22 | `test_controlled_target_completes_and_becomes_visible_before_guest_resumes`の36条件 | 両入口、転送先D/S/P、coherent/noncoherentモデル、即時完了または0/7回の保留を使う。保留中のguest全byteとTCBを照合し、デバイス役のREADYタスクが進む。部分転送を許し、成功markerとloadは可視化後だけ実行する |
| TEST-VSOC-62/65/73 | `test_generated_pending_prefixes_keep_guest_suspended` | 長さ4〜32のbyte列、0〜24回の保留、入口、転送先、可視性モデルを生成する。各保留・進捗・通知・可視化時点で、範囲外を含む全byteを比較する。無条件の有限完了は主張しない |
| TEST-VSOC-63 | `test_started_target_failure_preserves_partial_writes_and_skips_success`の24条件 | 両入口、D/S/P、即時/遅延、開始後0/3byte更新を使う。専用importはモックが定めるIOを返す。bulkはその結果をWASM trapへ変換する。部分byteを保存し、成功markerを実行しない。ロールバックを要求しない |
| TEST-VSOC-66 | `test_occupied_target_rejects_without_destroying_prior_transfer_and_reuses_after_stop`の6条件 | 先行するモック転送の部分byteとengine状態を保存する。占有時のAGAINはこのモック対象だけの契約である。拒否時の全状態を保存し、明示的な停止確認後に同じ端点で有効転送を行う |

遅延モデルは異なる待機キーの通知を先に投函する。
その通知では対象guestを起床しない。
完了の固定5ワードをFIFOへ投函した直後も、対象TCBはBLOCKEDである。
境界配送でREADYへ移り、同じ待機側が通知を消費する。
guest向けの追加完了vIRQは登録しない。

非coherentモデルはCPU側とDMA側のbyte列を分離する。
デバイスが全転送を終えた時点でもCPU側に旧値を残し、可視化直前にguest全byteを検査する。
復帰後のguest loadと転送先全体は転送前snapshotから導く値へ一致する。
clean、barrier、invalidateの手順は外部サービスモックの入力条件である。
その呼出し記録を、製品側のcache maintenance実装の証拠へ読み替えない。
製品側の操作順序も、下位のデバイス・cache境界をモックで置換して検査できる。
特定CPUのcache命令と物理バスの効果は、別の適合確認とする。

同じLinux x86-64・CPython 3.14.6で、前項と同じスイート実行コマンドを使った。
固定例66件と生成試験1件を含む。結果は307件成功、失敗0件、skip 0件、所要時間1.72秒である。
生成試験は32例が成功した。
内部例数をpytest収集件数へ加算しない。
関連9ファイルの回帰488件も成功した。
実Gatewayでの失敗結果の成功化、実SchedulerでのISR即時配送、実guest loadでの誤った読出し値を隔離プロセスで注入した。
3種類すべてを検出した。
実行コマンドは前掲のvSoCスイートと同じである。

### 実行対応

| 要求・契約 | 実行入口 | 直接観測する状態 |
| :--- | :--- | :--- |
| TEST-VSOC-10 | `test_system_guest_interpreter_returns_to_coos_and_resumes`、`test_coop_01_wasm_coroutine_yields_on_loop_threshold` | 実NativeInterpreterとSystem.run_guestを使う。しきい値1/3/7の各COOS境界でguestの進捗とREADY状態を照合し、同じ呼出しの結果10を得る |
| TEST-VSOC-52 | `test_virq_unregisters_dispatcher_at_coos_boundary` | 実host callによる登録・解除の保留、割り込み待機、scheduler配送、System.dispatch_current_interruptでの反映を照合する |
| TEST-VSOC-53 | `test_virq_53_dispatches_fixed_event_through_static_hierarchy` | root/category/deviceの順序に加え、固定5ワードの原因レコードを各段で照合する |
| TEST-VSOC-55 | `test_virq_55_does_not_enter_wasi_polling_path` | 実HAL TimerのpollableとWASI03p poll、実native dispatcherを使う。poll時の登録表・guestメモリ保存と、vIRQ時のHAL処理件数保存を照合する |
| JITのidle結線 | `test_idle_01_jit_batch_compilation_on_idle` | Systemのscheduler idleからLIFO compile順、キュー空、カードとcache登録を照合する。コンパイラだけは既知結果を返す境界doubleとする |
| TEST-LOG-06 | `test_idle_02_logging_flush_on_idle` | 実idle hookによる全ログ出力とリング空を照合する |
| Interpreter/JIT・COOS・Loggingの結合 | `test_tier_01_interpreter_to_jit_cooperative_flow` | 実WASMの全12回の値通知、初期再スケジュールとLOOPしきい値での全handoff、実JIT到達、cache登録、idle後の全6ログを照合する |

LoaderとDebuggerの補助統合ケースには、実際に観測する操作と限定したケースIDを記載する。
補助driverでの実行を、通常Interpreterの命令粒度変更や全native ABIの証拠へ読み替えない。
線形CPU copyのHypothesis試験も、pytest入口と通常ランナーから実行する。

## 4. 未検証・スコープ外

- TEST-VSOC-60/61の生成範囲はoffset 0〜192・長さ0〜64である。実メモリ末尾と大きい重複copyは固定例で検査する。全入力・全履歴の網羅は未証明である。
- TEST-VSOC-68のread/write切替はPASSTHROUGHで検査する。DYNAMICと実SHMは既存APIで定めた許可と非所有者拒否を検査し、独立した全許可組合せの証拠へ拡大しない。
- TEST-VSOC-69のaliasは手動SHMとPASSTHROUGHの物理RAMマッピングを対象とする。全種別間のaliasを保証しない。TEST-VSOC-72のSHM移譲は固定のrelease→grant→claimであり、任意の所有権移譲履歴は未検証である。
- TEST-VSOC-62/63/66/73は外部転送サービスモックと実client・COOSの境界で検査した。製品のSystem転送サービスへ実非同期driverを結線した証拠にはしない。下位サービスの開始・待機・停止を検査する場合も、デバイス境界をモックで制御できる。
- 非同期対象の安全性検証を、同期対象に非同期化を要求する根拠にしない。完了待ちの転送先は途中まで書き込まれ得るため、pending中の全byte不変を受入れ条件にしない。
- DYNAMICの `map-buffer` が返すBUSYはマッピング占有の契約である。TEST-VSOC-66は対象driverが定める転送engineの開始拒否だけを検査し、全vDMA共通のBUSY結果を新設しない。
- 通知の保持・ドロップは既存COOSの待機登録とFIFO契約に従う。未登録の通知を保持する追加経路や、ゲスト向け完了IRQを受入れ条件にしない。
- TEST-VSOC-65の成功復帰とguest loadの可視性は、CPU側とDMA側を分離した境界モックで検査した。製品driverのclean/barrier/invalidateの呼出し順序は未検証である。このソフトウェア契約の検査に実機は要しない。対象CPUのcache命令が物理的に効くことは別途確認する。

- [`runtime_vsoc_contract.wit`](docs/components/tier2_runtime/wit/runtime_vsoc_contract.wit)によるWIT型定義そのものとの整合性。
- ARMv8-M実機でのLOOP分岐回数しきい値と壁時計応答時間の対応（ハードウェア仕様・実測条件はTBD）。
- マルチコア環境でのメモリ可視性（「既知の制限」でスコープ外と明記）。
