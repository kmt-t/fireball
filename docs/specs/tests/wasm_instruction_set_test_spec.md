# WASM命令セット テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`wasm_instruction_set.md`](docs/specs/wasm_instruction_set.md)
関連: [`runtime_interpreter.md`](docs/components/tier2_runtime/runtime_interpreter.md)（インタープリタ側実装）, [`jit_compiler.md`](docs/components/tier3_jit/jit_compiler.md)（JIT側実装）
参考実装: [`interpreter_concept.py`](docs/components/tier2_runtime/concepts/interpreter_concept.py)

インタープリタ・JIT双方が対応すべきWASM MVPオプコード物理マトリクスを、命令カテゴリごとに検証する。本書は個々のオプコードのスタック遷移・トラップ条件を横断的に一覧化する（実行エンジンごとの内部実装詳細は`runtime_interpreter_test_spec.md`/`jit_compiler_test_spec.md`を参照）。

## 2. テストケース一覧

### 非サポート機能の拒否 ({Wasm32Only})

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| WASM-01 | Wasm64/Memory64/Table64の拒否 | 該当構文を含むバイナリ | ロード | `ERR_WASM_UNSUPPORTED_FEATURE`で即時拒否 | `{Wasm32Only}` |
| WASM-02 | SIMD(`0xFD`)の拒否 | SIMDプレフィックス命令 | ロード | 同上 | {Wasm32Only} |
| WASM-03 | Threads/Atomics(`0xFE`)の拒否 | 該当命令 | ロード | 同上 | {Wasm32Only} |
| WASM-04 | 参照型(`externref`/`funcref`をGC対象として)の拒否 | 該当構文 | ロード | 同上 | {Wasm32Only} |
| WASM-05 | 例外処理(EH)命令の拒否 | 該当命令 | ロード | 同上 | {Wasm32Only} |
| WASM-06 | Tail Call(`return_call`/`return_call_indirect`)の拒否 | 該当命令 | ロード | 同上 | {Wasm32Only} |

### 制御フロー

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| WASM-10 | `unreachable` | - | 実行 | トラップハンドラへジャンプ | wasm_instruction_set.md (Control Flow) |
| WASM-11 | `nop` | - | 実行(JIT) | 0バイト生成（命令生成スキップ） | wasm_instruction_set.md (Control Flow) |
| WASM-12 | `block`/`loop`/`if`/`else`/`end`のラベル解決 | ネストしたブロック | 実行 | 分岐先ラベルが正しく記録・解決される | wasm_instruction_set.md (Control Flow) |
| WASM-13 | `br`/`br_if`/`br_table` | 各種分岐条件 | 実行 | スタック遷移`[i32]->[]`等を満たし、正しい深さへジャンプ | wasm_instruction_set.md (Control Flow) |
| WASM-14 | `return` | 関数呼び出し中 | 実行 | コールフレームをpopして復帰 | wasm_instruction_set.md (Control Flow) |
| WASM-15 | `call`/`call_indirect` | 直接/間接呼び出し | 実行 | `call_frame`を積んで関数呼出。`call_indirect`は型シグネチャ照合を行う | wasm_instruction_set.md (Control Flow) |

### パラメトリック

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| WASM-20 | `drop` | スタックに1値 | 実行 | `[t]->[]` | wasm_instruction_set.md (Parametric) |
| WASM-21 | `select` | 条件+2値 | 実行 | `[t,t,i32]->[t]`、条件で選択 | wasm_instruction_set.md (Parametric) |

### 変数アクセス

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| WASM-30 | `local.get`/`set`/`tee` | ローカル変数宣言済み | 実行 | `local_base`起点の静的オフセットで正しく読み書き | wasm_instruction_set.md (Variable Access) |
| WASM-31 | `global.get`/`set` | グローバル変数宣言済み | 実行 | `env`(グローバル配列)経由で正しく読み書き | wasm_instruction_set.md (Variable Access) |

### メモリアクセス ({MemoryBoundaryCheck})

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| WASM-40 | `i32.load`/`i64.load`/`f32.load`/`f64.load` | メモリ確保済み、境界内アドレス | 実行 | 各幅で正しくロードされる。事前に境界チェック(比較+トラップ)を実施 | {MemoryBoundaryCheck} |
| WASM-41 | `i32.load8_s/u`, `load16_s/u` | 同上 | 実行 | 符号/ゼロ拡張が正しい | {MemoryBoundaryCheck} |
| WASM-42 | `i32.store`/`i64.store`/`f32.store`/`f64.store` | 同上 | 実行 | 各幅で正しくストア | {MemoryBoundaryCheck} |
| WASM-43 | `i32.store8`/`store16` | 同上 | 実行 | 指定幅のみ書き込む | {MemoryBoundaryCheck} |
| WASM-44 | 境界外アクセスのトラップ | `addr`がメモリ範囲外 | load/store実行 | 比較+トラップで即座に検出（黙って折り畳まない） | {MemoryBoundaryCheck} |
| WASM-45 | `memory.size` | - | 実行 | 現在のページ数(u32)を返す | {MemoryBoundaryCheck} |
| WASM-46 | `memory.grow` | 拡張要求ページ数 | 実行 | メモリ拡張後、旧ページ数を返す（ランタイムAPI呼出） | {MemoryBoundaryCheck} |

### 整数算術・論理・比較

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| WASM-50 | `i32.const`/`i64.const` | - | 実行 | 即値を正しくpush | wasm_instruction_set.md (Integer Arithmetic) |
| WASM-51 | `i32.eqz`/`eq`/`ne`/`lt_s`/`lt_u`(以降gt/le/ge含む全10種) | 2値または1値 | 実行 | 比較結果(0/1)を返す。符号付き/符号なしを区別する | wasm_instruction_set.md (Integer Arithmetic) |
| WASM-52 | `i32.clz`/`ctz`/`popcnt` | 既知のビットパターン | 実行 | 正しいビットカウント | wasm_instruction_set.md (Integer Arithmetic) |
| WASM-53 | `i32.add`/`sub`/`mul` | - | 実行 | 32bitラップアラウンド | wasm_instruction_set.md (Integer Arithmetic) |
| WASM-54 | `i32.div_s`/`div_u`のゼロ除算トラップ | 除数0 | 実行 | 0判定後にトラップ（SDIV/UDIVを実行しない） | wasm_instruction_set.md (Integer Arithmetic) |
| WASM-55 | `i32.and`/`or`/`xor`/`shl`/`shr_s`/`shr_u` | - | 実行 | ビット演算・シフトが正しい。シフト量は実装依存のマスク幅 | wasm_instruction_set.md (Integer Arithmetic) |
| WASM-56 | `i32.rotl`/`rotr` | - | 実行 | `RSB+ROR`相当（左右循環シフト）が正しい | wasm_instruction_set.md (Integer Arithmetic) |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- 本書自体の「物理動作・備考」列（Thumb-2実機命令列）は
- f32/f64の算術演算子（wasm_instruction_set.md に該当行が存在せず、スコープが不明瞭。README「Missing spec coverage」参照）。
