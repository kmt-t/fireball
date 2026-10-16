# JIT 実行コンテキスト ABI 契約

<!-- evidence:
     contract-only: true
     test: docs/qa/tier3_plugins/jit_runtime_test_spec.md
-->

<!-- traceability: {ContextPointerRegister} {PositionIndependentCode} {JIT_RuntimeAPI} {JIT_RegisterMapping} -->

## 1. コンセプト

この文書は、Tier 2ランタイムとTier 3 JIT拡張が共有するネイティブABIを定義する。Interpreter handlerは`execution_context* ctx`、命令カーソル`ip`、値スタック位置`sp`に、定数領域ポインタ`rel`を加えた4引数を使う。`ctx`、`ip`、`sp`は実行状態を表す3論理引数（`{CPS_3Args}`）である。 <!-- definition: {CPS_3Args} --> JIT trace entryも`rel`を含む4引数を使う。Interpreterの`rel`は関数所有の注釈プールを進む。x64 JITのconstant poolはtraceごとに一つのheader相対参照から始まり、Runtime API recordを続ける。recordはu8 API slot、u8 immediate count、u8 immediate byte span、u32 `code_ip_t` source IP、命令型に応じた幅のimmediate payloadから成る。数値即値とlocal offsetはtrace compile時に機械語へ埋め込む。trace内のInterpreter注釈bytesは複製せず、終端Runtime API recordに関数のannotation pool内byte offsetを一度だけ持つ。Runtime API recordを読むたびJITの`rel`をそのspan分進める。trace entryとchain先はpool anchorからrecord先頭を設定し、trace exitは同じanchorからtrace headerを解決する。pool footerやoperationごとのheader参照は設けない。操作間の継続先deltaも格納しない。trace実行中に次のWASM opcodeや次操作を取得しない。ローカルフレーム基点は`ctx.local_base`から取得する。WASM値は共有オペランドスタックに保持する。Interpreter handlerは`void`を返し、状態と終了理由を`execution_context`に記録する。JIT trace entryは`bool`を返す。正常完了時は未接続chain targetに到達した終端dispatcherだけがboundary metadata pointerを一時的に`ctx.rel`へ置き、metadataの`body_end_ip`を`ctx.ip`へ設定して`true`を返す。JIT traceはopcodeごとに次PCへ進めず、Runtime API stencilはtrapを特定するためAPI呼出し前に現在命令のPCを記録できる。trap/CPS exit時は`false`を返し、Interpreter dispatcherは`execution_context`の終了理由を処理する。C++ dispatcherは正常完了metadataからstackとInterpreter `rel`を更新し、終端control handlerを処理してから`block_index`とPCを照合し、次BasicBlockを直接取得する。lookup時にchain終端を探索したり、終端状態をdescriptorへ複製したりしない。両者が呼ぶRuntime APIは`bool`を返し、`false`はtrapまたは境界停止後の継続を禁止する。共通コードへの相対値と定数領域配置情報はtrace headerに保持する。実行コンテキストはJITのルーティング情報を保持しない。


## 2. アーキテクチャ分類

本書はTier 2 Runtimeが所有する契約である。Tier 2 InterpreterとTier 3 JIT拡張がこのABIを利用する。


## 3. 静的モデル

確認済みx64実行構成の `execution_context` は固定フィールド順、144バイトの標準レイアウト構造体である。インタープリタ単独実行とインタープリタからJITへの遷移で `ctx` の型を変えない。ポインタは非所有であり、呼び出し側が参照先の寿命を保証する。ARMv8-Mの配置、ポインタ幅、サイズ、offsetはTBDである。 `{ExecutionContext_Layout}` `{META_ZeroCostAbstraction}`

### 整数型と座標系

ネイティブABIはC互換`typedef`を使う。同じ基底整数型でも、PC、関数コード内IP、コード範囲、ブロック番号、フレーム内word位置を別名で区別する。16bit値の無効値は`0xFFFF`、8bit値の無効値は`0xFF`とする。

| 型 | 基底型 | 用途と上限根拠 |
| :--- | :--- | :--- |
| `block_index_t` | `uint16_t` | BasicBlock番号。`0xFFFF`を予約し、最大65,535ブロック、最大有効番号65,534とする |
| `frame_word_offset_t` | `uint16_t` | ローカルフレーム内word offset。最大ローカル256個と引数64個が各2 wordでも640 wordで収まる |
| `annotation_offset_t` | `uint32_t` | 注釈pool内byte offset/count。16bitへ制限しない |
| `wasm_pc_t` | `uint32_t` | WASM module PC。ROM 96KBを扱うため32bitを維持する |
| `code_ip_t` | `uint32_t` | 関数コード内の実行IP・復帰IP |
| `code_offset_t` | `uint32_t` | コード領域の長さ・offset・命令span。容量拡張に備えて32bitを維持する |
| `function_index_t` | `uint16_t` | 最大32 importと256定義関数の合計288を収める |
| `type_index_t` | `uint16_t` | 型数上限256。将来の拡張に備えて16bitを維持する |
| `global_index_t` | `uint16_t` | global数上限32 |
| `local_count_t` | `uint16_t` | local数256、または最大640 frame word |
| `operand_words_t` | `uint8_t` | operand stack上限64値。i64/f64を2 wordで数えても最大128 word |
| `frame_depth_t` | `uint8_t` | 制御構造の入れ子上限32。CallStackの深さも32で、同じ幅に収まる |
| `instruction_width_t` | `uint8_t` | LEB128即値を含む命令の符号化幅。255 byte超過はロード時に拒否する |
| `jit_stack_words_t` | `uint8_t` | Interpreterの64値上限と2-word値幅から最大128 word |
| `jit_body_count_t` | `uint8_t` | JIT trace命令上限64 |
| `card_index_t` | `uint32_t` | JIT card番号。module長に応じて増えるため32bitを維持する |

値幅、フレーム深さ、命令幅、trace長の各縮小はreaderとJIT入口の上限検査で保証する。`block_index_t`の有効番号と無効値を混同しない。Native ModuleExecutionViewはPCからBasicBlock番号を引く専用索引を持たない。

### コンテキスト拡張

実行状態、コードビュー、Runtimeへ制御を返すときのスタック位置、CallStackビュー、値スタックとローカル値領域それぞれの基点・現在位置・上限、LOOP後方分岐カウンタ、借用中のリニアメモリビュー、現在のローカルフレーム基点、Interpreter注釈プールカーソル、現在のBasicBlock indexを `execution_context` に置く。実行時制御フレーム配列と書き込み専用stack checkpointは持たない。x86-64のコンテキストは144バイトである。CallStackの現在サイズはCallStack自身から取得する。フィールド順と物理offsetは[`interpreter.md`](docs/components/tier2_runtime/interpreter.md)の`{ExecutionContext_Layout}`を正本とし、ここでは重複定義しない。

`loop_jump_count`は、実際に取得したLOOP後方辺を一度だけ数える。条件不成立の`br_if`は数えない。しきい値の正本はInterpreter設定の`FB_CONF_INTERPRETER_YIELD_THRESHOLD`である。`if`、`br`、`br_if`はJIT traceから除外し、trace完了後にC++ Interpreter CPS handlerが処理する。分岐先へtrace chainしない。後方辺数がしきい値に達した境界ではyield statusを保持してC++ dispatcherへ戻る。RuntimeはそのstatusをCOOSへ渡す。

値スタックとローカル値領域は、それぞれ基点・現在位置・上限をポインタで保持する。関数呼出しの状態保存には`CallFrame.operand_stack_base`と実行コンテキストの現在位置を使い、重複checkpointを持たない。リニアメモリのホスト基点とサイズは`+0x60`と`+0x68`にある。この2フィールドはゲストアドレスの`mem_base`と32bitの`mem_size`から区別する。ホスト基点は非所有の借用値である。実行呼出し中は参照先の寿命を保証し、メモリ再配置後は基点とサイズを更新する。

共通chain dispatcherへの相対値はtrace headerの`common_code_relative`に保持する。共通chain dispatcherは`lir/x64/common.S`から生成し、traceごとに複製しない。共通コード領域にはchain dispatcherに続けて、Tier 3 JITが読み取り専用領域に所有するRuntime API function pointer tableへの8バイト借用ポインタを配置する。表本体は共通コード領域やJIT cacheへ複製しない。JIT operationがRuntime APIを必要とする場合は、opcodeに対応するtable entryを呼び出す。trace終端から任意関数へ直接tail-callする契約は設けない。 `{PositionIndependentCode}`

`execution_context` は144バイトである。CallStack深さ、命令位置、値スタック位置、trap状態を戻り値構造体へ複製しない。オペランドスタックをWASM値の格納先とする。標準レイアウトを維持する。

### x64 継続引数と assembly ABI
<!-- traceability: {JIT_RegisterMapping} {CPS_3Args} {JIT_RuntimeAPI} -->

Interpreter handlerとJIT trace entryは`ctx`、`ip`、`sp`、`rel`の順で4引数を受け取る。`ctx`、`ip`、`sp`が実行状態を表す3論理引数であり、`rel`は定数領域ポインタである。C++関数を呼ぶ側とRuntime API関数呼出しはC ABIに従う。手書きassembly stencilはABIの受け渡し位置を明示する。SysV AMD64では`ctx`, `ip`, `sp`, `rel`を`rdi`, `rsi`, `rdx`, `rcx`で受け取り、Windows x64では`rcx`, `rdx`, `r8`, `r9`で受け取る。ABI probeは本番C++関数型と構造体offsetを照合する。WASM仮想レジスタは導入しない。

`local_base`は`ctx`が指す`execution_context.local_base`から取得する。trace entryは`header + const_rel_offset`のpool anchorを受け取り、先頭4-byte header相対参照を飛ばしてRuntime API recordの先頭に`rel`を設定する。chain dispatcherはchain先headerからpool anchorを計算し、同じ初期化を行う。trace exitは`ctx.rel`に保持したpool anchorを使ってheaderを解決する。`ctx.rel`はpool anchorまたは通常のポインタとして扱う。chain接続時はroot entryのprologueを再実行せず、entry stencilのcall slotからtarget traceへtail-jumpする。これにより保存済み`r12`とstack frameをchain全体で共有する。成功接続slotを次operation本体で置き換え、命令間call/jumpを使わずfall-throughする。operationごとの`ret`、スタック復元、エピローグは設けない。操作内分岐は生成metadataに基づいてtrace compile時に再配置する。未接続chain targetに到達した終端dispatcherだけがboundary metadata pointerを`ctx.rel`へ一時的に置き、`body_end_ip`を`ctx.ip`へ設定してtrace関数から`true`で戻る。分岐opcodeはJIT traceに含まれず、C++ Interpreter CPS handlerが一度実行する。その他のRuntime APIがfalseを返す場合はabort slotがtraceを終了し、C++ dispatcherがCPS stateを処理する。ARMv8-Mの論理入口と対象ABIはTBDとする。

Interpreter opcode handlerの型は`void (execution_context*, const uint8_t*, uint32_t*, const uint8_t*)`で統一する。4番目の`rel`はInterpreter注釈プールを指すバイトポインタである。Interpreter Runtime APIは`bool (execution_context*, const uint8_t *&, uint32_t *&, uint32_t *&, const uint8_t *&)`、JIT Runtime APIは`bool (execution_context*, uint32_t *&, uint32_t *&, const uint8_t*)`を使う。`true`は通常継続、`false`はAPIがtrapまたはCPS exitを`execution_context`へ記録して後続命令を実行してはならないことを示す。Interpreter APIは命令カーソル`ip`、スタック位置`sp`、ローカル基点、`rel`を参照で受け取り、命令カーソルと注釈カーソルを更新する。JIT APIはpool内の操作引数recordを値として受け取る。recordのimmediate payloadはRuntime APIごとの型付き値であり、終端recordに置くannotation byte offsetから関数所有のInterpreter注釈プールを参照できる。Interpreter opcode handler表はCPS handlerだけを保持し、JIT API関数ポインタを含めない。JIT APIのwrapperとopcode対応表はTier 3 JITが所有する。Interpreter handlerはRuntime APIが`true`を返した場合だけ更新済みの`ip`と`rel`を含むCPS継続へ末尾呼出しする。JIT Runtime API stencilは`true`なら後続operationへfall-throughし、`false`ならabort slotへ分岐する。Runtime APIは次のopcodeをdispatchしない。

### x64実行環境のトレースヘッダ

以下はWindows x64およびSystem V AMD64で共通に使用する物理配置である。ARMv8-Mの配置、命令列、ABIはすべてTBDであり、x64の確認結果から推定しない。物理ポインタを格納する欄は64ビットとし、32ビットのWASM PC欄と混同しない。

| オフセット | フィールド | サイズ | 用途 |
| :--- | :--- | ---: | :--- |
| `+0x00` | `chain_target_addr` | 8バイト | 共通chain dispatcherが読み取る次trace entryの機械語アドレス。dispatcherはentry prologue後のcall slotへtail-jumpする。未接続時は0 |
| `+0x08` | `common_code_relative` | 4バイト | ヘッダ位置からPIC共通コード基点への符号付き相対オフセット |
| `+0x0C` | `head_pc` | 4バイト | キャッシュ常駐entryのWASM開始PC |
| `+0x10` | `blob_bytes` | 4バイト | ヘッダからtrace末尾定数領域末尾までの配置サイズ |
| `+0x14` | `stack_words` | 1バイト | 当該トレースが書き込む最大スタック領域 |
| `+0x15` | `result_words` | 1バイト | トレースが確定する結果ワード数 |
| `+0x16` | `chain_words` | 1バイト | 現在のchain全体が必要とする最大スタック領域 |
| `+0x17` | `chain_body_count` | 1バイト | rootから終端または最初の再訪までに含む相異なるtrace body数。循環時もchain更新で有限に再計算 |
| `+0x18` | `frame_depth` | 1バイト | 関数開始位置から当該ブロック開始位置までの制御フレーム深さ（最大32） |
| `+0x19` | 予約 | 1バイト | `block_index`を2-byte境界へ揃える |
| `+0x1A` | `block_index` | 2バイト | Loaderの`block_index_t`基本ブロック番号（`0xFFFF`は無効） |
| `+0x20` | `const_rel_offset` | 4バイト | ヘッダ先頭からtrace専用定数領域先頭までの符号なし相対offset |

ヘッダ全体は32バイトである。assemblyから生成したtrace entryは `+0x20` から始まり、追加のentry adapterを挟まない。entryは合成済みoperation列へ入る。各operationの成功link slotは後続stencil本体に置き換えるため、命令間の接続命令やoperation単位のエピローグはtraceに残らない。定数領域先頭は`header + const_rel_offset`で求める。`blob_bytes`はヘッダ、trace entry/exitとoperation stencil、alignment padding、定数領域を含むtrace全体の配置サイズである。

ヘッダはJITコードから相対参照できるコード近傍に置く。生成コードまたは共通コードが読み取るtrace固有の値だけを保持する。
先頭PC、コード長、論理的な後続PCはcacheの管理情報に保持する。未使用のvariant予約欄は設けない。

trace末尾には境界metadataを置き、定数poolをその直後に置く。境界metadataは`body_end_ip`、`next_pc`、`loops_to`、`interpreter_rel_bytes`をu32、`block_index`をu16、`frame_depth`と`result_words`をu8で持ち、20バイトである。`interpreter_rel_bytes`はtrace内命令が消費するInterpreter注釈poolのbyte数であり、導出値recordと可変命令幅recordを含む。`body_end_ip`は関数内相対offsetで、x64終端dispatcherが`execution_context.ip`へ設定する。`next_pc`と`loops_to`はmodule内PCである。slot数fieldは設けない。共通chain dispatcherへの相対値は`common_code_relative`に記録する。ステンシルとchain dispatcherはPICとして生成し、GOT/PLTを使わない。生成器は出力objectのGOT/PLT sectionとコードrelocationを拒否する。

chainはtrace終端から共通コード領域のchain dispatcherへ移り、そのdispatcherが`chain_target_addr`を解決してentry prologue後のcall slotへtail-jumpする経路を指す。dispatcherはchain先headerの`const_rel_offset`を読み、target traceの`rel`を初期化する。trace entry prologueはroot traceで一度だけ実行し、chain先で保存済み`r12`とstack frameを積み重ねない。targetがない場合、終端dispatcherはfooter pointerを`execution_context.rel`へ一時保存し、footerの`body_end_ip`を`execution_context.ip`へ設定して`true`で戻る。C++ dispatcherは復帰後にmetadataからstackとInterpreter `rel`を更新して`block_index`から次のBasicBlockを直接取得し、PC一致を検証する。分岐opcodeはtraceに含めず、C++ Interpreter CPS handlerへ渡す。lookupはroot entry address、root `rel`、root frame depth、chain全体のstack上限とtrace body数、root trace cookieを返し、chain終端を走査しない。

`common.S`から生成したchain dispatcherは、trace末尾から次traceへ移る処理だけを担う。C++ ABI probeから本番構造体offsetを取得してassembly macroへ渡す。共通コード領域にはdispatcherと、Tier 3 JITが所有する読み取り専用のJIT Runtime API function pointer tableへの借用アドレスを配置する。表本体を各キャッシュへ複製しない。プラグインの保存領域は、表を借用する全JIT実行状態より長く存続する。Interpreter handler表から関数ポインタを流用しない。Runtime API stencilはAPI table entryを呼び、通常継続なら後続JIT operationへ戻る。local操作は操作・WASM型別のstencilを選び、word offsetは命令エンコーダがmemory instructionへ直接書き込む。

## 4. 動的モデル

### JITランタイム呼出し契約

Tier 2は共有実行状態とブロック開始PCを実行拡張へ渡す接続契約を定める。JIT providerは2-bit card-state表の読み取り専用ビューとカード範囲を渡す。Interpreter dispatcherはブロック開始時にカード状態を読み、`COMPILED`以外ではlookup関数を呼ばない。ビューの領域はdispatch期間中JIT拡張が保持する。JIT拡張が正確なPCのキャッシュ検索、chain終端の反映、履歴記録を内部で完了する。Tier 2へJITキャッシュ配置や置換状態を公開しない。

providerのlookupと観測callbackは0または1を返す。0はcache missまたは観測対象外を示す。境界処理callbackは1を返す。これ以外は内部接続契約の違反として呼出し直後でassertし、WASMのtrapや継続可能な内部エラーへ変換しない。callbackの実行はassert式の外へ置く。

ネイティブdispatchには `execution_context`、値スタック基点、ローカルスタック基点、実行拡張を渡す。命令位置、コードと制御スタックの情報、スタック位置と容量、local base、関数番号、yieldしきい値は `execution_context` と現在のCallFrameから取得し、別の呼出し構造体へ複製しない。戻り値はstatusとABIエラーだけを持ち、命令位置、スタック位置、trap codeは `execution_context` を唯一の格納先とする。実行拡張は `owner`、本体実行入口、観測入口で構成する。観測入口はInterpreterが対象ブロックを実行した後に候補判定と履歴記録を一度に行う。候補マスクと履歴配列をInterpreterへ渡さず、複製もしない。

CallFrameは関数実行view、ローカル基点、オペランドstack基点、復帰IP、JIT境界PC、u16復帰block indexを持つ40バイトの固定構造体である。関数番号は同じ関数を示すfunction viewの`function_index`から得る。コード・ローカル配置・引数・結果の静的値はfunction viewから得る。viewはLoaderが求めた`max_operand_stack_words`を32bit語単位で保持し、x64上で72バイトである。関数入口で引数を除いたSPを基準にこの最大高を検査し、関数途中のtrace入口には容量の再検査を置かない。`rel`はCallFrameに保存せず、Interpreterは基本ブロック開始時に注釈offsetから求める。関数呼出しスタックは32個の記述子とu8深さを保持する固定長配列で、全体は1,288バイトである。定数バッファ参照、関数参照、モジュール参照はWASMコードと関数メタデータを渡す非所有の標準レイアウト構造体である。この境界に文字列、`std::vector`、仮想関数、例外を含めない。 `{META_NoStdVector}`

実行時のオペランド領域とローカル値領域は、固定容量の構造体と配列として配置する。値領域は`WASM_VALUE_SLOT_BYTES`の境界に配置した型情報を持たない32bit word配列であり、WASMのi32/f32は1 word、i64/f64は2 wordを使う。各関数のローカル値も同じ32bit wordを連続して使う。Loaderは各ローカルの先頭word offsetとWASM型を関数実行viewへ渡す。幅は保持済みWASM型から導出し、サイズ表を別に保持しない。JITは関数引数を含むローカル数が16以下の場合を対象とし、`local.get/set/tee`はoperationとWASM値型に対応するstencilへlowerする。local indexはtrace compile時にLoaderのoffset表から解決し、そのcompact word offsetを命令エンコーダがtrace codeへ直接書き込む。offset別stencil variant、local Runtime API、実行時`rel`読出しは使わない。関数引数を含むローカル数が17以上のtraceは直接local stencilの対象外である。実行時の値には型tagを付けない。制御分岐はLoaderが生成した注釈済みedgeを使い、実行時ControlFrameを保持しない。 `{META_NoStdVector}`

関数の引数・戻り値バッファも同じ原則で扱う。実行要求と結果はバッファのアドレスと要素数だけを渡し、戻り値型や型タグを保持しない。呼び出し側が関数シグネチャを知っているため、必要なスロット幅と解釈は呼び出し側で決める。


## 5. インターフェース定義

### 委譲シグネチャ

JIT trace entryは`bool (execution_context*, const uint8_t*, uint32_t*, const uint8_t*) noexcept`を使う。`true`なら通常完了で、未接続chain targetに到達した終端dispatcherだけが終端metadata pointerを一時的に`execution_context.rel`へ格納し、`execution_context.ip`へ終端命令位置を設定する。`false`ならtrapまたはCPS exitであり、Interpreter dispatcherは後続実行を中断する。Interpreter Runtime APIは`bool (execution_context*, const uint8_t *&, uint32_t *&, uint32_t *&, const uint8_t *&)`、JIT Runtime APIは`bool (execution_context*, uint32_t *&, uint32_t *&, const uint8_t*)`を使う。Interpreter側の`rel`は関数注釈プールを指し、JIT側の`rel` cursorはtrace pool anchor内のRuntime API recordを指す。recordはu8 slot/count/span、`code_ip_t` source IP、型幅のimmediate payloadから成る。traceごとのanchorはheader相対参照を一つ持ち、終端API recordのannotation offsetから関数注釈プールを読む。InterpreterとJITのopcode handler/API tableは独立している。

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
