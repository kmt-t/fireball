# JIT 実行コンテキスト ABI 契約

<!-- evidence:
     contract-only: true
     test: docs/qa/tier3_plugins/jit_runtime_test_spec.md
-->

<!-- traceability: {ContextPointerRegister} {PositionIndependentCode} -->

## 1. コンセプト

この文書は、Tier 2ランタイムとTier 3 JIT拡張が共有するネイティブABI契約の正本である。Interpreter handlerおよびJIT trace entryは、実行状態を表す3論理引数（`ctx`、`ip`、`sp`：`{CPS_3Args}`）に、定数・注釈領域ポインタ`rel`を加えた4引数を共有する。 <!-- definition: {CPS_3Args} --> `execution_context`の物理レイアウトおよびCallStackは[`interpreter.md`](docs/components/tier2_runtime/interpreter.md)を正本とし、本書はx64における呼び出し規約・トレースヘッダ・Runtime API記録・拡張接続契約を定義する。ARMv8-Mの物理配置、ポインタ幅、ABIはすべてTBDとする。

## 2. アーキテクチャ分類

本書はTier 2 Runtimeが所有する契約である。Tier 2 InterpreterとTier 3 JIT拡張がこのABIを利用する。

## 3. 静的モデル

実行状態およびコンテキスト構造の正本は[`interpreter.md`](docs/components/tier2_runtime/interpreter.md)の`{ExecutionContext_Layout}`（x64では144バイト）であり、本書ではレイアウトを重複定義しない。`{ContextPointerRegister}` <!-- definition: {ContextPointerRegister} --> インタープリタ単独実行とJIT遷移で`ctx`の型を変えず、呼び出し側が参照先の寿命を保証する。ARMv8-Mの配置・ポインタ幅・サイズ・offsetはTBDである。 `{META_ZeroCostAbstraction}`

### 整数型と座標系

ネイティブABIはC互換`typedef`を使う。同じ基底整数型でも、PC、関数コード内IP、コード範囲、ブロック番号、フレーム内word位置を別名で区別する。16bit値の無効値は`0xFFFF`、8bit値の無効値は`0xFF`とする。

| 型 | 基底型 | 用途と上限根拠 |
| :--- | :--- | :--- |
| `block_index_t` | `uint16_t` | BasicBlock番号。`0xFFFF`を予約し、最大65,535ブロック、最大有効番号65,534とする |
| `frame_word_offset_t` | `uint16_t` | ローカルフレーム内word offset。`FB_CONF_MAX_LOCALS` 個と `FB_CONF_MAX_FUNCTION_ARGUMENTS` 個が各2 wordでも `2 × (FB_CONF_MAX_LOCALS + FB_CONF_MAX_FUNCTION_ARGUMENTS)` wordで収まる |
| `annotation_offset_t` | `uint32_t` | 注釈pool内byte offset/count。16bitへ制限しない |
| `wasm_pc_t` | `uint32_t` | WASM module PC。ROM 96KBを扱うため32bitを維持する |
| `code_ip_t` | `uint32_t` | 関数コード内の実行IP・復帰IP |
| `code_offset_t` | `uint32_t` | コード領域の長さ・offset・命令span。容量拡張に備えて32bitを維持する |
| `function_index_t` | `uint16_t` | `FB_CONF_MAX_IMPORTS` importと `FB_CONF_MAX_FUNCTIONS` 定義関数の合計を収める |
| `type_index_t` | `uint16_t` | 型数上限 `FB_CONF_MAX_TYPES`。将来の拡張に備えて16bitを維持する |
| `global_index_t` | `uint16_t` | global数上限 `FB_CONF_MAX_GLOBALS` |
| `local_count_t` | `uint16_t` | local数 `FB_CONF_MAX_LOCALS`、または最大 `2 × (FB_CONF_MAX_LOCALS + FB_CONF_MAX_FUNCTION_ARGUMENTS)` frame word |
| `operand_words_t` | `uint8_t` | operand stack上限 `FB_CONF_MAX_VALUE_STACK` 値。i64/f64を2 wordで数えても最大 `2 × FB_CONF_MAX_VALUE_STACK` word |
| `frame_depth_t` | `uint8_t` | 制御構造の入れ子上限 `FB_CONF_MAX_NESTING_DEPTH` |
| `instruction_width_t` | `uint8_t` | LEB128即値を含む命令の符号化幅。255 byte超過はロード時に拒否する |
| `jit_stack_words_t` | `uint8_t` | Interpreterの `FB_CONF_MAX_VALUE_STACK` 値上限と2-word値幅から最大 `2 × FB_CONF_MAX_VALUE_STACK` word |
| `jit_body_count_t` | `uint8_t` | JIT trace命令上限 `FB_CONF_JIT_MAX_TRACE_INSTRUCTIONS` |
| `card_index_t` | `uint32_t` | JIT card番号。module長に応じて増えるため32bitを維持する |

値幅、フレーム深さ、命令幅、trace長の各縮小はreaderとJIT入口の上限検査で保証する。`block_index_t`の有効番号と無効値を混同しない。Native ModuleExecutionViewはPCからBasicBlock番号を引く専用索引を持たない。

### コンテキスト拡張

`loop_jump_count`は、実際に取得したLOOP後方辺を一度だけ数える（`{JIT_BackedgeYield}`）。 <!-- definition: {JIT_BackedgeYield} --> 条件不成立の`br_if`は数えない。しきい値の正本はInterpreter設定の`FB_CONF_INTERPRETER_YIELD_THRESHOLD`である。`if`と`br_if`はJIT traceから除外し、trace完了後にC++ Interpreter CPS handlerが処理する。`br`はtrace内で静的なstack移しを行い遷移先へチェインする（後方`br`は`loop_jump_count`閾値到達時にCPS exitする）。後方辺数がしきい値に達した境界ではyield statusを保持してC++ dispatcherへ戻る。RuntimeはそのstatusをCOOSへ渡す。

値スタックとローカル値領域は、それぞれ基点・現在位置・上限をポインタで保持する。リニアメモリのホスト基点とサイズは`+0x60`と`+0x68`にある（ゲストアドレスの`mem_base`・32bit `mem_size`とは区別する）。ホスト基点は非所有の借用値であり、実行呼出し中の寿命保証と再配置後の更新を行う。

共通chain dispatcherへの相対値はtrace headerの`common_code_relative`に保持する。共通chain dispatcherは`lir/x64/common.S`から生成し、traceごとに複製しない。共通コード領域にはchain dispatcherに続けて、Tier 3 JITが所有するRuntime API function pointer tableへの8バイト借用ポインタを配置する。表本体は共通コード領域やJIT cacheへ複製しない。traceにはtable添字を置き、関数の絶対アドレスを埋め込まない。PICの共通コード参照幅はアーキテクチャごとのABIで定める。 `{PositionIndependentCode}` <!-- definition: {PositionIndependentCode} -->

### x64 継続引数と assembly ABI
<!-- traceability: {CPS_3Args} -->

Interpreter handlerとJIT trace entryは`ctx`、`ip`、`sp`、`rel`の順で4引数を受け取る（`{JIT_RegisterMapping}`）。 <!-- definition: {JIT_RegisterMapping} --> `ctx`、`ip`、`sp`が実行状態を表す3論理引数であり、`rel`は定数領域ポインタである。C++関数を呼ぶ側とRuntime API関数呼出しはC ABIに従う。手書きassembly stencilはABIの受け渡し位置を明示する。SysV AMD64では`ctx`, `ip`, `sp`, `rel`を`rdi`, `rsi`, `rdx`, `rcx`で受け取り、Windows x64では`rcx`, `rdx`, `r8`, `r9`で受け取る。ABI probeは本番C++関数型と構造体offsetを照合する。WASM仮想レジスタは導入しない。

x64 stencilの役割レジスタは、`ctx`、`ip`、`sp`、`rel`を受け取ったレジスタである。役割レジスタは一時値に使わない。役割レジスタを暗黙に書き換える命令（`div`、`idiv`、`cdq`、`cqo`、`mul`、`call`、可変シフトの`cl`など）を使うstencilは、書き換え前の値を同じstencil内で保存し、完了前に元の役割へ戻す。保存先は役割レジスタ以外のscratch register（`r10`、`r11`、`rax`）またはstackとする。終端stencil（CPS handlerへのtail-callと`ret`）は役割レジスタを変えてよい。この規約はSysV AMD64とWindows x64の両方に適用する。Windows x64では`ip`が`rdx`、`sp`が`r8`に当たるため、`rdx`を書く除算は`ip`を壊す。

`local_base`は`ctx`が指す`execution_context.local_base`から取得する。trace entryは`header + const_rel_offset`のpool anchorを受け取り、先頭4-byte header相対参照を飛ばしてRuntime API recordの先頭に`rel`を設定する。chain dispatcherはchain先headerからpool anchorを計算し、同じ初期化を行う。trace exitは`ctx.rel`に保持したpool anchorを使ってheaderを解決する。`ctx.rel`はpool anchorまたは通常のポインタとして扱う。chain接続時は共通入口のcall箇所へtail-jumpし、連結先のbodyを`rax`で渡す。入口のprologueは再実行しない。これにより保存済み`r12`とstack frameをchain全体で共有する。成功接続slotを次operation本体で置き換え、命令間call/jumpを使わずfall-throughする。operationごとの`ret`、スタック復元、エピローグは設けない。操作内分岐は生成metadataに基づいてtrace compile時に再配置する。未接続chain targetに到達した終端dispatcherだけがboundary metadata pointerを`ctx.rel`へ一時的に置き、`body_end_ip`を`ctx.ip`へ設定してtrace関数から`true`で戻る。分岐opcodeはJIT traceに含まれず、C++ Interpreter CPS handlerが一度実行する。`call`と`call_indirect`はtraceの最後のoperationとしてCPS handlerへtail-callする。x64 stencilはcommon code相対位置にあるtable pointerを介して、Runtime API tableに登録したTier 2 CPS handlerを添字で引く。呼出し意味論は[`interpreter.md`](docs/components/tier2_runtime/interpreter.md)を正本とする。その他のRuntime APIがfalseを返す場合はabort slotがtraceを終了し、C++ dispatcherがCPS stateを処理する。ARMv8-Mの論理入口、table参照幅と対象ABIはTBDとする。

Interpreter opcode handlerの型は`void (execution_context*, const uint8_t*, uint32_t*, const uint8_t*)`で統一する。4番目の`rel`はInterpreter注釈プールを指すバイトポインタである。Interpreter Runtime APIは`bool (execution_context*, const uint8_t *&, uint32_t *&, uint32_t *&, const uint8_t *&)`、JIT Runtime API（`{JIT_RuntimeAPI}`）は`bool (execution_context*, uint32_t *&, uint32_t *&, const uint8_t*)`を使う。 <!-- definition: {JIT_RuntimeAPI} --> `true`は通常継続、`false`はAPIがtrapまたはCPS exitを`execution_context`へ記録して後続命令を実行してはならないことを示す。Interpreter APIは命令カーソル`ip`、スタック位置`sp`、ローカル基点、`rel`を参照で受け取り、命令カーソルと注釈カーソルを更新する。JIT APIはpool内の操作引数recordを値として受け取る。recordのimmediate payloadはRuntime APIごとの型付き値であり、終端recordに置くannotation byte offsetから関数所有のInterpreter注釈プールを参照できる。Interpreter opcode handler表はCPS handlerだけを保持し、JIT API関数ポインタを含めない。JIT APIのwrapperとopcode対応表はTier 3 JITが所有する。Interpreter handlerはRuntime APIが`true`を返した場合だけ更新済みの`ip`と`rel`を含むCPS継続へ末尾呼出しする。JIT Runtime API stencilは`true`なら後続operationへfall-throughし、`false`ならabort slotへ分岐する。Runtime APIは次のopcodeをdispatchしない。

### x64実行環境のトレースヘッダ

以下はWindows x64およびSystem V AMD64で共通に使用する物理配置である。ARMv8-Mの配置、命令列、ABIはすべてTBDであり、x64の確認結果から推定しない。物理ポインタを格納する欄は64ビットとし、32ビットのWASM PC欄と混同しない。

| オフセット | フィールド | サイズ | 用途 |
| :--- | :--- | ---: | :--- |
| `+0x00` | `chain_target_addr` | 8バイト | 共通chain dispatcherが読み取る次traceのbodyの機械語アドレス。dispatcherは共通入口のcall箇所へtail-jumpする。未接続時は0 |
| `+0x08` | `common_code_relative` | 4バイト | ヘッダ位置からPIC共通コード基点への符号付き相対オフセット |
| `+0x0C` | `head_pc` | 4バイト | キャッシュ常駐entryのWASM開始PC |
| `+0x10` | `blob_bytes` | 4バイト | ヘッダからtrace末尾定数領域末尾までの配置サイズ |
| `+0x14` | `stack_words` | 1バイト | 当該トレースが書き込む最大スタック領域 |
| `+0x15` | `chain_words` | 1バイト | 現在のchain全体が必要とする最大スタック領域 |
| `+0x16` | `chain_body_count` | 1バイト | rootから終端までに含むtrace body数 |
| `+0x17` | `frame_depth` | 1バイト | 関数開始位置から当該ブロック開始位置までの制御フレーム深さ（最大 `FB_CONF_MAX_NESTING_DEPTH`） |
| `+0x18` | `exit_kind` | 1バイト | trace終了種別（normalは0、call_cpsは1、terminator_pendingは2。pendingは終了位置の制御opcodeをInterpreterが後で実行する種別） |
| `+0x19` | 予約 | 1バイト | `block_index`を2-byte境界へ揃える |
| `+0x1A` | `block_index` | 2バイト | Loaderの`block_index_t`基本ブロック番号（`0xFFFF`は無効） |
| `+0x1C` | `const_rel_offset` | 4バイト | ヘッダ先頭からtrace専用定数領域先頭までの符号なし相対offset |

ヘッダ全体は32バイトである。trace本体は `+0x20` から始まり、合成済みoperation列の直後に成功終端のret、abort終端のretを置く。trace本体は入口と出口の機械語を持たない。入口は共通コード領域の`fb_common_trace_entry`が担う。各operationの成功link slotは後続stencil本体に置き換えるため、命令間の接続命令やoperation単位のエピローグはtraceに残らない。定数領域先頭は`header + const_rel_offset`で求める。`blob_bytes`はヘッダ、trace entry/exitとoperation stencil、alignment padding、定数領域を含むtrace全体の配置サイズである。

ヘッダはJITコードから相対参照できるコード近傍に置く。生成コードまたは共通コードが読み取るtrace固有の値だけを保持する。
先頭PC、コード長、論理的な後続PCはcacheの管理情報に保持する。未使用のvariant予約欄は設けない。

trace末尾には境界metadataを置き、定数poolをその直後に置く。境界metadataは`body_end_ip`、`next_pc`、`loops_to`、`interpreter_rel_bytes`をu32、`block_index`をu16、`frame_depth`をu8で持ち、末尾の1バイトは`interpreter_terminator_pending`とし、その規則と位置は[`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md)の境界metadataの項を正本とする。全体は20バイトである。`interpreter_rel_bytes`はtrace内命令が消費するInterpreter注釈poolのbyte数であり、導出値recordと可変命令幅recordを含む。`body_end_ip`は関数内相対offsetで、x64終端dispatcherが`execution_context.ip`へ設定する。`next_pc`と`loops_to`はmodule内PCである。slot数fieldは設けない。共通chain dispatcherへの相対値は`common_code_relative`に記録する。ステンシルとchain dispatcherはPICとして生成し、GOT/PLTを使わない。生成器は出力objectのGOT/PLT sectionとコードrelocationを拒否する。

Runtime API 記録の形の正本は、この節である。記録は、u8 slot、u8 即値個数、u8 即値の幅の和、u32 `code_ip_t`、即値、末尾の u32 再開位置の順に並ぶ。即値の幅の和は、即値の幅の合計に末尾の再開位置の 4 byte を含めた値である。終端 API 記録では、先頭の即値が関数注釈 pool の byte offset（u32、命令の注釈の先頭）である。末尾の再開位置（u32）は、その命令を実行した後の注釈 pool の byte offset（命令の注釈の直後）である。Runtime API が Interpreter へ戻るとき、Interpreter はこの位置から続きを読む。スタブは、先頭 7 byte と即値の幅の和だけ `rel` を進める。

chainはtrace終端から共通コード領域のchain dispatcherへ移り、そのdispatcherが`chain_target_addr`（連結先のbody）を解決して共通入口のcall箇所へtail-jumpする経路を指す。dispatcherはchain先headerの`const_rel_offset`を読み、target traceの`rel`を初期化する。trace entry prologueはroot traceで一度だけ実行し、chain先で保存済み`r12`とstack frameを積み重ねない。targetがない場合、終端dispatcherはfooter pointerを`execution_context.rel`へ一時保存し、footerの`body_end_ip`を`execution_context.ip`へ設定して`true`で戻る。C++ dispatcherは復帰後にmetadataからstackとInterpreter `rel`を更新して`block_index`から次のBasicBlockを直接取得し、PC一致を検証する。分岐opcodeはtraceに含めず、C++ Interpreter CPS handlerへ渡す。chain終端は走査しない。root `rel`、stack上限、trace body数、exit kindはheaderから読む。

`common.S`から生成したchain dispatcherは、trace末尾から次traceへ移る処理だけを担う。C++ ABI probeから本番構造体offsetを取得してassembly macroへ渡す。共通コード領域にはdispatcherと、Tier 3 JITの実行状態が所有するRuntime API function pointer tableへの8-byte借用pointerを配置する。JIT初期化時にRuntime API entryとTier 2から渡す`call`／`call_indirect` CPS handler entryを表へ配置し、以後は読取り専用として扱う。表本体を共通コード領域や各cacheへ複製しない。表を借用するJIT実行状態は参照先より長く存続する。traceにはAPI/handlerの添字を格納し、関数の絶対アドレスを埋め込まない。Runtime API stencilはAPI table entryを呼び、通常継続なら後続JIT operationへ戻る。call stencilは専用handler entryへtail-callする。Interpreter opcode handler表はTier 2が所有し、JIT Runtime API tableと混同しない。local操作は操作・WASM型別のstencilを選び、word offsetは命令エンコーダがmemory instructionへ直接書き込む。

## 4. 動的モデル

### JITランタイム呼出し契約

Tier 2は共有実行状態とブロック開始PCを実行拡張へ渡す接続契約を定める。Interpreterは、ブロックの移りの判定点で、移り先のブロックの記録の印だけを読み、CPSを抜けるかどうかを決める。印の値は下記の正本に従う。Interpreterは印の値を比較し、常駐traceの参照があるときだけ、接続時に渡された表から trace の位置を得る。印を読むのは、JIT が接続された dispatch だけである。dispatcherは、抜けた先のブロック先頭で記録の印を読み、常駐traceの参照があるときだけ、その参照のtraceの入口を実行する。JIT拡張はキャッシュ検索を行わず、chain終端の反映と履歴記録を内部で完了する。Tier 2へJITキャッシュ配置や置換状態を公開しない。

ブロック記録の`jit_cache_reference`（1バイト）の値は次のとおりである。`FB_NATIVE_JIT_NO_TRACE_OBSERVED`（0xFF）は、traceがなく、先頭のカードがtrackableであることを示す。観測が要る。`FB_NATIVE_JIT_NO_TRACE_UNOBSERVED`（0xFE）は、traceがなく、観測が要らないことを示す。Loaderの既定値である。それ以外の値は常駐traceの参照であり、JIT拡張の内部の位置を示す。書き手はJIT拡張だけである。traceの設置・破棄、カードのtrackableの変化の各契機で、同じ手続きが印の値を決める。JIT拡張を閉じると、全記録はUNOBSERVEDへ戻る。

providerは接続時に次の値を一度だけ渡し、接続の後で変えない。共通のtrace entry addressは`trace_entry_address`、コード領域の先頭は`code_base`、参照から header の位置を引く表は`trace_offsets`（枠ごとに`code_base`からの16 bitのoffset）である。参照が示す枠のheaderは`code_base + trace_offsets[参照]`にある。表はJITが所有し、書くのは記録を書く手続きの1か所だけである。観測callbackは0または1を返す（0は観測対象外）。境界処理callbackは1を返す。これ以外は内部接続契約の違反として呼出し直後でassertし、WASMのtrapや継続可能な内部エラーへ変換しない。callbackの実行はassert式の外へ置く。

ネイティブdispatchには`execution_context`、値スタック基点、ローカルスタック基点、実行拡張を渡す。命令位置、コードと制御状態、スタック位置と容量、local base、関数番号、yieldしきい値はInterpreterが所有する実行状態から取得する。個別の呼出し構造体へ複製しない。戻り値はstatusとABIエラーだけを持ち、命令位置、スタック位置、trap codeは`execution_context`を唯一の格納先とする。実行拡張は`owner`、本体実行入口、観測入口で構成する。観測入口はInterpreterが対象ブロックを実行した後に候補判定と履歴記録を一度に行う。候補マスクと履歴配列をInterpreterへ渡さず、複製もしない。

CallFrameとCallStackの配置、容量、および関数呼出し時の状態管理は[`interpreter.md`](docs/components/tier2_runtime/interpreter.md)を正本とする。本仕様はInterpreterが所有する構造体のlayoutを重複定義しない。関数viewはLoaderが求めた`max_operand_stack_words`を32bit語単位で保持し、x64上で72バイトである。関数入口で引数を除いたSPを基準にこの最大高を検査し、関数途中のtrace入口では容量を再検査しない。定数バッファ参照、関数参照、モジュール参照はWASMコードと関数メタデータを渡す非所有の標準レイアウト構造体である。この境界に文字列、`std::vector`、仮想関数、例外を含めない。 `{META_NoStdVector}`

実行時のオペランド領域とローカル値領域は、固定容量の構造体と配列として配置する。値領域は`WASM_VALUE_SLOT_BYTES`の境界に配置した型情報を持たない32bit word配列であり、WASMのi32/f32は1 word、i64/f64は2 wordを使う。各関数のローカル値も同じ32bit wordを連続して使う。Loaderは各ローカルの先頭word offsetとWASM型を関数実行viewへ渡す。幅は保持済みWASM型から導出し、サイズ表を別に保持しない。JITの`local.get/set/tee`はoperationとWASM値型に対応するstencilへlowerする。local indexはtrace compile時にLoaderのoffset表から解決し、そのword offsetを命令エンコーダがtrace codeへ直接書き込む。offset別stencil variant、local Runtime API、実行時`rel`読出しは使わない。実行時の値には型tagを付けない。制御分岐はLoaderが生成した注釈済みedgeを使い、実行時ControlFrameを保持しない。 `{META_NoStdVector}`

関数の引数・戻り値バッファも同じ原則で扱う。実行要求と結果はバッファのアドレスと要素数だけを渡し、戻り値型や型タグを保持しない。呼び出し側が関数シグネチャを知っているため、必要なスロット幅と解釈は呼び出し側で決める。

## 5. インターフェース定義

### 委譲シグネチャ

JIT trace entryは`bool (execution_context*, const uint8_t*, uint32_t*, const uint8_t*) noexcept`を使う。`true`なら通常完了、`false`ならtrapまたはCPS exitであり、`execution_context`の終了状態と一致する。値スタックのsp（`FB_SP_REG`）の正本は`execution_context.stack_pointer`であり、trace本体（連結先を含む）が`fb_trace_return_success`で書き戻す。InterpreterとJITのRuntime APIシグネチャおよび定数領域のrecord形式は第3節の定義に従う。両者のopcode handler/API tableは独立している。

Runtime APIはtrace operationとして共通コード領域のtableから呼び出す。API固有の実行状態・trap状態を`execution_context`へ反映し、`true`で後続JIT operationへ継続する。`false`ではJIT stencilが後続operationを実行せずabort slotへ分岐し、RuntimeEngineが`execution_context`の終了理由を判定する。 `{JIT_RuntimeAPI}`

## 6. 制約達成の方策

ABIは標準レイアウト、固定サイズ、固定オフセットを維持する。x64以外のポインタ幅とレイアウトは未確定であり、確定前に他アーキテクチャの互換性を主張しない。

## 7. 形式検証・テスト仕様との対応

### 7.1 検証対象の不変条件
本書で定めた状態、境界、所有権、およびエラー処理を検証対象とする。

### 7.2 検証モデルと反証可能性
独立した形式検証モデルは設定しない。x64 ABIの物理サイズ、オフセット、呼び出し結果はテスト仕様と実行テストで検証する。

### 7.3 テスト仕様書との連携
対応するテスト仕様は[jit_runtime_test_spec.md](docs/qa/tier3_plugins/jit_runtime_test_spec.md)である。テストケースIDと実行可能テストは同仕様を正本とする。

### 7.4 既知の制限・対象外
ホスト実機依存の挙動、未実装アーキテクチャ、およびテスト仕様が明示する対象外条件は未検証として扱う。

## 8. 設計判断と参考実装

特記すべき独立したADRはない。採用方針は本書の各契約節に記載する。
