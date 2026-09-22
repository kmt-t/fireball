# WASMインタープリタ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`interpreter.md`](docs/components/tier3_executer/interpreter.md), [`wasm_instruction_set.md`](docs/specs/wasm_instruction_set.md)
`{ThreadedInterpreter}`（4論理引数の継続渡しハンドラ方式）、独立した3本の値領域と関数呼出し記述子領域、ラベルアリティに基づくスタックプルーニング、i32/i64演算、境界チェック付きメモリアクセス、Safepointポーリングを検証する。

## 2. テストケース一覧

### 継続渡しディスパッチ方式そのもの ({ThreadedInterpreter})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-01 | ハンドラのシグネチャと継続結果が4論理引数(`ctx, sp, local_base, tos`)である | 実装コードを確認 | 各opcodeハンドラの引数と結果を確認 | すべてのハンドラが同一の4引数シグネチャを持ち、継続時は次回呼び出し用の4引数とトラップ状態を返す。正常終了は継続なし、トラップは非NULLのトラップ情報で表す | wasm_instruction_set.md, interpreter.md |
| TEST-INTP-02 | ハンドラテーブルによるディスパッチ | - | ディスパッチ機構を確認 | opcode→ハンドラ関数のテーブル参照で分岐し、線形if-elif連鎖ではない | 同上 |
| TEST-INTP-03 | Interpreter handlerとJIT traceの引数ABI | JITトレース生成 | handlerとtrace entryの型・引数配置を比較 | 両者は`ctx, sp, local_base, tos`の4引数配置を共有するが、Interpreter handlerは`handler_result`、JIT trace entryは`void`を返し、関数ポインタ型は分離される | `{ContextPointerRegister}` `{AAPCS_FastCall}` `{PositionIndependentCode}` |
| TEST-INTP-04 | JITトレースからインタープリタへのシームレスフォールバック | 未コンパイルのブロックへ分岐 | トレース実行完了 | トレース末尾でインタープリタへスムーズに復帰し、後続ブロックをインタープリタが継続実行する | `{JIT_LazyChaining}` `{JIT_RuntimeAPI_Fallback}` |

### Python互換の継続入口とネイティブ境界

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-05 | C++ CPS handler入口 | ネイティブ拡張をClangでビルド済み | 4論理引数相当のcontext・operand・local・TOSをnative viewで渡して実行 | C++ handler tableからC++ handlerへ直接継続し、Pythonには境界結果だけを返す | `interpreter.py`, `native_interpreter.cxx` |
| TEST-INTP-06 | C++ handler tableとstepループ | `_interpreter_native`をビルド済み | code・operand stack・local stackを`memoryview`で`run_step`へ渡す | C++の固定256スロット表がi32直線区間を連続実行し、境界でPCとstack sizeを返す。未対応opcodeはスタックを変更せずPythonへ戻る | `tier3_executer/interpreter/native_interpreter.cxx` |
| TEST-INTP-07 | C++インタープリタのPython境界 | ネイティブ拡張をClangでビルド済み | 同じWASMモジュールを通常入口で実行 | code・operand・local・controlのnative recordをPython wrapperが保持し、Pythonには境界結果だけを返す | `interpreter.py`, `interop_abi.py`, `native_stacks.py` |
| TEST-INTP-08 | JITトレースのC++ 4引数ブリッジ | `native_trace_call`をビルド済み | `(ctx, sp, local_base, tos)`の関数ポインタ入口を実行 | ctypes経路と同一のトレース結果になり、ブリッジ実行中はPythonハンドラへ戻らない | `tier3_executer/jit/native_trace_call.cxx` |
| TEST-INTP-09 | ロード時LEB128の実行系分離 | WASMロード処理を実行可能 | モジュールロード後の実行経路を確認 | LEB128デコーダはロード時だけ使用され、Tier 3実行ホットパスへ入らない | `tier2_runtime/leb128.py` |
| TEST-INTP-08 | ジャンプ・分岐境界 | `br`、`br_if`、`br_table`、`call`を含むWASM | 境界命令を実行 | ジャンプ・分岐は継続チェインせず、既存フレーム処理へ戻って正しいPC・制御スタックを保つ | `{InterpreterContextStackless}` |
| TEST-INTP-09 | AO-Bench全命令差分 | wasmtimeとTier 2/Tier 3を利用可能 | `aobench.py`を実行 | Float32 sanity、全AO出力、Tier 2/Tier 3の528バイト出力が完全一致する | `{META_RecoveryStrategy}` |

### 3本の独立スタック・関数呼び出し ({ContextPointerRegister})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-10 | オペランド領域オーバーフロートラップ | `stack_capacity`を超えるpush | 再帰呼び出し等でオペランド領域を溢れさせる | `WASMTrap("STACK_OVERFLOW")`相当が発生する（無限に領域が伸びない） | interpreter_concept.py `ExecutionContext.push` |
| TEST-INTP-11 | オペランド領域アンダーフロートラップ | 空のオペランド領域でpop | pop操作 | `WASMTrap("STACK_UNDERFLOW")`相当 | interpreter_concept.py `ExecutionContext.pop` |
| TEST-INTP-12 | 再帰呼び出し（call）とローカル値領域 | `fact(n)`のような再帰関数 | `execute_function`で呼び出す | 各呼び出しごとに新しいローカル値領域の区画が割り当てられ、ローカル変数が互いに独立する | interpreter_concept.py `test_full_wasm_recursive_factorial` |
| TEST-INTP-13 | 戻り値の受け渡し | 関数が1個の結果を返す | `return`実行後の呼び出し元スタック | 呼び出し元のスタックに正しく結果が積まれる | interpreter_concept.py `execute_function` |
| TEST-INTP-14 | オペランド領域とローカル値領域の容量独立性 | オペランド領域の残容量が1、ローカル値領域に空きがある | 既存のオペランド値を保持したまま引数付き関数を呼び出す | ローカル値領域へ引数を積め、関数結果と呼び出し元オペランド領域の値が正しく保持される | interpreter_concept.py `test_independent_operand_and_local_stacks`, [`interpreter_stack_model.py`](docs/components/tier3_executer/formal/interpreter_stack_model.py) |
| TEST-INTP-15 | 3本の値領域・関数呼出し記述子分離・関数復帰結果の形式検証 | 通常モデルと`guards=False`変異モデル | [`interpreter_stack_model.py`](docs/components/tier3_executer/formal/interpreter_stack_model.py)を実行 | 通常モデルでは値領域独立性、関数呼出し記述子とローカル値の分離、関数結果保持の3性質が成立し、各変異モデルでは対応性質が反証される | [`interpreter_stack_model.py`](docs/components/tier3_executer/formal/interpreter_stack_model.py) |
| TEST-INTP-16 | ネストしたcalleeの戻り値と関数呼出し記述子の復帰 | callerがcalleeを呼び、calleeがi32/i64/f32/f64を返す | calleeの`return`処理を実行 | 戻り値は共有オペランド領域へ残り、calleeの関数呼出し記述子が取り除かれ、call helperが復帰sentinelを消費してcallerへ戻る。C/AAPCS戻り値レジスタや専用戻り値バッファは使用しない | `interpreter.md` 関数復帰の番兵 |
| TEST-INTP-17 | トップレベル復帰のRETURN sentinel | 最外周WASM関数がreturnする | return handlerとRuntimeEngineを実行 | sentinelはInterpreterのreturn handlerだけが生成し、RuntimeEngineが実行完了を判定する。JITはsentinelを生成しない | `interpreter.md` 関数復帰の番兵 |
| TEST-INTP-18 | 関数呼出し記述子とローカル値領域の分離 | callerが引数付きcalleeを呼び出す | call helperでcalleeの実行区画を開始し、calleeから復帰する | 記述子は独立した領域に置かれ、`frame_offset`がローカル値領域の開始ワード位置を示す。ローカル値領域にはローカル値だけが入り、復帰時に記述子を取り除いて`local_offset`とローカル値領域の長さを保存位置へ戻す | `interpreter.md` 関数呼び出し境界、`interpreter_concept.py`、`test_interpreter.py` TEST-INTP-70 |

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

### Cooperative Safepoint (poll_safepoint)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-50 | ループ背進辺でのSafepointポーリング | `safepoint_pending = True`かつ無限ループ | 実行 | `br`がループ先頭へ戻る直前に`SAFEPOINT_YIELD`を返して中断する | interpreter_concept.py `test_cooperative_safepoint` |
| TEST-INTP-51 | Safepoint未発生時は通常続行 | `interrupt_flag = False` | ループ実行 | ポーリングは行われるが中断されない | interpreter_concept.py `poll_safepoint` |

### デバッグ構成とインタープリタ専用実行 ({DebuggerInterpreterComposition})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-60 | デバッグ未アタッチ時のゼロオーバーヘッド | デバッガ未接続 (`is_debug_mode=False`) | 通常構成を実行 | 通常構成はデバッガを保持せず、インタープリタの命令意味論へデバッグ分岐を追加しない | `{DebuggerInterpreterComposition}` |
| TEST-INTP-61 | デバッグ構成の実行器固定 | `RuntimeCompositionConfig(execution=INTERPRETER, debugger=True)` | 構成を合成して実行 | デバッガ付き構成はインタープリタだけを生成し、JIT実行器を生成しない | `{DebuggerInterpreterComposition}` |
| TEST-INTP-62 | アタッチ中のブレークポイント検知・停止 | インタープリタとデバッガが接続され、PC=0x100 にブレークポイント設定 | 実行継続 | インタープリタ実行境界でブレークポイントを検知し、実行を中断して停止状態（SIGTRAP）へ遷移する | `{DebuggerInterpreterComposition}` |
| TEST-INTP-65 | アタッチ中のインタープリタ専用実行 | デバッグ構成でデバッガアタッチ中 | `step` または `continue` を実行 | アタッチ中は常にインタープリタが実行され、JITへの動的切替もハンドラテーブル切替も発生しない | `{DebuggerInterpreterComposition}` |

### ROM/Flash バイトコード直接デコードと命令オブジェクト生成ゼロ ({DirectBytecodeExecution})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-70 | 命令オブジェクト非生成とバイト直接フェッチ | WASM関数実行 | 実行ループのフェッチ処理を確認 | `frame.code[ip]` から $O(1)$ で直接バイトを読み出し、中間 `Instr` オブジェクトを生成しない | `interpreter.md` `{DirectBytecodeExecution}` |
| TEST-INTP-71 | 足し算による次PC進行と即値のその場デコード | 算術・即値命令実行 | `ip` の遷移を確認 | 命令長または即値長を加算した `ip + len` で直接進行し、二分探索マップ（FlatMapView）を走査しない | `interpreter.md` |
| TEST-INTP-72 | 静的制御表によるブロック境界解決 | `block/loop/if` 構文の実行 | 分岐および終了時の遷移を確認 | モジュールロード時に事前計算された静的 `control_map`（`blocks`, `br_tables`）を参照し、実行時の全命令再デコードを行わない | `interpreter.md` |
| TEST-INTP-73 | フレームごとのスロット幅の決定 | i32のみ、f32のみ、i64を含む、f64ローカルを含む、ローカルなしの関数 | ロード後の幅マップ（ローカルごとの幅を2ビットで保持）のスロット幅と `local_slot_count_cache` を確認し、各関数を実行する | i32/f32のみは1ワード、i64/f64を含むと2ワードになる。ローカルなしは1ワードでスロット数は0である。全ローカルの幅がスロット幅以下である。実行結果が正しい | GOTCHA-INTP-22 |
| TEST-INTP-74 | 32ビットのみのフレームによるローカル値領域の節約 | 16個のローカルを持つ再帰関数。一方はi32のみ、他方はf64ローカルを1個含む | 同じ再帰深さで実行する | i32のみの版は、1フレーム16ワードで7フレームが128ワードに収まり、成功する。f64を含む版は、1フレーム34ワードで7フレームが128ワードを超え、容量超過で停止する | GOTCHA-INTP-22 |
| TEST-INTP-75 | Native CallFrameの固定ABIと積載順序 | 関数を1つ開始し、Native CallStackが空 | `_build_frame` 後にコンテキストと最上位CallFrameを検査する | `call_stack` がコンテキストへ接続され、CallFrameが関数番号、コードビュー、ローカル幅・スロット数、引数個数、制御ベースの順序で保持される。終了後はCallStack深さと`call_offset`が0へ戻る | `interpreter.md` `{CallFrame_Layout}` `{ExecutionContext_Layout}` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-INTP-01 | 継続渡し第4論理引数 `tos`（R3）とスタックメモリの境界同期 | スタック空状態から複数回の push/pop | `i32.const` および二項演算を連続実行 | スタック空時は `tos=0`、値 push 時は旧 `tos` がスタックメモリへ退避され新値が `tos` に格納、値 pop 時はスタックメモリから次段値が `tos` に復元される。**実装の勘所**: `tos` は第4引数レジスタ `R3` に保持されるため、スタックメモリ（`[R1, #sp_offset]`）上の深さは「全オペランド数 - 1」となり、スタック空の境界条件でアンダーフロー誤検知や未定義値を発生させてはならない | `interpreter.md`「実行コンテキスト」, `{AAPCS_FastCall}` |
| GOTCHA-INTP-02 | Label Arity スタック巻き戻し時の TOS 復元 | `block (result i32)` 内で値を push 後に `br 0` | ブロック脱出を実行 | ブロック開始時の深さまでスタックが巻き戻され、宣言アリティ分の結果値のうち最上位値が正しく `R3: tos` レジスタへ復元されて次ハンドラへ渡る。**実装の勘所**: スタックメモリを巻き戻しただけで `tos` レジスタを更新し忘れると、脱出前の破棄された値が `tos` に残り後続命令で不正計算となる | `interpreter.md` |
| GOTCHA-INTP-03 | if 条件偽（else節なし）での制御フレームリーク防止 | `if (cond=0)` で else 節なし | `if` 命令を実行 | `match_offset + 1` へジャンプする際、`_Frame("if")` がスタックに残らずフレームスタックの深さが不変に保たれる。**実装の勘所**: 条件成立時と同様にフレームを積んでからジャンプすると、対応する `END` 命令をスキップした際にフレームが回収されずスタックリークとなる | `interpreter.md` |
| GOTCHA-INTP-04 | UnifiedPC による多重モジュール空間の衝突防止 | 複数モジュールがロードされ、同一オフセット（例: 0x0010）を持つ関数が存在 | 各モジュールの関数を実行 | JIT キャッシュ引き当てやデバッグブレークポイント判定において、`(func_index << 16) \| bytecode_offset` の32bit表現で一意に区別され、他モジュールの同一オフセットと決して誤衝突しない | `interpreter.md`, `{PositionIndependentCode}` |
| GOTCHA-INTP-05 | 実行時の命令オブジェクト生成・二分探索排除 | 関数呼び出しおよび命令ステップ実行 | `_build_frame` および `step()` を実行 | `_build_frame` および `step()` の命令フェッチが、オフセット→命令の逆引きテーブルを一切経由せず、生のバイト列（`frame.code[ip]`）から直接デコードする。**実装の勘所**: WASM バイト列に対して実行時に中間オブジェクトをアロケーションしたり、可変長オフセットを埋めるために二分探索を挟むと、組み込み環境でメモリを枯渇させ、実行時間の半分以上を探索に浪費する | `interpreter.md` `{DirectBytecodeExecution}` |
| GOTCHA-INTP-06 | JIT が代行した分岐脱出でのフレーム内容不正利用防止 | ループが if 文の中に入れ子になっており、内側ループの脱出条件がコンパイル済み | 内側・外側の双方のループが複数回実行されるまで駆動を継続する | 外側ループ自身の分岐が正しく外側ループの先頭へ解決され、結果値が期待通りになる。**実装の勘所**: `loop`/`block`/`if` の脱出を JIT トレースがインタープリタを介さずに直接解決すると、その脱出に対応するフレームの積み下ろしは一切行われない。以前インタープリタが直接その構文を実行していた際に積まれたフレームが回収されずに残留すると、後続の深さ相対な分岐命令が誤った階層を対象に解決してしまう。フレームスタックの深さを切り詰めるだけでは、逆方向（本来もっと積まれているべき）のズレは直せない。よって `br` / `br_if` / `else` の分岐先解決はフレームスタックの中身を一切信用せず、ベーシックブロック抽出時に静的解決済みの `next_pc` / `loops_to` を直接使う。深さの切り詰め自体は、JIT が `END` 通過を代行し続けることでスタックが際限なく伸びるのを防ぐ安全策としてのみ残す | `{InterpreterContextStackless}`, ADR-INTERP-03, `{JIT_RuntimeAPI_Fallback}` |

### 追加GOTCHA一覧（マージ判定用）

以下は、インタープリタの実装・C++移植・JIT接続に関して今回追加された仕様である。既存の6項目と重複するものは、より具体的な破壊パターンを明文化するために分離している。

| GOTCHA ID | 追加仕様・破壊してはいけない条件 | 破壊時の症状 | 実装の判定基準 | 現状確認 |
| :--- | :--- | :--- | :--- | :--- |
| GOTCHA-INTP-07 | ハンドラテーブルは直接4引数ハンドラを保持し、ラッパーや二重のハンドラ表を挟まない | 引数変換、`env`再取得、TOS再計算が命令ごとに入り、継続渡し契約と実行経路が二重化する | ハンドラ表の要素が `(ctx, sp, local_base, tos)`を直接受け取る関数である | 実装済み・テスト済み |
| GOTCHA-INTP-08 | 継続結果は次回ハンドラの4引数を必ず返し、トラップ状態を同じ結果に含める | 次のハンドラが再構築され、継続状態の取り違えやJIT境界での引数欠落が起きる | 正常継続は `(ctx, sp, local_base, tos, None)`、トラップは同じ4引数と非`None`の`Trap`を返す | 実装済み・テスト済み |
| GOTCHA-INTP-09 | 次PCは戻り値の別フィールドではなく、`ctx.native_context.ip`（C++では`ctx->ip`）に保持する | `ip`と継続引数の状態が分裂し、次命令・JITキャッシュ・デバッガのPCが食い違う | ハンドラが`ctx.ip`を更新し、呼び出し側はその値だけで次PCを取得する | 実装済み・テスト済み |
| GOTCHA-INTP-10 | WASM命令実行中のトラップはPython例外を制御フローに使わず、`InterpreterCall.trap`へ集約する | vMMIOだけ例外、算術だけ戻り値など、命令ごとに異なるエラー経路になる | vMMIO、`call_indirect`、整数変換、境界違反がハンドラ結果または`InterpreterCall.trap`になる。同期`call()`の最外周での再送出は互換境界として明示する | 実装済み・主要経路テスト済み |
| GOTCHA-INTP-11 | インタープリタとJITは同一の実行コンテキストと共有値領域を使用する | JIT専用locals／result bufferへのコピー、アドレス再キャスト、二重領域が発生する | JIT専用バッファや専用セットアップを作らず、境界引数の`sp`と`local_base`がインタープリタの共有記憶領域を指す | 実装済み・既存JITテストで確認 |
| GOTCHA-INTP-12 | Native値スタックは型タグを持たないバイナリスロット列とし、型は操作側が知る | 実行時型検査、型タグのメモリ、不要なキャストがホットパスへ混入する | `push_i32`／`pop_i32`等の操作を呼び分け、スタック自身に`types`や型オブジェクトを置かない | 実装済み・要追加網羅テスト |
| GOTCHA-INTP-13 | WASMのスロット幅に従い、i32/f32は1個、i64/f64は2個の32bitスロットを使う。ポインタは64bit | i64/f64の引数・戻り値・localが1スロット扱いになり、後続localの位置が破壊される | ローカルオフセット、引数搬送、Native stackのpush/popが同じスロット幅規則を使う | 実装済み・既存i64テスト済み |
| GOTCHA-INTP-14 | 関数呼出し記述子は関数番号から関数メタデータを一度取得して紐付け、実行時にコード検索をしない | 命令ごとの関数探索、二分探索、重複メタデータ保持が発生する | 関数コード、制御対応表、ローカル位置キャッシュ、入れ子呼出し情報を記述子生成時に紐付ける | 実装済み・要追加性能計測 |
| GOTCHA-INTP-15 | `local.get`／`local.set`／`local.tee`は型解釈ではなく、既知のスロット幅のバイナリコピーだけを行う | local操作ごとに型分岐・数値変換が入り、バイナリ状態が変質する | ローカル値領域とオペランド領域の間で1または2スロットを直接搬送し、型解釈は型付き演算またはABI境界だけで行う | 仕様反映済み・要追加直接テスト |
| GOTCHA-INTP-16 | スタック巻き戻しは要素ごとの`while pop`ではなく、記録した位置／長さを一度に戻す | 分岐・復帰の計算量が増え、途中状態を残してスタックを破壊する | 制御ブロック復帰情報の保存高さに対して、オペランド領域を一括truncateする。要素数分のpopループを置かない | 実装済み・gotchaテスト済み |
| GOTCHA-INTP-17 | 不正な引数、未初期化状態、無効なフレーム、範囲外の内部状態はリカバリーせず`assert`で停止する | 壊れた状態を隠したまま継続し、後段で原因不明のデータ破壊になる | `current_pc`、handler state、frame stack、Native stack pop結果などの事前条件を`assert`で検証する | 実装済み・要不足箇所監査 |
| GOTCHA-INTP-18 | 実行時引数のセットアップと外部資源の注入は呼び出し側の責務。インタープリタがJIT専用の初期化を行わない | 本番実行とテスト実行で別の初期化経路が生まれ、JITだけ異なる状態を参照する | `memory`、host function、vMMIO等は明示的に呼び出し側から渡し、テスト専用の起動処理を製品コードへ入れない | 方針反映・要実装境界監査 |
| GOTCHA-INTP-19 | 小さい`ControlMap`キャッシュは4エントリ固定、キーは32bit値をXORで4bitへ折りたたむ | 過大なキャッシュ、異なるハッシュ式、未定義の置換で局所性と決定性が崩れる | `temp = v ^ (v >> 16)`、`temp ^= temp >> 8`、`temp ^= temp >> 4`、`temp ^= temp >> 2`、`temp &= 0x3`を使い、キャッシュ挿入失敗は`assert`する | 実装済み・既存テスト済み |
| GOTCHA-INTP-20 | テスト専用のインタープリタ起動・検査コードを本番インタープリタへ混ぜない | 本番コードがテスト都合のAPIや特殊セットアップを持ち、Tier境界とROM/RAM責務が崩れる | テスト側が`Interpreter.start()`／`step()`を使って状態を組み立て、本番側は実行責務だけを持つ | 方針反映・要配置監査 |
| GOTCHA-INTP-21 | インタープリタの`fireball_call` importはhost callとして直接実行する | WASMが`fireball:host/trap`の`fireball_call`を呼ぶ | IDと6引数をimportへ渡して戻り値を読む | 実行エンジンがホストハンドラを直接呼び、戻り値をWASMへ返す。SYSCTL doorbell、vMMIO syscall vector table、`REG_SYSCALL_*`は使用しない | `runtime_vmmio.md`、`runtime_syscall.md` |
| GOTCHA-INTP-22 | フレームごとのローカルスロット幅 | 混在型の関数引数・ローカル（i32, i64, f64等）を持つ関数と、i32/f32だけの関数 | ロード済みFunctionの幅メタデータ、スロット幅、呼び出し結果を確認する | スロット幅は、フレーム内で最大の変数サイズで決まる。i32/f32だけのフレームは4バイト、i64/f64を含むフレームは8バイトである。アドレスは `local_base + local_index * スロット幅` から直接計算し、オフセット表を実行時に参照しない。i32/f32は1ワード、i64/f64は2ワードで、wide値はスロット境界を満たす。同一フレーム内でスロット幅は混在しない | `interpreter.md`、`wasm_module.py` |

## 3. テスト検証実績と網羅状況

- **継続渡しディスパッチと3本の独立領域 (TEST-INTP-01〜14)**: 4論理引数、オペランド領域のアンダー／オーバーフロー、ローカル値領域上の再帰呼び出し、戻り値、容量独立性。
- **Python互換のネイティブ継続実験 (TEST-INTP-05〜09)**: 通常Python入口、全175ハンドラを対象にしたC関数ポインタチェイン、musttail継続、分岐境界、AO-Bench差分の一致。
- **ラベルアリティ & プルーニング (TEST-INTP-20〜23)**: ブロック脱出、ループ背進辺、多重ネスト br_table。
- **i64全演算 & メモリアクセス (TEST-INTP-30〜43)**: 64bit算術・シフト・ビットカウント・境界外トラップ。
- **Safepointポーリング (TEST-INTP-50〜51)**: ループ背進辺での協調的ポーリング。
- **デバッグ構成 (TEST-INTP-60〜62, 65)**: インタープリタ専用構成、ブレークポイント停止、およびアタッチ中のJIT不使用。

## 4. 未検証・スコープ外

- f32/f64演算（`interpreter_concept.py`自体にも実装がなく、スコープが仕様上不明瞭。README「Missing spec coverage」参照）。
