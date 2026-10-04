# WASM命令セット テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`wasm_instruction_set.md`](docs/specs/wasm_instruction_set.md)
関連: [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)（インタープリタ側実装）, [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md)（JIT側実装）
参考実装: [`interpreter_concept.py`](docs/components/tier2_runtime/concepts/interpreter_concept.py), [`bulk_memory_concept.py`](docs/components/tier2_runtime/concepts/bulk_memory_concept.py)

インタープリタ・JIT双方が対応すべきWASM Core 1.0 MVPと、`{WasmFCSubset}`が列挙する`0xFC`部分集合の意味論を命令カテゴリごとに検証する。本書は個々のオプコードのスタック遷移・トラップ条件を横断的に一覧化する（実行エンジンごとの内部実装詳細は`interpreter_test_spec.md`/`jit_compiler_test_spec.md`を参照）。x64で確認した実装を対象とし、ARMv8-Mの物理命令列と実機受入れはTBDである。

## 2. テストケース一覧

### 非サポート機能の拒否 (Wasm32Only)
<!-- traceability: {Wasm32Only} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WASM-01 | Wasm64/Memory64/Table64の拒否 | 該当構文を含むバイナリ | ロード | `ERR_WASM_UNSUPPORTED_FEATURE`で即時拒否 | `Wasm32Only` |
| TEST-WASM-02 | SIMD(`0xFD`)の拒否 | SIMDプレフィックス命令 | ロード | 同上 | Wasm32Only |
| TEST-WASM-03 | Threads/Atomics(`0xFE`)の拒否 | 該当命令 | ロード | 同上 | Wasm32Only |
| TEST-WASM-04 | 参照型(`externref`/`funcref`をGC対象として)の拒否 | 該当構文 | ロード | 同上 | Wasm32Only |
| TEST-WASM-05 | 例外処理(EH)命令の拒否 | 該当命令 | ロード | 同上 | Wasm32Only |
| TEST-WASM-06 | Tail Call(`return_call`/`return_call_indirect`)の拒否 | 該当命令 | ロード | 同上 | Wasm32Only |

### 制御フロー

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WASM-10 | `unreachable` | - | 実行 | トラップハンドラへジャンプ | wasm_instruction_set.md (Control Flow) |
| TEST-WASM-11 | `nop` | - | 実行(JIT) | 0バイト生成（命令生成スキップ） | wasm_instruction_set.md (Control Flow) |
| TEST-WASM-12 | `block`/`loop`/`if`/`else`/`end`のラベル解決 | ネストしたブロック | 実行 | 分岐先ラベルが正しく記録・解決される | wasm_instruction_set.md (Control Flow) |
| TEST-WASM-13 | `br`/`br_if`/`br_table` | 各種分岐条件 | 実行 | スタック遷移`[i32]->[]`等を満たし、正しい深さへジャンプ | wasm_instruction_set.md (Control Flow) |
| TEST-WASM-14 | `return` | 関数呼び出し中 | 実行 | コールフレームをpopして復帰 | wasm_instruction_set.md (Control Flow) |
| TEST-WASM-15 | `call`/`call_indirect` | 直接/間接呼び出し | 実行 | 関数呼出し記述子を積んで関数を呼び出す。`call_indirect`は型シグネチャ照合を行う | wasm_instruction_set.md (Control Flow) |

### パラメトリック

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WASM-20 | `drop` | スタックに1値 | 実行 | `[t]->[]` | wasm_instruction_set.md (Parametric) |
| TEST-WASM-21 | `select` | 条件+2値 | 実行 | `[t,t,i32]->[t]`、条件で選択 | wasm_instruction_set.md (Parametric) |
| TEST-WASM-22 | 広幅値を含む混在operand stack上の`drop` | 周囲にi32値を残し、f32/i64/f64値を順に積む | 各値を`drop`してからi32演算・returnを実行 | i64/f64はそれぞれ2 raw word分が除去され、下のi32値を壊さない | wasm_instruction_set.md (Parametric), pysim `test_jitr_mixed_typed_stack_declines_jit_without_losing_drop_widths` |

### 変数アクセス

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WASM-30 | `local.get`/`set`/`tee` | ローカル変数宣言済み | 実行 | `local_base`起点の静的オフセットで正しく読み書き | wasm_instruction_set.md (Variable Access) |
| TEST-WASM-31 | `global.get`/`set` | グローバル変数宣言済み | 実行 | `env`(グローバル配列)経由で正しく読み書き | wasm_instruction_set.md (Variable Access) |

### メモリアクセス ({MemoryBoundaryCheck})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WASM-40 | `i32.load`/`i64.load`/`f32.load`/`f64.load` | メモリ確保済み、境界内アドレス | 実行 | 各幅で正しくロードされる。事前に境界チェック(比較+トラップ)を実施 | {MemoryBoundaryCheck} |
| TEST-WASM-41 | `i32.load8_s/u`, `load16_s/u` | 同上 | 実行 | 符号/ゼロ拡張が正しい | {MemoryBoundaryCheck} |
| TEST-WASM-42 | `i32.store`/`i64.store`/`f32.store`/`f64.store` | 同上 | 実行 | 各幅で正しくストア | {MemoryBoundaryCheck} |
| TEST-WASM-43 | `i32.store8`/`store16` | 同上 | 実行 | 指定幅のみ書き込む | {MemoryBoundaryCheck} |
| TEST-WASM-44 | 境界外アクセスのトラップ | `addr`がメモリ範囲外 | load/store実行 | 比較+トラップで即座に検出（黙って折り畳まない） | {MemoryBoundaryCheck} |
| TEST-WASM-45 | `memory.size` | - | 実行 | 現在のページ数(u32)を返す | {MemoryBoundaryCheck} |
| TEST-WASM-46 | `memory.grow` | 拡張要求ページ数 | 実行 | メモリ拡張後、旧ページ数を返す（ランタイムAPI呼出） | {MemoryBoundaryCheck} |

### 整数算術・論理・比較

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WASM-50 | `i32.const`/`i64.const` | - | 実行 | 即値を正しくpush | wasm_instruction_set.md (Integer Arithmetic) |
| TEST-WASM-51 | `i32.eqz`/`eq`/`ne`/`lt_s`/`lt_u`(以降gt/le/ge含む全10種) | 2値または1値 | 実行 | 比較結果(0/1)を返す。符号付き/符号なしを区別する | wasm_instruction_set.md (Integer Arithmetic) |
| TEST-WASM-52 | `i32.clz`/`ctz`/`popcnt` | 既知のビットパターン | 実行 | 正しいビットカウント | wasm_instruction_set.md (Integer Arithmetic) |
| TEST-WASM-53 | `i32.add`/`sub`/`mul` | - | 実行 | 32bitラップアラウンド | wasm_instruction_set.md (Integer Arithmetic) |
| TEST-WASM-54 | 整数除算・剰余のゼロ除算トラップ | i32/i64の`div_s`、`div_u`、`rem_s`、`rem_u`、除数0 | pysimとwasmtimeで同じ命令列を実行する | pysimは`INTEGER_DIVIDE_BY_ZERO`、wasmtimeは`INTEGER_DIVISION_BY_ZERO`を返す。pysimは成功結果を公開しない | [`test_wasm_differential.py`](experiments/pysim/qa/tier2_runtime/test_wasm_differential.py) |
| TEST-WASM-55 | `i32.and`/`or`/`xor`/`shl`/`shr_s`/`shr_u` | - | 実行 | ビット演算・シフトが正しい。i32のシフト量は下位5 bitだけを使う | wasm_instruction_set.md (Integer Arithmetic) |
| TEST-WASM-56 | `i32.rotl`/`rotr` | - | 実行 | 左右循環シフトのWASM意味論に従う | wasm_instruction_set.md (Integer Arithmetic) |
| TEST-WASM-57 | 符号付き整数除算のoverflow | i32/i64の最小値を-1で除算する | pysimとwasmtimeで同じ命令列を実行する | 両エンジンが`INTEGER_OVERFLOW`を返す。ゼロ除算と区別し、pysimは成功結果を公開しない | [`test_wasm_differential.py`](experiments/pysim/qa/tier2_runtime/test_wasm_differential.py) |
| TEST-WASM-58 | NaNから整数への非飽和変換 | `i32.trunc_f64_s`の入力がNaNである | pysimとwasmtimeで同じ命令列を実行する | pysimは`INVALID_CONVERSION`、wasmtimeは`BAD_CONVERSION_TO_INTEGER`を返す。pysimは成功結果を公開しない | [`test_wasm_differential.py`](experiments/pysim/qa/tier2_runtime/test_wasm_differential.py) |

### 選択された`0xFC`命令 ({WasmFCSubset})
<!-- traceability: {WasmFCSubset} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-WASM-60 | `0xFC`サブオペコードのデコード | サブオペコード0〜12および不正LEB128 | モジュールをロード | 0〜7、10、11のみ受理。8、9、12以上および不正LEB128はロード時に拒否 | `{WasmFCSubset}` |
| TEST-WASM-61 | 飽和変換のNaN・無限大 | f32/f64のNaN、`+∞`、`-∞` | 8変換命令を実行 | NaNは0、正の無限大は整数上限、負の無限大はsigned下限またはunsigned 0。変換trapなし | `{WasmFCSubset}` |
| TEST-WASM-62 | 飽和変換のsigned境界 | i32/i64 signed変換先の下限・上限および隣接値 | f32/f64から実行 | 0方向への切り捨て後にsigned範囲へ収まり、範囲外値は下限/上限へ飽和 | `{WasmFCSubset}` |
| TEST-WASM-63 | 飽和変換のunsigned境界 | i32/i64 unsigned変換先、負の小数を含む | f32/f64から実行 | 負の値は0方向切捨て後に0、上限超過はunsigned最大値へ飽和 | `{WasmFCSubset}` |
| TEST-WASM-64 | Interpreter/JIT間の飽和変換一致 | 同一の変換命令列 | InterpreterとJIT有効構成で実行 | 8命令の結果ビット幅、NaN、境界値が一致する。未実装JIT命令はInterpreterへ委譲する | `{WasmFCSubset}` `{JIT_RuntimeAPI_Fallback}` |
| TEST-WASM-70 | `memory.copy`の重複領域 | 同一memory内で前方・後方重複、完全一致 | copy実行後に全byteを比較 | memmove意味論でコピー元の元データと一致し、vDMAを起動しない | `{WasmFCSubset}` `{VDMA}` |
| TEST-WASM-71 | `memory.copy`の非重複領域とゼロ長 | 有効な範囲、境界ちょうどのゼロ長範囲 | copy実行 | byte列が一致する。`len == 0`は範囲内offsetで無変更・DMAなし | `{WasmFCSubset}` |
| TEST-WASM-72 | `memory.copy`の両範囲境界 | sourceまたはdestinationが終端を超える。ゼロ長でoffsetが終端より大きい場合も含む | copy実行 | WASM memory trapとなり、宛先を含むメモリに部分更新がない | `{WasmFCSubset}` `{MemoryBoundaryCheck}` |
| TEST-WASM-73 | `memory.fill`の値と範囲 | byteパターン`0x00`、`0xFF`、`-1`、複数上位bitを持つvalue | fill実行 | i32のbit pattern下位8 bitだけが指定範囲へ書かれ、範囲外はtrap、vDMAは使わない | `{WasmFCSubset}` |
| TEST-WASM-74 | `memory.copy`/`fill`のmemory index | index 0および非ゼロ即値 | モジュールをロード | index 0だけ受理し、非ゼロindexは単一メモリ仕様によりロード時拒否 | `{WasmFCSubset}` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

### pysim差分試験の契約と観測

[`test_wasm_differential.py`](experiments/pysim/qa/tier2_runtime/test_wasm_differential.py)は同じWASMバイナリと引数をpysimとwasmtimeへ渡す。成功時は整数値、浮動小数点のbit pattern、NaN分類を比較する。異常系は事前に宣言したguest trap理由を両エンジンで照合する。pysimは`start`と`step`が返す構造化trapを観測し、`AssertionError`などの実装不変条件違反をguest trapとして捕捉しない。wasmtimeのロードエラーもguest trapとして捕捉しない。

| 仕様契約 | 実行関数 | 具体的な範囲とオラクル |
| :--- | :--- | :--- |
| TEST-WASM-52〜54/56 | `test_differential_i32_i64_arithmetic` | 既存11入力を維持する。正常値とゼロ除算の具体理由をwasmtimeと比較する |
| TEST-WASM-54 | `test_differential_integer_zero_divisor_trap` | 2整数幅×4除算・剰余形式の8組を全数実行する |
| TEST-WASM-57 | `test_differential_signed_division_overflow_trap` | i32/i64の2境界を実行する |
| TEST-WASM-10/58 | `test_differential_declared_instruction_traps` | unreachableとNaN変換の2原因を区別する |
| TEST-WASM-40/42/44 | `test_differential_memory_boundary_and_side_effects` | 1ページmemoryのload/store×4アドレスを全数実行する。65532は最終有効word、65533はword途中の越境、65536は先頭が境界外、0x7FFFFFFFは通常領域の上端である。全メモリbyteをwasmtimeと比較し、不正storeの部分更新を検出する |
| 浮動小数点意味論 | `test_differential_f32_operations`、`test_differential_f64_operations` | f32の13入力、f64の8入力を維持する。符号付きゼロはbit pattern、NaNは分類で比較する |
| TEST-WASM-12/13/30 | `test_differential_control_flow` | Collatzの入力6、1、27の3例を維持し、最終値をwasmtimeと比較する |

この差分試験は列挙した契約の部分検証である。表に紐付けたケース全体の網羅を主張しない。行・分岐カバレッジ率は受入れ条件に使わない。

2026-10-01にLinux x64、Python 3.14.6で次のコマンドを実行し、24件PASS、FAIL 0件、SKIP 0件を確認した。

```bash
UV_CACHE_DIR=/tmp/fireball-test-design-uv uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier2_runtime/test_wasm_differential.py
```

試験プロセス内だけでゼロ除算の結果を変更する2変異を実行した。無関係な`AssertionError`への置換は、例外を成功扱いせずFAIL 1件となった。trap理由を`INTEGER_OVERFLOW`へ変更する変異は、期待した`INTEGER_DIVIDE_BY_ZERO`との不一致でFAIL 1件となった。製品ソースは変更していない。

## 4. 未検証・スコープ外

- ARMv8-M向け物理命令列、ABI、メモリ保護、実機の受け入れ条件はTBDである。x64で確認した意味論・実装結果から推定しない。
- f32/f64の全演算子の個別テスト網羅は本書では定義しない。WASM意味論の詳細は [`wasm_instruction_set.md`](docs/specs/wasm_instruction_set.md) を参照する。
- この差分試験は全WASM opcode、全値、全制御履歴、JIT生成コードを網羅しない。
- `0x80000000`以上のFireball固有VMMIO領域はwasmtimeと意味論が異なるため、この差分試験の対象外である。
- NaN payloadの一致、浮動小数点から整数への全境界変換、間接callの全trap理由は、この差分試験では未検証である。
