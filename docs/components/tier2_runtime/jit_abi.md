# JIT 実行コンテキスト ABI 契約

<!-- traceability: {ContextPointerRegister} {PositionIndependentCode} {JIT_RuntimeAPI_Fallback} -->

この文書は、Tier 2ランタイムがTier 3 JITへ提供する、複雑な処理をCヘルパーへ委譲するためのABI契約を定義する。JITはプロセス内の関数アドレスを命令列へ埋め込まず、CPSの第1引数 `ctx` が指す実行コンテキストから関数ポインタを読み出す。これにより、コードキャッシュを任意のアドレスへ複製しても同じコードを実行できる。 `{ContextPointerRegister}` `{PositionIndependentCode}`

PythonシミュレータとC++実装の共有正本は [`wasm_interop.hxx`](experiments/pysim/tier2_runtime/wasm_interop.hxx) とし、Python側は [`interop_abi.py`](experiments/pysim/tier2_runtime/interop_abi.py) の `ctypes.Structure` で同じフィールド順・サイズ・オフセットを検査する。`InterpreterContext` と `WASMContext` は同じ `ExecutionContextNative` を実行コンテキストとして保持し、インタープリタ単独実行とインタープリタからJITへの遷移で `ctx` のABI型を変えない。Native構造体は所有権を持たず、コード・関数表・結果配列は `address + length` の非所有ビューとして渡す。したがって、ネイティブ呼び出しが終了するまで、バッファと記述子の寿命をPython側で保持する。 `{ExecutionContext_Layout}` `{META_ZeroCostAbstraction}`

## コンテキスト拡張

既存の15個の32bit状態ワード（`+0x00`〜`+0x3B`）に続けて、4バイトのアライメント領域と、委譲命令ごとのJIT専用64bit関数ポインタ配列を置く。コンテキスト全体は152バイトである。

| オフセット | サイズ | フィールド | 用途 |
| :--- | :--- | :--- | :--- |
| `+0x3C` | 4バイト | reserved | 64bitポインタのアライメント |
| `+0x40` | 88バイト | `jit_helper_ptrs[11]` | 命令ごとのJIT直接末尾遷移先（各要素8バイト） |

`jit_helper_ptrs` は実行時に設定される命令別の関数ポインタ配列であり、JITコードのリロケーション対象ではない。実行する委譲命令に対応する要素が0であってはならない。JITはコンパイル時に決めた要素をコンテキスト相対で直接ロードし、インタープリタはこの配列を参照しない。 `{PositionIndependentCode}`

`fireball_execution_context_native` は、32bitのゲスト状態フィールド15個、予約領域4バイト、および11個のJIT専用64bit helperアドレスからなる152バイトの標準レイアウトである。`fireball_const_buffer_view_native`、`fireball_wasm_function_view_native`、`fireball_wasm_module_view_native` は、Python/C++間でWASMコードと関数メタデータを渡すための非所有Native構造体である。文字列、`std::vector`、Pythonオブジェクト、仮想関数、例外はこの境界に含めない。 `{ExecutionContext_Layout}` `{META_NoStdVector}`

実行時の `OperandStack`、`LocalStack`、`control_frame` も Python コンテナではなく固定容量の Native レコードを実体とする。値スタックは8バイト境界に配置した raw 32-bit word 配列で、WASM の i32/f32 は1スロット、i64/f64 は2スロットを使用する。ローカルは1個あたり16バイトの固定スロットとし、JIT/インタープリタとも `slot * 16` からアドレスを直接計算する。したがってローカルオフセット表を保持・参照する必要はなく、i64/f64の有効ワードも自然に8バイト境界へ置かれる。値の型タグは記録せず、型を知っているインタープリタ/JIT ハンドラが対応する読み書きメソッドを選択する。制御フレームは固定長の4ワードレコード配列として保持する。 `{META_NoStdVector}`

関数の引数・戻り値バッファも同じ原則で扱う。`WasmRunRequestNative` と `WasmRunResultNative` はバッファポインタと個数だけを渡し、戻り値型や型タグを保持しない。呼び出し側が関数シグネチャを知っているため、必要なスロット幅と解釈は呼び出し側で決める。インタープリタのPython向け `call()` が型付き値へ変換する処理は、このNative ABIの外側にあるアダプタ処理である。

## 委譲シグネチャ

ヘルパーはJITトレースと同じCPS 4引数規約を使う。

| 引数 | Windows x64 | System V AMD64 | 意味 |
| :--- | :--- | :--- | :--- |
| 第1引数 | `RCX` | `RDI` | `ctx` |
| 第2引数 | `RDX` | `RSI` | `sp` |
| 第3引数 | `R8` | `RDX` | `local_base` |
| 第4引数 | `R9` | `RCX` | `tos` |

委譲スタブは対象命令の `jit_helper_ptrs[]` 要素を `ctx` 相対で直接ロードし、JIT自身のcallee-savedレジスタを復元してから `jmp` する。委譲前にJITのハードウェアスタックを共有Nativeスタックへraw 32bitワードとして書き戻す。したがってヘルパーはJITの戻りアドレスを新たに積まず、同じABIで呼び出し元へ復帰する。スタック状態を同期できないトレースはこの委譲経路へコンパイルしてはならない。 `{JIT_RuntimeAPI_Fallback}`
