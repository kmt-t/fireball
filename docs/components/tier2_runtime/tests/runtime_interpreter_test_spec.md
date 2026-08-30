# WASMインタープリタ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier2_runtime/runtime_interpreter.md`, `docs/specs/wasm_instruction_set.md`
参考実装: `docs/components/tier2_runtime/concepts/interpreter_concept.py`

`{ThreadedInterpreter}`（CPS 4引数ハンドラ方式）、統合スタック（`execution_context`）、ラベルアリティに基づくスタックプルーニング、i32/i64演算、境界チェック付きメモリアクセス、Safepointポーリングを検証する。

## 2. テストケース一覧

### CPSディスパッチ方式そのもの (§1, interpreter_concept.py冒頭)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INTP-01 | ハンドラのシグネチャがCPS 4引数(`ip, stack_bot, env, local_base`)である | 実装コードを確認 | 各opcodeハンドラの引数を確認 | すべてのハンドラが同一の4引数シグネチャを持ち、次の継続を自ら返す（中央のswitch/if-elifループが「次に何をするか」を決定しない） | wasm_instruction_set.md §1, interpreter_concept.py冒頭 |
| INTP-02 | ハンドラテーブルによるディスパッチ | - | ディスパッチ機構を確認 | opcode→ハンドラ関数のテーブル参照で分岐し、線形if-elif連鎖ではない | 同上 |
| INTP-03 | インタープリタとJITトレースのCPS 4引数規約完全一致 | JITトレース生成 | JITエントリとハンドラシグネチャを比較 | `int64_t (*)(uint32_t ip, void* stack_bot, void* env, void* local_base)` で完全一致し、ディスパッチテーブルから直接 C 呼び出し可能 | `{ContextPointerRegister}` `{EnvironmentPointer}` `{PositionIndependentCode}` |
| INTP-04 | JITトレースからインタープリタへのシームレスフォールバック | 未コンパイルのブロックへ分岐 | トレース実行完了 | トレース末尾でインタープリタへスムーズに復帰し、後続ブロックをインタープリタが継続実行する | `{JIT_LazyChaining}` `{JIT_RuntimeAPI_Fallback}` |

### 統合スタック・関数呼び出し (interpreter_concept.py §1)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INTP-10 | スタックオーバーフロートラップ | `stack_capacity`を超えるpush | 再帰呼び出し等でスタックを溢れさせる | `WASMTrap("STACK_OVERFLOW")`相当が発生する（無限にリストが伸びない） | interpreter_concept.py `ExecutionContext.push` |
| INTP-11 | スタックアンダーフロートラップ | 空スタックでpop | pop操作 | `WASMTrap("STACK_UNDERFLOW")`相当 | interpreter_concept.py `ExecutionContext.pop` |
| INTP-12 | 再帰呼び出し（call）とlocal_base | `fact(n)`のような再帰関数 | `execute_function`で呼び出す | 各呼び出しごとに新しい`local_base`が割り当てられ、ローカル変数が互いに独立する | interpreter_concept.py `test_full_wasm_recursive_factorial` |
| INTP-13 | 戻り値の受け渡し | 関数が1個の結果を返す | `return`実行後の呼び出し元スタック | 呼び出し元のスタックに正しく結果が積まれる | interpreter_concept.py `execute_function` |

### ラベルアリティ・スタックプルーニング (interpreter_concept.py §1, `prune_stack`)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INTP-20 | `block (result i32)`から`br`で抜ける際のスタックプルーニング | `block`内で複数値をpushしてから`br 0` | ブロック終端まで実行 | ブロック開始時の高さまでロールバックしつつ、ブロックの宣言アリティ分（末尾のN個）の値だけが保持される | interpreter_concept.py `test_block_loop_and_stack_pruning`（`i32.const 10; i32.const 42; br 0`→結果42のみ残る） |
| INTP-21 | void結果のブロックからの脱出 | `block`の結果型が空 | `br`で脱出 | ブロック開始時の高さまで完全にロールバックされる（保持する値なし） | wasm_instruction_set.md br のスタック遷移 `[] -> []` |
| INTP-22 | br_tableでの多重ネストとプルーニング | 3階層以上ネストしたblock+br_table | 各indexで実行 | 深さに応じた正しいプルーニング＋分岐先ジャンプが行われる | interpreter_concept.py `test_br_table_and_parametric` |
| INTP-23 | ループ背進辺でのプルーニング挙動 | `loop`から`br 0`（継続） | 実行 | ループ本体の先頭へ戻り、ループ自身のアリティに応じたプルーニングが行われる | interpreter_concept.py `br`の`is_loop`分岐 |

### i64整数演算 (interpreter_concept.py §3.7, wasm_instruction_set.md §3.4)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INTP-30 | `i64.const`/`i64.add`/`i64.sub`/`i64.mul` | - | 64bit範囲の値で演算 | 64bitでラップアラウンドする（32bitではない） | interpreter_concept.py `test_64bit_integer_arithmetic` |
| INTP-31 | `i64.div_s`/`div_u`/`rem_s`/`rem_u`のゼロ除算 | 除数0 | 演算実行 | `WASMTrap("INTEGER_DIVIDE_BY_ZERO")` | interpreter_concept.py i64.div系 |
| INTP-32 | `i64.clz`/`ctz`/`popcnt` | 既知のビットパターン | 演算実行 | 64bit幅で正しいビットカウントを返す | interpreter_concept.py |
| INTP-33 | `i64.shl`/`shr_s`/`shr_u`/`rotl`/`rotr`のシフト量マスク | シフト量>63 | 演算実行 | シフト量が`& 63`でマスクされる（32ではない） | interpreter_concept.py |
| INTP-34 | `i32.wrap_i64` | i64値 | 変換実行 | 下位32bitのみ抽出 | interpreter_concept.py |
| INTP-35 | `i64.extend_i32_s`/`extend_i32_u` | i32値（符号付き/符号なし） | 変換実行 | 符号拡張/ゼロ拡張された64bit値になる | interpreter_concept.py |

### メモリアクセス全幅 (interpreter_concept.py §3.4)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INTP-40 | `i64.load`/`i64.store` | メモリ確保済み | 8バイト境界内アクセス | 正しく読み書きされる | interpreter_concept.py |
| INTP-41 | `i64.load8_s/u`, `load16_s/u`, `load32_s/u` | 同上 | 各幅でアクセス | 符号/ゼロ拡張が幅ごとに正しい | interpreter_concept.py |
| INTP-42 | `i64.store8/16/32` | 同上 | 各幅で書き込み | 指定幅のみ書き込まれ、他バイトは変化しない | interpreter_concept.py |
| INTP-43 | 全幅共通の境界外トラップ | `addr + width > len(memory)` | 各load/store | `WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")` | interpreter_concept.py 全load/store |

### Cooperative Safepoint (interpreter_concept.py §末尾)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INTP-50 | ループ背進辺でのSafepointポーリング | `safepoint_pending = True`かつ無限ループ | 実行 | `br`がループ先頭へ戻る直前に`SAFEPOINT_YIELD`を返して中断する | interpreter_concept.py `test_cooperative_safepoint` |
| INTP-51 | Safepoint未発生時は通常続行 | `interrupt_flag = False` | ループ実行 | ポーリングは行われるが中断されない | interpreter_concept.py `poll_safepoint` |

### デバッガ・プロファイラ統合とハンドラテーブル切り替え (§3.3, §4.1, {DebuggerLabelTableSwitch}, {Debug_Integrated})

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| INTP-60 | デバッグ未アタッチ時のゼロオーバーヘッド | デバッガ未接続 (`is_debug_mode=False`) | 通常実行 | インタープリタは標準ハンドラテーブル（`normal_handler_table`）を使用し、デバッグフックやテーブル分岐のオーバーヘッドなしで最高速実行される | §3.3「有効命令ハンドラ」, §4.1 `{DebuggerLabelTableSwitch}` |
| INTP-61 | デバッガアタッチ時のハンドラテーブル切り替え | デバッガ接続 (`is_debug_mode=True`) | 実行 | インタープリタの有効ハンドラがデバッグ用テーブル（`debug_handler_table`）へ動的に切り替わり、命令実行ごとにデバッグフックが呼び出される | §3.3「有効命令ハンドラ」, §4.1 手順3 `{DebuggerLabelTableSwitch}` |
| INTP-62 | デバッグハンドラでのブレークポイント検知・停止 | PC=0x100 にブレークポイント設定 | 実行継続 | インタープリタが命令実行前にブレークポイントを検知し、実行を中断して停止状態（SIGTRAP）へ遷移する | §4.1「デバッグ・プロファイラフック」 |
| INTP-63 | 統合プロファイラフック（PCサンプリング） | プロファイラ有効 | 命令実行 | デバッグハンドラ経由で命令が実行されるたびに実行中PCがサンプリングされ、頻度統計が正確に集計される | §4.1「デバッグ・プロファイラフック」 `{Debug_Integrated}` |
| INTP-64 | 動的メモリアサーションフック | メモリアサーション登録 | 命令実行 | 命令実行後にメモリフックが呼び出され、期待値と異なる値が検知された場合にアサーション違反が記録される | §4.1「デバッグ・プロファイラフック」 `{Debug_Integrated}` |
| INTP-65 | デバッグアタッチ時のJITバイパス（インタープリタフォールバック） | JITキャッシュにトレースが存在 | デバッグアタッチ下で実行 | JIT直接ジャンプをバイパスし、インタープリタのデバッグハンドラで1命令ずつ安全にステップ制御される | §4.1 `{DebuggerLabelTableSwitch}` |

## 3. テスト検証実績と網羅状況

- **CPSディスパッチ & 統合スタック (INTP-01〜13)**: 4引数規約、スタックアンダー/オーバーフロー、再帰呼び出し、戻り値。
- **ラベルアリティ & プルーニング (INTP-20〜23)**: ブロック脱出、ループ背進辺、多重ネスト br_table。
- **i64全演算 & メモリアクセス (INTP-30〜43)**: 64bit算術・シフト・ビットカウント・境界外トラップ。
- **Safepointポーリング (INTP-50〜51)**: ループ背進辺での協調的ポーリング。
- **デバッガ・プロファイラ統合 (INTP-60〜65)**: ハンドラテーブル切り替え（`{DebuggerLabelTableSwitch}`）、ブレークポイント停止、PCサンプリング、動的アサーション（`{Debug_Integrated}`）、JITバイパス。

## 4. 未検証・スコープ外

- f32/f64演算（`interpreter_concept.py`自体にも実装がなく、スコープが仕様上不明瞭。README「Missing spec coverage」参照）。
- `wit/execution_context.wit`によるWIT型定義そのものとの整合性。
