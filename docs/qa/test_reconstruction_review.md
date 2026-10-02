# pysimテストの要求対応レビューと再構築

<!-- traceability: {META_BinarySearch} {FlatViewNarrowing} {PackedBitView} {GOTCHA-CONT-04} {MultiModule_Support} {IPCRouter} {MemoryBoundaryCheck} {RSPMinimalSet} {WIT_Interface_Spec} {Pairwise_Combinatorial_Testing} -->

## 1. 目的と監査範囲

2026-10-01に、仕様違反と予想外の状態破壊を検出するため、pysimの既存テストをレビューした。
要求の正本は[`requirement_list.md`](docs/requires/requirement_list.md)である。
各動作の契約はコンポーネント設計書とWasm命令仕様から導出した。
テスト仕様書のケースID、実行関数、期待値、実際に観測する状態を照合した。
本書はこの作業の証拠と未達を記録する。

対象の登録一覧は[`verification_factor_matrix.md`](docs/qa/verification_factor_matrix.md)と[`run_all.py`](experiments/pysim/qa/run_all.py)を参照する。
通常ランナーの実行対象は、各検証時点の登録一覧に従う。
要求対応の重点再構築対象は、コンテナ、スケジューラ、IPC、ローダ、vMMIO、syscallである。
Wasm差分、JITメモリ保護、libfireball、HAL、ログ、回復処理、デバッガ、実行入口も再構築した。
形式モデル全体の再監査と実機検証は、この作業の対象外である。
既存のユーザー変更を含む作業ツリーで検証した。
本書の結果を変更前のHEADやC++製品の検証実績へ読み替えない。

## 2. 判定方法

各検査は、要求、前提、操作、観測値、独立した期待値、失敗条件を持つ。
テスト名にケースIDがあるだけでは要求を満たしたと判定しない。
拒否経路では、戻り値に加えてデータ、所有権、保留状態、次の正常操作を観測する。
件数、ログの存在、内部フラグだけで機能適合を判定しない。
行・分岐カバレッジ率を受入条件に使わない。

コンテナは標準の`dict`、`set`、整数表現を参照モデルに使う。
Wasm差分は同じバイナリをwasmtimeへ渡し、結果と具体的なtrap理由を比較する。
W^XはLinuxカーネルが公開する実メモリ権限を観測する。
境界の呼出し契約は、実ポートへ渡る引数と戻り値を検査する。
内部の任意の呼出し順序を合格条件にしない。

## 3. 発見と再構築内容

| 対象 | 旧検査の問題 | 再構築した観測 | 対応する仕様 |
| :--- | :--- | :--- | :--- |
| コンテナ | 整列テストが入力を先に整列していた。TEST-CONT-15が読み取り専用契約と対応していなかった。満杯更新と拒否後の状態を操作履歴で比較していなかった | 未整列入力の全ペア保存、容量0から満杯・更新・拒否・削除・再利用・clear、借用View、全バイト保存、多段narrow、検索参照範囲 | [`system_containers_test_spec.md`](docs/qa/tier1_core/system_containers_test_spec.md) |
| スケジューラ | TEST-SCHED-02/03がspawn末尾追加とyield遷移を検査せず、容量と重複IDを検査していた | 正しい02/03を追加し、容量と重複を09/10へ対応させた。通知前後、対象起床、FIFO、世代、タイマー取消しを直接観測する | [`os_scheduler_test_spec.md`](docs/qa/tier1_core/os_scheduler_test_spec.md) |
| IPC | TEST-IPCという別のIDを使い、RBAC拒否とURI不存在を混同していた。lookupだけの検査をsendとrollbackの検査としていた | 9role×8URIの72組、拒否時のownerとmetadata保存、実送受信、到着順、送信者ID、32bit resource、echoと新規応答の所有権 | [`ipc_router_test_spec.md`](docs/qa/tier1_interface/ipc_router_test_spec.md) |
| ローダ | TEST-LOAD-20〜24の正常リンクだけを、欠落依存・型不一致・容量上限の証拠としていた。ハッシュ衝突テストに衝突がなかった | 各失敗原因を個別入力で発生させる。失敗後のwatermarkと登録を保存する。実際に衝突する2名を異なるindexで解決する | [`runtime_loader_test_spec.md`](docs/qa/tier2_runtime/runtime_loader_test_spec.md) |
| vMMIO | ケース番号と拒否原因がずれていた。TLB件数だけでは誤った物理領域へのアクセスを検出できなかった | 最終byte、非2の冪容量、越境ラップ禁止、実物理アドレスとhandler引数、cold/hit、FC間分離、権限、衝突、Revoke後の旧所有者拒否 | [`runtime_vmmio_test_spec.md`](docs/qa/tier2_runtime/runtime_vmmio_test_spec.md) |
| syscall | vIRQの保留と反映を十分に区別していなかった。IPC出力領域の重複拒否後の状態を観測していなかった | 登録・解除はcommit前に非公開。不正要求後も既存保留を保存。SHM vDMA拒否前後の全byteとownerを保存。recv重複4形状で送信待機を保存し再試行する | [`runtime_syscall_test_spec.md`](docs/qa/tier2_runtime/runtime_syscall_test_spec.md) |
| Wasm差分 | 無関係な`AssertionError`もguest trapとして成功扱いにできた | 構造化trapの具体原因、正常値、全memory byteをwasmtimeと比較する。実装例外は試験失敗にする | [`wasm_instruction_set_test_spec.md`](docs/qa/specs/wasm_instruction_set_test_spec.md) |
| JIT W^X | 内部フラグが正しければ、実OSがRWXでも合格できた | `/proc/self/maps`のRW→RX→RW→RXを確認し、変更前後の生成コードを実行する | [`jit_compiler_test_spec.md`](docs/qa/tier3_executer/jit_compiler_test_spec.md) |
| Native容量trap | JITR-61は任意の`AssertionError`を容量超過として受理していた。実際は有効な定数命令を未実装opcodeと誤報告していた | 具体的な`OPERAND_STACK_CAPACITY`を照合する。4定数型の空きword境界で停止PCと全guard領域の保存を確認する | [`interpreter_test_spec.md`](docs/qa/tier3_executer/interpreter_test_spec.md)、[`jit_runtime_test_spec.md`](docs/qa/tier3_executer/jit_runtime_test_spec.md) |
| libfireball/WIT | libfireballの試験が通常ランナーに登録されていなかった。WIT方向とホスト名解決の証拠がなかった | 全引数と戻り値のu32境界、拒否前の副作用ゼロ、専用port、4fieldの実ホスト解決、HAL worldのimport/export方向 | [`libfireball_test_spec.md`](docs/qa/tier3_platform/libfireball_test_spec.md)、[`interface_wit_test_spec.md`](docs/qa/tier3_platform/interface_wit_test_spec.md) |
| HAL IPC出力 | 成功statusとbyte数だけでは、同長の異なる内容を出力しても合格できた | offset0/17の2形状で実stdoutの全byte一致と重複なしを確認する。固定スロット全byteを保存し、unmap後のguestアクセスを拒否する | [`hal_dispatch_test_spec.md`](docs/qa/tier2_runtime/hal_dispatch_test_spec.md)、[`platform_driver_test_spec.md`](docs/qa/tier3_platform/platform_driver_test_spec.md) |
| ログ | 一部の文字列と件数だけで上書きと割り込みflushを合格にしていた。IDが別の要求へ対応していた | 固定20byteの全フィールド、独立復号、レベル境界、全FIFO順、上書き戻り値、残存順、非0関数・offsetの実trap PCを確認する | [`runtime_logging_test_spec.md`](docs/qa/tier2_runtime/runtime_logging_test_spec.md) |
| 回復処理 | 待機をno-opへ置換し、10msの引数を検査していなかった。存在しない仕様を参照していた | sleepの0.010秒と操作順、初期3操作、reset後の自動再実行禁止、IGNORE/RESTART/PANIC分岐を確認する。実TCB初期化と停止は別の未検証要求として残す | [`interface_wit_test_spec.md`](docs/qa/tier3_platform/interface_wit_test_spec.md) |
| デバッガ | QA独自opcode handlerのブロック実行を1命令ステップと呼んでいた。PCサンプルを通常構成とInterpreter統合の証拠へ割り当てていた | ブロックdriverと実DebuggerManagerの観測を区別する。1命令の期待値を持つ反例を保持する。TCP受信はchecksum終端まで待つ | [`debugger_test_spec.md`](docs/qa/tier3_plugins/debugger_test_spec.md) |
| 実行入口 | 新規スイートが登録されなくても通常ランナーが合格できた。マトリクスの集計が横断Wasm仕様を除外していた | 全試験モジュールと登録一覧をASTで照合し、未登録・重複・空登録を拒否する。成功時にもpytest集計を表示する。QA FORMATの両スコープを分母へ含める | [`integration_test_scenarios.md`](docs/qa/integration_test_scenarios.md) |

### 3.1 検出して修正したローダ不具合

実装は同名・同kindの関数を、シグネチャが異なっていてもリンクしていた。
パラメータ型、パラメータ数、結果型、結果数の反例を追加した。
後半の欠落シンボルまたは型不一致で失敗すると、先に登録した解決エントリが残っていた。
修正前の局所実行で6件の失敗を確認した。

[`loader.py`](experiments/pysim/tier2_runtime/loader.py)はモジュールごとの型番号を実シグネチャへ解決して比較する。
importした関数の再exportにも同じ比較を適用する。
リンク失敗時は仮登録を破棄する。
変更後は追加した失敗系と正常リンクがともに合格した。

### 3.2 検出して修正したNative容量trap

JITR-61の例外を厳密に検査すると、容量超過時の原因は`C++ interpreter does not implement opcode 0x41`だった。
有効な定数命令のpush失敗が、未対応命令のfallbackと同じ経路へ流れていた。
4定数型の境界試験で、修正前に12件の失敗を確認した。
[`native_interpreter.cxx`](experiments/pysim/native/tier3_executer/interpreter/native_interpreter.cxx)は、有効なi32/i64/f32/f64定数のpush失敗だけを容量trapへ変換する。
即値のデコード失敗と未対応opcodeのfallbackは維持する。
通常pushと負例も含めて検査する。

### 3.3 生成テストの探索範囲

Hypothesisを開発依存へ追加した。
TEST-CONT-17はmap・radix・byte-swap射影の3構成で、容量0〜8、最大60操作を探索する。
構成ごとの生成上限は80例である。
TEST-CONT-18は集合について同じ容量と最大60操作を80例まで探索する。
TEST-CONT-19は1/2/4bit、最大8byte、最大30書込みを各80例まで探索する。
TEST-CONT-20は最大25keyと2〜6段のnarrowを100例まで探索する。
満杯での更新と拒否、削除後の再利用、clear後の再利用には固定例も置く。
生成上限は実行された例の実測数ではない。
有限探索の合格から全入力・全履歴の網羅を主張しない。
Hypothesisは失敗入力を縮小し、再現用blobを表示する設定にした。

## 4. 製品側に残る未達と決定済みの契約

| 項目 | 確認した根拠 | 現在の扱い |
| :--- | :--- | :--- |
| 1命令デバッグ | 旧QA経路のPC=6/stack=[15]の反例を、静的デバッグ構成へ結線した | 構成済みnativeフックでPC=2/stack=[5]に停止し、同じ呼出しを継続する。通常stepの実行粒度は維持する。第5.6節を参照する |
| ABI配置 | ユーザー確認により、設計変更の文書反映漏れと確定した。native実装の144byteは、末尾のlinear-memory host baseとsizeを含む | x86_64のABI正本と関連文書を144byteへ同期した。追加2フィールドのoffsetは0x80と0x88である |
| DMA経路と完了方式 | 要求正本へlinear間CPU／vMMIO端点vDMAを同期した。内部の同期・非同期は転送対象と操作に応じる | 同期対象の全32構成、両端の拒否、物理alias、DYNAMIC履歴、実SHM移譲を追加実装した。実SHMの参照先不一致も検出して修正した。外部サービスのモックで保留、開始後失敗、engine占有、CPU可視化前の停止を検査した。製品ドライバの待機・cache操作の結線と対象CPU上の効果は別の証拠を要する。第5.9〜5.12節を参照する |
| サービスの自己再起動 | 概念モデルにはload/start/targeted restartがあるが、IPCとvSoCの試験は実サービスのTCB・heap初期化を観測しない | [`system_service_test_spec.md`](docs/qa/tier1_interface/system_service_test_spec.md)に概念証拠と実統合の未検証を分けて記載した |
| 起床先検索の計算量 | 正本のO(1)要求はREADYキューpush/popであり、割り込み待機者検索は別途評価する。`drain_interrupts`はO(T)である | QAの過剰なO(1)検索要求を修正した。待機者検索の応答時間は未計測である |
| IPC本文の非所有検索ビュー | 全体と狭めた区間を実SharedBlockへの借用へ修正した。旧ビューは実Revoke後に失効する | ルーティング表は従来からROM配列を借用していた。未達箇所をルーティング表とした旧記述は誤りである |
| ゲストC/C++のWIT ABI | WIT由来binding→object→archive→guestリンク→実NativeInterpreter実行を検査する | raw 4 importの型・u32・引数配置を検査済みである。WASI Preview1/HAL loweringと実機ABIは対象外である |

ABI配置はユーザーが確認した設計変更を関連文書へ反映した。DMAの観測境界と要求の自動再実行禁止はユーザー回答を反映した。実装・結線の欠落を未達として残す。
各テスト仕様の第4節には、上表に加えて局所スイートで未検証のケースを列挙した。
製品全体の要求適合判定は、実装・結線の未達が残るため合格にできない。
テスト側のリファクタリング完了は第5.8節で判定する。
テストの実行成功と、要求全体の達成を分けて判断する。

## 5. 検証記録

第5.1〜5.4節は初回再構築時の履歴である。ABI文書同期と修正撤回は第5.5節、既存の静的構成を使った修正は第5.6節へ記録する。
対象はLinux x86_64、CPython 3.14.6の作業ツリーである。
基点のHEADは`c42a2246ff0bac4f9e244fea7e4aef91f6c3c334`である。

### 5.1 通常ランナー

```bash
.venv/bin/python experiments/pysim/qa/run_all.py
```

31スイートが成功終了した。
pytestの集計は649件成功、0件失敗、0件skip、1件xfailである。
実行時間は約31.8秒だった。
生成テストの内部例数とペアワイズの実行行数は、pytestの収集件数へ加算しない。
xfailはTEST-DBG-10/23の既知未達である。
通常ランナーはこれを含むpytest集計を表示する。

| スイート | 成功件数 | その他 |
| :--- | ---: | :--- |
| COOS | 15 | 保持 |
| Scheduler | 18 | 再構築 |
| Containers | 35 | 再構築、Hypothesisの生成探索を含む |
| IPC Router | 111 | 再構築、72組のRBACを含む |
| Logging | 12 | 再構築 |
| Loader | 33 | 再構築 |
| JIT scoring | 3 | 保持 |
| Interpreter | 30 | 保持 |
| CPS Interpreter | 31 | 定数の容量境界と負例を追加 |
| Interop ABI | 9 | Native間の配置整合。128byte正本への適合は未達 |
| Wasm differential | 24 | 原因と全memory byteを補強 |
| Syscall | 38 | 再構築 |
| vMMIO | 25 | 再構築 |
| Recovery | 8 | 再構築 |
| vSoC | 25 | 保持。非同期DMA契約は未検証 |
| Runtime composition | 5 | ユーザー変更を保全 |
| Debugger | 11 | 1件xfail。QAブロックdriverの範囲を明示 |
| GDB remote | 2 | 実TCPとSinkの範囲を明示 |
| Guest profiler | 4 | ユーザー変更を保全 |
| Runtime event logger | 1 | 保持 |
| Memory | 13 | 保持 |
| HAL | 13 | 実出力を補強 |
| libfireball | 42 | 契約補強、通常ランナーへ登録 |
| x64 assembler | 9 | 保持 |
| x64 stencils | 31 | 実OSのW^Xを追加 |
| JIT runtime | 47 | 保持 |
| x64 JIT | 12 | 保持 |
| JIT differential | 9 | 容量例外の具体原因を補強 |
| Pairwise | 1 | 26ケース、288因子対の完全被覆を維持 |
| Gotchas | 27 | ユーザー変更を保全 |
| Entry point | 5 | 登録と仕様分母の負例を追加 |

### 5.2 限定した反証確認

製品ソースを保存したまま、一時コピーまたは試験プロセス内の差し替えで次の28変異を検査した。
全28変異で対応する試験が失敗した。
この有限集合は、全欠陥に対するmutation coverageの測定ではない。

| 対象 | 変異数 | 破壊した契約 |
| :--- | ---: | :--- |
| Containers | 6 | 満杯時の既存key更新、拒否時保存、radix再構築、隣接bit保存、narrow単調縮小、card prefilter |
| Scheduler/vMMIO/Loader | 4 | spawn末尾、境界外ラップ禁止、関数型一致、失敗時の仮登録破棄 |
| IPC | 4 | RBAC、拒否前のowner保存、sender ID、resource内容 |
| Syscall | 6 | SHM owner gate、登録と解除の反映境界、不正要求時の保留保存、recv重複拒否 |
| W^X/Wasm/HAL | 4 | 実OSをRWXへ変更、実装例外の混同、別trap原因、同長別ペイロード |
| Logging/Recovery | 4 | 中間順序、OVERWRITTEN戻り値、ms換算、初期試行上限 |

Native定数の30境界は、Clang 21.1.8でAddressSanitizerとUndefinedBehaviorSanitizerを有効にした実C ABIハーネスでも検査した。
両sanitizerで検出はなかった。
LeakSanitizerはこの環境のprocess検査制約により動作せず、`detect_leaks=0`で実行した。

### 5.3 静的検査と実行入口

変更したQAとローダの29個のPythonファイルを、`./tools/check-src.sh -g pysim <対象ファイル>`で検査した。
規約、反サボタージュ、Tier import、通常ランナーの実行は成功した。
Ruffの対象検査と`git diff --check`も成功した。
公式のC++規約検査の対象設定はnative配下を含まず、実際の検査対象は0ファイルだった。
この成功をNative C++の規約検査結果へ採用しない。
[`check_verification_matrix.py`](tools/check_verification_matrix.py)は、Tierと横断Wasmの両方の仕様を集計する。
マトリクス検査は26コンポーネント、14概念、20形式モデル、26テスト仕様、12シナリオ、ペアワイズ完全被覆を確認した。
この件数と実在性の成功を、各要求の達成へ読み替えない。

結合シナリオは`.venv/bin/python`で[`run_all.py`](experiments/pysim/qa/scenarios/run_all.py)を実行した。
12ファイルが成功終了し、pytest集計は12件成功、0件失敗、0件skip、0件xfailだった。
約11.0秒で完了した。
この成功は、RTMの未検証要求を達成へ変更しない。

QA文書と正本の51文書を公式の文書ゲートへ渡した。
Hierarchy、Formal、WIT、Evidence、Consistency、SemanticTopicは成功した。
自動Formal Gateは参照された19モデルを監査し、成功した。
これは全形式モデルの手動レビューを意味しない。
コマンドをコードブロックへ移し、追加したパス参照のFormatエラーを修正した。
修正後に同じFileLinkFormatCheckを51文書へ適用し、0件を確認した。
Traceabilityには既存の`Size_20KSLOC`の下位参照不足1件が残る。
Obligationには33件の失敗が残る。
内訳は変更文書のリスク評価の陳腐化、既存LLM監査の陳腐化とFAIL判定である。
有料のLLM再評価と判定更新は実行していない。
文書ゲート全体は未通過として記録する。

### 5.4 対象ソースの識別

pysim配下の`.py`、`.cxx`、`.hxx`と、[`check_verification_matrix.py`](tools/check_verification_matrix.py)、`pyproject.toml`、`uv.lock`、`spec-integrator.yaml`の172ファイルを識別対象とした。
リポジトリ相対パスで整列し、各パスのUTF-8、NUL、ファイルの全byte、NULを順にSHA-256へ入力した。
検証時の集約値は`ff53ddfff060cd194dabbb0295879e8fd01b36b31e7c8814d0b7df245a08862f`である。


### 5.5 ABI文書同期とデバッガ修正試行の撤回

本節は撤回時点の履歴である。後続の修正結果は第5.6節へ記録する。

ABI配置は、ユーザーが設計変更の反映漏れと確認した。
x86_64のexecution_contextは144byteであり、末尾のhost baseは0x80、sizeは0x88である。
関連するABI、vSoC、InterpreterとQAの文書へ反映した。
ARMの配置を同じ値へ確定してはいない。

デバッガ修正試行で追加した1命令API、実行時フラグ、実行セッション、QA bridgeを撤回した。
試行中の29件成功と665件成功は、仕様適合の証拠として採用しない。
通常の基本ブロック単位の実行契約を維持する。
RSPの1命令要求の反例は既知未達として残す。
デバッガ停止機構の修正は未完了である。
Tier 2の静的DIとweave、デバッグ有効構成だけへのフック挿入、ExecutionControlの結線が必要である。
これを通常実行の1命令復帰で代替しない。

撤回後の局所回帰は、Interpreter、Interop ABI、Debugger、GDB remote、Scenario 7と8の54件が成功し、1件がxfailだった。
この実行は3.37秒で完了した。
xfailは1命令停止の既知未達である。
同じ反例を`--runxfail`で実行すると、期待PC=2に対して実PC=6で1件失敗した。
撤回による回帰確認を、デバッガ不具合の修正完了へ読み替えない。
既存のInterpreterの`step`、`_step`、`step_native`、`_complete_call`とNativeInterpreterの`step`のASTをHEADと照合し、実行処理が一致することを確認した。
既存のユーザー変更はこの照合対象外の箇所で保全した。


### 5.6 既存の静的構成と停止フックによる修正

Tier 2のRuntimeComposerがデバッグ有効構成へ実行制御を結線する。
Tier 3のExecutionControlが停止要求を設定し、同じNativeInterpreterのstepを再開する。
C++ dispatcherとCPS handlerは構成時のDebugger型を受け取る。
void構成ではif constexprが停止フックを除去する。
デバッグ構成では、命令前のフックがブレークポイントまたは1命令後の停止を確定する。
停止したときだけランタイムへdebug stop statusを返す。
RSPのcはPythonの命令実行ループを持たない。
ブレークポイント配列はDebuggerが単独所有し、nativeフックは同じ配列を借用する。
通常の144byte ABIと通常stepの実行粒度は維持する。
デバッグ構成だけが実行コンテキストへ制御状態の借用スロットを結線する。
1命令実行API、追加のruntime flag、attach時のハンドラテーブル切替は導入しない。

最小反例はPC=2、stack=[5]で停止する。
2回目のsはPC=4、stack=[5,10]となる。
同じ状態からcを再開して結果15を得る。
ブロック内ブレークポイント、call/call_indirect、分岐、loop、全memory byte、即値、host import、trapと複数構成の独立性を局所回帰へ含めた。
1000組の定数とdrop、結果定数とendの2002命令は、cから既存native stepを1回呼んで完了する。
この確認は絶対実行時間の測定ではない。

32bitを超えるブレークポイントPCをRSP境界で拒否する検査も不足していた。
追加要求がassertとなり、削除要求がOKとなる2件の反例を確認した。
修正後は両方ともE01を返し、PCと固定配列を保存する。
最大PCの0xffffffffは登録でき、幅超過をPC=0へ丸めない。

修正後のDebugger、GDB remote、Scenario 7と8の局所回帰は38件が成功した。
skipとxfailは0件であり、3.22秒で完了した。
通常ランナーは31スイート、672件が成功した。
両実行には同じDebugger試験が含まれるため、件数を合算しない。
TCP応答はチェックサム2桁まで受信し、期待する剰余値を試験側で計算して照合する。
変更した12個のPythonファイルの公式検査は、規約、反サボタージュ、Tier importを通過した。
検証マトリクスの26仕様、14概念、20形式モデル、26テスト仕様、12シナリオとペアワイズ完全被覆を維持した。

Native C++はClang 21.1.8のC++23、O2とWall、Wextra、Wpedantic、Werrorでコンパイルした。
通常入口だけをリンクした実行ファイルには、debugger_aspectとデバッグ入口のシンボルが0件だった。
デバッグ入口をリンクした実行ファイルには、同じ判定条件のシンボルが残った。
これは各構成のリンク結果の確認であり、実機のROM容量や絶対時間の測定ではない。

製品ソースを変えず、一時共有ライブラリへ4種類の欠陥を入れた。
停止要求無視、ブレークポイント無視、trap PC誤記録、命令ごとの復帰は、それぞれ対応する回帰試験で失敗した。
trap PCの変異はPC=3のケースで検出し、PC=0のケースは成功した。
import失敗や収集失敗を、欠陥の検出へ数えない。
この4変異は全欠陥に対するmutation coverageではない。

51文書の公式ゲートではFormat、Hierarchy、Formal、WIT、Evidence、Consistency、SemanticTopicが成功した。
Traceabilityの下位参照不足1件とObligationの38件は未解消である。
Obligationは変更後のリスク評価の陳腐化と、既存LLM監査の陳腐化またはFAIL判定である。
有料LLM監査を再実行していないため、文書ゲート全体は未通過である。

第5.4節と同じ172ファイルの修正後の集約値は`ae417df79aa933158975749a2e4ded4a177497b2a37b1473924dcad51f410243`である。

### 5.7 確定した回答に基づくテスト修正

実行日は2026-10-01である。
Linux x86_64、CPython 3.14.6、Clang 21.1.8、llvm-ar-21、wasm-ld-21を使用した。
本節の記録は、初回再構築時のreset後再実行およびguest ABI未実装の記録を更新する。

| 対象 | 要求から導いた期待 | 実際の検査 |
| :--- | :--- | :--- |
| linear memory.copy | サイズや重複にかかわらずCPU memmove | 実NativeInterpreterで全メモリを独立snapshotと比較する。vDMA呼出しが0回であることも確認する |
| vMMIO端点copy | 両端点検査後の転送と成功復帰時のCPU可視性 | 専用guest importと内部memory.copyの両方を実行する。PASSTHROUGHとDYNAMICについて直後guest load、HAL実バッファ、隣接byteを確認する |
| DYNAMIC境界 | HAL実容量と所有権の契約 | 256byte外の3条件を拒否し、guest/実バッファ/PTE/ownerを保存する |
| DYNAMIC命令幅 | 命令の全幅が実容量内に収まること | 実NativeInterpreterの1/2/4/8byte load/storeで末尾ちょうどの値と全bytesを確認する。1byte超過は具体trapを返し、実バッファとguest RAMを保存する |
| IPC本文 | 共有実体への非所有検索ビュー | 借用時のKV読出し0回、8要素検索5回以下、狭めた区間の実体参照、Revoke/Grant後の旧借用失効を確認する |
| 内部KV構築 | 最大8個、一意なu32キー | 重複、容量外、key/valueのu32外を変更前にassertし、共有bytesを保存する。破損countを丸めない |
| 再起動 | 失敗要求を自動再実行しない | reset成功時もoperation回数を増やさず、元errorとRESTARTを返す。reset失敗時だけPANICへ進む |
| heap返却・再貸与 | 他タスクの実体を保持し、対象だけゼロ初期化 | 固定2スロット構成で対象を4回再貸与し、非重複・総貸与量・他タスクのowner/base/size/identityと全bytesを確認する |
| raw guest ABI | WITの4 import、u32、引数配置、静的リンク | WIT生成→object→archive→コンパイル済みguest→実NativeInterpreter/COOSを実行する。archiveなしリンクと未対応WITの拒否も検査する |

関連する14スイートを一括実行し、436件成功、0件失敗、0件skip、0件xfailを確認した。
既存pairwiseスイートを含む。
生成例の内部回数をpytestの件数へ加算しない。
対象ファイルの一覧は本節の対象に加え、Scheduler、HAL、Interpreter互換試験である。

```bash
.venv/bin/python -m pytest -q \
  experiments/pysim/qa/tier1_core/test_containers.py \
  experiments/pysim/qa/tier1_core/test_scheduler.py \
  experiments/pysim/qa/tier1_interface/test_ipc_router.py \
  experiments/pysim/qa/tier2_runtime/test_logging.py \
  experiments/pysim/qa/tier2_runtime/test_recovery.py \
  experiments/pysim/qa/tier2_runtime/test_syscall.py \
  experiments/pysim/qa/tier2_runtime/test_vsoc.py \
  experiments/pysim/qa/tier2_runtime/test_vmmio.py \
  experiments/pysim/qa/tier3_platform/test_hal.py \
  experiments/pysim/qa/tier3_platform/test_memory.py \
  experiments/pysim/qa/tier3_platform/test_libfireball.py \
  experiments/pysim/qa/tier3_executer/interpreter/test_interpreter.py \
  experiments/pysim/qa/tier3_executer/interpreter/test_cps_interpreter.py \
  experiments/pysim/qa/cross_cutting/test_pairwise_combinations.py
```

9つの制御した故障を、1つずつ検査対象コードへ入れて検査した。
IPCの中間コピー、重複キー許容、raw u32復元漏れ、DMA参照先誤り、load参照先誤りを検出した。
reset後再実行、heap再貸与の重複、余剰WIT interfaceの黙認、guest load幅の伝達漏れも検出した。
故障は各検査後に復元した。

変更したpysim Python 17ファイルを、正本設定を読み込んだ公式SourceAnalyzerで検査し、指摘0件を確認した。
全スイートを起動するrun_testsだけを呼出し内で無効にし、上記の関連回帰を別に実行した。
正本設定ファイルは変更しない。
先行する通常check-srcは全31スイートを自動実行し、旧Logging試験が内部APIで9要素を構築していたため1スイート失敗した。
そのfixtureを送信境界への不正count注入へ修正した。
修正後のLogging試験12件は上記436件に含まれる。
通常check-src全体の再実行合格は主張しない。

変更した形式モデル2件とコンセプト2件の公式check-srcは成功した。
形式モデルでは通常構造とguards=False変異の両方を検査した。
bulk memoryモデルにはDMA pendingの自己ループを追加し、外部デバイスの無条件な有限完了を仮定しない。
serviceモデルには失敗要求の再実行禁止を追加した。
vMMIOコンセプトはDYNAMICの実容量を記録し、製品コードと同じ3条件の境界拒否を検査する。

全89文書を対象にした通常check-docでは、Format、Traceability、Hierarchy、Formal、WIT、Evidence、Consistency、SemanticTopicの各ゲートは成功した。
検証matrixも成功した。
Obligation Gateは47件のerrorで失敗した。変更後のLLM判定の再取得、過去の未解消FAILが理由である。
全体検査のReadability warningは2件だった。
追加記録の長文は分割し、公式の個別ProseReadabilityCheckで指摘0件を確認した。
残る1件は未変更のruntime_plugin_architecture.mdにある。
LLM監査は有料のため、明示指示時だけ実行するルールに従って再実行していない。
通常check-doc全体の合格は主張しない。

非同期DMAのpending・busy・完了通知・実機cache maintenanceは、現在の即時コピー実装には結線されていない。
実サービスのload/start/fault/reboot、対象Runtime破棄、実TCB初期化、未完了IPC整理も結線されていない。
今回のテスト成功から、これらを達成済みとはしない。
割り込み待機者検索はO(T)であり、応答時間は未計測である。
READYキューpush/popのO(1)契約と混同しない。

### 5.8 既存テストのリファクタリング完了（2026-10-02）

完了対象は、pysimの登録済み31単体スイートと12統合シナリオのテスト再構築である。
要求との対応、前提条件の作成、独立した期待値、状態・副作用の検査、拒否後の保全、実行入口を確認した。
既存の実装済み契約に対して確認できたテスト側の不足を修正し、リファクタリングを完了した。
製品全体の要求達成は第4節の未達として別に追跡する。

| 対象 | 最終確認で直したテスト側の不足 |
| :--- | :--- |
| 実行・COOS | Schedulerの観測を強制するpatchと旧命令quantumの主張を除去した。実NativeInterpreterのLOOPしきい値1/3/7から実COOSへ戻り、進捗とREADY状態を照合する |
| vIRQ | 未結線poll変数による恒真検査を、実HAL TimerとWASI03p pollへ置き換えた。5ワードの原因レコードと、実host登録・解除からscheduler割り込み配送時の反映を確認する |
| idle・JIT・ログ | scheduler idleからcompileキューとログを処理する。Python側の疑似WASMループを、実WASM通知・実JIT到達・全handoffと全ログの照合へ置き換えた |
| pairwise | 既存26行を維持した。許可12行の実動作と禁止14行の生成前拒否を確認する。288組の静的計画被覆、適用外水準、独立したdebugger補助試験を文書へ記録した |
| COOS・Scheduler | RECV/RECVの二重待機、通知満杯の拒否後FIFOと世代保存、世代保留中の値移譲を補強した。READY列長0/1/4/16/64でリンク操作数の一定上限を観測する |
| Memory・HAL | 無効・drop済みclaim、所有権通知、別スロット競合と各境界拒否を検査する。全バイト・台帳・mappingの保存と、拒否後の正常操作を確認する |
| Syscall | 単調時計IDと呼出前後の64bit値、出力外保存を照合する。SHM拒否は全物理領域を比較し、EOF/close/random/iovecの状態と副作用を補強した |
| Profiler・RuntimeComposer | batchの累積欠落、Trap直後の開いたフレーム、DEBUG_STOP、固定表容量を検査する。選択した両observerへ渡る全イベントを独立期待値と照合する |
| Interpreter・JIT | 任意assertの受理を具体TrapCode照合へ変えた。JIT機械語の自己比較をABI由来のbytes・アドレス・分岐先の期待値へ変えた |
| 統合シナリオ | Scenario 6を新しい共有メモリと既存System.run_guestへ移した。全24協調境界と全100要素を比較する。Scenario 9は内部APIの容量違反と送信境界の不正countを分けた |
| 実行入口・要求対応 | vSoCの後置Hypothesis試験とInterpreterの単独入口をpytestへ統一した。広すぎるケースID、誤った物理ページの主張、実証範囲の説明を同期した |

最終の公式ソース検査は、今回変更した14試験ファイルを対象にした。
正本設定のrun_testsを有効なまま使用し、通常ランナーの全31スイートが成功した。
言語規約・サボり検査は指摘0、warning 0、検証マトリクスも成功した。
登録31ファイルのpytest収集結果は726ケースである。
この件数を要求カバレッジの割合として使用しない。

```bash
UV_CACHE_DIR=/tmp/fireball-pysim-uv-cache UV_OFFLINE=1 ./tools/check-src.sh -g pysim \
  experiments/pysim/qa/tier1_core/test_coos.py \
  experiments/pysim/qa/tier1_core/test_scheduler.py \
  experiments/pysim/qa/tier3_platform/test_memory.py \
  experiments/pysim/qa/tier3_platform/test_hal.py \
  experiments/pysim/qa/tier2_runtime/test_syscall.py \
  experiments/pysim/qa/tier2_runtime/test_vsoc.py \
  experiments/pysim/qa/tier2_runtime/test_runtime_composer.py \
  experiments/pysim/qa/tier3_plugins/profiler/test_guest_profiler.py \
  experiments/pysim/qa/cross_cutting/test_gotchas.py \
  experiments/pysim/qa/cross_cutting/test_pairwise_combinations.py \
  experiments/pysim/qa/tier3_executer/jit/test_x64_jit.py \
  experiments/pysim/qa/tier3_executer/interpreter/test_interpreter.py \
  experiments/pysim/qa/scenarios/scenario6_coos_multitask_yield.py \
  experiments/pysim/qa/scenarios/scenario9_ipc_router_and_logging.py
.venv/bin/python experiments/pysim/qa/scenarios/run_all.py
```

12統合シナリオも12件成功、失敗0、skip 0、xfail 0である。
所要時間は約7.6秒である。
環境はLinux x86_64、CPython 3.14.6である。
変更は作業ツリーに保存している。

今回の識別対象は、pysim配下のPythonファイル、C++ソース、C++ヘッダである。
guest_bindingsのPythonファイル、検証matrixツール、正本設定も含める。
第5.4節と同じパス・NUL・全byte・NULの順でSHA-256へ入力した。
識別対象は176ファイル、集約値は`8ce52afe4f918f77d7fb22a48821630d5bbedc227c8f25766a35b63b087075ab`である。

非同期DMAのbusy・保留・完了通知、実サービスの再起動結線、実機ABIなどの未達は第4節と各QAの残課題へ保持する。
Memoryの型付きslotとinvalid-owner戻り値、Profilerの方式フラグ属性と実Runtime全状態の不変性も未実装・未検証として明示した。
実装がない項目へ成功する試験を作り、達成済みとして扱わない。
通常Interpreterのstep粒度と既存ランタイム経路を維持する。

全92文書を対象にした文書検査では、Obligation Gateに49件のerrorが残った。
変更後の有料LLM判定の陳腐化、既存判定のFAIL、検証義務タグの不足が理由である。
未変更のruntime_plugin_architecture.mdにReadability warningが1件ある。
有料LLM再監査は実行していない。
文書ゲート全体の合格は主張しない。
追加記録の拡張子列がファイル名として扱われたEvidence指摘1件は、文言を修正した。
修正後に公式DanglingArtifactRefCheckで対象89文書を再検査し、指摘0件を確認した。
修正後の文書ゲート全体は再実行していない。

### 5.9 DMAの完了方式と未検証範囲の修正（2026-10-02）

ゲストから見た成功復帰は、転送完了とCPU可視性の確認後である。
ランタイム内部の同期・非同期は、転送対象と操作に応じる。
同期完了では、完了確認後に直接復帰する。
非同期完了では、既存COOS待機と完了通知の経路を使う。
FC値によるvDMA委譲と、転送実体による完了方式は別の判断である。

第5.7節で検査した即時コピー経路は、同期完了する転送の証拠として扱う。
同期であることを、実装未達の根拠にしない。
第5.7〜5.8節の非同期DMAに関する残課題は、非同期転送を伴う対象と操作に限定する。
engine占有、開始後失敗、実機cache maintenanceも、それぞれの適用条件で追跡する。

要求、命令仕様、Interpreter、vSoC、QAの記述を、この観測境界へ揃えた。
bulk memory形式モデルには、要求から同期完了へ進む遷移を追加した。
既存の非同期保留と、完了・可視化前の再開を検出する変異は保持した。
TEST-SYS-22は、非同期転送の内部完了通知を検査する契約へ対応付けた。
ゲスト向けの追加完了IRQは正本に定義されていない。

Linux x86-64・CPython 3.14.6で、修正した形式モデルを次のコマンドで実行した。
通常モデルの10性質が成立し、`guards=False`の変異は10性質すべてで検出した。

```bash
.venv/bin/python docs/specs/formal/wasm_bulk_memory_model.py
```

この変更では、pysimの製品実行経路と実行テストは変更していない。
第5.7節の実行結果は、同節に記録した検証時点の証拠として参照する。

### 5.10 vDMAのテスト設計（2026-10-02）
<!-- traceability: {VDMA} {WasmFCSubset} {MemoryBoundaryCheck} {OwnershipTransfer} -->

完了契約と内部方式に関する判断は、第5.9節の観測境界で解消している。
vDMA全体の実装・検証が完了したとは判定しない。
[`runtime_vsoc_test_spec.md`](docs/qa/tier2_runtime/runtime_vsoc_test_spec.md)へ、転送のテスト設計を保存した。
[`runtime_syscall_test_spec.md`](docs/qa/tier2_runtime/runtime_syscall_test_spec.md)から、専用importの契約を対応付けた。

正常系は、両入口と4種類の転送元・転送先による32構成を計画する。
既存の実guest転送は、RAMからDYNAMICとPASSTHROUGHへの4構成である。
DYNAMIC容量拒否の3条件とSHM非所有者拒否の2条件は、ハンドラの直接呼出しである。
この実行境界を明示し、実guest経路の証拠へ拡大しない。

全範囲と方向別権限、物理alias、ゼロ長端点、所有権変更履歴を検査対象へ加えた。
固定例、制約付きpairwise、独立snapshotを使うHypothesis試験を対応付けた。
因子表と違反仮説には、開始前拒否と開始後失敗の異なる事後条件を記載した。
非同期保留とengine占有は、該当する転送対象と操作へ適用する。
実DMAとCPU cacheの可視性は、対象構成の実機証拠を要する。

同期の全端点方向と拒否を先に実装し、非同期対象と実機証拠を後続の単位にする。
正常転送のpairwise行N01〜N32を設計した。
7因子の有効構成672通りから得る165水準対と、入口・元・先の32構成を含む。
有限因子の列挙と集合比較で、保存した全行の成立条件と網羅性を確認した。
この入力設計を使う実行コードと、追加設計の生成試験は未実装である。
本変更では製品実行経路と実行テストを変更していない。
既存の横断pairwise suiteを保持し、line/branch coverageは診断情報として扱う。

### 5.11 vDMAの実装可能なテストの追加（2026-10-02）
<!-- traceability: {VDMA} {WasmFCSubset} {MemoryBoundaryCheck} {OwnershipTransfer} {Pairwise_Combinatorial_Testing} -->

第5.10節の設計のうち、現行pysimの同期対象で実装できる範囲を実行コードへ移した。
[`test_vdma.py`](experiments/pysim/qa/tier2_runtime/test_vdma.py)を新設し、通常ランナーへ登録した。
正常32構成、境界88条件、両端不正32構成、u32桁あふれ4条件、方向別許可24条件、非所有者18条件を実guestから検査する。
ゼロ長と不適格FC、物理alias、拒否後の同じ端点の再利用も検査する。
Hypothesisでbyte列、境界、DYNAMICの操作履歴を生成する。
実MemoryManagerのSHMは固定のrelease→grant→claimで移譲を検査する。
詳細な入力と各条件の証拠は[`runtime_vsoc_test_spec.md`](docs/qa/tier2_runtime/runtime_vsoc_test_spec.md)第3節を参照する。
既存の横断pairwise suiteは保持した。

修正前に、L→実SHMの正常行N08と両入口の実SHM移譲試験が失敗した。
PTEが示す権限と所有者は正しいが、DMAはMemoryManagerのSHM実体と別の配列を更新していた。
独立snapshotでSHM実体と物理配列を両方比較するため、誤った参照先同士の往復では合格しない。
既存のPTE `mapped_storage` とMemoryManagerのマッピング通知を使い、実SHMを借用するように結線した。
Systemの転送とguest scalar load/storeは同じ実体を参照する。
新しい命令単位の実行経路、待機モデル、ゲストABIは追加していない。

Linux x86-64・CPython 3.14.6で追加スイートを実行した。

```bash
.venv/bin/python -m pytest -q experiments/pysim/qa/tier2_runtime/test_vdma.py --hypothesis-show-statistics
```

240件成功、失敗0件、skip 0件、所要時間1.25秒である。
Hypothesisの生成成功例はbyte列64、alias 48、境界48、履歴32である。
aliasには固定3例もある。
生成試験の内部例数はpytest収集件数と区別する。
有効672構成に現れる165水準対と全32方向の包含、および文書の行と実行行の一致は別テストで検査する。

変更箇所に直接関連する回帰を次のコマンドで実行した。

```bash
.venv/bin/python -m pytest -q \
  experiments/pysim/qa/tier2_runtime/test_vdma.py \
  experiments/pysim/qa/tier2_runtime/test_vsoc.py \
  experiments/pysim/qa/tier2_runtime/test_syscall.py \
  experiments/pysim/qa/tier2_runtime/test_vmmio.py \
  experiments/pysim/qa/tier3_platform/test_memory.py \
  experiments/pysim/qa/tier3_executer/interpreter/test_interpreter.py \
  experiments/pysim/qa/tier3_executer/interpreter/test_cps_interpreter.py \
  experiments/pysim/qa/cross_cutting/test_entrypoint.py \
  experiments/pysim/qa/cross_cutting/test_pairwise_combinations.py
```

結果は428件成功、失敗0件、skip 0件、所要時間2.01秒である。
登録漏れの検査と既存pairwise試験を含む。
全スイートの再実行結果としては扱わない。

隔離したPythonプロセスで3種類の変異を別々に与えた。
元と先の取り違えと末尾1byteの転送漏れはN08の全byte照合で失敗した。
read/write許可ゲートの欠落は、両入口・両方向・cold/warmの拒否12条件で失敗した。
製品ファイルは変異検査で変更していない。

変更したPython 6ファイルを正本設定と公式SourceFacadeで検査し、指摘0件を確認した。
全スイートを起動する `run_tests` だけを呼出し内で無効にし、関連回帰を上記のコマンドで実行した。
検査ルールの正本は変更していない。
スイート登録の期待数だけを31から32へ同期した。
公式ソースformatterも6ファイルで成功した。

全89文書を文書ゲートへ渡し、Format、Traceability、Hierarchy、Formal、WIT、Evidence、Consistency、SemanticTopicが成功した。
Obligationの52エラーとReadabilityの1警告は、追加前の記録と同じ指摘である。
有料LLM監査は実行していない。
文書ゲート全体の合格は主張しない。
検証マトリクスは新規スイートの登録期待数を同期した後に再検査し、成功した。
登録32スイート、コンポーネント26、コンセプト14、形式モデル20、テスト仕様26、シナリオ12の実在性と既存pairwiseの被覆が成立した。

この検証時点ではTEST-VSOC-62/63/65/66/73とTEST-SYS-22を未検証とした。
第5.12節で外部サービスのモックを追加し、保留・失敗・占有・可視化のソフトウェア契約を検査した。
製品ドライバの操作順と結線は、下位のデバイス境界をモック化して検査できる。
対象CPU上のcache操作の物理的効果は別の検証対象である。
専用importのゼロ長・重複へBulk Memoryの保証を追加しない。
同期対象に非同期化を要求しない。
今回の実行成功をvDMA全体や製品全体の完了判定へ拡大しない。

### 5.12 外部vDMAサービスのモックによる契約検査（2026-10-02）
<!-- traceability: {VDMA} {WasmFCSubset} {MemoryBoundaryCheck} {GLOBAL_InterruptWakeup} -->

実機を用意できないことは、保留・失敗・占有・CPU可視化のソフトウェア契約を検査しない理由にならない。
製品ドライバや実機の確認予定を、モック試験の省略・延期の根拠にしない。
[`vdma_mock.py`](experiments/pysim/qa/vdma_mock.py)を新設した。
既存の同期呼出し境界`VdmaTransfer`へ、制御可能な外部転送サービスを注入する。
実NativeInterpreter、RuntimeHostCallGateway、COOSを使用する。
モックが持つ待機adapterとcacheモデルは、製品`System._run_vdma`や実ドライバの実装証拠として扱わない。
製品コードとゲストABIは今回の追加で変更していない。
通常Interpreterの実行粒度も変更していない。

[`test_vdma.py`](experiments/pysim/qa/tier2_runtime/test_vdma.py)へ、固定66条件と生成試験1件を追加した。

| 検査 | 入力と直接観測する契約 |
| :--- | :--- |
| 完了と可視化の固定36条件 | 両入口、DYNAMIC・SHM・PASSTHROUGH、即時・遅延0・遅延7、coherent・非coherentを組み合わせる。保留中のguest markerとloadが進まないこと、完了後の全byteとload値を照合する |
| 保留履歴の生成試験 | Hypothesisで入口、端点、可視性モデル、0〜24回の遅延、4〜32byteの内容を生成する。部分書込みと可視化前の各段階でguest進捗と実タスク状態を観測する |
| 開始後失敗の固定24条件 | 両入口、3端点、即時・遅延、書込み0・3byteを組み合わせる。importはIO、bulkは対応する構造化trapを返す。部分データを保存し、成功後のmarkerを実行しない |
| 占有と再利用の固定6条件 | 両入口と3端点で、既存転送が1byte進んだengineを用意する。このモック対象のAGAIN拒否が元のbyte列と転送状態を壊さないこと、明示的な停止確認後に再利用できることを検査する |

遅延転送は実COOS待機へ入り、別タスクのモックproducerが転送を進める。
異なるキーの通知では起床しない。
完了イベントのFIFO投入直後はBLOCKEDと保留状態を保存する。
境界で起床した後は5wordのイベント全体を照合する。
モック内の内部キーはfixtureが所有し、ゲストvIRQを追加しない。
scriptの有限遅延を検査するための実行上限は、外部転送のtimeoutや有限完了保証ではない。
engineの再利用前には明示的な停止を確認する。
timeoutによる自動停止や自動再利用を仮定しない。

非coherentモデルはCPU側とDMA側のbyte列を分離する。
転送完了からCPU可視化までの時間差を作り、可視化前のguest停止と成功後の全byteを検査する。
モックのclean・barrier・invalidate記録は、fixtureの動作確認である。
この記録から製品ドライバの操作順の正しさを主張しない。
製品ドライバの操作順は下位デバイスをモック化して検査できる。
対象CPU上のcache操作の効果は、そのソフトウェア検査と分けて扱う。

Linux x86-64・CPython 3.14.6で、追加後のスイートを次のコマンドで実行した。

```bash
.venv/bin/python -m pytest -q experiments/pysim/qa/tier2_runtime/test_vdma.py --hypothesis-show-statistics
```

307件成功、失敗0件、skip 0件、所要時間1.72秒である。
追加した保留履歴の生成成功例は32例である。
この内部例数をpytest収集件数へ加算しない。
既存のbyte列64例、alias 48例、境界48例、DYNAMIC履歴32例も成功した。

直接関連する回帰は次のコマンドで実行した。

```bash
.venv/bin/python -m pytest -q \
  experiments/pysim/qa/tier2_runtime/test_vdma.py \
  experiments/pysim/qa/tier2_runtime/test_syscall.py \
  experiments/pysim/qa/tier2_runtime/test_vsoc.py \
  experiments/pysim/qa/tier1_core/test_coos.py \
  experiments/pysim/qa/tier1_core/test_scheduler.py \
  experiments/pysim/qa/tier3_executer/interpreter/test_interpreter.py \
  experiments/pysim/qa/tier3_executer/interpreter/test_cps_interpreter.py \
  experiments/pysim/qa/cross_cutting/test_entrypoint.py \
  experiments/pysim/qa/cross_cutting/test_pairwise_combinations.py
```

488件成功、失敗0件、skip 0件、所要時間2.27秒である。
この集計にはvDMAの307件を含む。
登録漏れの検査と既存の横断pairwise試験も含む。
全スイートの再実行結果としては扱わない。

隔離したPythonプロセスで、実Gateway・Scheduler・Interpreterへ3種類の変異を別々に与えた。
Gatewayが転送エラーを成功値へ変える変異は、開始後失敗のimport 12条件で失敗した。
ISR通知時に即座にFIFOをdrainする変異は、遅延転送12条件でBLOCKED状態の保存検査が失敗した。
転送先の1byte loadを0へ変える変異は、即時転送12条件でguestの観測byteが独立期待値と一致せず失敗した。
変異検査で製品ファイルは変更していない。

変更したPython 2ファイルを公式formatterと正本設定のSourceFacadeで検査し、静的指摘0件を確認した。
全スイートを起動する`run_tests`だけを呼出し内で無効にし、関連回帰を上記のコマンドで実行した。
検査ルールと登録スイート数は変更していない。
検証マトリクスは成功した。
全89文書の検査では、Obligationの52エラーとReadabilityの1警告が残る。
これらは追加前と同じ指摘である。
有料LLM監査は実行していない。
文書ゲート全体の合格は主張しない。
製品ドライバの非同期待機とcache操作の結線は、このサービス境界の試験では未検証である。
ソフトウェアの未検証を実機待ちへまとめず、必要な実装がある境界でモックを使って検査する。

### 5.13 Clang生成のSDKゲストによるドライバ層横断試験（2026-10-02）
<!-- traceability: {WASI_Implementation} {WASI_ScatteredIO} {WASI_InMemVFS} {IPCRouter} {HAL_Interface} {IPC_ZeroCopy} {MemoryBoundaryCheck} -->

従来のScenario 2は、手書きWASMからホストPreview1互換層、IPC、HALタスク、DummyDriverへ進む。
Scenario 11と12はPythonからの直接呼出しである。
raw Fireball guest ABIのC++試験も、WASI-SDKとwasi-libcをリンクしたguestを検査しない。
この不足を補うため、[`test_wasi_guest.py`](experiments/pysim/qa/tier3_platform/test_wasi_guest.py)を新設した。
通常pysimランナーへ登録し、期待スイート数を32から33へ同期した。

ワークロードは[`wasi_libc_probe.c`](experiments/pysim/qa/tier3_platform/guest/wasi_libc_probe.c)からClangでコンパイルする。
実SDKのheaders、crt、wasi-libc archiveを使い、writev、read、close、clock_gettimeを実行する。
ビルド入口は[`build_wasi_guest.py`](tools/guest_bindings/build_wasi_guest.py)である。
最終importとlink mapで実libcのリンクを確認する。
テスト自身はワークロードのWASM命令列を手書きしない。
ゲストは1ページのreactor構成であり、組込み製品のRAM/ROM適合証拠とは区別する。

Preview1 importを使う構成と、Fireball syscallへ接続するQA fixtureの2構成を実行する。
後者は[`wasi_syscall_fixture.cxx`](experiments/pysim/qa/tier3_platform/guest/wasi_syscall_fixture.cxx)を静的リンクする。
WIT生成の既存raw bindingとlibfireballのinline wrapperを使い、公開syscall ID 0x80〜0x83へ接続する。
このfixtureは製品libfireballのWASI/HAL guest adapterを実装したことにはならない。
製品コードと公開ABIは本追加で変更していない。

実NativeInterpreter、RuntimeEngine、Gateway、WASI変換、Tier 1 IPC/COOS、HALタスク、DummyDriverを接続する。
物理ストリームを既存の注入境界でモック化する。
ドライバ呼出し時に、実HALタスクのID、実行状態、固定スロットの実体、マップしたguestのIDを観測する。
guestの結果フィールドが更新されていないことも照合する。
結果復帰後には、全出力、返却長、全固定スロット、unmapを独立期待値と比較する。

標準出力以外のread/write/close/clockは、既存uvwasi portへ制御可能なモックを注入する。
guest libcからバックエンドまでの引数、返却byte、u64時刻、-1とerrnoの変換を検査する。
実uvwasi内部や物理ハードウェアの検証へ読み替えない。
操作経路、前提、独立oracle、要求IDは[`integration_test_scenarios.md`](docs/qa/integration_test_scenarios.md)のTEST-INT-120〜128を参照する。
実機の確認予定を、これらのモック試験の省略・延期の根拠にしない。

公式WASI-SDK 27.0 Linux x86_64の配布物を、SHA-256 `b7d4d944c88503e4f21d84af07ac293e3440b1b6210bfd7fe78e0afd92c23bc2`へ固定した。
展開先はGit管理外のbuild配下である。
既存SDKは`FIREBALL_WASI_SDK`で指定できる。
未配置の場合はテストを明示的に失敗させ、自動ダウンロードやskipを行わない。

```bash
.venv/bin/python tools/guest_bindings/build_wasi_guest.py --prepare-sdk
.venv/bin/python -m pytest -q experiments/pysim/qa/tier3_platform/test_wasi_guest.py --hypothesis-show-statistics
```

Linux x86_64、CPython 3.14.6、WASI-SDK 27.0、Clang 20.1.8で52件成功、失敗0件、skip 0件、所要時間1.71秒だった。
固定50条件と生成試験2件を含む。
生成試験は各構成32成功例であり、pytest件数へ加算しない。

直接関連する回帰を次のコマンドで実行した。

```bash
.venv/bin/python -m pytest -q \
  experiments/pysim/qa/tier3_platform/test_wasi_guest.py \
  experiments/pysim/qa/tier3_platform/test_hal.py \
  experiments/pysim/qa/tier3_platform/test_libfireball.py \
  experiments/pysim/qa/tier2_runtime/test_syscall.py \
  experiments/pysim/qa/tier1_interface/test_ipc_router.py \
  experiments/pysim/qa/cross_cutting/test_entrypoint.py \
  experiments/pysim/qa/cross_cutting/test_pairwise_combinations.py
```

最終実行は304件成功、失敗0件、skip 0件、所要時間2.82秒だった。
この集計にはSDKゲストの52件を含む。
Scenario 2、11、12の直接関連する回帰も局所実行し、3件成功、失敗0件、skip 0件、所要時間0.17秒だった。
全スイートの再実行結果としては扱わない。

隔離したプロセスで3種類の実装変異を与えた。
WASI変換がIPCを経由せず成功応答だけを返す変異は、非空のstdout 12条件で失敗した。
stderrをstdoutへ送る変異は、両構成の4条件で失敗した。
実Gatewayでiovec pointerと要素数を入れ替える変異は、Fireball構成の7条件で失敗した。
製品ファイルは変異検査で変更していない。

変更したpysim Python 2ファイルは、公式formatterと正本設定のSourceFacadeで検査し、指摘0件だった。
全スイートを起動するrun_testsだけを呼出し内で無効にし、上記の局所回帰を別に実行した。
ビルド入口はRuffで検査し、C/C++ fixtureはClangのWall/Wextra/Werrorでコンパイルした。
検証マトリクスも成功した。

製品libfireballのWASI/HAL guest adapter、Component Model lowering、実UVWASI、GPIO/I2C/SPI、IRQは未検証の範囲として残す。
SDK guestの_start、argv/environ、stdio初期化、printf、ファイルpreopenも本試験では実行しない。
これらを52件の成功で達成へ変更しない。
