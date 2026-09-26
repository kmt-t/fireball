# JIT 実行コンテキスト ABI 契約

<!-- traceability: {ContextPointerRegister} {PositionIndependentCode} {JIT_RuntimeAPI_Fallback} -->

この文書は、Tier 2ランタイムがTier 3 Executerへ提供する、複雑な処理をCヘルパーへ委譲するためのABI契約を定義する。共通コード領域の入口offsetはビルド構成または配置時のrelocation情報で解決し、JIT trace headerにはtrace固有の委譲先関数アドレスだけを保持する。実行コンテキストはこのルーティング情報を保持しない。

確認済みx64実行構成の `execution_context` と関連ビュー型は、固定フィールド順・サイズ・オフセットを持つ標準レイアウト構造体として定義する。インタープリタ単独実行とインタープリタからJITへの遷移で `ctx` のABI型を変えない。構造体は所有権を持たず、コード・関数表・結果配列は `address + length` の非所有ビューとして渡すため、呼び出し側が呼び出し完了まで対象領域の寿命を保証する。ARMv8-Mの構造体配置、ポインタ幅、サイズ、offsetはすべてTBDである。 `{ExecutionContext_Layout}` `{META_ZeroCostAbstraction}`

## コンテキスト拡張

既存の16個の32bit状態ワード（`+0x00`〜`+0x3F`）に、コードビュー、制御スタックビュー、境界フォールバック用チェックポイント、CallStackビュー、オペランドスタック容量、およびLOOP後方分岐カウンタを続けて配置する。x86-64のコンテキスト全体は128バイトである。Cヘルパーアドレスはトレースごとのヘッダに置く。

| オフセット | サイズ | フィールド | 用途 |
| :--- | :--- | :--- | :--- |
| `+0x3C` | 4バイト | runtime_flags | 実行時制御フラグ。Interpreterのブロック境界停止要求を含む |
| `+0x40` | 8バイト | code | 現在のWASMコードの非所有アドレス |
| `+0x48` | 4バイト | code_size | 現在のWASMコードのバイト数 |
| `+0x50` | 8バイト | control_stack | 制御スタックの非所有アドレス |
| `+0x58` | 4バイト | control_base | 現在の関数の制御フレーム窓の開始深さ |
| `+0x5C` | 4バイト | stack_checkpoint | 境界フォールバック時のoperand stack高さ |
| `+0x60` | 8バイト | call_stack | CallFrame配列の非所有アドレス |
| `+0x68` | 4バイト | call_base | 現在のCallFrame窓の開始深さ |
| `+0x6C` | 4バイト | call_offset | CallStackの現在の深さ |
| `+0x70` | 4バイト | sp_capacity | オペランドスタックの論理容量（32bitワード数） |
| `+0x74` | 4バイト | loop_jump_count | C++ Interpreterのbranch handlerが記録する、協調yieldまでの取得済み回数 |
| `+0x78` | 4バイト | loop_jump_threshold | Tier 3が設定するLOOP後方分岐のyieldしきい値 |

トレースヘッダの `helper_target_addr` は、当該トレースが委譲する関数のアドレスである。ヘルパー契約別入口はヘッダアドレスを受け取り、このフィールドをロードして対象ABIの関数呼出し規則で呼び出す。共通領域の開始・終了・helper・chain dispatcherオフセットはビルド構成または配置時のrelocation情報で解決し、トレースごとの物理ヘッダには保持しない。共通領域に置く呼出しコードはトレースごとに複製しない。 `{PositionIndependentCode}`

`fireball_execution_context_native` は、32ビットのゲスト状態フィールド16個を持つ。コード、制御スタック、CallStack の非所有ビュー、オペランドスタック容量、LOOP後方分岐カウンタとしきい値も含む。x86-64 でのサイズは128バイトである。標準レイアウトを維持する。

## JITランタイム呼出し契約

Tier 2のJIT runtime APIは、Tier 3実行器にモジュール登録、基本ブロック情報、履歴記録、トレース検索、およびchain終端情報を提供する。APIはトレースキャッシュの配置・置換・リンク構造を公開しない。

`fireball_call_frame_native` は、関数コード、ローカル幅、引数搬送、戻り境界を持つ96バイトの固定記述子である。`fireball_call_stack_native` は、32個の記述子を保持する固定長配列である。`fireball_const_buffer_view_native`、`fireball_wasm_function_view_native`、`fireball_wasm_module_view_native` は、WASM コードと関数メタデータを渡す非所有の標準レイアウト構造体である。この境界に文字列、`std::vector`、仮想関数、例外を含めない。 `{ExecutionContext_Layout}` `{META_NoStdVector}`

実行時のオペランド領域、ローカル値領域、制御ブロック復帰情報領域は、固定容量の構造体と配列として配置する。値領域は `WASM_VALUE_SLOT_BYTES` の境界に配置した型情報を持たない32ビットワード配列で、WASM の i32/f32 は1スロット、i64/f64 は2スロットを使用する。ローカル値は、フレームごとのスロット幅（4 / 8 / 16バイト）の固定スロットとし、JIT/インタープリタともスロット番号とスロット幅からアドレスを直接計算する。スロット幅は、フレーム内で最大の変数サイズで決める。JITはこの幅を命令生成時に埋め込む。したがってローカルオフセット表を保持・参照する必要はなく、i64/f64の有効ワードも自然に境界へ置かれる。値の型タグは記録せず、型を知っているハンドラが対応する読み書きメソッドを選択する。制御ブロックの復帰情報は、構造種別、開始位置、終了位置、保存済みスタック長、結果個数を持つ20バイトの固定長レコード配列として保持する。 `{META_NoStdVector}`

関数の引数・戻り値バッファも同じ原則で扱う。`WasmRunRequestNative` と `WasmRunResultNative` はバッファポインタと個数だけを渡し、戻り値型や型タグを保持しない。呼び出し側が関数シグネチャを知っているため、必要なスロット幅と解釈は呼び出し側で決める。

## 委譲シグネチャ

JITトレースの入口が使用する4論理引数契約を、すべてのヘルパーへ強制してはならない。ヘルパーは通常の関数呼出しであり、命令ごとに入力型、入力個数、結果の返却方法、およびトラップの扱いを定義する。ヘルパーの物理引数配置は対象ABIに従う。

| ヘルパーの契約例 | 入力 | 出力 | 入力の保持場所 |
| :--- | :--- | :--- | :--- |
| `i32.div_s` / `i32.div_u` / `i32.rem_s` / `i32.rem_u` | 32ビット整数2個 | 結果領域への書込み | 対象ABIの整数引数レジスタ |
| `i64` の算術演算 | 64ビット整数2個 | 結果領域への書込み | 個別の対象ABI契約で定義する |
| `f32` の算術演算 | 32ビット浮動小数点値2個 | 結果領域への書込み | 個別の対象ABI契約で定義する |
| `f64` の算術演算 | 64ビット浮動小数点値2個 | 結果領域への書込み | 個別の対象ABI契約で定義する |

x64では、整数除算・剰余の4命令は、2つの入力を整数引数レジスタで受け、結果領域へのポインタを第3引数で受ける。入力を共有オペランド領域へ先に書き戻すことは、この契約の前提ではない。広い型の契約は命令ごとに定義し、入力を共有オペランド領域から読む方式を採用する場合も、そのヘルパー固有の契約として記述する。

ヘルパーへの遷移は、トレースヘッダから対象関数のアドレスを取得し、共通コード領域に配置した対象ABIの呼出しコードを経由して呼び出す。JITの保護レジスタ復元、結果領域への結果反映、実行コンテキストの更新、およびトラップ状態の反映は、各ヘルパー契約に従う。入力形式を一律に32ビットワード列へ変換する共通規則は設けない。 `{JIT_RuntimeAPI_Fallback}`

## x64実行環境のトレースヘッダ

以下はWindows x64およびSystem V AMD64で共通に使用する物理配置である。ARMv8-Mの配置、命令列、ABIはすべてTBDであり、x64の確認結果から推定しない。物理ポインタを格納する欄は64ビットとし、32ビットのWASM PC欄と混同しない。

| オフセット | フィールド | サイズ | 用途 |
| :--- | :--- | ---: | :--- |
| `+0x00` | `head_wasm_pc` | 4バイト | トレース先頭のWASM PC |
| `+0x04` | `trace_byte_size` | 2バイト | ヘッダを含むトレース全体の長さ |
| `+0x06` | `flags` | 1バイト | 昇格・ループ先頭などの状態 |
| `+0x07` | `variant_id` | 1バイト | レジスタ状態の識別子 |
| `+0x08` | `chain_target_addr` | 8バイト | 共通chain dispatcherが読み取る次trace bodyのネイティブアドレス。未接続時は0 |
| `+0x10` | `helper_target_addr` | 8バイト | trace固有のC helperアドレス |

ヘッダ全体は24バイトであり、traceのentry stubは `+0x18` から始まる。`chain_next` PCは実行時cache descriptorに保持し、物理ヘッダには重複して格納しない。`common_prologue_offset`、`common_epilogue_offset`、`common_helper_offset`、絶対アドレスpool位置はビルド構成または配置時のrelocation情報であり、traceごとに物理ヘッダへ保持しない。

chainはtrace終端から共通コード領域のchain dispatcherへ移り、そのdispatcherが `chain_target_addr` のbodyへtail-jumpする経路を指す。dispatcherはWASM opcodeを判定しない。分岐命令の条件評価、control frame更新、後方分岐回数の記録はC++ Interpreterの命令別handlerが行い、そのhandler実行だけをchainとは呼ばない。chain targetの更新にはW^X手順を適用する。

共通helper入口は契約ごとに個別配置し、trace bodyのexit rel32をinstallation時に選択した入口へpatchする。コンテキスト型入口は `0x030`、x64のi32整数除算・剰余は `0x160` から32バイト単位で4入口、wideヘルパーは `0x200` から32バイト単位で11入口を置く。ARMv8-Mのhelper呼出し入口と共通コード配置はTBDである。トレース本体には関数引数の組み替えや呼出し用のスタック領域確保を置かない。
