# WASMインタープリタ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`runtime_interpreter.md`](docs/components/tier2_runtime/runtime_interpreter.md), [`wasm_instruction_set.md`](docs/specs/wasm_instruction_set.md)
参考実装: [`interpreter_concept.py`](docs/components/tier2_runtime/concepts/interpreter_concept.py)

`{ThreadedInterpreter}`（CPS 4引数ハンドラ方式）、`OperandStack`・`LocalStack`・`control_frame` の3本の独立スタック、ラベルアリティに基づくスタックプルーニング、i32/i64演算、境界チェック付きメモリアクセス、Safepointポーリングを検証する。

## 2. テストケース一覧

### CPSディスパッチ方式そのもの ({ThreadedInterpreter})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-01 | ハンドラのシグネチャがCPS 4引数(`ctx, sp, local_base, tos`)である | 実装コードを確認 | 各opcodeハンドラの引数を確認 | すべてのハンドラが同一の4引数シグネチャを持ち、次の継続を自ら返す（中央のswitch/if-elifループが「次に何をするか」を決定しない） | wasm_instruction_set.md, runtime_interpreter.md |
| TEST-INTP-02 | ハンドラテーブルによるディスパッチ | - | ディスパッチ機構を確認 | opcode→ハンドラ関数のテーブル参照で分岐し、線形if-elif連鎖ではない | 同上 |
| TEST-INTP-03 | インタープリタとJITトレースのCPS 4引数規約完全一致 | JITトレース生成 | JITエントリとハンドラシグネチャを比較 | `void (*)(execution_context* ctx, uint32_t* sp, uint32_t* local_base, uint32_t tos)` で完全一致し、ディスパッチテーブルから直接 C 呼び出し可能 | `{ContextPointerRegister}` `{AAPCS_FastCall}` `{PositionIndependentCode}` |
| TEST-INTP-04 | JITトレースからインタープリタへのシームレスフォールバック | 未コンパイルのブロックへ分岐 | トレース実行完了 | トレース末尾でインタープリタへスムーズに復帰し、後続ブロックをインタープリタが継続実行する | `{JIT_LazyChaining}` `{JIT_RuntimeAPI_Fallback}` |

### 3本の独立スタック・関数呼び出し ({ContextPointerRegister})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-10 | OperandStackオーバーフロートラップ | `stack_capacity`を超えるpush | 再帰呼び出し等でOperandStackを溢れさせる | `WASMTrap("STACK_OVERFLOW")`相当が発生する（無限に領域が伸びない） | interpreter_concept.py `ExecutionContext.push` |
| TEST-INTP-11 | OperandStackアンダーフロートラップ | 空のOperandStackでpop | pop操作 | `WASMTrap("STACK_UNDERFLOW")`相当 | interpreter_concept.py `ExecutionContext.pop` |
| TEST-INTP-12 | 再帰呼び出し（call）とLocalStack | `fact(n)`のような再帰関数 | `execute_function`で呼び出す | 各呼び出しごとに新しいLocalStackブロックが割り当てられ、ローカル変数が互いに独立する | interpreter_concept.py `test_full_wasm_recursive_factorial` |
| TEST-INTP-13 | 戻り値の受け渡し | 関数が1個の結果を返す | `return`実行後の呼び出し元スタック | 呼び出し元のスタックに正しく結果が積まれる | interpreter_concept.py `execute_function` |
| TEST-INTP-14 | OperandStackとLocalStackの容量独立性 | OperandStackの残容量が1、LocalStackに空きがある | 既存のOperandStack値を保持したまま引数付き関数を呼び出す | LocalStackへ引数を積め、関数結果と呼び出し元OperandStackの値が正しく保持される | interpreter_concept.py `test_independent_operand_and_local_stacks`, `../formal/interpreter_stack_model.py` |
| TEST-INTP-15 | 3本のスタック独立性と関数復帰結果の形式検証 | 通常モデルと`guards=False`変異モデル | `interpreter_stack_model.py`を実行 | 通常モデルでは`interpreter_stacks_remain_independent`と`call_result_reaches_operand_stack`が成立し、変異モデルでは両方が反証される | `../formal/interpreter_stack_model.py` |

### ラベルアリティ・スタックプルーニング (`prune_stack`)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-20 | `block (result i32)`から`br`で抜ける際のスタックプルーニング | `block`内で複数値をpushしてから`br 0` | ブロック終端まで実行 | ブロック開始時の高さまでロールバックしつつ、ブロックの宣言アリティ分（末尾のN個）の値だけが保持される | interpreter_concept.py `test_block_loop_and_stack_pruning`（`i32.const 10; i32.const 42; br 0`→結果42のみ残る） |
| TEST-INTP-21 | void結果のブロックからの脱出 | `block`の結果型が空 | `br`で脱出 | ブロック開始時の高さまで完全にロールバックされる（保持する値なし） | wasm_instruction_set.md br のスタック遷移 `[] -> []` |
| TEST-INTP-22 | br_tableでの多重ネストとプルーニング | 3階層以上ネストしたblock+br_table | 各indexで実行 | 深さに応じた正しいプルーニング＋分岐先ジャンプが行われる | interpreter_concept.py `test_br_table_and_parametric` |
| TEST-INTP-23 | ループ背進辺でのプルーニング挙動 | `loop`から`br 0`（継続） | 実行 | ループ本体の先頭へ戻り、ループ自身のアリティに応じたプルーニングが行われる | interpreter_concept.py `br`の`is_loop`分岐 |
| TEST-INTP-24 | `if`条件偽（elseなし）でのフレームリーク防止 | `if (cond=0)` で else 節なし | `if` 命令実行 | `match_offset + 1` へジャンプする際、`_Frame("if")` がスタックに残らずフレームスタックの深さが不変に保たれる | `runtime_interpreter.md` |
| TEST-INTP-25 | `if-else` 条件偽での else 節遷移 | `if (cond=0)` で else 節あり | `if` 命令実行 | `else_offset + 1` へジャンプし、`_Frame("if")` が積まれ、`END` で正しくポップされる | `runtime_interpreter.md` |
| TEST-INTP-26 | `if` 内からの `br` 脱出とフレーム Pruning | `loop` 内に `if` を配置し `br 1` 脱出 | ループ内実行 | `if` フレームと `loop` フレームが正しく unwind され、後続の命令が正しいスコープで実行される | `runtime_interpreter.md` |

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

### デバッガ・プロファイラ統合とハンドラテーブル切り替え ({DebuggerLabelTableSwitch}, {Debug_Integrated})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-60 | デバッグ未アタッチ時のゼロオーバーヘッド | デバッガ未接続 (`is_debug_mode=False`) | 通常実行 | インタープリタは標準ハンドラテーブル（`normal_handler_table`）を使用し、デバッグフックやテーブル分岐のオーバーヘッドなしで最高速実行される | `{DebuggerLabelTableSwitch}` |
| TEST-INTP-61 | デバッガアタッチ時のハンドラテーブル切り替え | デバッガ接続 (`is_debug_mode=True`) | 実行 | インタープリタの有効ハンドラがデバッグ用テーブル（`debug_handler_table`）へ動的に切り替わり、命令実行ごとにデバッグフックが呼び出される | `{DebuggerLabelTableSwitch}` |
| TEST-INTP-62 | デバッグハンドラでのブレークポイント検知・停止 | PC=0x100 にブレークポイント設定 | 実行継続 | インタープリタが命令実行前にブレークポイントを検知し、実行を中断して停止状態（SIGTRAP）へ遷移する | `{Debug_Integrated}` |
| TEST-INTP-63 | 統合プロファイラフック（PCサンプリング） | プロファイラ有効 | 命令実行 | デバッグハンドラ経由で命令が実行されるたびに実行中PCがサンプリングされ、頻度統計が正確に集計される | `{Debug_Integrated}` |
| TEST-INTP-64 | 動的メモリアサーションフック | メモリアサーション登録 | 命令実行 | 命令実行後にメモリフックが呼び出され、期待値と異なる値が検知された場合にアサーション違反が記録される | `{Debug_Integrated}` |
| TEST-INTP-65 | デバッグアタッチ時のJITバイパス（インタープリタフォールバック） | JITキャッシュにトレースが存在 | デバッグアタッチ下で実行 | JIT直接ジャンプをバイパスし、インタープリタのデバッグハンドラで1命令ずつ安全にステップ制御される | `{DebuggerLabelTableSwitch}` |

### ROM/Flash バイトコード直接デコードと命令オブジェクト生成ゼロ ({DirectBytecodeExecution})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-INTP-70 | 命令オブジェクト非生成とバイト直接フェッチ | WASM関数実行 | 実行ループのフェッチ処理を確認 | `frame.code[ip]` から $O(1)$ で直接バイトを読み出し、中間 `Instr` オブジェクトを生成しない | `runtime_interpreter.md` `{DirectBytecodeExecution}` |
| TEST-INTP-71 | 足し算による次PC進行と即値のその場デコード | 算術・即値命令実行 | `ip` の遷移を確認 | 命令長または即値長を加算した `ip + len` で直接進行し、二分探索マップ（FlatMapView）を走査しない | `runtime_interpreter.md` |
| TEST-INTP-72 | 静的制御表によるブロック境界解決 | `block/loop/if` 構文の実行 | 分岐および終了時の遷移を確認 | モジュールロード時に事前計算された静的 `control_map`（`blocks`, `br_tables`）を参照し、実行時の全命令再デコードを行わない | `runtime_interpreter.md` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-INTP-01 | CPS第4引数 `tos`（R3）とスタックメモリの境界同期 | スタック空状態から複数回の push/pop | `i32.const` および二項演算を連続実行 | スタック空時は `tos=0`、値 push 時は旧 `tos` がスタックメモリへ退避され新値が `tos` に格納、値 pop 時はスタックメモリから次段値が `tos` に復元される。**実装の勘所**: `tos` は第4引数レジスタ `R3` に保持されるため、スタックメモリ（`[R1, #sp_offset]`）上の深さは「全オペランド数 - 1」となり、スタック空の境界条件でアンダーフロー誤検知や未定義値を発生させてはならない | `runtime_interpreter.md`「実行コンテキスト」, `{AAPCS_FastCall}` |
| GOTCHA-INTP-02 | Label Arity スタック巻き戻し時の TOS 復元 | `block (result i32)` 内で値を push 後に `br 0` | ブロック脱出を実行 | ブロック開始時の深さまでスタックが巻き戻され、宣言アリティ分の結果値のうち最上位値が正しく `R3: tos` レジスタへ復元されて次ハンドラへ渡る。**実装の勘所**: スタックメモリを巻き戻しただけで `tos` レジスタを更新し忘れると、脱出前の破棄された値が `tos` に残り後続命令で不正計算となる | `runtime_interpreter.md` |
| GOTCHA-INTP-03 | if 条件偽（else節なし）での制御フレームリーク防止 | `if (cond=0)` で else 節なし | `if` 命令を実行 | `match_offset + 1` へジャンプする際、`_Frame("if")` がスタックに残らずフレームスタックの深さが不変に保たれる。**実装の勘所**: 条件成立時と同様にフレームを積んでからジャンプすると、対応する `END` 命令をスキップした際にフレームが回収されずスタックリークとなる | `runtime_interpreter.md` |
| GOTCHA-INTP-04 | UnifiedPC による多重モジュール空間の衝突防止 | 複数モジュールがロードされ、同一オフセット（例: 0x0010）を持つ関数が存在 | 各モジュールの関数を実行 | JIT キャッシュ引き当てやデバッグブレークポイント判定において、`(func_index << 16) \| bytecode_offset` の32bit表現で一意に区別され、他モジュールの同一オフセットと決して誤衝突しない | `runtime_interpreter.md`, `{PositionIndependentCode}` |
| GOTCHA-INTP-05 | 実行時の命令オブジェクト生成・二分探索排除 | 関数呼び出しおよび命令ステップ実行 | `_build_frame` および `step()` を実行 | `_build_frame` および `step()` の命令フェッチが、オフセット→命令の逆引きテーブルを一切経由せず、生のバイト列（`frame.code[ip]`）から直接デコードする。**実装の勘所**: WASM バイト列に対して実行時に中間オブジェクトをアロケーションしたり、可変長オフセットを埋めるために二分探索を挟むと、組み込み環境でメモリを枯渇させ、実行時間の半分以上を探索に浪費する | `runtime_interpreter.md` `{DirectBytecodeExecution}` |
| GOTCHA-INTP-06 | JIT が代行した分岐脱出でのフレーム内容不正利用防止 | ループが if 文の中に入れ子になっており、内側ループの脱出条件がコンパイル済み | 内側・外側の双方のループが複数回実行されるまで駆動を継続する | 外側ループ自身の分岐が正しく外側ループの先頭へ解決され、結果値が期待通りになる。**実装の勘所**: `loop`/`block`/`if` の脱出を JIT トレースがインタープリタを介さずに直接解決すると、その脱出に対応するフレームの積み下ろしは一切行われない。以前インタープリタが直接その構文を実行していた際に積まれたフレームが回収されずに残留すると、後続の深さ相対な分岐命令が誤った階層を対象に解決してしまう。フレームスタックの深さを切り詰めるだけでは、逆方向（本来もっと積まれているべき）のズレは直せない。よって `br` / `br_if` / `else` の分岐先解決はフレームスタックの中身を一切信用せず、ベーシックブロック抽出時に静的解決済みの `next_pc` / `loops_to` を直接使う。深さの切り詰め自体は、JIT が `END` 通過を代行し続けることでスタックが際限なく伸びるのを防ぐ安全策としてのみ残す | `{InterpreterContextStackless}`, ADR-INTERP-03, `{JIT_RuntimeAPI_Fallback}` |

## 3. テスト検証実績と網羅状況

- **CPSディスパッチ & 3本の独立スタック (TEST-INTP-01〜14)**: 4引数規約、OperandStackのアンダー/オーバーフロー、LocalStack上の再帰呼び出し、戻り値、容量独立性。
- **ラベルアリティ & プルーニング (TEST-INTP-20〜23)**: ブロック脱出、ループ背進辺、多重ネスト br_table。
- **i64全演算 & メモリアクセス (TEST-INTP-30〜43)**: 64bit算術・シフト・ビットカウント・境界外トラップ。
- **Safepointポーリング (TEST-INTP-50〜51)**: ループ背進辺での協調的ポーリング。
- **デバッガ・プロファイラ統合 (TEST-INTP-60〜65)**: ハンドラテーブル切り替え（`{DebuggerLabelTableSwitch}`）、ブレークポイント停止、PCサンプリング、動的アサーション（`{Debug_Integrated}`）、JITバイパス。

## 4. 未検証・スコープ外

- f32/f64演算（`interpreter_concept.py`自体にも実装がなく、スコープが仕様上不明瞭。README「Missing spec coverage」参照）。
