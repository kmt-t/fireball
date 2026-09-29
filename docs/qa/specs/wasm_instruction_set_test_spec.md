# WASM命令セット テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`wasm_instruction_set.md`](docs/specs/wasm_instruction_set.md)
関連: [`interpreter.md`](docs/components/tier3_executer/interpreter.md)（インタープリタ側実装）, [`jit_compiler.md`](docs/components/tier3_executer/jit_compiler.md)（JIT側実装）
参考実装: [`interpreter_concept.py`](docs/components/tier3_executer/concepts/interpreter_concept.py), [`bulk_memory_concept.py`](docs/components/tier3_executer/concepts/bulk_memory_concept.py)

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
| TEST-WASM-54 | `i32.div_s`/`div_u`のゼロ除算トラップ | 除数0 | 実行 | ゼロ除算をWASM trapにし、演算結果をstackへ書かない | wasm_instruction_set.md (Integer Arithmetic) |
| TEST-WASM-55 | `i32.and`/`or`/`xor`/`shl`/`shr_s`/`shr_u` | - | 実行 | ビット演算・シフトが正しい。i32のシフト量は下位5 bitだけを使う | wasm_instruction_set.md (Integer Arithmetic) |
| TEST-WASM-56 | `i32.rotl`/`rotr` | - | 実行 | 左右循環シフトのWASM意味論に従う | wasm_instruction_set.md (Integer Arithmetic) |

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

## 4. 未検証・スコープ外

- ARMv8-M向け物理命令列、ABI、メモリ保護、実機の受け入れ条件はTBDである。x64で確認した意味論・実装結果から推定しない。
- f32/f64の全演算子の個別テスト網羅は本書では定義しない。WASM意味論の詳細は [`wasm_instruction_set.md`](docs/specs/wasm_instruction_set.md) を参照する。
