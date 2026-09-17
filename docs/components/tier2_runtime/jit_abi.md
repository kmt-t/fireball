# JIT 実行コンテキスト ABI 契約

<!-- traceability: {ContextPointerRegister} {PositionIndependentCode} {JIT_RuntimeAPI_Fallback} -->

この文書は、Tier 2ランタイムがTier 3 JITへ提供する、複雑な処理をCヘルパーへ委譲するためのABI契約を定義する。JITトレースはトレースヘッダに共通領域オフセットとトレース固有のCPS関数ポインタを保持し、共通コードから関数ポインタを読み出す。実行コンテキストはこのルーティング情報を保持しない。

`execution_context` と関連するビュー型のC++ ABIは、固定フィールド順・サイズ・オフセットを持つ標準レイアウト構造体として定義する。インタープリタ単独実行とインタープリタからJITへの遷移で `ctx` のABI型を変えない。構造体は所有権を持たず、コード・関数表・結果配列は `address + length` の非所有ビューとして渡すため、呼び出し側が呼び出し完了まで対象領域の寿命を保証する。 `{ExecutionContext_Layout}` `{META_ZeroCostAbstraction}`

## コンテキスト拡張

既存の16個の32bit状態ワード（`+0x00`〜`+0x3F`）でコンテキストを終端する。コンテキスト全体は64バイトである。Cヘルパーアドレスはトレースごとのヘッダに置く。

| オフセット | サイズ | フィールド | 用途 |
| :--- | :--- | :--- | :--- |
| `+0x3C` | 4バイト | reserved | 64bitポインタのアライメント |

トレースヘッダの `helper_target_addr` は、当該トレースが委譲するCPS関数のアドレスである。共通ヘルパー入口はヘッダアドレスを受け取り、このフィールドをロードしてフレーム復元後に `jmp` する。共通領域のオフセットは `common_prologue_offset`、`common_epilogue_offset`、`common_helper_offset`、`absolute_pool_offset` で解決する。 `{PositionIndependentCode}`

`fireball_execution_context_native` は、32bitのゲスト状態フィールド16個からなる64バイトの標準レイアウトである。`fireball_const_buffer_view_native`、`fireball_wasm_function_view_native`、`fireball_wasm_module_view_native` は、WASMコードと関数メタデータを渡す非所有の標準レイアウト構造体である。文字列、`std::vector`、仮想関数、例外はこの境界に含めない。 `{ExecutionContext_Layout}` `{META_NoStdVector}`

実行時の `OperandStack`、`LocalStack`、`control_frame` は、固定容量の構造体と配列として配置する。値スタックは `WASM_VALUE_SLOT_BYTES` の境界に配置した raw 32-bit word 配列で、WASM の i32/f32 は1スロット、i64/f64 は2スロットを使用する。ローカルは `WASM_LOCAL_ALIGNMENT_BYTES` 固定スロットとし、JIT/インタープリタとも `slot * WASM_LOCAL_ALIGNMENT_BYTES` からアドレスを直接計算する。したがってローカルオフセット表を保持・参照する必要はなく、i64/f64の有効ワードも自然に境界へ置かれる。値の型タグは記録せず、型を知っているハンドラが対応する読み書きメソッドを選択する。制御フレームは`kind/start/match_end/stack_height/result_arity`を持つ20バイトの固定長レコード配列として保持する。 `{META_NoStdVector}`

関数の引数・戻り値バッファも同じ原則で扱う。`WasmRunRequestNative` と `WasmRunResultNative` はバッファポインタと個数だけを渡し、戻り値型や型タグを保持しない。呼び出し側が関数シグネチャを知っているため、必要なスロット幅と解釈は呼び出し側で決める。

## 委譲シグネチャ

ヘルパーはJITトレースと同じCPS 4引数規約を使う。

| 引数 | Windows x64 | System V AMD64 | 意味 |
| :--- | :--- | :--- | :--- |
| 第1引数 | `RCX` | `RDI` | `ctx` |
| 第2引数 | `RDX` | `RSI` | `sp` |
| 第3引数 | `R8` | `RDX` | `local_base` |
| 第4引数 | `R9` | `RCX` | `tos` |

共通委譲スタブはトレースヘッダの `helper_target_addr` をロードし、JIT自身のcallee-savedレジスタを復元してから `jmp` する。委譲前にJITのハードウェアスタックを共有Nativeスタックへraw 32bitワードとして書き戻す。したがってヘルパーはJITの戻りアドレスを新たに積まず、同じABIで呼び出し元へ復帰する。スタック状態を同期できないトレースはこの委譲経路へコンパイルしてはならない。 `{JIT_RuntimeAPI_Fallback}`
