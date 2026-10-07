# WASMインタープリタ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`interpreter.md`](docs/components/tier2_runtime/interpreter.md), [`wasm_instruction_set.md`](docs/specs/wasm_instruction_set.md)
`{ThreadedInterpreter}` は、4論理引数の継続渡しハンドラ方式を示す。本書では、独立した3本の値領域と関数呼出し記述子領域も検証する。ラベルアリティに基づくスタックプルーニング、i32/i64演算、境界チェック付きメモリアクセス、およびLOOP後方分岐回数による協調yield境界も対象とする。

## 2. テストケース一覧
<!-- traceability: {InterpreterContextStackless} -->

### 継続渡しディスパッチ方式そのもの ({ThreadedInterpreter})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-01 | ハンドラのシグネチャが4論理引数(`ctx, sp, local_base, tos`)である | 実装コードを確認 | 各opcodeハンドラの引数と境界結果を確認 | すべてのハンドラが同一の4引数シグネチャを持ち、継続時は次ハンドラへ末尾呼び出しする。実行境界では終了種別を返し、トラップコードは`ctx`に保持する | wasm_instruction_set.md, interpreter.md |
| TEST-INTP-02 | 継続渡しの命令ディスパッチ | - | opcodeハンドラ表と命令ハンドラへの継続を確認 | 各ハンドラ末尾で次opcodeに対応する関数ポインタを固定長表から選び、同じ4論理引数で末尾呼び出しする。命令ごとに中央のopcode分岐へ戻らない | 同上 |
| TEST-INTP-03 | Interpreter handlerとJIT traceの論理引数契約 | JITトレース生成 | handlerとtrace entryの型・引数配置を比較 | 両者は`ctx, sp, local_base, tos`の4論理引数を共有するが、Interpreter handlerは実行境界で`op_result`、JIT trace entryは`void`を返し、関数ポインタ型は分離される。x64の物理ABIは対象ABI定義に従い、ARMv8-Mの物理配置はTBD | `{ContextPointerRegister}` `{CPS_4Args}` `{PositionIndependentCode}` |
| TEST-INTP-04 | JITトレースからインタープリタへのシームレスフォールバック | 未コンパイルのブロックへ分岐 | トレース実行完了 | トレース末尾でインタープリタへスムーズに復帰し、後続ブロックをインタープリタが継続実行する | `{JIT_LazyChaining}` `{JIT_RuntimeAPI_Fallback}` |
| TEST-INTP-05 | Runtime APIとCPSハンドラの責務分離 | 主opcodeと`0xFC`副opcodeの実装がある | 各C++ハンドラと対応するRuntime APIを確認し、通常継続・trap・フォールバックを実行する | 命令意味論と状態更新はRuntime APIにあり、ハンドラはAPIを呼んで結果を返すか、更新済み4論理引数で関数ポインタ表へ末尾継続する。通常経路のAPIはインライン展開される | `{ThreadedInterpreter}`, `interpreter.md`「継続渡しハンドラの実行境界」 |

### 命令実行とロード済みモジュール

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-06 | 命令別handlerの継続ディスパッチ | WASMのi32命令列と実行コンテキスト | 直線区間、未対応の主opcode、未対応の`0xFC`副opcodeを含む命令列を実行する | 直線区間を連続実行し、境界でPCとスタック位置を返す。未対応opcodeでは共通の境界復帰handlerへ進み、スタックを変更せず戻る | `{ThreadedInterpreter}`, `interpreter.md` |
| TEST-INTP-09 | ロード時デコードと命令実行の分離 | WASMモジュールをロード済み | 実行経路が即値を処理する方法を確認する | LEB128デコードはロード時に完了し、Tier 3の命令実行経路では再デコードしない | `{DirectBytecodeExecution}`, `runtime_loader.md` |
| TEST-INTP-27 | ジャンプ・分岐境界 | `br`、`br_if`、`br_table`、`call`を含むWASM | 境界命令を実行 | 定義済みゲスト関数の呼出しと分岐はC++ハンドラ間で継続し、Runtimeが要求する境界で正しいPC・制御スタックを保って戻る | InterpreterContextStackless |
| TEST-INTP-28 | AO-Bench全命令差分 | wasmtimeとTier 2/Tier 3を利用可能 | `aobench.py`を実行 | Float32 sanity、全AO出力、Tier 2/Tier 3の528バイト出力が完全一致する | `{META_RecoveryStrategy}` |

### 3本の独立スタック・関数呼び出し ({ContextPointerRegister})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-10 | オペランド領域オーバーフロートラップ | `stack_capacity`を超えるpush | 再帰呼び出し等でオペランド領域を溢れさせる。nativeの4型constを空き0/1/2 word境界で実行する | 概念実装は`WASMTrap("STACK_OVERFLOW")`相当、pysim/nativeは`OPERAND_STACK_CAPACITY`を返す。constの拒否時はPC、stackサイズ、既存wordと隣接sentinelを保持し、未対応opcode扱いにしない | interpreter_concept.py `ExecutionContext.push`、[`test_cps_interpreter.py`](experiments/pysim/qa/tier2_runtime/interpreter/test_cps_interpreter.py) |
| TEST-INTP-11 | オペランド領域アンダーフロートラップ | 空のオペランド領域でpop | pop操作 | `WASMTrap("STACK_UNDERFLOW")`相当 | interpreter_concept.py `ExecutionContext.pop` |
| TEST-INTP-12 | 再帰呼び出し（call）とローカル値領域 | `fact(n)`のような再帰関数 | `execute_function`で呼び出す | 各呼び出しごとに新しいローカル値領域の区画が割り当てられ、ローカル変数が互いに独立する | interpreter_concept.py `test_full_wasm_recursive_factorial` |
| TEST-INTP-13 | 戻り値の受け渡し | 関数が1個の結果を返す | `return`実行後の呼び出し元スタック | 呼び出し元のスタックに正しく結果が積まれる | interpreter_concept.py `execute_function` |
| TEST-INTP-14 | オペランド領域とローカル値領域の容量独立性 | オペランド領域の残容量が1、ローカル値領域に空きがある | 既存のオペランド値を保持したまま引数付き関数を呼び出す | ローカル値領域へ引数を積め、関数結果と呼び出し元オペランド領域の値が正しく保持される | interpreter_concept.py `test_independent_operand_and_local_stacks`, [`interpreter_stack_model.py`](docs/components/tier2_runtime/formal/interpreter_stack_model.py) |
| TEST-INTP-15 | 3本の値領域・関数呼出し記述子分離・関数復帰結果の形式検証 | 通常モデルと`guards=False`統合変異モデル | [`interpreter_stack_model.py`](docs/components/tier2_runtime/formal/interpreter_stack_model.py)を実行 | 通常モデルでは値領域独立性、関数呼出し記述子とローカル値の分離、関数結果保持の3性質が成立し、統合変異モデルでは各違反遷移によって3性質すべてが反証される | [`interpreter_stack_model.py`](docs/components/tier2_runtime/formal/interpreter_stack_model.py) |
| TEST-INTP-19 | 分岐時制御frame復元の形式検証 | 通常モデルと`guards=False`変異モデル | [`interpreter_control_flow_model.py`](docs/components/tier2_runtime/formal/interpreter_control_flow_model.py)を実行 | loop対象frameの維持、block対象frameの除去、オペランド高さ・分岐結果の保持、elseなし偽`if`のframe不在が成立し、各変異モデルで反証される | [`interpreter_control_flow_model.py`](docs/components/tier2_runtime/formal/interpreter_control_flow_model.py) |
| TEST-INTP-16 | ネストしたcalleeの戻り値と関数呼出し記述子の復帰 | callerがcalleeを呼び、calleeがi32/i64/f32/f64を返す | calleeの`return`処理を実行 | 戻り値は共有オペランド領域へ残り、calleeの関数呼出し記述子が取り除かれ、call helperが復帰sentinelを消費してcallerへ戻る。ホストABIの戻り値規約とWASM共有領域の戻り値規約を混同しない。x64の物理ABIは定義済み、ARMv8-MはTBD | `interpreter.md` 関数復帰の番兵 |
| TEST-INTP-17 | トップレベル復帰のRETURN sentinel | 最外周WASM関数がreturnする | return handlerとRuntimeEngineを実行 | sentinelはInterpreterのreturn handlerだけが生成し、RuntimeEngineが実行完了を判定する。JITはsentinelを生成しない | `interpreter.md` 関数復帰の番兵 |
| TEST-INTP-18 | 関数呼出し記述子とローカル値領域の分離 | callerが引数付きcalleeを呼び出す | call helperでcalleeの実行区画を開始し、calleeから復帰する | 記述子は独立した領域に置かれ、`frame_offset`がローカル値領域の開始ワード位置を示す。ローカル値領域にはローカル値だけが入り、復帰時に記述子を取り除いて`local_offset`とローカル値領域の長さを保存位置へ戻す | `interpreter.md` 関数呼び出し境界、`interpreter_concept.py` |
| TEST-INTP-77 | 静的分岐先での制御フレーム整理 | 入れ子の`block`/`loop`と条件分岐を含むWASM | 複数の`end`を越える静的後続PCへ分岐する | 分岐先より後ろで終端を迎える制御フレームを破棄し、分岐先と一致するフレームは`end`処理へ残す | `interpreter.md` 分岐処理 |

### ラベルアリティ・スタックプルーニング (`prune_stack`)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-20 | `block (result i32)`から`br`で抜ける際のスタックプルーニング | `block`内で複数値をpushしてから`br 0` | ブロック終端まで実行 | ブロック開始時の高さまでロールバックしつつ、ブロックの宣言アリティ分（末尾のN個）の値だけが保持される | interpreter_concept.py `test_block_loop_and_stack_pruning`（`i32.const 10; i32.const 42; br 0`→結果42のみ残る） |
| TEST-INTP-21 | void結果のブロックからの脱出 | `block`の結果型が空 | `br`で脱出 | ブロック開始時の高さまで完全にロールバックされる（保持する値なし） | wasm_instruction_set.md br のスタック遷移 `[] -> []` |
| TEST-INTP-22 | br_tableでの多重ネストとプルーニング | 3階層以上ネストしたblock+br_table | 各indexで実行 | 深さに応じた正しいプルーニング＋分岐先ジャンプが行われる | interpreter_concept.py `test_br_table_and_parametric` |
| TEST-INTP-23 | ループ背進辺でのプルーニング挙動 | `loop`から`br 0`（継続） | 実行 | ループ本体の先頭へ戻り、ループ自身のアリティに応じたプルーニングが行われる | interpreter_concept.py `br`の`is_loop`分岐 |
| TEST-INTP-24 | `if`条件偽（elseなし）でのフレームリーク防止 | `if (cond=0)` で else 節なし | `if` 命令実行 | `match_offset + 1` へジャンプする際、`_Frame("if")` がスタックに残らずフレームスタックの深さが不変に保たれる | `interpreter.md` |
| TEST-INTP-25 | `if-else` 条件偽での else 節遷移 | `if (cond=0)` で else 節あり | `if` 命令実行 | `else_offset + 1` へジャンプし、`_Frame("if")` が積まれ、`END` で正しくポップされる | `interpreter.md` |
| TEST-INTP-26 | `if` 内からの `br` 脱出とフレーム Pruning | `loop` 内に `if` を配置し `br 1` 脱出 | ループ内実行 | `if` フレームと `loop` フレームが正しく unwind され、後続の命令が正しいスコープで実行される | `interpreter.md` |

### i64整数演算 (interpreter_concept.py)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-30 | `i64.const`/`i64.add`/`i64.sub`/`i64.mul` | - | 64bit範囲の値で演算 | 64bitでラップアラウンドする（32bitではない） | interpreter_concept.py `test_64bit_integer_arithmetic` |
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
| TEST-INTP-50 | LOOP後方分岐回数による協調yield | 共通コンテキストの後方分岐回数がしきい値未満 | InterpreterまたはHybrid JITでループを実行する | 取得したLOOP後方分岐を数え、しきい値到達時にRuntimeEngineへyield状態を返す。命令ごとにはRuntimeEngineへ戻らない | `runtime_vsoc.md`「後方分岐とyield回数」 |
| TEST-INTP-51 | LOOP後方分岐yieldしきい値未満で実行継続 | 共通contextの後方分岐回数がしきい値未満 | ループを実行 | C++ dispatcherはhandlerと常駐JIT traceを同一実行内で継続し、しきい値前にRuntimeEngineやCOOSへyieldしない | `runtime_vsoc.md`「後方分岐とyield回数」 |

### デバッグ構成とインタープリタ専用実行 ({DebuggerInterpreterComposition})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-60 | デバッグ未アタッチ時のゼロオーバーヘッド | デバッガ未接続 (`is_debug_mode=False`) | 通常構成を実行 | 通常構成はデバッガを保持せず、インタープリタの命令意味論へデバッグ分岐を追加しない | `{DebuggerInterpreterComposition}` |
| TEST-INTP-61 | デバッグ構成の実行器固定 | `RuntimeCompositionConfig(execution=INTERPRETER, debugger=True)` | 構成を合成して実行 | デバッガ付き構成はインタープリタだけを生成し、JIT実行器を生成しない | `{DebuggerInterpreterComposition}` |
| TEST-INTP-62 | アタッチ中のブレークポイント検知・停止 | インタープリタとデバッガが接続され、PC=0x100 にブレークポイント設定 | 実行継続 | インタープリタ実行境界でブレークポイントを検知し、実行を中断して停止状態（SIGTRAP）へ遷移する | `{DebuggerInterpreterComposition}` |
| TEST-INTP-65 | アタッチ中のインタープリタ専用実行 | デバッグ構成でデバッガアタッチ中 | `step` または `continue` を実行 | アタッチ中は常にインタープリタが実行され、JITへの動的切替が発生しない | `{DebuggerInterpreterComposition}` |

### ROM/Flash バイトコード直接デコードと命令オブジェクト生成ゼロ (DirectBytecodeExecution)
<!-- traceability: {CallFrame_Layout} {DirectBytecodeExecution} {GOTCHA-INTP-22} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-70 | 命令オブジェクト非生成とバイト直接フェッチ | WASM関数実行 | 実行ループのフェッチ処理を確認 | 関数内カーソルから `frame.code[cursor_offset]` を使って $O(1)$ で直接バイトを読み出し、中間 `Instr` オブジェクトを生成しない。コンテキストPCとの変換は `cursor_offset = pc - function_start_pc` とする | `interpreter.md` `DirectBytecodeExecution` |
| TEST-INTP-71 | 足し算による次PC進行と即値のその場デコード | 算術・即値命令実行 | `ip` の遷移を確認 | 命令長または即値長を加算した `ip + len` で直接進行し、二分探索マップ（FlatMapView）を走査しない | `interpreter.md` |
| TEST-INTP-72 | 静的制御表によるブロック境界解決 | `block/loop/if` 構文の実行 | 分岐および終了時の遷移を確認 | モジュールロード時に事前計算された静的 `control_map`（`blocks`, `br_tables`）を参照し、実行時の全命令再デコードを行わない | `interpreter.md` |
| TEST-INTP-73 | フレームごとのスロット幅の決定 | i32のみ、f32のみ、i64を含む、f64ローカルを含む、ローカルなしの関数 | ロード後の幅マップ（ローカルごとの幅を2ビットで保持）のスロット幅と `local_slot_count_cache` を確認し、各関数を実行する | i32/f32のみは1ワード、i64/f64を含むと2ワードになる。ローカルなしは1ワードでスロット数は0である。全ローカルの幅がスロット幅以下である。実行結果が正しい | GOTCHA-INTP-22 |
| TEST-INTP-74 | 32ビットのみのフレームによるローカル値領域の節約 | 16個のローカルを持つ再帰関数。一方はi32のみ、他方はf64ローカルを1個含む | 同じ再帰深さで実行する | i32のみの版は、1フレーム16ワードで7フレームが128ワードに収まり、成功する。f64を含む版は、1フレーム34ワードで7フレームが128ワードを超え、容量超過で停止する | GOTCHA-INTP-22 |
| TEST-INTP-75 | CallFrameの固定ABIと積載順序 | 関数を1つ開始し、CallStackが空 | `_build_frame` 後にコンテキストと最上位CallFrameを検査する | `call_stack` がコンテキストへ接続され、CallFrameが関数番号、コードビュー、ローカル幅・スロット数、引数個数、制御ベースの順序で保持される。終了後はCallStack自身の深さが0へ戻る | `interpreter.md` `CallFrame_Layout` `{ExecutionContext_Layout}` |
| TEST-INTP-76 | LOOP後方分岐しきい値と再開 | LOOP後方辺を持つWASM関数がある | しきい値の2倍を超える後方分岐を実行し、yield後に再開する | しきい値到達ごとにyield状態を返し、再開時に回数を0へ戻す。最終的に実行が完了する | `{ADR_LoopBackedgeYield}`, `runtime_vsoc.md` |

### 実装上の注意点に対応する検証
<!-- traceability: {InterpreterContextStackless} {JIT_RuntimeAPI_Fallback} {GOTCHA-INTP-01} {GOTCHA-INTP-02} {GOTCHA-INTP-03} {WasmCodeSectionPC} {GOTCHA-INTP-04} {GOTCHA-INTP-05} {GOTCHA-INTP-06} -->

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-INTP-01 | 継続渡し第4論理引数 `tos` とスタック領域の境界同期 | スタック空状態から複数回の push/pop | `i32.const` および二項演算を連続実行 | スタック空時は `tos=0`、値push時は旧`tos`がスタック領域へ退避され新値が`tos`に格納される。pop時は次段値が`tos`へ復元され、次命令のフェッチでは頂点を再読込しない。物理レジスタ割当は対象ABIに従い、ARMv8-MはTBD | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)「実行コンテキスト」, `{CPS_4Args}` |
| GOTCHA-INTP-02 | Label Arity スタック巻き戻し時の TOS 復元 | `block (result i32)` 内で値をpush後に`br 0` | ブロック脱出を実行 | ブロック開始時の深さまでスタックが巻き戻され、宣言アリティ分の結果値のうち最上位値が論理`tos`へ復元されて次handlerへ渡る。破棄された値が`tos`に残ってはならない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| GOTCHA-INTP-03 | if 条件偽（else節なし）での制御フレームリーク防止 | `if (cond=0)` で else 節なし | `if` 命令を実行 | `match_offset + 1` へジャンプする際、`_Frame("if")` がスタックに残らずフレームスタックの深さが不変に保たれる。 | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| GOTCHA-INTP-04 | Code section 相対PCとモジュールスコープ | Code section payloadより前に複数関数bodyがあり、対象関数のbody sizeとlocals宣言を含むWASMモジュールを用意する | 先行bodyと対象命令のファイル位置、Code section payload先頭位置、Loaderが返すPCを比較し、別モジュールで同じPC値になる基本ブロックも照合する | PCは命令先頭のファイル位置からCode section payload先頭のファイル位置を引いた値である。関数数・body size・locals宣言の符号化バイトを含み、関数内オフセットを使わない。別モジュールで同じPC値を許し、モジュール横断のJIT・履歴キーは`(module_id, pc)`で区別する | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| GOTCHA-INTP-05 | 実行時の命令オブジェクト生成・二分探索排除 | 関数呼び出しおよび命令ステップ実行 | `_build_frame` および `step()` を実行 | `_build_frame` および `step()` の命令フェッチが、オフセット→命令の逆引きテーブルを一切経由せず、生のバイト列（`frame.code[cursor_offset]`）から直接デコードする。Code section payload相対PCから関数内カーソルへ変換しても、逆引き表を使わない。 | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) `{DirectBytecodeExecution}` |
| GOTCHA-INTP-06 | JIT終端命令の分岐と制御フレーム整理 | 入れ子のloop/ifを持つ関数で、内側の分岐条件ブロックがコンパイル済み | JIT実行後、条件分岐を複数回通過する | 終端命令PCから命令handlerを呼び、Runtime APIが残余条件の消費、制御フレームに従う分岐とスタック整理を行う。Interpreter単独実行と同じ結果になる | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)、`{InterpreterContextStackless}`, `{JIT_RuntimeAPI_Fallback}` |

### 継続状態と資源境界の検証
<!-- traceability: {GOTCHA-INTP-07} {GOTCHA-INTP-08} {GOTCHA-INTP-09} {GOTCHA-INTP-10} {GOTCHA-INTP-11} {GOTCHA-INTP-12} {GOTCHA-INTP-13} {GOTCHA-INTP-14} {GOTCHA-INTP-15} {GOTCHA-INTP-16} {GOTCHA-INTP-17} {GOTCHA-INTP-18} {GOTCHA-INTP-19} {GOTCHA-INTP-20} {GOTCHA-INTP-21} {GOTCHA-INTP-22} -->

各行はコンポーネント仕様書のGOTCHAを参照する検証項目である。確認状況は既存の記録を保持する。

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-INTP-07 | Opcode属性と継続ディスパッチ | 複数処理区分のopcodeがある | 属性表、独立したハンドラ表、対応handlerへの継続を検査する | 属性表は関数ポインタを持たず、ハンドラ表から選んだhandlerへ4論理引数が末尾継続で渡る | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 実装済み・テスト済み |
| GOTCHA-INTP-08 | 正常継続とtrapの結果 | 正常命令とtrapを起こす命令がある | handlerの継続と境界結果を比較する | 正常命令は4論理引数を末尾呼び出しで渡し、trap時は終了種別を返して具体的なtrapコードを`ctx`へ残す | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)。確認状況: 実装済み・テスト済み |
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
| GOTCHA-INTP-22 | フレームごとのlocal幅と境界 | i32/f32だけの関数とi64/f64を含む関数がある | ロード済み幅メタ情報、localアクセス、占有ワード数、戻り値を照合する | 幅が正本のフレーム別規則と一致し、各localの値と隣接境界が保たれる。実行時にオフセット表を参照しない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |

## 3. テスト検証実績と網羅状況

- **継続渡しディスパッチと3本の独立領域 (TEST-INTP-01〜14)**: 4論理引数、オペランド領域のアンダー／オーバーフロー、ローカル値領域上の再帰呼び出し、戻り値、容量独立性。
- **命令実行とロード済みモジュール (TEST-INTP-06, 09)**: 命令別handlerの継続ディスパッチ、境界復帰、ロード時デコードと命令実行の分離。
- **LOOP後方分岐のyield境界 (TEST-INTP-76)**: 共有しきい値へ達したときにyieldし、再開後に実行を完了すること。
- **静的分岐先の制御フレーム整理 (TEST-INTP-77)**: 分岐先より後ろの終端を持つフレームを破棄し、分岐先と一致するフレームを残すこと。
- **ラベルアリティ & プルーニング (TEST-INTP-20〜23)**: ブロック脱出、ループ背進辺、多重ネスト br_table。
- **i64全演算 & メモリアクセス (TEST-INTP-30〜43)**: 64bit算術・シフト・ビットカウント・境界外トラップ。
- **LOOP後方分岐の協調yield (TEST-INTP-50〜51)**: 共通回数しきい値に達した時だけRuntimeEngineへ戻る。
- **デバッグ構成 (TEST-INTP-60〜62, 65)**: インタープリタ専用構成、ブレークポイント停止、およびアタッチ中のJIT不使用。

### native constの容量境界と原因の識別

[`test_cps_interpreter.py`](experiments/pysim/qa/tier2_runtime/interpreter/test_cps_interpreter.py)の`test_native_const_capacity_traps_without_partial_push`は、4型constと5境界の20組を全数実行する。境界は容量0で空き0、容量1で空き1、容量4で空き0/1/2である。i32/f32は1 word、i64/f64は2 wordを使う。i32/i64の-1とf32/f64の負のゼロについて、成功時の具体raw wordも確認する。

拒否時はnative C ABIのstatusがtrap、原因が`OPERAND_STACK_CAPACITY`、停止PCがconstの位置、stackサイズが操作前と一致することを確認する。物理128 wordを個別sentinelで埋め、全wordを操作前後で比較する。wide constの片側だけの書込みと、利用可能容量の外側への書込みを検出する。

`test_native_invalid_const_is_not_misclassified_as_capacity_trap`は、4型の切れた即値と未対応opcodeを、空き0/2 wordの10組で実行する。容量trapへ一括変更せず、元のfallback境界、PC、全word保存が維持されることを確認する。この低位ABI試験は不正WASMのロード受入れを意味しない。

2026-10-01、Linux x64、Python 3.14.6、Clang 21.1.8で、修正前は容量不足の12組がFAIL、正常・負例など19件がPASSとなった。4 constハンドラのpush失敗を具体容量trapへ分離し、nativeを再ビルドした後、次の局所検証で31件PASS、FAIL 0件、SKIP 0件を確認した。

```bash
UV_CACHE_DIR=/tmp/fireball-test-design-uv bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
UV_CACHE_DIR=/tmp/fireball-test-design-uv uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier2_runtime/interpreter/test_cps_interpreter.py
```

同じ20容量境界と10負例をstandalone C ABIの一時ハーネスから実行し、AddressSanitizerとUndefinedBehaviorSanitizerの検出なしを確認した。LeakSanitizerは実行環境のprocess検査制約により動作しないため、この確認では無効にした。

### Interpreter試験の観測とケース対応

[`test_interpreter.py`](experiments/pysim/qa/tier2_runtime/interpreter/test_interpreter.py)はPython参照実装の試験と、既存native入口の試験を区別する。
Python参照実装のhandler表を検査した結果は、native dispatcherの方式を検証した結果として扱わない。
関数名の旧番号が異なる要求を指していた試験は、実際に観測する内容へ名称と対応を修正した。

| 要求・対象 | 実行関数 | 独立した期待値と観測 |
| :--- | :--- | :--- |
| TEST-INTP-01の参照handler | `test_intp_01_python_reference_cps_handlers_and_dispatch_table`、`test_intp_01_handler_returns_specific_trap_outcome` | 論理引数の名前と順序を比較し、handler結果と実行境界が具体的な`UNREACHABLE`を報告することを確認する。native dispatcherのTEST-INTP-02の証拠へ読み替えない |
| 制御フレーム型とopcode属性 | `test_control_frame_enum_and_opcode_attribute_table` | 実フレームのLOOP種別と、CALL、BR_IF、LOOP、I32_ADDの属性を比較する。handler/JITの論理引数契約を扱うTEST-INTP-03へ紐付けない |
| TEST-INTP-72の静的制御表 | `test_intp_72_control_map_uses_four_entry_locality_caches` | void、i32、i64のblock終端とアリティ、および固定容量のcacheを比較する。TEST-INTP-04のJIT復帰経路へ紐付けない |
| TEST-INTP-73の広幅フレーム | `test_intp_73_wide_frame_uses_eight_byte_slots` | 混在型の幅マップ、引数の生ワード数、フレームのスロット数と関数結果42を比較する |
| ラベルアリティによる広幅結果の保存 | `test_typed_block_results_keep_wide_native_slots` | i64、f32、f64のblockからの分岐結果42、1.5、2.5を比較する。関数呼出し記述子の分離を扱うTEST-INTP-18の証拠へ読み替えない |
| TEST-INTP-18/75の記述子とlocal領域 | `test_intp_70_to_72_direct_bytecode_execution` | 既存のフレーム構築後にnative記述子の関数番号、コード、幅マップ、引数・local数、独立したlocal領域位置を比較する。復帰後の記述子とlocal領域の長さが0へ戻ることを確認する |
| 命令のTrap原因 | `test_wasm_10_to_15_control_flow_and_calls`、`test_wasm_40_to_46_memory_load_store_grow_and_data`、`test_wasm_50_to_56_integer_arithmetic_and_bitwise`、`test_wasm_mvp_packed_memory_and_i64_float_conversions` | 対象呼出しだけの`AssertionError`を捕捉し、外側で`UNREACHABLE`、`MEMORY_OUT_OF_BOUNDS`、`INTEGER_DIVIDE_BY_ZERO`、`INVALID_CONVERSION`を比較する。境界外メモリアクセスでは全メモリの不変も比較する |
| TEST-INTP-74のlocal容量 | `test_intp_74_all_32bit_frames_use_half_the_local_stack` | 狭幅版は参照実装とnativeで結果6を得る。広幅版は既存native入口から`LOCAL_STACK_CAPACITY`を返す。任意の内部assertを容量Trapの代替にしない |

この対応表は既存の実行入口と試験の観測対象を示す。
TEST-INTP-03の全handler/JIT戻り型比較や、TEST-INTP-04のJIT復帰は上記の補助試験で代替せず、対応するABI試験とJIT試験の範囲で確認する。

## 4. 未検証・スコープ外

- f32/f64演算（`interpreter_concept.py`自体にも実装がなく、スコープが仕様上不明瞭。README「Missing spec coverage」参照）。
- 上記const境界試験は全opcodeの容量超過、全即値、全呼出し履歴を網羅しない。JITを含む深いstackの回帰は[`jit_runtime_test_spec.md`](docs/qa/tier3_plugins/jit_runtime_test_spec.md)のTEST-JITR-61を参照する。
