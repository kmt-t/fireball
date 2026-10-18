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
| TEST-JITC-01 | build-time raw stencilによるfall-through trace composition | 単純な直線コード（`i32.const`→`i32.add`等） | `generate_assembly.py`で`operations.S`、`trace.S`、`common.S`を生成し、trace compilerでtraceを組み立てる。生成headerと共有library依存を調べる | trace bodyはassembly entryとoperation本体を一つの直列blobへ配置し、成功接続slotを後続stencilで置換する。通常経路はfall-throughし、命令間call/jumpや操作ごとのエピローグがない。WASM数値即値とlocal offsetはコードへ直接埋め込む。定数poolはtraceごとのheader相対参照1個とcompact Runtime API recordを置き、Interpreter注釈bytesやノードごとのheader参照を複製しない。実行時codegen、次WASM opcode fetch、operation dispatch、未解決relocationはない | `{JIT_AssemblyStencilCodegen}`, `{PositionIndependentCode}` |
| TEST-JITC-02 | 直接stencilを持たないopcodeのAPI実行 | Loaderが受理したWASM opcodeのうち直接演算stencilを持たない命令と、その後続に直接stencilを持つ命令がある | 連続traceを生成・実行し、Runtime API呼出しと後続演算を観測する | 対象opcodeをJIT Runtime API stencilで処理し、APIが`true`を返すと同じJIT traceの後続operationを実行する | `{JIT_RuntimeAPI}`, `` |
| TEST-JITC-03 | 生成trace本体の演算意味論 | x64の算術・ビット演算・ローカル操作を含むtrace | build-time生成したassembly stencilをtraceへ連結し、Python handler経由なしで実行する | 生成コードの演算結果、値の配置、境界での状態同期がWASM意味論と一致する | |
| TEST-JITC-63 | trace codeへの数値即値埋め込みとcompact constant pool | 4-byte/8-byte即値を持つtraceとRuntime APIを呼ぶtrace | 対象traceをC++ native compiler経由で生成し、完成native codeの即値、poolのrecord境界、trace単位header参照、終端annotation offsetを確認する | `i32/f32/i64/f64.const`と即値演算の固定値はtrace compile時に命令へ埋め込む。Runtime API recordはu8 API slot、u8 immediate count、u8 payload byte span、u32 `code_ip_t` source IP、型付きimmediate payloadを持つ。pool先頭にはtraceごとに一つの4-byte相対参照を置き、終端recordにInterpreter annotation byte offsetを一度だけ置く。Interpreter注釈の複製、ノードごとのheader参照、pool末尾参照を置かない。operation間継続deltaはなく、success pathはfall-throughし、template内分岐は配置後位置へ再配置する | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md), ``, `` |
| TEST-JITC-64 | Clang-WASM `local.get, local.get, i32.*` 融合 | 空スタックから始まる2つのi32 local readと整数二項演算を含むtrace | 加算・減算・乗算・ビット演算・比較を含むtraceを生成して実行する。index 7より大きいlocal offsetも含める。Clang生成guestのprofileから対象n-gramの静的頻度も集計する | 各演算結果がWASMの32ビット意味と一致し、trace compilerが論理indexではなく型とcompact offsetに基づいて融合operationを選ぶ。アセンブリから生成した対応stencilの機械語命令数を記録し、固定した削減数を合否条件にしない。比較演算はWASMの符号付き・符号なし規則に一致する | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md), [`bench_x64_stencil_instructions.py`](experiments/pysim/benchmarks/jit/bench_x64_stencil_instructions.py), `` |
| TEST-JITC-65 | 16以下のローカルを命令エンコーダでtrace化 | 関数引数を含む論理ローカル数が16以下で、i32/i64/f32/f64ローカルを読み書きするtrace | Loaderが前計算した型とオフセット表を入力し、index 0〜15の生成body、全raw wordの状態、JIT Runtime API tableのopcode slotを確認する | `local.get` / `local.set` / `local.tee` はoperation・WASM型に対応するstencilへlowerし、命令エンコーダがlocal indexをcompact word offsetへ解決してnative instructionへ直接書き込む。offset別stencil variant、実行時`rel`読出し、local Runtime APIを使わない。JIT Runtime API tableのopcode `0x20`〜`0x22`は未登録である。共有スタックとローカル領域のword順序・副作用がInterpreter意味論と一致する | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md), ``, ``, `` |
| TEST-JITC-66 | local index 0〜15のdisplacement encodeと上限 | 論理ローカル数16以下でindex 0〜15へアクセスするtrace、およびローカル数17のtrace | i32/i64を含むlayoutの各local get/set/teeと融合操作をcompile・実行し、ローカル数17のtraceも試す | 最大byte offset 120を`disp8`へ直接encodeし、全local操作をtraceへコンパイルする。local offsetのために`rel`を進めず、Runtime APIも呼ばない。論理ローカル数17は直接local stencilの対象外となる | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md), `` |
| TEST-JITC-67 | 定数local.set/tee融合時のstack pointer | `i32.const; local.set`または`i32.const; local.tee`を含み、既存stack値があるtrace | sentinelをstackに置いて融合traceを実行し、local値、stack内容、`sp`を確認する | `i32.const; local.set`は定数pushを除いたため`sp`を変更せず、`local.tee`はlocalへ書いた後に定数値をstackへpushする。既存stack値とlocal値が保持される | `{GOTCHA-JITC-08}` |
| TEST-JITC-68 | trace exitの次BasicBlock index | Loaderが作成した次ブロックmetadataを持つtrace | traceを生成し、20-byte boundary recordの`block_index`と`next_pc`を確認する | `block_index`はLoaderの`next_block_index`と一致し、そのBasicBlockの`head_pc`も`next_pc`と一致する。u16 indexとu8深度・結果数を使い、recordは20バイトとなる | [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md), `` |
| TEST-JITC-04 | Runtime API操作後もJIT traceを継続 | `i32.clz`など直接演算stencilを持たないopcodeと後続の通常演算 | traceを生成し、JIT実行回数と最終値を確認する | Runtime API tableの対応APIをJIT trace内で呼び出し、正常継続時は後続operationも同じtraceで実行する。API呼出しをInterpreterへのフォールバックとして扱わない | `` |
| TEST-JITC-05 | x64実行可能バッファのRWX初期設定 | Linux x64上で初期化したバッファ | `/proc/self/maps`でOSの実権限を読み、commit、再パッチ、再commit後に再確認する。生成コードをCPUで実行する | 実権限は全状態でRWXとなる。commit後のコードは42を返し、再パッチ後は7を返す。API経由のcommit後書込みは拒否される | |
| TEST-JITC-06 | インタープリタ⇔JIT境界での共有スタック状態 | JITトレースから脱出 | 脱出時のスタックとコンテキストを確認 | 融合後も生存するWASM値は共有オペランド領域に保持し、trace終端の`ip`と`stack_pointer`が実行位置・スタック位置に一致する。融合で除去した中間値はスタック領域を要求しない | `{CPS_3Args}`, `TraceBoundaryInvariant` |
| TEST-JITC-07 | Runtime APIによる整数除算・剰余とWASM trap | 後続の通常演算を持つi32除算・剰余trace | x64向けにコンパイルして実行し、除算結果、ゼロ除算・符号付きoverflow時のtrapと後続命令実行有無を確認する | 除算・剰余は共通Runtime API tableから対応APIを呼ぶ。通常結果では後続JIT operationへ進み、trap条件では`trap_code`を記録してtraceを終了する | ``, [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITC-08 | assembly chain dispatcherとRuntime API tableの共通配置 | x64の直線後続traceとRuntime API stencil | 共通コード領域、chain経路、API tableの配置と実行を確認する | `common.S`から生成したchain dispatcherが固定slotに配置され、Tier 3 JITが所有する読み取り専用JIT Runtime API tableへの借用ポインタがその後ろにある。表本体をキャッシュへ複製しない。table entryはJIT Runtime API wrapperへ対応づける。Interpreter opcode handler表からAPI tableを構築しない。dispatcherは未接続時に現在traceへ戻る | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md), [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md), `{JIT_MultiBuffer_Cache}` |
| TEST-JITC-09 | trace形成上限と定数pool容量 | 最大64 WASM operationとそのRuntime API引数recordを含むtrace、および最大stack使用量を含む命令列 | C++ trace compilerで上限までのoperation数、body/poolサイズ、定数record staging数を確認する。stack上限内・超過の両方を実行してtrap結果を確認する | 対応命令列が64 operation・1,536 byteのtrace出力領域と129 record stagingの範囲内に収まる。stack overflowはJITもInterpreterと同じtrapにする。入口stack shapeを理由にコンパイルを拒否しない | `{JIT_AssemblyStencilCodegen}`, `TraceBoundaryInvariant`, [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md) |

### ABI規約と共有オペランドスタック

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-10 | Interpreter/JIT entry signatureとcompact rel cursor | Runtime API recordを必要とするtraceと数値即値だけのtrace | 両エンジンの関数ポインタ型、引数、pool参照を確認する | Interpreter handlerとJIT trace entryはともに`ctx, ip*, sp*, rel*`の順で4引数を持つ。JITでは数値即値とlocal offsetを機械語へ埋め込み、recordごとにu8 slot/count/span、u32 source IP、型付きpayloadを読む。trace入口とchain先ではpool anchorの4-byte参照を保持してrecord先頭へ進み、trace exitは同じanchorからheaderを得る。終端recordが持つannotation offsetからInterpreter注釈を再参照する | `{CPS_3Args}`, [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITC-12 | 共有スタックとトレース間状態 | 直線traceとそのchain先 | trace実行前後のoperand stack、`ip`、`stack_pointer`、`rel`を確認する | WASM値は共有operand stackを正本とし、trace entry間でTOSキャッシュ同期を行わない。chain先はtarget固有pool anchorからrecord先頭を復元し、遷移先に必要なstackとframe状態を引き継ぐ | 「トレース境界とチェイニングの安全性」 |
| TEST-JITC-13 | ローカル変数アクセスの静的オフセット畳み込み | 同一関数フレーム内のローカル変数アクセス | コンパイル | ローカル領域の基底を起点とする固定オフセットとして直接アクセスされる | 「ローカル変数アクセスの静的オフセット畳み込み」`{ContextPointerRegister}` |

### JITトレースヘッダ
<!-- traceability: {CPS_3Args} {JIT_RegisterMapping} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-20 | x64ヘッダサイズと配置 | x64向けに生成したトレース | C++/ctypesの物理ヘッダサイズと全フィールドoffsetを確認する。最大許容深度32と、上限を超える制御深度を確認する | ヘッダは32バイト。全フィールドのoffsetと型が一致し、u8 `frame_depth`が最大深度32を保持する。上限超過はLoaderが拒否する | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITC-21 | Oldest実行で物理ヘッダを保持する | 常駐traceがOldestにある | lookup前後の物理ヘッダとコードアドレスを比較する | ヘッダとアドレスは変わらない。昇格状態用のflags欄を持たない | `jit_runtime_test_spec.md` TEST-JITR-23 |
| TEST-JITC-22 | x64 header直後から始まるtrace entry | x64向けに生成したtrace | ヘッダサイズ、body開始address、C ABI entryを確認して生成コードを実行する | 32バイトheader直後(+0x20)からassembly trace entryと操作stencilが始まり、追加のentry adapterを挟まず4論理引数C ABIで実行できる。結果が一致する。ARMv8-Mの配置はTBD | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md), `` |
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
| TEST-JITC-43 | ホスト呼出し終端operationからTier 2 CPS handlerへの接続 | 0〜6引数のimport／host call | terminal call stencilからTier 2 CPS handlerへのtail-callを実行し、Interpreter単独時との結果を比較する | call operationはtraceの最後に置く。x64 stencilは共通コード領域の借用function pointer tableのslotからTier 2 CPS handlerへtail-callする。traceに関数の絶対アドレスを埋め込まない。呼出し意味論はInterpreter仕様に従う | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md), `TEST-INTP-05` |
| TEST-JITC-44 | 境界委譲後のコンテキスト保持 | 複雑命令またはimport／host call | 境界復帰後に実行を継続 | 共有実行コンテキスト、共有オペランド領域、ローカル値領域を正本として状態が保持され、JIT専用戻り値バッファや専用オペランド領域が生成されない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) |
| TEST-JITC-45 | 複数型Runtime APIのワード配置 | `i64/f32/f64` 定数と算術命令 | JITトレースを実行 | `i64/f64` は2ワード、`f32` は1ワードで演算結果が共有オペランド領域へ保存される | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |

### トレース境界不変条件とハンドラ委譲
<!-- traceability: {TraceBoundaryInvariant} {JIT_LazyChaining} {JIT_RuntimeAPI} {ContextPointerRegister} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-JITC-50 | BasicBlock入口から持ち越すoperand stack値 | 有効なWASM control-flow mergeで既存stack値を消費する命令から始まるBasicBlock | 直前命令が積んだ値を残してBasicBlockへ入り、traceを生成・実行する | 相対stack depthが負になることを拒否理由にせず、traceが入口`sp`を基準に既存値を読み、正しい`sp`と結果を維持してJIT実行を続ける | `TraceBoundaryInvariant`, `` |
| TEST-JITC-51 | 制御命令と実行境界の状態整合 | `call`, `call_indirect`, `br_table`, import／host call, `return`を含む関数 | Interpreter単独実行と、対応するJIT経路の結果・共有状態・境界理由を比較する | 観測結果と実行状態はInterpreter単独実行と一致する。呼出し意味論はInterpreter仕様に従う。JIT処理済み命令をInterpreterで再実行しない | [`interpreter.md`](docs/components/tier2_runtime/interpreter.md)、TraceBoundaryInvariant |
| TEST-JITC-52 | トレース境界での共有スタック位置 | JIT トレース実行終了時 | スタック内容とコンテキストを確認 | 融合後に生存するWASM値は共有オペランド領域に保持し、trace脱出時の`ip`と`stack_pointer`が最終状態に一致する | 「共有スタック範囲不変条件」 |
| TEST-JITC-53 | 制御分岐の意味論とInterpreter CPS境界 | `if`、`br`、`br_if`、`br_table`で終わるtraceと常駐target | 条件値、stack、遷移先、CPS handlerとnative chainの有無を観測する | `if`、`br`、`br_if`はtraceとRuntime APIに含めず、Interpreter CPS handlerが一度だけ実行する。分岐先へnative chainせず、制御状態はInterpreter単独実行と一致する | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md), `TraceBoundaryInvariant` |
| TEST-JITC-54 | x64共通コード領域chain dispatcher経由のtrace遷移 | 直線後続ブロックのtraceが常駐 | trace終端の相対分岐とchain targetを確認して実行 | trace末尾の`rel32`が共通コード領域のchain dispatcherを指す。dispatcherはヘッダ`+0x00`のtarget bodyへtail-jumpし、未接続なら現在traceへ戻る。C++ handler実行後のdispatcher遷移はchainに含めない | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) |
| TEST-JITC-55 | x64 JIT対応命令と制御終端のhandler経由 | x64 JITが対応する算術・ローカル命令、および`BR`/`BR_IF`/`BR_TABLE`等の制御終端 | 各traceの生成・実行とC++ handler呼出しを確認する | 対応命令はx64 trace bodyで実行され、制御終端はC++ Interpreter handlerが一度処理する。構文終端を無条件に消去せず、条件値を一度消費し、静的branch metadataからstackと遷移先を復元する | `jit_abi.md`, `.py` |
| TEST-JITC-58 | Interpreter CPS分岐時のstack巻き戻しと結果値保持 | ラベル脱出に伴うstack巻き戻しを持つ`br` / `br_if` | Interpreter単独実行と、分岐直前trace後にCPS handlerを実行する経路を比較する | 分岐opcodeはtrace外で一度だけ実行する。条件値の消費、結果値保持、stack復元、遷移先PCが一致する | 「制御フロー・コール境界の状態整合不変条件」 |
| TEST-JITC-59 | 事前計算したオフセットに従うローカルアドレス | ローカルを読み書きするブロック。i32だけのフレームと、i64ローカルを含むフレームの2通り | Loader相当のローカル表からJIT traceをコンパイルし、配列を指定して実行する | 書き込み先は各ローカルの表引き済みワードオフセットになる。混在幅のフレームではi64後のローカル位置が詰められ、未更新wordのsentinelも保持される | `` |
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

- **TEST-JITC-01〜09 (assembly stencil composition)**: build-time assembly raw stencilからのtrace composition、compact constant pool、即値とlocal offsetのtrace code埋め込み、連続trace blob、実行時codegen/次opcode fetch/operation dispatchの不在とRuntime API stencilの継続、Runtime APIによる除算・剰余、共通chain dispatcher、trace上限を検証対象とする。x64の検証対象に限定する。ARMv8-Mの実装と検証はTBDである。
- **TEST-JITC-05の反証確認**: 実権限をRWまたはRXへ変更した場合は、OS実権限の期待値`rwx`との不一致で失敗することを確認する。
- **TEST-JITC-10 (entry signatureと定数cursor)**: Interpreterの`(ctx, ip*, sp*)`とJITの`(ctx, ip*, sp*, rel*)`の関数型を照合する。WASM数値即値とlocal offsetはnative codeへ埋め込み、`rel`はRuntime API argument recordを読むときだけ進める。pool先頭のtrace単位4-byte referenceを`ctx.rel` anchorに保持し、chain先ではanchorからrecord cursorを再初期化する。終端recordのannotation offsetで関数のInterpreter注釈を再参照する。
- **TEST-JITC-20〜22 (x64 32バイト物理ヘッダ)**: x64の`jit_trace_header`は実行に必要なtrace targetとmetadataを保持し、header直後の`+0x20`からassembly trace entryとoperation stencil、alignment後の末尾に定数領域を配置する。追加のentry adapterは置かない。ARMv8-Mの配置はTBDである。
- **TEST-JITC-40 (PIC 位置独立性)**: トレースバイナリを別のメモリ領域・オフセットへコピーして再コンパイルなしで直接実行し、完全同一の演算結果を返すことを実証済み。
- **TEST-JITC-42 (3面キャッシュ代謝 & 有界アンリンク)**: 3面マルチバッファキャッシュのローテーション、破棄バンク全体の消去、および被チェイン逆引きテーブルに基づく `O(n + k log n)` 処理を実証済み。

### x64実行可能バッファの局所検証

次のコマンドで実行可能バッファの初期権限とパッチ区間の契約を検証する。


## 4. 未検証・スコープ外

`if`、`br`、`br_if`をJIT機械語またはRuntime APIで直接処理する経路と、分岐先へのnative chainは対象外である。TEST-JITC-51、53、55、58はtrace終端後のInterpreter CPS処理を検証する。`br_table`、関数境界、および他の境界命令はそれぞれのInterpreter復帰契約に従う。

- TEST-JITC-22は本番C++ ABI probeと通常C ABIでコンパイルしたステンシルの関数型、header、body位置を確認する。`local_base`は`execution_context`から読み出す。異なるホストABIの検証は対応する環境で行う。ARMv8-Mの物理配置はTBDとする。

- ARMv8-Mの物理JIT仕様と実機検証はTBDであり、本書は受け入れ条件を定めない。
- WindowsとLinux以外のOSにおける実権限の独立確認は未検証である。Linux専用の実権限試験は他OSで明示的にskipする。APIの状態検査だけでOS実権限の検証済みとは扱わない。
- RWX領域で別スレッドが実行中にパッチする競合履歴は本試験の対象外である。対象ランタイムは同一JIT領域の実行とパッチを直列化する。
