# JITコンパイラ (コード生成コア) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`jit_compiler.md`](docs/components/tier3_jit/jit_compiler.md)

物理レジスタ、スタック整列、命令列、およびヘッダ配置は対象アーキテクチャごとに検証する。以下のx64項目は [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) の契約を対象とし、ARMv8-Mの項目は [`jit_stencil_catalog.md`](docs/specs/jit_stencil_catalog.md) の契約を対象とする。

Copy-and-Patchエンジンによるネイティブコード生成、4論理引数の境界規約とJITトレース独自のTOS/NOSキャッシュの非対称性（`{ADR_TosCacheAsymmetry}`）、JITトレースヘッダのメモリレイアウト、`code_offset`スケーラビリティ（`{ADR_ScalableCodeOffset}`）、および位置独立性（`{PositionIndependentCode}`）を検証する。

## 2. テストケース一覧

### Copy-and-Patchエンジン ({JIT_CopyAndPatch})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-01 | テンプレートのコピー＋パッチのみ（最適化なし） | 単純な直線コード（`i32.const`→`i32.add`等） | コンパイル | 生成ネイティブコードは事前定義テンプレートの連結+即値/分岐先パッチのみで構成される（IRを介した最適化パスが存在しない） | 「Zero Compile Cost」, `{SinglePassCompilation}` |
| TEST-JITC-02 | 未対応命令のエラー | ステンシル未定義のWASM opcode | コンパイル | 明確なエラー（`NO_STENCIL_FOR`相当）で失敗し、無音の誤コンパイルをしない | runtime_engine_concept.py `CopyPatchCompiler.compile_trace`のraise |
| TEST-JITC-03 | ステンシルの必須リロケーションホール検証 | 必要な`imm_lo`/`imm_hi`等を渡さない | `emit`を呼ぶ | `KeyError`相当で拒否される | runtime_engine_concept.py `test_stencil_requires_its_relocation_holes` |
| TEST-JITC-04 | AAPCS境界フォールバック | 複雑な命令・import／host call | コンパイル | 境界命令の直前でトレースを終了し、ランタイムAPI呼び出しスタブをJITコード内に生成せずInterpreter／RuntimeEngineへ委譲する |  `{JIT_RuntimeAPI_Fallback}` |
| TEST-JITC-05 | 命令キャッシュ同期バリア | パッチ完了後 | コンパイル完了を確認 | `__DSB()`/`__ISB()`相当のバリアが発行される | {JIT_CopyAndPatch} |
| TEST-JITC-06 | インタープリタ⇔JIT境界でのレジスタ書き戻しコスト | JITトレースから脱出 | 脱出処理を確認 | 値キャッシュの共有オペランド領域への書戻しが対象ABIで定める有界コストに収まる |  `{ADR_TosCacheAsymmetry}` |
| TEST-JITC-07 | x64整数除算・剰余のヘルパー委譲 | i32除算・剰余を含むトレース | x64向けコンパイルと実行を確認 | 2つの32ビット整数を対象ABIの引数レジスタからヘルパーへ渡し、ヘルパーが結果領域ポインタへ1ワードを書き込んで対象ABIの終了処理へ戻る | `{JIT_RuntimeAPI_Fallback}` |
| TEST-JITC-08 | ヘルパー呼出しコードの共通配置 | ARMv8-Mまたはx64のヘルパー委譲 | 共通コード領域とトレース本体のバイト数を確認 | ARMv8-MのAAPCS呼出しコードは契約ごとに共通コード領域へ配置し、x64整数ヘルパー直接入口は契約ごとに32バイトの固定スロットへ配置する。各トレースには対応入口へ遷移する経路だけを置く | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) `{JIT_MultiBuffer_Cache}` |

### レジスタ規約とTOS/NOS非対称性

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-10 | インタープリタとJITが共有する4論理引数規約 | - | 両エンジンの呼び出し規約を比較 | 論理引数の順序は`ctx, sp, local_base, tos`で一致し、物理レジスタはARMv8-M、Windows x64、System V AMD64の各ABI定義に従う | `{AAPCS_FastCall}` |
| TEST-JITC-11 | ARMv8-M JITトレース内部でのTOS/NOS/NNOSキャッシュ | ARMv8-M JITトレース生成 | 生成コードのレジスタ使用を確認 | 最上段、次段、第3段のキャッシュがARMv8-Mの対象レジスタへ割り当てられる | `{ADR_TosCacheAsymmetry}` |
| TEST-JITC-12 | 基本ブロック末尾でのスタックフラッシュとコンテキスト同期 | トレース脱出・分岐 | 生成コードの末尾を確認 | 値キャッシュが共有オペランド領域へ書き戻され、実行コンテキストの`ip`（`+0x00`）および`sp_offset`（`+0x0C`）が同期される | 「トレース境界とチェイニングの安全性」 |
| TEST-JITC-13 | ローカル変数アクセスの静的オフセット畳み込み | 同一関数フレーム内のローカル変数アクセス | コンパイル | ローカル領域の基底を起点とする固定オフセットとして直接アクセスされる | 「ローカル変数アクセスの静的オフセット畳み込み」`{ContextPointerRegister}` |

### JITトレースヘッダ

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-20 | x64ヘッダサイズは固定52バイト | x64向けに生成したトレース | ヘッダを解析 | `+0x00 head_wasm_pc(u32)`, `+0x04 trace_byte_size(u16)`, `+0x06 flags(u8)`, `+0x07 variant_id(u8)`, `+0x08 chain_next_pc(u32)`, `+0x10 chain_target_addr(u64)`, `+0x24 helper_target_addr(u64)`を含む52バイト構造 | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITC-21 | flagsビットの意味 | PROMOTED済み/LOOP_HEADERのトレース | flagsを確認 | `0x01: PROMOTED`, `0x02: LOOP_HEADER`が正しく設定される | 同上 |
| TEST-JITC-22 | x64ネイティブコード列は+0x34から展開 | x64向けに生成したトレース | メモリレイアウトを確認 | 52バイトヘッダ直後(+0x34)からx64命令列が始まる。ARMv8-MのThumb-2配置は別の物理仕様で検証する | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITC-23 | `variant_id`とレジスタ割り当ての対応 | 命令テンプレートごとに異なる値キャッシュ常駐数 | variant選択とチェイン遷移を確認 | `variant_id`は命令テンプレートのレジスタ割り当て状態を表し、互換variantへは直接遷移し、非互換variantへは共有オペランド領域から再構成して遷移する | `{JIT_RegisterMapping}` |
| TEST-JITC-24 | AAPCS 準拠開始プロローグの新規入口 | Interpreter／RuntimeEngineから新規JITトレースへ遷移 | 入口アドレスと生成コードを確認 | 4論理引数を受け、callee-savedレジスタを退避し、入口variantを準備してからJIT本体へ進む。内部chain entryへ直接入らない | `jit_stencil_catalog.md` `STENCIL_PROLOGUE_FULL`, `{AAPCS_FastCall}` |

### ADR_ScalableCodeOffset

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-30 | code_offsetはアライメントシフト保持 | エントリテーブル | `code_offset`の格納方式を確認 | `actual_offset >> code_align_shift`で16bitに収めている（32bit化していない） | ADR_ScalableCodeOffset 結論 |
| TEST-JITC-31 | 最大キャッシュサイズの計算 | `code_align_shift`設定値 | 最大アドレス可能範囲を確認 | `65535 << code_align_shift`が理論上限と一致する | 同上 |

### 安全性制約

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-40 | 生成コードの位置独立性 (PIC) | 同一トレースバイナリを別のバッファ/オフセットにコピー | 実行 | 再コンパイルやリロケーション修正なしで完全に同一の結果を出力する | `{PositionIndependentCode}` |
| TEST-JITC-41 | ゲストメモリ境界チェックのインライン埋め込み | メモリアクセス命令を含むトレース | コンパイル | 開始アドレスを `CMP addr, mem_size; BHS.W <trap>` で検査し、2バイト以上のアクセスは `addr + width - 1` も同じ比較で検査して、境界外で安全にトラップする | `{MemoryBoundaryCheck}` `{FastAddressCheck}` |
| TEST-JITC-42 | キャッシュ溢れの3面ローテーション処理 | キャッシュ容量超過 | コンパイル試行 | Oldestバンクを破棄して再利用し、被チェイン元の解除を行う。解除対象の探索に加えて破棄バンク全体の消去を行うため、処理量は`O(k + n)`である | 「Cache Capacity Check」, `{JIT_LazyChaining}` |
| TEST-JITC-43 | ホストコール (WASI / fireball_call) のJIT境界 | 0〜6引数のimport／host call | トレース実行 | host call直前で対象ABIの終了処理を実行し、共有オペランド領域と実行コンテキストを同期してInterpreter／RuntimeEngineへ委譲する。JIT内で対象ABIと無関係なhost call stubを実行しない |  `{JIT_RuntimeAPI_Fallback}` |
| TEST-JITC-44 | 境界委譲後のコンテキスト保持 | 複雑命令またはimport／host call | 境界復帰後に実行を継続 | 共有実行コンテキスト、共有オペランド領域、ローカル値領域を正本として状態が保持され、JIT専用戻り値バッファや専用オペランド領域が生成されない | [`interpreter.md`](docs/components/tier3_plugins/interpreter.md) `{PositionIndependentCode}` |
| TEST-JITC-45 | 複数型Cヘルパーのワード配置 | `i64/f32/f64` 定数と算術命令 | JITトレースを実行 | `i64/f64` は2ワード、`f32` は1ワードで演算結果が共有オペランド領域へ保存される | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |

### トレース境界不変条件とハンドラ委譲

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-50 | スタック自己完結性検査 | 先頭で `local.set` や二項演算が先行するブロック | コンパイル試行 | 累積スタック深さが負になるため JIT 化を安全に拒否（`None` 返却）し、インタープリタ実行にフォールバックする | 「スタック自己完結性不変条件」 |
| TEST-JITC-51 | 真の境界命令でのブロック終端 | `CALL`, `CALL_INDIRECT`, `BR_TABLE`, import／host call, `RETURN` を含む関数 | BasicBlock 抽出 | 各境界命令の直前で BasicBlock が終端され、共有状態を同期してInterpreter／RuntimeEngineへ安全に委譲される。JITは`RETURN sentinel`を生成しない | 「制御フロー・コール境界のインタープリタ委譲不変条件」 |
| TEST-JITC-52 | トレース境界でのメモリ同期 | JIT トレース実行終了時 | レジスタおよびメモリ確認 | 対象ABIが定める値キャッシュを共有オペランド領域へ書き戻し、実行コンテキストの`ip`と`sp_offset`を同期する | 「メモリ同期不変条件」 |
| TEST-JITC-53 | ヘッダ参照動的チェイン分岐と基本ブロック終端同期 | JIT基本ブロック末尾でのチェイン分岐 | 共有状態同期、状態識別と遷移を確認 | 互換状態では後続本体へ直接分岐し、非互換状態では対象ABIの再構成処理を通る。target未解決時は対象ABIの終了処理を経て復帰する | 「専用分岐ハンドラ分離」`{JIT_LazyChaining}` |
| TEST-JITC-56 | 外部AAPCS call前のSP 8-byte整列 | 共通開始プロローグ後にexternal call stubを実行 | `SP mod 8`とpush/pop量を確認 | プロローグは8レジスタを32 bytes保存し、6レジスタのcaller-save退避は24 bytesとする。いずれも8の倍数であり、追加paddingなしに`BL`境界のSP 8-byte整列を維持する。最終エピローグは保存分を解除して元のSPを復元する | `jit_stencil_catalog.md` `STENCIL_PROLOGUE_FULL`, `STENCIL_EXTERNAL_CALL_STUB`; AAPCS32 |
| TEST-JITC-54 | x64トレースヘッダによる直接チェイニング連携 | 構文デリミタを含むブロックとフォールスルー先後続ブロック | x64トレース登録とチェイニング実行 | コンパイル時に先読み解決されたフォールスルー先PCがx64ヘッダの`chain_next_pc`（`+0x08`）に格納され、常駐確認後に`chain_target_addr`（`+0x10`）が更新される | 「構文デリミタのトレースヘッダ直接埋め込みと直接チェイニング連携」`{JIT_LazyChaining}` |
| TEST-JITC-55 | 全53命令語彙カバレッジと構文デリミタ消去 | JIT対象全53命令（境界命令の`CALL`, `CALL_INDIRECT`, import／host call, `BR_TABLE`, `RETURN`を除く） | 各命令のコンパイルと実行 | 全53命令が例外なくコンパイルされ、構文デリミタ（`BLOCK`, `LOOP`, `ELSE`, `END`, `NOP`）はコストゼロ（命令生成なし）で消去される | 「JIT コンパイル対象命令セット（語彙）一覧」 |
| TEST-JITC-58 | SP即値巻き戻しを伴う多段分岐インライン展開 | ラベル脱出に伴うスタック巻き戻しを持つ `BR` / `BR_IF` | トレースコンパイルと実行 | 分岐直前に対象ABIのスタック位置更新および実行コンテキスト書き込みがインライン生成される | 「制御フロー・コール境界のインタープリタ委譲不変条件」 |
| TEST-JITC-59 | フレームのスロット幅に従うローカルアドレス | ローカルを読み書きするブロック。全ローカルがi32のフレームと、i64ローカルを含むフレームの2通り | 各幅でコンパイルし、ローカル配列を指定して実行する | 書き込み先は `local index × スロット幅` になる。4バイトスロットでは3番目のローカルがワード2、8バイトスロットではワード4である。他のスロットは変化しない | `{ContextPointerRegister}` |
| TEST-JITC-60 | 押し出された値の順序と書き込み範囲 | 乱数で生成した逆ポーランド式（最大深さ3〜11）。TOSとNOSに載らない値を持つ | 各式をコンパイルして実行し、参照実装と比較する。書き込まれたワードも検査する | 結果が参照実装と一致する。書き込みは `[sp, sp + stack_words)` に収まる。`stack_words` は、押し出しの最大ワード数（最小1）と等しい | `{ContextPointerRegister}`, `{TraceBoundaryInvariant}` |
| TEST-JITC-61 | 右にネストした式の被演算子の復元 | 右にネストした `i32.sub` の連鎖と、シフトの連鎖（深さ11） | コンパイルして実行する | 押し出された値が、左被演算子として正しい順序で戻る。結果が参照実装と一致する | `{TraceBoundaryInvariant}` |

| TEST-JITC-57 | 共通コード区画の存続と相対分岐解決 | 8KB JIT領域に境界stub／相対ジャンプstubを配置 | rotation・bank flush後もstubと参照先を確認し、生成時に分岐変位を検査 | 共通コード2KBは保持され、Active/Warm/Oldestだけが無効化される。直接条件分岐の範囲外はrelay veneer経由となり、変位超過コードを生成しない | system_config.md, jit_stencil_catalog.md, runtime_vsoc.md |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-JITC-01 | 境界引数レジスタとJIT内部一時レジスタの物理競合防止 | トレース生成 | 各ステンシルのレジスタ割り当てを走査 | 境界の4論理引数（R0=ctx, R1=sp, R2=local_base, R3=tos）は呼び出し境界でのみ用いられ、JIT内部一時レジスタ（assignable pool: R4-R6, R8-R11）と物理的に一切重複しない。**実装の勘所**: x86-64 等のホストシミュレータ上の引数レジスタと実機Thumb-2のR8/R9（mem_base/mem_size）を混同して同一レジスタとして扱ってはならない | `{JIT_RegisterMapping}` |
| GOTCHA-JITC-02 | ARMv8-Mのメモリ基底・サイズのロード起点（`execution_context` 統合） | メモリアクセス命令を含むARMv8-Mトレース | プロローグ命令列を検証 | `mem_base` と `mem_size` は、実行コンテキストの`+0x28`および`+0x2C`から一度だけロードされる。物理レジスタはAAPCS32の規約に従う | `{ExecutionContext_Layout}` `{JIT_RegisterMapping}` |
| GOTCHA-JITC-03 | 基本ブロック末尾のスタックフラッシュとIP/SP同期 | ARMv8-Mトレースエピローグ生成 | エピローグ命令列を検証 | 基本ブロック末尾で値キャッシュを共有オペランド領域へ書き戻し、実行コンテキストの`ip`（`+0x00`）および`sp_offset`（`+0x0C`）を同期する。物理レジスタと書戻し順序はAAPCS32の規約に従う | `{ADR_TosCacheAsymmetry}` |
| GOTCHA-JITC-04 | 境界チェック先行性と副作用ゼロ（Wrapping禁止） | メモリアクセス命令 | `addr + width - 1 >= mem_size` でトレース実行 | メモリアクセス（LDR/STR）前に開始アドレスとアクセス末尾が `CMP ..., r9; BHS.W <trap>` で評価され、境界外時はメモリ書き込みや値更新の副作用が一切発生せず即座にトラップテールへ分岐する。**実装の勘所**: マスク等でアドレスを巡回（Wrapping）させて継続実行することは安全上絶対に許容されない | `{MemoryBoundaryCheck}` `{FastAddressCheck}` |
| GOTCHA-JITC-05 | トラップ分岐（`BHS.W`）の2パスバックパッチ | トレース生成 | トラップ分岐命令のパッチ履歴を検証 | `BHS.W` の発行時点ではトラップテールのアドレスが未確定なため、オフセット0で仮発行した上で位置を記録し、エピローグ・トラップテール生成後に実アドレスへバックパッチされる。**実装の勘所**: 1パスで未確定アドレスへ分岐命令を発行すると未定義ジャンプを引き起こす | `{FastAddressCheck}` |
| GOTCHA-JITC-06 | ARM MLS 命令のオペランド順序 | `i32.rem_s` / `i32.rem_u` を含むトレース | 生成されたネイティブコードを実行 | `mls r3, r12, r3, r4` が $Rd(r3) = Ra(r4) - Rn(r12) \times Rm(r3)$（被除数 - 商×除数 = 剰余）を算出する。**実装の勘所**: ARM MLS 命令は $Rd = Ra - (Rn \times Rm)$ という引数順序規約を持ち、$Ra - Rn \times Rm$ の順序を逆にすると負の剰余値を出力するバグとなる | `{JIT_CopyAndPatch}` |
| GOTCHA-JITC-07 | トレース結果値と C 呼び出し規約の無関係性 | 残余値を持つトレース（`stack_depth == 1`） | エピローグ命令列と呼び出し元の受け取り方を検証 | トレースの残余値（VM オペランドスタックの状態）は `R1 (sp)` 経由でメモリへ書き込まれ、コンテキスト `R0` の `ip` と `sp_offset` を更新して終了する。呼び出し元はその値を戻り値からではなくこのメモリ位置から読む。**実装の勘所**: ホストシミュレータ上の呼び出しは ctypes 経由の実 C 関数呼び出しであるため、C の戻り値レジスタに「ついでに」結果を乗せたくなるが、VM のオペランドスタック状態と呼び出し規約上の戻り値は無関係であり、これを混同するとスタックに何も書き込まれず、分岐条件や結果値が常に0として誤って読まれる | `{ADR_TosCacheAsymmetry}` |

## 3. テスト検証実績と網羅状況

- **TEST-JITC-01〜08 (Copy-and-Patch)**: 単一パスによる命令テンプレートのコピー＆パッチ、必須リロケーションホール、x64整数演算のヘルパー委譲、および共通呼出しコードのバイト数と共有配置を検証対象とする。x64側の実行検証は完了し、ARMv8-M側は実機検証を残す。
- **TEST-JITC-10 (4論理引数規約)**: `(ctx, sp, local_base, tos)` を物理レジスタにマップし、インタープリタと共通のシグネチャで直接 C 関数呼び出しできることを実証済み。
- **TEST-JITC-20〜22 (x64 52バイト物理ヘッダ)**: x64の `jit_trace_header`（`head_wasm_pc`, `trace_byte_size`, `flags`, `variant_id`, `chain_next_pc`, `chain_target_addr`, `helper_target_addr`）が `+0x00` に配置され、ネイティブ命令列が `+0x34` から展開されることを実証済み。ARMv8-MのThumb-2配置は別カタログの契約で扱う。
- **TEST-JITC-40 (PIC 位置独立性)**: トレースバイナリを別のメモリ領域・オフセットへコピーして再コンパイルなしで直接実行し、完全同一の演算結果を返すことを実証済み。
- **TEST-JITC-42 (3面キャッシュ代謝 & 有界アンリンク)**: 3面マルチバッファキャッシュのローテーション、破棄バンク全体の消去、および被チェイン逆引きテーブルに基づく `O(n + k log n)` 処理を実証済み。
- **TEST-JITC-43 (ホストコール ABI)**: 0〜6引数のホスト関数呼び出しにおけるスタックアライメントおよびCaller-savedレジスタの完全保護を実証済み。

## 4. 未検証・スコープ外

- Thumb-2/RISC-V 実機ターゲットでの `constexpr` アセンブラ生成バイナリの実機検証。
