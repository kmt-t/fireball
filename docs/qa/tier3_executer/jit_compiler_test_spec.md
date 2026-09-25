# JITコンパイラ (コード生成コア) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲
<!-- traceability: {ADR_ScalableCodeOffset} -->

正本: [`jit_compiler.md`](docs/components/tier3_executer/jit_compiler.md)

本書の物理コード生成受け入れ条件は、[`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) とx64向け実装テストで確認する。ARMv8-MのABI、命令列、ヘッダ、メモリ保護、物理配置はすべてTBDであり、受け入れ条件を定めない。

Copy-and-Patchエンジンによるネイティブコード生成、4論理引数の境界規約とJITトレース独自のTOS/NOSキャッシュの非対称性（`{ADR_TosCacheAsymmetry}`）、JITトレースヘッダのメモリレイアウト、`code_offset`スケーラビリティ（`ADR_ScalableCodeOffset`）、および位置独立性（`{PositionIndependentCode}`）を検証する。

## 2. テストケース一覧

### Copy-and-Patchエンジン ({JIT_CopyAndPatch})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-01 | テンプレートのコピー＋パッチのみ（最適化なし） | 単純な直線コード（`i32.const`→`i32.add`等） | コンパイル | 生成ネイティブコードは事前定義テンプレートの連結+即値/分岐先パッチのみで構成される（IRを介した最適化パスが存在しない） | 「Zero Compile Cost」, `{SinglePassCompilation}` |
| TEST-JITC-02 | 未対応命令のエラー | x64コンパイラが未対応のWASM opcode | コンパイル | 明確なcompile-failure結果を返し、無音の誤コンパイルをしない | [`test_x64_jit.py`](experiments/pysim/qa/tier3_executer/jit/test_x64_jit.py) |
| TEST-JITC-03 | ステンシル即値とrelocation範囲 | x64の即値命令と相対分岐を含むtrace | コンパイル結果を実行し、境界値も確認する | x64のバイト列と実行結果が正しく、範囲外relocationはcompile failureとなる | [`test_x64_stencils.py`](experiments/pysim/qa/tier3_executer/jit/test_x64_stencils.py), [`test_x64_asm.py`](experiments/pysim/qa/tier3_executer/jit/test_x64_asm.py) |
| TEST-JITC-04 | C++ Interpreter handlerへの境界フォールバック | 制御終端命令、複雑命令、import／host call | trace実行後のhandlerとRuntimeEngine境界を確認する | 制御終端命令は対応するC++ Interpreter handlerを通る。JIT内にopcode別handler dispatcherやhost call stubを生成しない | `{JIT_RuntimeAPI_Fallback}` |
| TEST-JITC-05 | x64実行可能バッファのW^X確定 | パッチ完了後 | executable bufferの権限と実行結果を確認する | commit後は実行可能で書込み不可となり、ARMv8-Mの同期命令や保護機構は受入れ条件に含めない | [`test_x64_stencils.py`](experiments/pysim/qa/tier3_executer/jit/test_x64_stencils.py) |
| TEST-JITC-06 | インタープリタ⇔JIT境界でのレジスタ書き戻しコスト | JITトレースから脱出 | 脱出処理を確認 | 値キャッシュの共有オペランド領域への書戻しが対象ABIで定める有界コストに収まる |  `{ADR_TosCacheAsymmetry}` |
| TEST-JITC-07 | x64整数除算・剰余のヘルパー委譲 | i32除算・剰余を含むトレース | x64向けコンパイルと実行を確認 | 2つの32ビット整数を対象ABIの引数レジスタからヘルパーへ渡し、ヘルパーが結果領域ポインタへ1ワードを書き込んで対象ABIの終了処理へ戻る | `{JIT_RuntimeAPI_Fallback}` |
| TEST-JITC-08 | ヘルパー呼出しコードの共通配置 | ARMv8-Mまたはx64のヘルパー委譲 | 共通コード領域とトレース本体のバイト数を確認 | x64整数ヘルパー入口は契約ごとに32バイトの固定スロットへ配置する。ARMv8-Mのhelper入口と配置はTBD | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) `{JIT_MultiBuffer_Cache}` |

### レジスタ規約とTOS/NOS非対称性

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-10 | インタープリタとJITが共有する4論理引数規約 | - | 両エンジンの呼び出し規約を比較 | 論理引数の順序は`ctx, sp, local_base, tos`で一致する。x64の物理配置は対象ABI定義に従い、ARMv8-Mの物理配置はTBD | `{CPS_4Args}` |
| TEST-JITC-12 | 基本ブロック末尾でのスタックフラッシュとコンテキスト同期 | トレース脱出・分岐 | 生成コードの末尾を確認 | x64では共有オペランド領域と実行コンテキストがトレース境界で同期される。ARMv8-Mのレジスタ割当と同期方式はTBD | 「トレース境界とチェイニングの安全性」 |
| TEST-JITC-13 | ローカル変数アクセスの静的オフセット畳み込み | 同一関数フレーム内のローカル変数アクセス | コンパイル | ローカル領域の基底を起点とする固定オフセットとして直接アクセスされる | 「ローカル変数アクセスの静的オフセット畳み込み」`{ContextPointerRegister}` |

### JITトレースヘッダ
<!-- traceability: {CPS_4Args} {JIT_RegisterMapping} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-20 | x64ヘッダサイズは固定24バイト | x64向けに生成したトレース | ヘッダを解析 | `+0x00 head_wasm_pc(u32)`, `+0x04 trace_byte_size(u16)`, `+0x06 flags(u8)`, `+0x07 variant_id(u8)`, `+0x08 chain_target_addr(u64)`, `+0x10 helper_target_addr(u64)`を含む24バイト構造 | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITC-21 | flagsビットの意味 | PROMOTED済み/LOOP_HEADERのトレース | flagsを確認 | `0x01: PROMOTED`, `0x02: LOOP_HEADER`が正しく設定される | 同上 |
| TEST-JITC-22 | x64エントリstubは24バイトヘッダ直後に配置 | x64向けに生成したトレース | メモリレイアウトを確認 | 24バイトヘッダ直後(+0x18)から15バイトのentry stubが始まり、bodyはその直後(+0x27)から始まる。ARMv8-Mの配置はTBD | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITC-23 | `variant_id`とレジスタ割り当ての対応 | 命令テンプレートごとに異なる値キャッシュ常駐数 | variant選択とチェイン遷移を確認 | `variant_id`は命令テンプレートのレジスタ割り当て状態を表し、互換variantへは直接遷移し、非互換variantへは共有オペランド領域から再構成して遷移する | `JIT_RegisterMapping` |

### ADR_ScalableCodeOffset

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-30 | code_offsetはアライメントシフト保持 | エントリテーブル | `code_offset`の格納方式を確認 | `actual_offset >> code_align_shift`で16bitに収めている（32bit化していない） | ADR_ScalableCodeOffset 結論 |
| TEST-JITC-31 | 最大キャッシュサイズの計算 | `code_align_shift`設定値 | 最大アドレス可能範囲を確認 | `65535 << code_align_shift`が理論上限と一致する | 同上 |

### 安全性制約
<!-- traceability: {PositionIndependentCode} {MemoryBoundaryCheck} {FastAddressCheck} {JIT_LazyChaining} {JIT_RuntimeAPI_Fallback} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-40 | 生成コードの位置独立性 (PIC) | 同一トレースバイナリを別のバッファ/オフセットにコピー | 実行 | 再コンパイルやリロケーション修正なしで完全に同一の結果を出力する | `` |
| TEST-JITC-41 | ゲストメモリ境界チェックと副作用抑止 | メモリアクセス命令を含むx64トレース | 境界内外のアクセスを実行 | 範囲外アクセスはメモリ副作用前に検出され、WASM trapとなる。ARMv8-Mの命令列とアドレス検査方式はTBD | MemoryBoundaryCheck, FastAddressCheck |
| TEST-JITC-42 | キャッシュ溢れの3面ローテーション処理 | キャッシュ容量超過 | コンパイル試行 | Oldestバンクを破棄して再利用し、被チェイン元の解除を行う。解除対象の探索に加えて破棄バンク全体の消去を行うため、処理量は`O(k + n)`である | 「Cache Capacity Check」, `` |
| TEST-JITC-43 | ホストコール (WASI / fireball_call) のJIT境界 | 0〜6引数のimport／host call | トレース実行 | host call直前で対象ABIの終了処理を実行し、共有オペランド領域と実行コンテキストを同期してInterpreter／RuntimeEngineへ委譲する。JIT内で対象ABIと無関係なhost call stubを実行しない |  `` |
| TEST-JITC-44 | 境界委譲後のコンテキスト保持 | 複雑命令またはimport／host call | 境界復帰後に実行を継続 | 共有実行コンテキスト、共有オペランド領域、ローカル値領域を正本として状態が保持され、JIT専用戻り値バッファや専用オペランド領域が生成されない | [`interpreter.md`](docs/components/tier3_executer/interpreter.md) `` |
| TEST-JITC-45 | 複数型Cヘルパーのワード配置 | `i64/f32/f64` 定数と算術命令 | JITトレースを実行 | `i64/f64` は2ワード、`f32` は1ワードで演算結果が共有オペランド領域へ保存される | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |

### トレース境界不変条件とハンドラ委譲
<!-- traceability: {TraceBoundaryInvariant} {JIT_LazyChaining} {JIT_RuntimeAPI_Fallback} {ContextPointerRegister} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-50 | スタック自己完結性検査 | 先頭で `local.set` や二項演算が先行するブロック | コンパイル試行 | 累積スタック深さが負になるため JIT 化を安全に拒否（`None` 返却）し、インタープリタ実行にフォールバックする | 「スタック自己完結性不変条件」 |
| TEST-JITC-51 | 真の境界命令でのブロック終端 | `CALL`, `CALL_INDIRECT`, `BR_TABLE`, import／host call, `RETURN` を含む関数 | BasicBlock 抽出 | 各境界命令の直前で BasicBlock が終端され、共有状態を同期してInterpreter／RuntimeEngineへ安全に委譲される。JITは`RETURN sentinel`を生成しない | 「制御フロー・コール境界のインタープリタ委譲不変条件」 |
| TEST-JITC-52 | トレース境界でのメモリ同期 | JIT トレース実行終了時 | レジスタおよびメモリ確認 | 対象ABIが定める値キャッシュを共有オペランド領域へ書き戻し、実行コンテキストの`ip`と`sp_offset`を同期する | 「メモリ同期不変条件」 |
| TEST-JITC-53 | 制御分岐handlerとmachine-code chainの分離 | `BR`、`BR_IF`、`BR_TABLE`で終わるtraceと、直線後続trace | 終端handlerの実行とtrace末尾のmachine codeを確認 | 分岐条件・control frame・遷移先はC++ Interpreterの命令別handlerが処理する。trace末尾はopcode共通dispatcherへ集約せず、直線後続traceの場合に限り共有コード領域のchain dispatcherへ移る。handler後にC++ dispatcherがtraceを選ぶ遷移はchainとして数えない | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) `` |
| TEST-JITC-54 | x64共通コード領域chain dispatcher経由のtrace遷移 | 直線後続ブロックのtraceが常駐 | trace終端の相対分岐とchain targetを確認して実行 | trace末尾の`rel32`が共通コード領域のchain dispatcherを指す。dispatcherはヘッダ`+0x08`のtarget bodyへtail-jumpし、未接続なら共通epilogueへ戻る。C++ handler実行後のdispatcher遷移はchainに含めない | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) `` |
| TEST-JITC-55 | x64 JIT対応命令と制御終端のhandler経由 | x64 JITが対応する算術・ローカル命令、および`BR`/`BR_IF`/`BR_TABLE`等の制御終端 | 各traceの生成・実行とC++ handler呼出しを確認する | 対応命令はx64 trace bodyで実行され、制御終端はC++ Interpreter handlerが一度処理する。構文終端を無条件に消去せず、条件値やcontrol frameを更新する | ``, `test_x64_jit.py` |
| TEST-JITC-58 | 分岐handlerによるstack巻き戻し | ラベル脱出に伴うスタック巻き戻しを持つ `BR` / `BR_IF` | 対応するC++ Interpreter handlerを通して実行する | handlerが条件値を消費し、stackとcontrol frameを更新する。JIT traceにbranch条件評価や分岐handlerをインライン展開しない | 「制御フロー・コール境界のインタープリタ委譲不変条件」 |
| TEST-JITC-59 | フレームのスロット幅に従うローカルアドレス | ローカルを読み書きするブロック。全ローカルがi32のフレームと、i64ローカルを含むフレームの2通り | 各幅でコンパイルし、ローカル配列を指定して実行する | 書き込み先は `local index × スロット幅` になる。4バイトスロットでは3番目のローカルがワード2、8バイトスロットではワード4である。他のスロットは変化しない | `` |
| TEST-JITC-60 | 押し出された値の順序と書き込み範囲 | 乱数で生成した逆ポーランド式（最大深さ3〜11）。TOSとNOSに載らない値を持つ | 各式をコンパイルして実行し、参照実装と比較する。書き込まれたワードも検査する | 結果が参照実装と一致する。書き込みは `[sp, sp + stack_words)` に収まる。`stack_words` は、押し出しの最大ワード数（最小1）と等しい | ``, TraceBoundaryInvariant |
| TEST-JITC-61 | 右にネストした式の被演算子の復元 | 右にネストした `i32.sub` の連鎖と、シフトの連鎖（深さ11） | コンパイルして実行する | 押し出された値が、左被演算子として正しい順序で戻る。結果が参照実装と一致する | TraceBoundaryInvariant |

| TEST-JITC-57 | x64共通コード区画とchain targetの有効性 | x64 JIT cacheでtraceをlinkし、対象バンクをrotate | chain targetと共通dispatcherを確認 | 共通コードはcache rotationで保持され、無効化されたtraceを指すtargetは0に戻る。ARMv8-Mの分岐範囲とrelay方式はTBD | `jit_abi.md`, `jit_runtime.md` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）
<!-- traceability: {MemoryBoundaryCheck} {FastAddressCheck} -->

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-JITC-01 | 境界引数レジスタとJIT内部一時レジスタの物理競合防止 | トレース生成 | 各ステンシルのレジスタ割り当てを走査 | x64の物理レジスタ配置は対象ABIと一致すること。ARMv8-Mの物理レジスタ割当と競合条件はTBD | `{JIT_RegisterMapping}` |
| GOTCHA-JITC-03 | trace境界の共有stackと実行状態同期 | JIT trace終端 | C++ Interpreter handlerへ復帰した状態を確認する | x64の共有状態は同期される。ARMv8-Mの物理保存先と同期方法はTBD | `{ADR_TosCacheAsymmetry}` |
| GOTCHA-JITC-04 | 境界チェック先行性と副作用ゼロ（Wrapping禁止） | メモリアクセス命令 | `addr + width - 1 >= mem_size` でトレース実行 | メモリアクセス（LDR/STR）前にx64実装の範囲検査で評価され、境界外時はメモリ書き込みや値更新の副作用が一切発生せず即座にトラップテールへ分岐する。**実装の勘所**: マスク等でアドレスを巡回（Wrapping）させて継続実行することは安全上絶対に許容されない | MemoryBoundaryCheck, FastAddressCheck |
| GOTCHA-JITC-07 | trace結果値とホスト関数戻り値の分離 | 残余値を持つtrace | 共通stackとC++ handler復帰後の値を確認する | WASM値は共有operand stackに残し、JIT traceのC戻り値として返さない。x64の物理配置は`jit_abi.md`に従い、ARMv8-Mの物理配置はTBD | `{ADR_TosCacheAsymmetry}` |

## 3. テスト検証実績と網羅状況

- **TEST-JITC-01〜08 (Copy-and-Patch)**: 単一パスによる命令テンプレートのコピー＆パッチ、必須リロケーションホール、x64整数演算のヘルパー委譲、および共通呼出しコードのバイト数と共有配置を検証対象とする。x64の検証対象に限定する。ARMv8-Mの実装と検証はTBDである。
- **TEST-JITC-10 (4論理引数規約)**: `(ctx, sp, local_base, tos)` の論理引数順序がInterpreterとJITで一致することを確認する。x64の物理配置は [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) が定める。
- **TEST-JITC-20〜22 (x64 24バイト物理ヘッダ)**: x64の `jit_trace_header` はtrace識別情報とchain/helper targetだけを保持し、24バイト直後に15バイトのentry stub、さらにその後にnative bodyを配置する。ARMv8-Mの配置はTBDである。
- **TEST-JITC-40 (PIC 位置独立性)**: トレースバイナリを別のメモリ領域・オフセットへコピーして再コンパイルなしで直接実行し、完全同一の演算結果を返すことを実証済み。
- **TEST-JITC-42 (3面キャッシュ代謝 & 有界アンリンク)**: 3面マルチバッファキャッシュのローテーション、破棄バンク全体の消去、および被チェイン逆引きテーブルに基づく `O(n + k log n)` 処理を実証済み。
- **TEST-JITC-43 (ホストコール ABI)**: 0〜6引数のホスト関数呼び出しにおけるスタックアライメントおよびCaller-savedレジスタの完全保護を実証済み。

## 4. 未検証・スコープ外

- ARMv8-Mの物理JIT仕様と実機検証はTBDであり、本書は受け入れ条件を定めない。
