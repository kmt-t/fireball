# JITコンパイラ (コード生成コア) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲
<!-- traceability: {ADR_ScalableCodeOffset} -->

正本: [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md)

本書の物理コード生成受け入れ条件は、[`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) とx64向け実装テストで確認する。ARMv8-MのABI、命令列、ヘッダ、メモリ保護、物理配置はすべてTBDであり、受け入れ条件を定めない。

三つのアセンブリsourceから生成するPIC stencilとtrace単位のネイティブコード生成、Runtime APIのJIT内呼出し、Interpreter/JITのentry signature、共有オペランドスタックの不変条件、JITトレースヘッダ、`code_offset`スケーラビリティ（`ADR_ScalableCodeOffset`）、位置独立性（`{PositionIndependentCode}`）を検証する。

## 2. テストケース一覧

### build-time stencil compiler ({JIT_AssemblyStencilCodegen})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-01 | build-time raw stencilによるfall-through trace composition | 単純な直線コード（`i32.const`→`i32.add`等） | `generate_assembly.py`で`operations.S`、`trace.S`、`common.S`を生成し、trace compilerでtraceを組み立てる。生成headerと共有library依存を調べる | trace bodyはassembly entryとoperation本体を一つの直列blobへ配置し、成功接続slotを後続stencilで置換する。通常経路はfall-throughし、命令間call/jumpや操作ごとのエピローグがない。WASM数値即値とlocal offsetはコードへ直接埋め込む。Runtime API引数recordとtrace header参照をtrace末尾poolに置く。実行時codegen、次WASM opcode fetch、operation dispatch、未解決relocationはない | `{JIT_AssemblyStencilCodegen}`, `{PositionIndependentCode}` |
| TEST-JITC-02 | 未対応命令のエラー | x64コンパイラが未対応のWASM opcode | コンパイル | 明確なcompile-failure結果を返し、無音の誤コンパイルをしない | [`test_x64_jit.py`](experiments/pysim/qa/tier3_plugins/jit/test_x64_jit.py) |
| TEST-JITC-03 | 生成trace本体の演算意味論 | x64の算術・ビット演算・ローカル操作を含むtrace | build-time生成したassembly stencilをtraceへ連結し、Python handler経由なしで実行する | 生成コードの演算結果、値の配置、境界での状態同期がWASM意味論と一致する | [`test_x64_jit.py`](experiments/pysim/qa/tier3_plugins/jit/test_x64_jit.py) |
| TEST-JITC-63 | trace codeへの数値即値埋め込みとfall-through slot composition | 4-byte/8-byte即値を持つtrace、および即値演算trace | 対象traceをC++ native compiler経由で生成し、完成native codeに即値bytesがあること、末尾poolにRuntime API recordと最終referenceだけがあること、operation挿入slotを調べる | `i32/f32/i64/f64.const`と即値演算の固定値はtrace compile時に命令へ埋め込む。これらのために`rel`を進めない。Runtime APIが第4引数で読む操作別即値はpool recordに残す。pool末尾の4-byte相対referenceからtrace exitがheaderを得る。operation間継続deltaはない。slot命令は完成traceに残らず、success pathはfall-throughし、template内分岐は配置後位置へ再配置する | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md), `test_jitr_clang_wasm_ngrams_embed_i32_immediates_in_trace_code`, `test_jit_embeds_immutable_numeric_immediates_in_native_code` |
| TEST-JITC-64 | Clang-WASM `local.get, local.get, i32.*` 融合 | 空スタックから始まる2つのi32 local readと整数二項演算を含むtrace | 加算・減算・乗算・ビット演算・比較を含むtraceを生成して実行する。index 7より大きくてもoffset variantがあるlocalを含める。Clang生成guestのprofileから対象n-gramの静的頻度も集計する | 各演算結果がWASMの32ビット意味と一致し、trace compilerが論理indexではなく型とcompact offsetに基づいて融合operationを選ぶ。アセンブリから生成した対応stencilの機械語命令数を記録し、固定した削減数を合否条件にしない。比較演算はWASMの符号付き・符号なし規則に一致する | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md), [`bench_x64_stencil_instructions.py`](experiments/pysim/benchmarks/jit/bench_x64_stencil_instructions.py), `test_local_fusions_select_by_offset_without_index_cap` |
| TEST-JITC-65 | 16以下のローカルをx64命令エンコーダでtrace化 | 関数引数を含む論理ローカル数が16以下で、i32/i64/f32/f64ローカルを読み書きするtrace | Loaderが前計算した型・オフセット・サイズ表を入力し、生成bodyと全raw wordの状態を確認する | `local.get` / `local.set` / `local.tee` は型と操作別の単一テンプレートへlowerし、命令エンコーダがlocal memory instructionの`disp8`へcompact offsetを直接書く。local offset variantや`rel`読出しはない。共有スタックとローカル領域のword順序・副作用がInterpreter意味論と一致する | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md), `test_trace_local_layout_is_fixed_during_compilation`, `test_local_stencils_encode_each_compact_offset_into_the_trace`, `test_local_fusions_select_by_offset_without_index_cap` |
| TEST-JITC-66 | local index 0〜15のdisplacement encodeと上限 | 論理ローカル数16以下でindex 0〜15へアクセスするtrace、およびローカル数17のtrace | i32/i64を含むlayoutの各local get/set/teeと融合操作をcompile・実行し、ローカル数17のtraceも試す | 最大byte offset 120を`disp8`へ直接encodeし、全local操作をtraceへコンパイルする。local offsetのために`rel`を進めず、Runtime APIも呼ばない。論理ローカル数17はcompile refusalとなる | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md), `test_local_stencils_encode_each_compact_offset_into_the_trace` |
| TEST-JITC-67 | 定数local.set/tee融合時のstack pointer | `i32.const; local.set`または`i32.const; local.tee`を含み、既存stack値があるtrace | sentinelをstackに置いて融合traceを実行し、local値、stack内容、`sp`を確認する | `i32.const; local.set`は定数pushを除いたため`sp`を変更せず、`local.tee`はlocalへ書いた後に定数値をstackへpushする。既存stack値とlocal値が保持される | [`test_x64_jit.py`](experiments/pysim/qa/tier3_plugins/jit/test_x64_jit.py), `{GOTCHA-JITC-08}` |
| TEST-JITC-04 | Runtime API操作後もJIT traceを継続 | `i32.clz`など直接演算stencilを持たないopcodeと後続の通常演算 | traceを生成し、JIT実行回数と最終値を確認する | Runtime API tableの対応APIをJIT trace内で呼び出し、正常継続時は後続operationも同じtraceで実行する。API呼出しをInterpreterへのフォールバックとして扱わない | `test_jitr_runtime_api_continues_into_following_jit_operations` |
| TEST-JITC-05 | x64実行可能バッファのW^X確定 | Linux x64上で初期化したバッファ | `/proc/self/maps`でOSの実権限を読み、commit、再パッチ、再commit後に再確認する。生成コードをCPUで実行する | 実権限が`RW → RX → RW → RX`となる。commit後のコードは42を返し、再パッチ後は7を返す。API経由のcommit後書込みは拒否される | [`test_exec_memory.py`](experiments/pysim/qa/tier3_plugins/jit/test_exec_memory.py) |
| TEST-JITC-06 | インタープリタ⇔JIT境界での共有スタック状態 | JITトレースから脱出 | 脱出時のスタックとコンテキストを確認 | 融合後も生存するWASM値は共有オペランド領域に保持し、trace終端の`ip`と`stack_pointer`が実行位置・スタック位置に一致する。融合で除去した中間値はスタック領域を要求しない | `{CPS_3Args}`, `TraceBoundaryInvariant` |
| TEST-JITC-07 | Runtime APIによる整数除算・剰余とWASM trap | 後続の通常演算を持つi32除算・剰余trace | x64向けにコンパイルして実行し、除算結果、ゼロ除算・符号付きoverflow時のtrapと後続命令実行有無を確認する | 除算・剰余は共通Runtime API tableから対応APIを呼ぶ。通常結果では後続JIT operationへ進み、trap条件では`trap_code`を記録してtraceを終了する | `test_x64_division_and_remainder_use_runtime_api_stencils`, [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITC-08 | assembly chain dispatcherとRuntime API tableの共通配置 | x64の直線後続traceとRuntime API stencil | 共通コード領域、chain経路、API tableの配置と実行を確認する | `common.S`から生成したchain dispatcherが固定slotに配置され、C++ opcode handler entryから構築したAPI tableがその後ろにある。dispatcherは未接続時に現在traceへ戻る。API stencilはtable entryを呼ぶ | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md), `{JIT_MultiBuffer_Cache}` |
| TEST-JITC-09 | トレース作業領域の固定上限 | 命令数64、body・定数領域合計1,536バイト、作業stack深さ128 wordの境界内外 | 境界内の適格traceと、命令数・body/pool合計長・stack深さを個別に超えるtraceをコンパイルし、結果blobとキャッシュ登録状態を確認する | 境界内はコンパイル可能で、header込みblobは1,584バイト以下となる。各上限を超えるtraceはJIT化を辞退してInterpreterへフォールバックし、キャッシュへ登録しない。relocation stagingは最大129 recordで固定する | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md) |

### ABI規約と共有オペランドスタック

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-10 | Interpreter/JIT entry signatureとrel pointer | Runtime API recordを必要とするtraceと数値即値だけのtrace | 両エンジンの関数ポインタ型、引数、定数参照を確認する | Interpreter handlerとJIT trace entryはともに`ctx, ip*, sp*, rel*`の順で4引数を持つ。JITでは数値即値とlocal offsetを機械語へ埋め込み、`rel`はRuntime API引数recordを使う場合だけそのrecord幅を進む。trace入口とchain先ではtrace固有pool先頭に初期化し、操作列後の4-byte referenceからheaderを得る | `{CPS_3Args}`, [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITC-12 | 共有スタックとトレース間状態 | 直線traceとそのchain先 | trace実行前後のoperand stack、`ip`、`stack_pointer`、`rel`を確認する | WASM値は共有operand stackを正本とし、trace entry間でTOSキャッシュ同期を行わない。chain先はtarget固有の`rel`先頭から実行し、同一frame深さでのみスタック状態を引き継ぐ | 「トレース境界とチェイニングの安全性」 |
| TEST-JITC-13 | ローカル変数アクセスの静的オフセット畳み込み | 同一関数フレーム内のローカル変数アクセス | コンパイル | ローカル領域の基底を起点とする固定オフセットとして直接アクセスされる | 「ローカル変数アクセスの静的オフセット畳み込み」`{ContextPointerRegister}` |

### JITトレースヘッダ
<!-- traceability: {CPS_3Args} {JIT_RegisterMapping} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-20 | x64ヘッダサイズと配置 | x64向けに生成したトレース | C++/ctypesの物理ヘッダサイズと全フィールドoffsetを確認する。255を超える関数相対depthをコンパイルして読む | ヘッダは48バイト。全フィールドのoffsetと型が一致する。`frame_depth`は32ビット値として切り詰めず保持される | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITC-21 | 昇格状態を管理情報で保持する | OldestからActiveへ昇格するトレース | cache管理情報と物理ヘッダを確認する | 管理情報の `0x01: PROMOTED` が設定される。物理ヘッダへflags欄を追加しない | 同上 |
| TEST-JITC-22 | x64 header直後から始まるtrace entry | x64向けに生成したtrace | ヘッダサイズ、body開始address、C ABI entryを確認して生成コードを実行する | 48バイトheader直後(+0x30)からassembly trace entryと操作stencilが始まり、追加のentry adapterを挟まず4論理引数C ABIで実行できる。結果が一致する。ARMv8-Mの配置はTBD | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md), `test_trace_compiler_cps_4arg_and_pic` |
| TEST-JITC-23 | 物理ヘッダの実行に必要な情報だけを保持 | x64向けトレースを生成 | ヘッダとcache indexを解析 | 固定headerに実行時target、配置長、スタック要件、関数相対frame depthを持ち、patch位置一覧・再配置位置表・汎用flags・variant欄を別管理で複製しない | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md), `{PositionIndependentCode}` |

### ADR_ScalableCodeOffset

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-30 | code_offsetはアライメントシフト保持 | エントリテーブル | `code_offset`の格納方式を確認 | `actual_offset >> code_align_shift`で16bitに収めている（32bit化していない） | ADR_ScalableCodeOffset 結論 |
| TEST-JITC-31 | 最大キャッシュサイズの計算 | `code_align_shift`設定値 | 最大アドレス可能範囲を確認 | `65535 << code_align_shift`が理論上限と一致する | 同上 |

### 安全性制約
<!-- traceability: {PositionIndependentCode} {MemoryBoundaryCheck} {FastAddressCheck} {JIT_LazyChaining} {JIT_RuntimeAPI} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-40 | 生成コードの位置独立性 (PIC) | 同一トレースバイナリを別のバッファ/オフセットにコピー | 実行 | 再コンパイルや外部シンボルrelocationなしで完全に同一の結果を出力する。生成objectの外部relocationはcompose時に除去する接続slotだけを指す | `{PositionIndependentCode}` |
| TEST-JITC-41 | ゲストメモリ境界チェックと副作用抑止 | メモリアクセス命令を含むx64トレース | 境界内外のアクセスを実行 | 範囲外アクセスはメモリ副作用前に検出され、WASM trapとなる。ARMv8-Mの命令列とアドレス検査方式はTBD | MemoryBoundaryCheck, FastAddressCheck |
| TEST-JITC-42 | キャッシュ溢れの3面ローテーション処理 | キャッシュ容量超過 | コンパイル試行 | Oldestバンクを破棄して再利用し、被チェイン元の解除を行う。解除対象の探索に加えて破棄バンク全体の消去を行うため、処理量は`O(k + n)`である | 「Cache Capacity Check」, `` |
| TEST-JITC-43 | ホストコール (WASI / fireball_call) のJIT境界 | 0〜6引数のimport／host call | トレース実行 | host call直前で対象ABIの終了処理を実行し、共有オペランド領域と実行コンテキストを同期してInterpreter／RuntimeEngineへ委譲する。JIT内で対象ABIと無関係なhost call stubを実行しない |  `` |
| TEST-JITC-44 | 境界委譲後のコンテキスト保持 | 複雑命令またはimport／host call | 境界復帰後に実行を継続 | 共有実行コンテキスト、共有オペランド領域、ローカル値領域を正本として状態が保持され、JIT専用戻り値バッファや専用オペランド領域が生成されない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| TEST-JITC-45 | 複数型Runtime APIのワード配置 | `i64/f32/f64` 定数と算術命令 | JITトレースを実行 | `i64/f64` は2ワード、`f32` は1ワードで演算結果が共有オペランド領域へ保存される | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |

### トレース境界不変条件とハンドラ委譲
<!-- traceability: {TraceBoundaryInvariant} {JIT_LazyChaining} {JIT_RuntimeAPI} {ContextPointerRegister} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-50 | スタック自己完結性検査 | 先頭で `local.set` や二項演算が先行するブロック | コンパイル試行 | 累積スタック深さが負になるため JIT 化を安全に拒否（`None` 返却）し、インタープリタ実行にフォールバックする | 「スタック自己完結性不変条件」 |
| TEST-JITC-51 | 真の境界命令でのブロック終端 | `CALL`, `CALL_INDIRECT`, `BR_TABLE`, import／host call, `RETURN` を含む関数 | BasicBlock 抽出 | 各境界命令の直前で BasicBlock が終端され、共有状態を同期してInterpreter／RuntimeEngineへ安全に委譲される。JITは`RETURN sentinel`を生成しない | 「制御フロー・コール境界のインタープリタ委譲不変条件」 |
| TEST-JITC-52 | トレース境界での共有スタック位置 | JIT トレース実行終了時 | スタック内容とコンテキストを確認 | 融合後に生存するWASM値は共有オペランド領域に保持し、trace脱出時の`ip`と`stack_pointer`が最終状態に一致する | 「共有スタック範囲不変条件」 |
| TEST-JITC-53 | 制御分岐handlerとmachine-code chainの分離 | `BR`、`BR_IF`、`BR_TABLE`で終わるtraceと、直線後続trace | 終端handlerの実行とtrace末尾のmachine codeを確認 | 分岐条件・control frame・遷移先はC++ Interpreterの命令別handlerが処理する。trace末尾はopcode共通dispatcherへ集約せず、直線後続traceの場合に限り共有コード領域のchain dispatcherへ移る。handler後にC++ dispatcherがtraceを選ぶ遷移はchainとして数えない | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITC-54 | x64共通コード領域chain dispatcher経由のtrace遷移 | 直線後続ブロックのtraceが常駐 | trace終端の相対分岐とchain targetを確認して実行 | trace末尾の`rel32`が共通コード領域のchain dispatcherを指す。dispatcherはヘッダ`+0x00`のtarget bodyへtail-jumpし、未接続なら現在traceへ戻る。C++ handler実行後のdispatcher遷移はchainに含めない | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITC-55 | x64 JIT対応命令と制御終端のhandler経由 | x64 JITが対応する算術・ローカル命令、および`BR`/`BR_IF`/`BR_TABLE`等の制御終端 | 各traceの生成・実行とC++ handler呼出しを確認する | 対応命令はx64 trace bodyで実行され、制御終端はC++ Interpreter handlerが一度処理する。構文終端を無条件に消去せず、条件値やcontrol frameを更新する | ``, `test_x64_jit.py` |
| TEST-JITC-58 | 分岐handlerによるstack巻き戻し | ラベル脱出に伴うスタック巻き戻しを持つ `BR` / `BR_IF` | 対応するC++ Interpreter handlerを通して実行する | handlerが条件値を消費し、stackとcontrol frameを更新する。JIT traceにbranch条件評価や分岐handlerをインライン展開しない | 「制御フロー・コール境界のインタープリタ委譲不変条件」 |
| TEST-JITC-59 | 事前計算したオフセットに従うローカルアドレス | ローカルを読み書きするブロック。i32だけのフレームと、i64ローカルを含むフレームの2通り | Loader相当のローカル表からJIT traceをコンパイルし、配列を指定して実行する | 書き込み先は各ローカルの表引き済みワードオフセットになる。混在幅のフレームではi64後のローカル位置が詰められ、未更新wordのsentinelも保持される | `test_trace_local_addressing_uses_precomputed_compact_offsets` |
| TEST-JITC-60 | 深い式の値順序と共有スタック範囲 | 乱数で生成した逆ポーランド式（最大深さ3〜11） | 各式をコンパイルして実行し、参照実装と比較する。実書込領域も検査する | 結果が参照実装と一致する。実書込領域は`[sp, sp + stack_words)`に収まり、`stack_words`は結果を含む生成コードの実使用量以上である。定数畳込みと命令融合で意味論上の最大深さより小さくなることがある | ``, TraceBoundaryInvariant |
| TEST-JITC-61 | 動的値を使う深い式の共有スタック順序 | 11個のローカル読出しからなる右ネスト`i32.sub`とローカル読出しのシフト連鎖 | コンパイルして実行し、結果、`stack_words`、実書込領域を確認する | 被演算子は共有スタックから正しい順序で読み込まれ、結果が参照実装と一致する。融合できない深さ11の式では`stack_words`が11語となり、全実書込が宣言範囲内に収まる | TraceBoundaryInvariant |

| TEST-JITC-57 | x64共通コード区画とchain targetの有効性 | x64 JIT cacheでtraceをlinkし、対象バンクをrotate | chain targetと共通dispatcherを確認 | 共通コードはcache rotationで保持され、無効化されたtraceを指すtargetは0に戻る。ARMv8-Mの分岐範囲とrelay方式はTBD | `jit_abi.md`, `jit_runtime.md` |

### 実装上の注意点に対応する検証
<!-- traceability: {MemoryBoundaryCheck} {FastAddressCheck} {GOTCHA-JITC-01} {GOTCHA-JITC-03} {GOTCHA-JITC-04} {GOTCHA-JITC-07} {GOTCHA-JITC-08} -->

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `GOTCHA-JITC-01` | ABI probeと各生成経路のレジスタ規約 | トレース生成 | C++ probe由来の関数型と構造体offsetを確認し、assembly sourceの引数レジスタを対象ABIに照合する | assembly sourceはSysV AMD64とWindows x64の引数レジスタを明示し、Runtime API stencilは対象ABIの保存規則に従う。ABI probeは関数型と構造体offsetの一致を検証する。ARMv8-MのABIはTBD | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md)、`{JIT_RegisterMapping}` |
| GOTCHA-JITC-03 | trace境界の共有stackと実行状態同期 | JIT trace終端 | C++ Interpreter handlerへ復帰した状態を確認する | WASM値は既に共有スタック上にあり、x64では`ip`と`stack_pointer`を同期する。ARMv8-Mの物理配置とポインタ更新はTBD | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md)、`{CPS_3Args}` |
| GOTCHA-JITC-04 | 境界チェック先行性と副作用ゼロ（Wrapping禁止） | メモリアクセス命令 | `addr + width - 1 >= mem_size` でトレース実行 | メモリアクセス（LDR/STR）前にx64実装の範囲検査で評価され、境界外時はメモリ書き込みや値更新の副作用が一切発生せず即座にトラップテールへ分岐する。 | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md)、MemoryBoundaryCheck, FastAddressCheck |
| GOTCHA-JITC-07 | trace結果値とホスト関数戻り値の分離 | 残余値を持つtrace | 共通stackとC++ handler復帰後の値を確認する | WASM値は共有operand stackに残し、JIT traceのC戻り値として返さない。x64の物理配置は`jit_abi.md`に従い、ARMv8-Mの物理配置はTBD | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md)、`{CPS_3Args}` |
| `GOTCHA-JITC-08` | `i32.const; local.set/tee`融合時の共有stack位置 | 定数local書込みを融合したtrace | 既存stack sentinelとlocal値、`sp`の更新有無を実行後に確認する | `local.set`融合は除去した定数pushに対応する`sp`減算を行わない。`local.tee`融合はlocal書込みの後に定数値を一度だけpushする | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md), `TEST-JITC-67` |

## 3. テスト検証実績と網羅状況

- **TEST-JITC-01〜09 (assembly stencil composition)**: build-time assembly raw stencilからのtrace composition、即値とlocal offsetのtrace code埋め込み、連続trace blob、実行時codegen/次opcode fetch/operation dispatchの不在とRuntime API stencilの継続、Runtime API引数用trace末尾pool、Runtime APIによる除算・剰余、共通chain dispatcher、trace上限を検証対象とする。x64の検証対象に限定する。ARMv8-Mの実装と検証はTBDである。
- **TEST-JITC-05 (W^X)**: [`test_exec_memory.py`](experiments/pysim/qa/tier3_plugins/jit/test_exec_memory.py)の`test_executable_buffer_wx_protection_lifecycle`はAPIのトランザクション状態と書込み拒否を検査する。`test_executable_buffer_linux_mapping_enforces_wx`は内部の`current_protection`を期待値に使わず、Linuxカーネルが公開する実権限を独立に確認する。初期状態、初回commit、再パッチ、再commitの4状態を確認する。
- **TEST-JITC-05の反証確認**: `mprotect`への指定に書込み・実行ビットを追加して実権限をRWXにする試験内変異を用いる。メタデータがRXのままでも、OS実権限の期待値`r-x`との不一致で失敗することを確認する。
- **TEST-JITC-10 (entry signatureと定数cursor)**: Interpreterの`(ctx, ip*, sp*)`とJITの`(ctx, ip*, sp*, rel*)`の関数型を照合する。WASM数値即値とlocal offsetはnative codeへ埋め込み、`rel`はRuntime API argument recordを使う操作でのみ進める。操作列末尾の4-byte referenceからheaderを取得し、chain先ではpool先頭へ再初期化する。
- **TEST-JITC-20〜22 (x64 48バイト物理ヘッダ)**: x64の`jit_trace_header`は実行に必要なtrace targetとmetadataを保持し、header直後の`+0x30`からassembly trace entryとoperation stencil、alignment後の末尾に定数領域を配置する。追加のentry adapterは置かない。ARMv8-Mの配置はTBDである。
- **TEST-JITC-40 (PIC 位置独立性)**: トレースバイナリを別のメモリ領域・オフセットへコピーして再コンパイルなしで直接実行し、完全同一の演算結果を返すことを実証済み。
- **TEST-JITC-42 (3面キャッシュ代謝 & 有界アンリンク)**: 3面マルチバッファキャッシュのローテーション、破棄バンク全体の消去、および被チェイン逆引きテーブルに基づく `O(n + k log n)` 処理を実証済み。
- **TEST-JITC-43 (ホストコール ABI)**: `test_jitr_host_import_stays_on_interpreter_runtime_boundary`は、traceからInterpreterへ戻った後のhost import呼出しと結果を検査する。raw guest bindingの0〜6引数検査は型・値・port搬送の証拠である。0〜6引数の物理スタックアライメントとCaller-savedレジスタの完全保護は未検証である。

### W^X局所検証

次のコマンドで実行可能バッファのW^X契約を検証する。

```bash
UV_CACHE_DIR=/tmp/fireball-test-design-uv uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier3_plugins/jit/test_exec_memory.py
```

## 4. 未検証・スコープ外

- TEST-JITC-22は本番C++ ABI probeと通常C ABIでコンパイルしたステンシルの関数型、header、body位置を確認する。`local_base`は`execution_context`から読み出す。異なるホストABIの検証は対応する環境で行う。ARMv8-Mの物理配置はTBDとする。

- ARMv8-Mの物理JIT仕様と実機検証はTBDであり、本書は受け入れ条件を定めない。
- WindowsとLinux以外のOSにおける実権限の独立確認は未検証である。Linux専用の実権限試験は他OSで明示的にskipする。APIの状態検査だけでOS実権限の検証済みとは扱わない。
- W^X遷移中の別スレッドによる並行実行と、同時書込み・実行の競合履歴は本試験の対象外である。
