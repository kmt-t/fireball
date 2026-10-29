# WASMインタープリタ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`interpreter.md`](docs/components/tier2_runtime/interpreter.md), [`wasm_instruction_set.md`](docs/specs/wasm_instruction_set.md)
`{ThreadedInterpreter}` は、`ctx`, `ip*`, `sp*`の3論理引数による継続渡しハンドラ方式を示す。本書では、独立した3本の値領域と関数呼出し記述子領域も検証する。共有オペランドスタックをWASM値の格納先とする。ラベルアリティに基づくスタックプルーニング、i32/i64演算、境界チェック付きメモリアクセス、およびLOOP後方分岐回数による協調yield境界も対象とする。

## 2. テストケース一覧
<!-- traceability: {InterpreterContextStackless} -->

### 継続渡しディスパッチ方式そのもの ({ThreadedInterpreter})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-01 | ハンドラの統一プロトタイプと実行境界の終了判定 | 実装コードを確認 | 各opcodeハンドラ、CPS関数型、共有コンテキストを確認 | すべてのhandlerが`ctx`, `ip*`, `sp*`, `rel*`の同一4引数・`void`関数型を持つ。`ctx`, `ip`, `sp`が3つの実行状態引数であり、`rel`はInterpreterでは注釈プール、JIT traceではtrace定数プールのカーソルである。Runtime APIは命令の状態更新を行って`bool`を返す。`true`の場合にCPS handlerが次handlerへ末尾呼び出しし、`false`の場合は継続しない。終端理由は`ctx.runtime_flags`へ記録し、実行境界は同フィールドを読む | wasm_instruction_set.md, interpreter.md |
| TEST-INTP-02 | 継続渡しの命令ディスパッチ | - | opcodeハンドラ表と命令ハンドラへの継続を確認 | 各ハンドラ末尾で次opcodeに対応する関数ポインタを固定長表から選び、同じ4引数で末尾呼び出しする。命令ごとに中央のopcode分岐へ戻らない | 同上 |
| TEST-INTP-03 | Interpreter handlerとJIT traceの関数型 | JITトレース生成 | handlerとtrace entryの型・引数、および境界の状態参照を比較 | Interpreter handlerは`ctx, ip*, sp*, rel*`の順で4引数と`void`戻り値を使う。JIT trace entryは同じ4引数で`bool`を返す。`true`なら未接続chain targetに到達した終端dispatcherがmetadata pointerを一時的に`execution_context.rel`へ渡し、`body_end_ip`を`execution_context.ip`へ設定している。Interpreter dispatcherはmetadataからstackとInterpreter `rel`を更新する。`false`ならmetadataを読まずtrap/CPS exitを処理する。x64の物理ABIは対象ABI定義に従い、ARMv8-Mの物理配置はTBD | `{ContextPointerRegister}` `{CPS_3Args}` `{PositionIndependentCode}` |
| TEST-INTP-04 | JIT trace exit後のInterpreter dispatcher継続 | JIT拡張付きInterpreterで、traceの後に未コンパイルBasicBlockと常駐traceを続ける | trace実行後、未コンパイルブロックのhandler処理と後続traceの実行を確認する | trace exit後はInterpreter dispatcherへ戻る。dispatcherが未コンパイルブロックのhandlerを実行し、後続BasicBlockでも同じdispatch内でtrace lookupと実行を続ける | `{JIT_LazyChaining}` `{JIT_RuntimeAPI}` |
| TEST-INTP-05 | Runtime APIとCPSハンドラの責務分離 | 主opcodeと`0xFC`副opcodeの実装がある | 各C++ハンドラと対応するRuntime APIを確認し、通常継続・trap・Runtime API境界を実行する | 命令意味論と状態更新はRuntime APIにある。通常時は更新済み4引数で次handlerへ継続する。trapはその場でCPS列を中断し、Runtime API所有の処理は明示的なRuntime API境界でRuntimeへ渡す。汎用fallback経路は持たない | `{ThreadedInterpreter}`, `interpreter.md`「継続渡しハンドラの実行境界」 |

### 命令実行とロード済みモジュール
<!-- traceability: {DirectBytecodeExecution} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-06 | 命令別handlerの継続ディスパッチ | WASMのi32命令列と実行コンテキスト | 直線区間、未対応の主opcode、未対応の`0xFC`副opcodeを含む命令列を実行する | 直線区間を連続実行し、yieldまたはRuntime handoff時にPCとスタック位置を保存する。未対応opcodeは`UNREACHABLE` trapで実行を中断し、PCとスタックの全wordを変更しない | `{ThreadedInterpreter}`, `interpreter.md` |
| TEST-INTP-09 | BasicBlock単位の導出値注釈プール走査 | 複数ブロックに可変長LEB128即値と固定幅float定数を含むWASMモジュールをロード済み | 各BasicBlockの開始byte offsetと注釈bytesを確認し、NativeInterpreterで各ブロックへの制御移動を実行する | Loaderが復号値を保持せず、local frame offset・分岐先block index・呼出し先index・符号拡張済み定数などの実行用導出値を格納する。可変LEB128を持つ命令だけu8幅recordを持ち、固定幅命令はopcodeから進める。固定幅float payloadは元コードから読む。制御移動を終える命令が遷移先の`ip`、block index、`rel`を確定して停止し、dispatcherは確定済み状態をそのまま使う。PC/即値offset表を使わない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)、`TEST-LOAD-58` |
| TEST-INTP-27 | ジャンプ・分岐・関数入口境界 | `br`、`br_if`、`br_table`、`call`を含むWASM | 境界命令を実行し、定義関数の呼出しと復帰時のBasicBlock index・注釈プール開始offsetを確認する | Loaderの注釈プールが分岐・構造制御・call戻り先のBasicBlock indexを持つ。`execution_context.current_block_index`とCallFrameの戻りindexからBasicBlock metadataと`rel`を直接参照し、PCとの一致をassertする。ゲスト分岐・関数呼出しからRuntimeEngineへ復帰しない | InterpreterContextStackless、`interpreter.md` |
| TEST-INTP-28 | AO-Bench全命令差分 | wasmtimeとTier 2/Tier 3を利用可能 | `aobench.py`を実行 | Float32 sanity、全AO出力、Tier 2/Tier 3の528バイト出力が完全一致する | `{META_RecoveryStrategy}` |

### 2本の独立値領域と関数呼出し記述子 ({ContextPointerRegister})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-10 | オペランド領域オーバーフロートラップ | `stack_capacity`を超えるpush | 再帰呼び出し等でオペランド領域を溢れさせる。nativeの4型constを空き0/1/2 word境界で実行する | 概念実装は`WASMTrap("STACK_OVERFLOW")`相当、pysim/nativeは`OPERAND_STACK_CAPACITY`を返す。関数入口の拒否時は本体を実行せず、stackサイズと既存wordを保持する。十分な容量のconstは宣言されたword幅だけを書き込み、隣接wordを保持する | interpreter_concept.py `ExecutionContext.push`|
| TEST-INTP-11 | オペランド領域アンダーフロートラップ | 空のオペランド領域でpop | pop操作 | `WASMTrap("STACK_UNDERFLOW")`相当 | interpreter_concept.py `ExecutionContext.pop` |
| TEST-INTP-12 | 再帰呼び出し（call）とローカル値領域 | `fact(n)`のような再帰関数 | `execute_function`で呼び出す | 各呼び出しごとに新しいローカル値領域の区画が割り当てられ、ローカル変数が互いに独立する | interpreter_concept.py `` |
| TEST-INTP-13 | 戻り値の受け渡し | 関数が1個の結果を返す | `return`実行後の呼び出し元スタック | 呼び出し元のスタックに正しく結果が積まれる | interpreter_concept.py `execute_function` |
| TEST-INTP-14 | オペランド領域とローカル値領域の容量独立性 | オペランド領域の残容量が1、ローカル値領域に空きがある | 既存のオペランド値を保持したまま引数付き関数を呼び出す | ローカル値領域へ引数を積め、関数結果と呼び出し元オペランド領域の値が正しく保持される | interpreter_concept.py ``, [`interpreter_stack_model.py`](docs/components/tier2_runtime/formal/interpreter_stack_model.py) |
| TEST-INTP-15 | 3本の値領域・関数呼出し記述子分離・関数復帰結果の形式検証 | 通常モデルと`guards=False`統合変異モデル | [`interpreter_stack_model.py`](docs/components/tier2_runtime/formal/interpreter_stack_model.py)を実行 | 通常モデルでは値領域独立性、関数呼出し記述子とローカル値の分離、関数結果保持の3性質が成立し、統合変異モデルでは各違反遷移によって3性質すべてが反証される | [`interpreter_stack_model.py`](docs/components/tier2_runtime/formal/interpreter_stack_model.py) |
| TEST-INTP-19 | 分岐時制御frame復元の形式検証 | 通常モデルと`guards=False`変異モデル | [`interpreter_control_flow_model.py`](docs/components/tier2_runtime/formal/interpreter_control_flow_model.py)を実行 | loop対象frameの維持、block対象frameの除去、オペランド高さ・分岐結果の保持、elseなし偽`if`のframe不在が成立し、各変異モデルで反証される | [`interpreter_control_flow_model.py`](docs/components/tier2_runtime/formal/interpreter_control_flow_model.py) |
| TEST-INTP-16 | ネストしたcalleeの戻り値と関数呼出し記述子の復帰 | callerがcalleeを呼び、calleeがi32/i64/f32/f64を返す | calleeの`return`処理を実行 | 戻り値は共有オペランド領域へ残り、calleeの関数呼出し記述子が取り除かれ、call helperが復帰sentinelを消費してcallerへ戻る。ホストABIの戻り値規約とWASM共有領域の戻り値規約を混同しない。x64の物理ABIは定義済み、ARMv8-MはTBD | `interpreter.md` 関数復帰の番兵 |
| TEST-INTP-17 | トップレベル復帰のRETURN sentinel | 最外周WASM関数がreturnする | return handlerとRuntimeEngineを実行 | sentinelはInterpreterのreturn handlerだけが生成し、RuntimeEngineが実行完了を判定する。JITはsentinelを生成しない | `interpreter.md` 関数復帰の番兵 |
| TEST-INTP-18 | 関数呼出し記述子とローカル値領域の分離 | callerが引数付きcalleeを呼び出す | call helperでcalleeの実行区画を開始し、calleeから復帰する | 記述子は独立した領域に置かれ、`local_base`はローカル値領域内のフレーム先頭を指す。ローカル値領域にはローカル値だけが入り、復帰時に記述子を取り除いて`local_stack_pointer`を保存位置へ戻す | `interpreter.md` 関数呼び出し境界、`interpreter_concept.py` |
| TEST-INTP-82 | 静的分岐edgeによるstack復元 | 入れ子の`block`/`loop`と条件分岐を含むWASM | 複数の`end`を越える静的後続PCへ分岐する | Loaderが記録したtarget block index、stack base、結果word数から分岐先とstackを直接復元し、実行時制御フレームを使わない | `interpreter.md` 分岐処理 |
| TEST-INTP-78 | 間接呼出しの選択先型と副作用 | 型の混在、重複型定義、未初期化、範囲外を含む不変テーブル | Python参照とNativeで間接呼出しを実行する。引数・結果の型、個数、ワード幅を変える | 適合する選択先だけを呼び、未選択の別型は許可する。不一致はtrapコード8となり、本体のglobal更新とhost callbackを実行しない | `interpreter.md` 関数呼出し境界、`indirect_call_concept.py`、`interpreter_indirect_call_model.py` |
| TEST-INTP-84 | 左から右の評価順 | 固定revisionの公式`left-to-right.wast` | 全95アサーションをPython参照とNativeで実行する | メモリに残る副作用と結果値が全ケースで期待値と一致し、skipは0件となる | `Wasm32Only`、`.py` |
| TEST-INTP-80 | 関数入口のオペランド容量検査 | 最大値数をi64で渡す入口とネストした呼出し、混在幅の最大高、callerの生存値、callee引数、先行trapとglobal更新を持つ関数 | Python参照とNativeで最大値数のトップレベル引数・ネスト呼出し、および十分な容量と不足する容量を実行する | `FB_CONF_MAX_VALUE_STACK` 個のi64引数は各2 wordで格納される。トップレベル引数の符号化とcallee引数の転送は最大2 × `FB_CONF_MAX_VALUE_STACK` wordに収まる。入口SPは引数を除いて一度検査し、不足時は本体の副作用と先行WASM trapより容量trapを優先する。十分な容量では本体を実行し、先行するglobal更新と元のWASM trapを観測する。trap後の副作用を実行しない | `interpreter_stack_model.py`、``、`` |
| TEST-INTP-81 | 意味別整数型とBasicBlock index ABI | x64 Interpreter ABIと16bit BasicBlock indexを持つロード済みmodule | ctypes構造体のサイズ・offsetを検査し、Pythonの`Module.get_block()`とNativeの注釈済みindex遷移を確認する | `wasm_pc_t`と`code_ip_t`はu32、block/local indexはu16、8bit word/depth/widthは上限内である。Pythonのblock lookupとNativeの実行状態は同じBasicBlock番号・PCを示す。Native ModuleExecutionViewにPC検索配列を持たない | `integer_types.hxx`, `integer_types.py`, `interpreter_abi.hxx`, `.py`, `` |

### ラベルアリティ・スタックプルーニング (`prune_stack`)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-20 | `block (result i32)`から`br`で抜ける際のスタックプルーニング | `block`内で複数値をpushしてから`br 0` | ブロック終端まで実行 | ブロック開始時の高さまでロールバックしつつ、ブロックの宣言アリティ分（末尾のN個）の値だけが保持される | interpreter_concept.py ``（`i32.const 10; i32.const 42; br 0`→結果42のみ残る） |
| TEST-INTP-21 | void結果のブロックからの脱出 | `block`の結果型が空 | `br`で脱出 | ブロック開始時の高さまで完全にロールバックされる（保持する値なし） | wasm_instruction_set.md br のスタック遷移 `[] -> []` |
| TEST-INTP-22 | br_tableでの多重ネストとプルーニング | 3階層以上ネストしたblock+br_table | 各indexで実行 | 深さに応じた正しいプルーニング＋分岐先ジャンプが行われる | interpreter_concept.py `` |
| TEST-INTP-23 | ループ背進辺でのプルーニング挙動 | `loop`から`br 0`（継続） | 実行 | ループ本体の先頭へ戻り、ループ自身のアリティに応じたプルーニングが行われる | interpreter_concept.py `br`の`is_loop`分岐 |
| TEST-INTP-24 | `if`条件偽（elseなし）でのフレームリーク防止 | `if (cond=0)` で else 節なし | `if` 命令実行 | `match_offset + 1` へジャンプする際、`_Frame("if")` がスタックに残らずフレームスタックの深さが不変に保たれる | `interpreter.md` |
| TEST-INTP-25 | `if-else` 条件偽での静的else target遷移 | `if (cond=0)` でelse節あり | `if` 命令を実行する | Loaderが記録したelse targetへ移り、実行時制御フレームを生成せずEND後へ継続する | `interpreter.md` |
| TEST-INTP-26 | `if` 内からの`br`脱出と静的stack復元 | `loop`内に`if`を配置し`br 1`で脱出 | ループ内を実行する | 静的branch edgeが`if`と`loop`を越えるtargetとstack baseを選び、後続命令が正しい結果を返す | `interpreter.md` |

### i64整数演算 (interpreter_concept.py)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-30 | `i64.const`/`i64.add`/`i64.sub`/`i64.mul` | - | 64bit範囲の値で演算 | 64bitでラップアラウンドする（32bitではない） | interpreter_concept.py `` |
| TEST-INTP-31 | `i64.div_s`/`div_u`/`rem_s`/`rem_u`のゼロ除算 | 除数0 | 演算実行 | `WASMTrap("INTEGER_DIVIDE_BY_ZERO")` | interpreter_concept.py i64.div系 |
| TEST-INTP-32 | `i64.clz`/`ctz`/`popcnt` | 既知のビットパターン | 演算実行 | 64bit幅で正しいビットカウントを返す | interpreter_concept.py |
| TEST-INTP-33 | `i64.shl`/`shr_s`/`shr_u`/`rotl`/`rotr`のシフト量マスク | シフト量>63 | 演算実行 | シフト量が`& 63`でマスクされる（32ではない） | interpreter_concept.py |
| TEST-INTP-34 | `i32.wrap_i64` | i64値 | 変換実行 | 下位32bitのみ抽出 | interpreter_concept.py |
| TEST-INTP-35 | `i64.extend_i32_s`/`extend_i32_u` | i32値（符号付き/符号なし） | 変換実行 | 符号拡張/ゼロ拡張された64bit値になる | interpreter_concept.py |

### メモリアクセス全幅 ({MemoryBoundaryCheck})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-40 | `i64.load`/`i64.store` | メモリ確保済み | 8バイト境界内アクセス | 正しく読み書きされる | interpreter_concept.py |
| TEST-INTP-41 | `i64.load8_s/u`, `load16_s/u`, `load32_s/u` | 同上 | 各幅でアクセス | 符号/ゼロ拡張が幅ごとに正しい | interpreter_concept.py |
| TEST-INTP-42 | `i64.store8/16/32` | 同上 | 各幅で書き込み | 指定幅のみ書き込まれ、他バイトは変化しない | interpreter_concept.py |
| TEST-INTP-43 | 全幅共通の境界外トラップ | `addr + width > len(memory)` | 各load/store | `WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")` | interpreter_concept.py 全load/store |

### LOOP後方分岐のyield threshold

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-50 | LOOP後方分岐回数による協調yield | Interpreter構成の後方分岐しきい値が設定され、実行contextの回数が未到達 | InterpreterまたはHybrid JITでループを実行する | Interpreter dispatcherが取得したLOOP後方分岐を数え、しきい値到達時にyield statusをRuntimeEngineへ返す。命令ごとにはRuntimeEngineへ戻らない | `runtime_vsoc.md`「後方分岐とyield回数」 |
| TEST-INTP-51 | LOOP後方分岐yieldしきい値未満で実行継続 | 実行contextの後方分岐回数がInterpreter設定のしきい値未満 | ループを実行 | C++ dispatcherはhandlerと常駐JIT traceを同一実行内で継続し、しきい値前にRuntimeEngineやCOOSへyieldしない | `runtime_vsoc.md`「後方分岐とyield回数」 |

### デバッグ構成とインタープリタ専用実行 ({DebuggerInterpreterComposition})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-60 | デバッグ未アタッチ時のゼロオーバーヘッド | デバッガ未接続 (`is_debug_mode=False`) | 通常構成を実行 | 通常構成はデバッガを保持せず、インタープリタの命令意味論へデバッグ分岐を追加しない | `{DebuggerInterpreterComposition}` |
| TEST-INTP-61 | デバッグ構成の実行器固定 | Debuggerを接続したNativeInterpreter | native dispatch前に実行拡張を接続しようとする | Interpreterがassertで拒否し、デバッグ実行にJIT拡張を接続しない | `{DebuggerInterpreterComposition}` |
| TEST-INTP-62 | アタッチ中のブレークポイント検知・停止 | インタープリタとデバッガが接続され、PC=0x100 にブレークポイント設定 | 実行継続 | インタープリタ実行境界でブレークポイントを検知し、実行を中断して停止状態（SIGTRAP）へ遷移する | `{DebuggerInterpreterComposition}` |
| TEST-INTP-65 | アタッチ中のインタープリタ専用実行 | デバッグ構成でデバッガアタッチ中 | `step` または `continue` を実行 | アタッチ中はInterpreter handlerだけが実行され、JITへの動的切替が発生しない | `{DebuggerInterpreterComposition}` |

### ROM/Flash バイトコード直接フェッチと即値プール (DirectBytecodeExecution)
<!-- traceability: {CallFrame_Layout} {DirectBytecodeExecution} {GOTCHA-INTP-22} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-70 | 命令オブジェクト非生成とopcode直接フェッチ | WASM関数実行 | 実行ループのopcodeフェッチを確認 | 関数内カーソルからコードを直接読み出し、中間`Instr`オブジェクトやopcode逆引き表を使わない。Runtime・Debuggerへ渡すPCは関数開始PCと`ctx.ip`から得る | `interpreter.md` `DirectBytecodeExecution` |
| TEST-INTP-71 | BasicBlock単位の導出値注釈プール走査と遷移先`rel`確定 | 複数ブロックにLEB128即値と固定幅float定数を持ち、呼出し後に即値命令を実行する関数 | Loader生成後のブロック別開始byte offsetと注釈bytesを確認し、NativeInterpreterで命令と呼出し復帰を実行する | LEB128の復号値を保持せず、実行用の導出値を格納する。可変長即値を持つ命令だけu8幅recordを使い、固定幅命令はopcodeから進む。branchはu16 block index、`br_table`はu16 label countに続き各ラベルのu8制御深さとu16 block indexを使う。固定幅float payloadは元コードから読む。制御移動を終える命令が遷移先の`ip`、block index、`rel`を確定して停止し、dispatcherは確定済み状態をそのまま使う。命令ごとのPC/注釈offset表を持たない | `interpreter.md`「Interpreter注釈プール」、TEST-LOAD-58 |
| TEST-INTP-83 | br_table注釈件数と容量計算の一致 | label countが0、1、255、256のbr_tableと、255ラベルを選択する実行可能な関数 | 各入力をロードし、計算容量と書込byte数を照合する。NativeInterpreterでdefault、1ラベル、255ラベルの分岐先を実行する | 格納形式はu16 label countとdefaultを含む`label_count + 1`個の3-byte記録（u8制御深さ、u16 block index）である。計算容量は`2 + 3 × (label_count + 1)` byteで実書込量と一致し、分岐結果とtrapは既存意味論を保つ | `interpreter.md`「Interpreter注釈プール」、``, `` |
| TEST-INTP-72 | 静的制御表によるブロック境界解決 | `block/loop/if` 構文の実行 | 分岐および終了時の遷移を確認 | モジュールロード時に事前計算された疎なdelimiterとbranch edge metadataを参照し、実行時制御フレームを持たず、全命令再デコードを行わない | `interpreter.md` |
| TEST-INTP-73 | ローカルoffsetと型からの幅導出 | i32/f32/i64/f64が混在する関数とローカルなしの関数 | ロード後の各ローカルoffset、保持WASM型、`local_slot_count_cache`を確認し、読み書きする | 幅1または2 wordを保持済みWASM型から導出する。offsetが先行ローカルの型幅の合計に一致し、個別のsize表なしで値と隣接境界を保持する | GOTCHA-INTP-22 |
| TEST-INTP-74 | wide localを含む再帰関数のローカル領域境界 | 16個のi32値を持つ関数と、さらにf64を1個持つ関数 | 8フレーム分の再帰を実行する | i32のみの版は128ワードに収まり成功する。f64を含む版は1フレームあたり18ワードとなり、8フレームで128ワードを超えて容量超過で停止する | GOTCHA-INTP-22 |
| TEST-INTP-75 | 縮小CallFrame ABIと固定順序 | 関数を1つ開始し、CallStackが空 | `_build_frame` 後にコンテキスト、CallFrame、function viewを検査する | `call_stack` がコンテキストへ接続される。40-byte CallFrameはfunction view、local base、operand stack base、復帰IP、JIT境界PC、復帰block indexだけを持ち、関数番号はfunction viewから参照し、コード・ローカル配置・引数数・結果アリティはfunction viewから参照する。終了後はCallStack自身の深さが0へ戻る | `interpreter.md` `CallFrame_Layout` `{ExecutionContext_Layout}` |
| TEST-INTP-76 | LOOP辺のBasicBlock index継続とyield | 同じloop headへ繰り返し戻るWASM関数 | Native Interpreterで実行し、yield前後のPC、loop count、結果とBasicBlock indexの再開契約を確認する | backedge終端でloop headの`ip`、block index、`rel`を確定して境界停止する。遷移先block indexとPCの一致は、遷移先を確定する側でassertする。block PC検索cacheは使わず、しきい値到達時にyieldし、再開後に実行を完了する | `interpreter.md`「BasicBlock注釈開始位置」、`runtime_loader.md` `block_storage` |
| TEST-INTP-77 | LOOP後方分岐しきい値と再開 | LOOP後方辺を持つWASM関数がある | しきい値の2倍を超える後方分岐を実行し、yield後に再開する | しきい値到達ごとにyield状態を返し、再開時に回数を0へ戻す。最終的に実行が完了する | `{ADR_LoopBackedgeYield}`, `runtime_vsoc.md` |

| TEST-INTP-79 | assert無効化時のcopy/fill継続 | `NDEBUG`でビルドしたNative Interpreterとリニアメモリ | `memory.copy`と`memory.fill`の直後に結果を返す命令を実行する | 注釈カーソル更新がassert無効化でも残る。関数結果42、宛先4byteの更新、コピー元と周辺メモリの保持を確認する | `{WasmFCSubset}`、`` |

### 実装上の注意点に対応する検証
<!-- traceability: {InterpreterContextStackless} {JIT_RuntimeAPI} {GOTCHA-INTP-01} {GOTCHA-INTP-02} {GOTCHA-INTP-03} {WasmCodeSectionPC} {GOTCHA-INTP-04} {GOTCHA-INTP-05} {GOTCHA-INTP-06} -->

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-INTP-01 | 共有オペランドスタックを正本とするハンドラ状態 | スタック空状態から複数回の push/pop | `i32.const` および二項演算を連続実行 | 各値がpush/popごとに共有オペランドスタックへ反映され、次命令は`sp`から現行の頂点を読む | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)「実行コンテキスト」, `{CPS_3Args}` |
| GOTCHA-INTP-02 | Label Arity スタック巻き戻し時の結果値復元 | `block (result i32)` 内で値をpush後に`br 0` | ブロック脱出を実行 | ブロック開始時の深さまでスタックが巻き戻され、宣言アリティ分の結果値が共有オペランドスタックへ復元される。破棄された値は残らない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| GOTCHA-INTP-03 | if条件偽時の静的end target | `if (cond=0)` で else 節なし | `if` 命令を実行 | Loaderが記録したend後targetへ移り、実行時制御フレームを参照しない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| GOTCHA-INTP-04 | Code section 相対PCとモジュールスコープ | Code section payloadより前に複数関数bodyがあり、対象関数のbody sizeとlocals宣言を含むWASMモジュールを用意する | 先行bodyと対象命令のファイル位置、Code section payload先頭位置、Loaderが返すPCを比較し、別モジュールで同じPC値になる基本ブロックも照合する | PCは命令先頭のファイル位置からCode section payload先頭のファイル位置を引いた値である。関数数・body size・locals宣言の符号化バイトを含み、関数内オフセットを使わない。別モジュールで同じPC値を許し、モジュール横断のJIT・履歴キーは`(module_id, pc)`で区別する | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| GOTCHA-INTP-05 | 実行時の命令オブジェクトと逆引き表の排除 | 関数呼び出しおよび命令ステップ実行 | opcodeフェッチと注釈プール開始位置の選択を確認する | opcodeを関数コードから直接読み、中間命令オブジェクトや命令PCごとの逆引き表を使わない。即値はLoader生成済み注釈プールから読む。 | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) `{DirectBytecodeExecution}` |
| GOTCHA-INTP-06 | JIT終端命令の分岐と静的edge復元 | 入れ子のloop/ifを持つ関数で、内側の分岐条件ブロックがコンパイル済み | JIT実行後、条件分岐を複数回通過する | 終端命令PCから命令handlerを呼び、Runtime APIが残余条件を一度だけ消費し、注釈済みbranch edgeから分岐先とstackを復元する。Interpreter単独実行と同じ結果になる | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)、`{InterpreterContextStackless}`, `{JIT_RuntimeAPI}` |

### 継続状態と資源境界の検証
<!-- traceability: {GOTCHA-INTP-07} {GOTCHA-INTP-08} {GOTCHA-INTP-09} {GOTCHA-INTP-10} {GOTCHA-INTP-11} {GOTCHA-INTP-12} {GOTCHA-INTP-13} {GOTCHA-INTP-14} {GOTCHA-INTP-15} {GOTCHA-INTP-16} {GOTCHA-INTP-17} {GOTCHA-INTP-18} {GOTCHA-INTP-19} {GOTCHA-INTP-20} {GOTCHA-INTP-21} {GOTCHA-INTP-22} -->

各行はコンポーネント仕様書のGOTCHAを参照する検証項目である。確認状況は既存の記録を保持する。

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-INTP-07 | Opcode属性と継続ディスパッチ | 複数処理区分のopcodeがある | 属性表、独立したハンドラ表、対応handlerへの継続を検査する | 属性表は関数ポインタを持たず、ハンドラ表から選んだhandlerへ3つの実行状態引数と、Interpreterでは注釈プール、JIT traceではtrace定数プールを指すカーソル`rel`が末尾継続で渡る | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 実装済み・テスト済み |
| GOTCHA-INTP-08 | コンテキスト終了理由の判定とtrap伝播 | 複数の正常命令の後にtrapを起こす命令がある | 正常handlerの連鎖後にtrap命令を実行し、native実行境界の結果を確認する | Interpreter Runtime APIはtrapまたは境界停止を記録して`false`を返す。CPS handlerは`false`時に次のhandlerを呼ばず、実行境界は終了理由と具体的なtrapコードを`ctx`から取得する | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)、pysim ``。確認状況: 実装済み・テスト済み |
| GOTCHA-INTP-09 | 次PCの観測元 | 連続実行、分岐、JIT境界の操作列がある | handler後のctx.ipと次の命令位置を照合する | 次PCがctx.ipと一致し、別の戻り値フィールドから再構築されない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 実装済み・テスト済み |
| GOTCHA-INTP-10 | 異なる原因のtrap報告 | vMMIO、call_indirect、整数変換、メモリ境界の異常入力がある | 各入力を実行し、実行境界の結果を確認する | 各原因が同じ結果形式の具体的なTrapとして報告される | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 実装済み・主要経路テスト済み |
| GOTCHA-INTP-11 | Interpreter/JIT境界の共有記憶領域 | JITへ移行する関数がある | 境界前後のctx、sp、local_baseと値を確認する | 同一ctxと共有値領域を参照し、JIT専用のlocals/resultコピーが発生しない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 実装済み・既存JITテストで確認 |
| GOTCHA-INTP-12 | 値スタックのraw表現 | 複数型の値がある | 型付きpush/popとraw wordを比較する | 値が復元され、スタックの要素へ型タグが追加されない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 実装済み・要追加網羅テスト |
| GOTCHA-INTP-13 | 型別ワード数の整合 | i32/f32/i64/f64の引数、戻り値、localを持つ関数がある | 引数搬送、push/pop、復帰後の値と占有ワード数を確認する | 占有ワード数が正本の型別規則と一致し、後続値が保存される | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 実装済み・既存i64テスト済み |
| GOTCHA-INTP-14 | 関数記述子の生成と実行時参照 | ロード済みの呼出し対象がある | 記述子生成後に命令実行を繰り返し、検索の発生を観測する | コード、制御表、local情報が同じ記述子から参照され、命令ごとの再探索が発生しない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 実装済み・要追加性能計測 |
| GOTCHA-INTP-15 | local操作のraw値保存 | 1/2ワードのraw値がある | local.get/local.set/local.teeの前後を比較する | 有効ワードが同じビット列で搬送され、型変換による値変更がない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 仕様反映済み・要追加直接テスト |
| GOTCHA-INTP-16 | 分岐・復帰時の一括巻き戻し | 保存高さの異なる制御ブロックがある | 分岐・復帰後の高さと値、巻き戻し操作を確認する | 保存位置とarityに一致し、要素ごとのpopループが実行されない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 実装済み・gotchaテスト済み |
| GOTCHA-INTP-17 | 内部事前条件違反の停止 | 未初期化状態、無効frame、範囲外位置がある | 各不正状態で対象APIを呼ぶ | 当該事前条件のassertで停止し、後続命令を実行しない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 実装済み・要不足箇所監査 |
| GOTCHA-INTP-18 | 注入資源による通常経路の実行 | memory、host function、vMMIOを呼び出し側から注入済み | 製品の実行APIから対象命令を実行する | 注入した資源へアクセスし、JIT専用・テスト専用の初期化を要求しない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 方針反映・要実装境界監査 |
| GOTCHA-INTP-19 | ControlMapキャッシュの容量と衝突 | 異なるキーが同じ縮約スロットへ対応する | 挿入・検索・挿入失敗を検査する | 4エントリを超えず、正本の2bit縮約式と一致する。衝突してもキーを誤認せず、挿入失敗はassertで検出する | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 実装済み・既存テスト済み |
| GOTCHA-INTP-20 | テスト用起動処理の配置監査 | 製品Interpreterとテストコードがある | 製品側APIとテストの起動処理を確認する | テストはstart/stepで状態を構成し、専用起動・検査APIが製品側へ追加されていない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 方針反映・要配置監査 |
| GOTCHA-INTP-21 | fireball_callのhost import経路 | WASMがfireball:host/trapのfireball_callを呼ぶ | IDと6引数を渡し、呼出し先・戻り値・vMMIO副作用を観測する | ホストハンドラが指定引数を受け、戻り値がWASMへ返る。SYSCTL doorbellとsyscall vectorへのアクセスがない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| GOTCHA-INTP-22 | Loaderのローカルoffsetと型幅 | i32/f32/i64/f64を混在する関数がある | ロード済みoffset、WASM型、localアクセス、占有word数、戻り値を照合する | offset表は連続配置規則と一致する。幅はWASM型から導出し、各localの値と隣接境界が保たれる | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |

## 3. テスト検証実績と網羅状況

- **継続渡しディスパッチと2本の独立値領域 (TEST-INTP-01..TEST-INTP-14)**: 3論理引数、共有オペランド領域のアンダー／オーバーフロー、ローカル値領域上の再帰呼出し、戻り値、値領域の容量独立性。CallFrameは固定CallStack上の呼出し記述子であり、制御ブロック用の実行時フレーム配列は持たない。
- **命令実行とロード済みモジュール (TEST-INTP-06, TEST-INTP-09)**: 命令別handlerの継続ディスパッチ、境界復帰、Loaderによる即値プール構築と実行時の順次消費。
- **LOOP後方分岐のyield境界 (TEST-INTP-76)**: 共有しきい値へ達したときにyieldし、再開後に実行を完了すること。
- **静的分岐先のstack復元 (TEST-INTP-82)**: 分岐先indexとPCを照合し、Loader注釈のstack baseへ復元して結果wordを保つこと。
- **ラベルアリティ & プルーニング (TEST-INTP-20..TEST-INTP-23)**: ブロック脱出、ループ背進辺、多重ネスト br_table。
- **i64全演算 & メモリアクセス (TEST-INTP-30..TEST-INTP-43)**: 64bit算術・シフト・ビットカウント・境界外トラップ。
- **LOOP後方分岐の協調yield (TEST-INTP-50..TEST-INTP-51)**: Interpreter設定の回数しきい値に達した時だけyield statusをRuntimeEngineへ返す。
- **デバッグ構成 (TEST-INTP-60..TEST-INTP-62, TEST-INTP-65)**: インタープリタ専用構成、ブレークポイント停止、およびアタッチ中のJIT不使用。

### native constの容量境界と原因の識別


拒否時はnative C ABIのstatusがtrap、原因が`OPERAND_STACK_CAPACITY`、停止PCがconstの位置、stackサイズが操作前と一致することを確認する。物理128 wordを個別sentinelで埋め、全wordを操作前後で比較する。wide constの片側だけの書込みと、利用可能容量の外側への書込みを検出する。

Native低位ABI入口も即値を実行時にデコードしない。即値を使う試験はLoaderと同じ形式の32bit値プールを明示的に渡す。切れた即値を含む不正WASMはロード時に拒否し、実行時のフォールバックデコーダでは扱わない。

2026-10-01、Linux x64、Python 3.14.6、Clang 21.1.8で、修正前は容量不足の12組がFAIL、正常・負例など19件がPASSとなった。4 constハンドラのpush失敗を具体容量trapへ分離し、nativeを再ビルドした後、次の局所検証で31件PASS、FAIL 0件、SKIP 0件を確認した。


同じ20容量境界と10負例をstandalone C ABIの一時ハーネスから実行し、AddressSanitizerとUndefinedBehaviorSanitizerの検出なしを確認した。LeakSanitizerは実行環境のprocess検査制約により動作しないため、この確認では無効にした。

### Interpreter試験の観測とケース対応

Python参照実装のhandler表を検査した結果は、native dispatcherの方式を検証した結果として扱わない。
関数名の旧番号が異なる要求を指していた試験は、実際に観測する内容へ名称と対応を修正した。

| 要求・対象 | 実行関数 | 独立した期待値と観測 |
| :--- | :--- | :--- |
| TEST-INTP-01の参照handler | ``、`` | 論理引数の名前と順序を比較し、handler結果と実行境界が具体的な`UNREACHABLE`を報告することを確認する。native dispatcherのTEST-INTP-02の証拠へ読み替えない |
| TEST-INTP-72の静的制御表 | `` | void、i32、i64のblock終端とアリティ、および疎なPython lookupの固定容量cacheを比較する。Nativeのdense per-byte control mapがないことを確認する。TEST-INTP-04のJIT復帰経路へ紐付けない |
| TEST-INTP-73の混在幅フレーム | `` | 混在型のローカルオフセット表とWASM型、引数の生ワード数、フレームのワード数と関数結果42を比較する |
| ラベルアリティによる広幅結果の保存 | `` | i64、f32、f64のblockからの分岐結果42、1.5、2.5を比較する。関数呼出し記述子の分離を扱うTEST-INTP-18の証拠へ読み替えない |
| TEST-INTP-18, TEST-INTP-75の記述子とlocal領域 | `` | 既存のフレーム構築後に40-byte native記述子のfunction viewとlocal baseを比較する。関数番号は共有function viewから読み、コードとlocal型はfunction viewから参照する。復帰後は記述子とlocal領域の長さが0へ戻ることを確認する |
| 命令のTrap原因 | ``、``、``、`` | 対象呼出しだけの`AssertionError`を捕捉し、外側で`UNREACHABLE`、`MEMORY_OUT_OF_BOUNDS`、`INTEGER_DIVIDE_BY_ZERO`、`INVALID_CONVERSION`を比較する。境界外メモリアクセスでは全メモリの不変も比較する |
| TEST-INTP-74のlocal容量 | `` | i32のみの8フレームは結果7を返し、f64を追加した8フレームは既存native入口から`LOCAL_STACK_CAPACITY`を返す。任意の内部assertを容量Trapの代替にしない |

この対応表は既存の実行入口と試験の観測対象を示す。
TEST-INTP-03の全handler/JIT戻り型比較や、TEST-INTP-04のJIT復帰は上記の補助試験で代替せず、対応するABI試験とJIT試験の範囲で確認する。

## 4. 未検証・スコープ外

- f32/f64演算（`interpreter_concept.py`自体にも実装がなく、スコープが仕様上不明瞭。README「Missing spec coverage」参照）。
- 上記const境界試験は全opcodeの容量超過、全即値、全呼出し履歴を網羅しない。JITを含む深いstackの回帰は[`jit_runtime_test_spec.md`](docs/qa/tier3_plugins/jit_runtime_test_spec.md)のTEST-JITR-61を参照する。
