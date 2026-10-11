# JIT コンパイラ コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} {VERIFY_BENCHMARK}
<!-- evidence:
     implementation: experiments/pysim/native/tier3_plugins/jit/trace_compiler.cxx
     formal: formal/jit_cache_model.py
     benchmark: ../../../experiments/pysim/benchmarks/jit/bench_jit.py
     benchmark: ../../../experiments/pysim/benchmarks/jit/bench_x64_stencil_instructions.py
     test: docs/qa/tier3_plugins/jit_compiler_test_spec.md
     test: docs/qa/specs/wasm_instruction_set_test_spec.md
-->

## 1. コンセプト
<!-- traceability: {LowLatencyJIT} {JIT_AssemblyStencilCodegen} {SimpleJITArchitecture} {PositionIndependentCode} -->
JIT Compilerは、Tier 2 Interpreterの任意拡張としてWASM命令列をネイティブコードへ変換するTier 3コンポーネントである。
x64の操作、trace entry/exit、共通chain dispatcherを手書きアセンブリで記述する。
Clang integrated assemblerがPIC objectを生成し、`objcopy`が`.text`をraw binaryとして抽出する。
生成器はsymbol範囲と分岐metadataを含むC++ byte array headerを出力する。

実行時のC++コンパイラはWASM命令をデコードして操作variantを選ぶ。
操作の成功接続slotを次のステンシル本体で置き換え、命令列を一つのflatなtrace bodyへ合成する。
slot命令はtraceに残らないため、操作ごとのreturn、エピローグ、call、jumpを通らず次の操作へfall-throughする。
trap経路はtrace共通のabort resultへ分岐し、state returnとexit処理はtrace単位で行う。
生成traceはopcodeや次操作を読み取らず、operation stencilを関数ポインタでdispatchしない。
Runtime API操作は共通コード領域のAPI tableからAPIを呼び、APIが通常継続を返すと合成済みtraceの後続stencilへ進む。

現行のx64 trace compilerは数値即値をtrace compile時に命令へ埋め込み、local offsetは命令エンコーダがtraceへ直接書き込む。
trace末尾の定数領域は、Runtime APIへ渡す即値recordと最後のtrace header参照を保持する。
即値を`rel`から読む方式はデータロードと`rel`更新を要する。
一方で、即値の幅によってはtrace codeを小さくできる候補である。
現行実装は即値をコードへ埋め込む。
別方式を採用する場合は、命令数だけで断定せず、代表traceのコードサイズと実測性能で比較する。
WASM値は共有オペランドスタックに保持する。
アセンブリ操作はSysV AMD64またはWindows x64の引数レジスタを明示し、caller-save scratch registerを使う。
`i32.const`と直後の整数演算、localアクセスと整数演算、`drop`、`i32.eqz`は一部を融合する。
Loaderが受理したopcodeに直接assembly stencilがない場合は、対応するJIT Runtime API stencilへlowerする。APIが通常継続を返す限り、後続operationも同じflat trace内で実行する。WASM命令セットの範囲外や不正な命令はLoaderが拒否する。
Tier 3のC++ JitRuntimeはコンパイラを直接呼び、生成結果を所有キャッシュへ登録する。
WASMコードと実行メタデータはLoaderからRuntime構成へ借用ビューとして渡す。
Tier 2にJIT専用APIを追加しない。
ARMv8-Mの実装と資源予算はTBDである。

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} {JIT_AssemblyStencilCodegen} -->
本コンポーネントは **Tier 3 Plugin 内の詳細リーフコンポーネント (Leaf Component)** である。Tier 2 Runtime / Interpreterへ任意接続するJIT pluginのうち、build-time stencil生成とtrace単位のネイティブコード構築を担当する。ホットスポット状態とキャッシュ管理は同じplugin内の [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) が担当する。

### 2.1 JIT サブシステムのデコンポジション
<!-- traceability: {JIT_AssemblyStencilCodegen} {SimpleJITArchitecture} {JIT_MultiBuffer_Cache} -->
JITサブシステムは、以下の2つの独立した設計書に責務を分離して構成される。

- **[jit_compiler.md](docs/components/tier3_plugins/jit_compiler.md)**: C++のtrace構築と、ビルド時にアセンブルした機械語ステンシルの連結を担当する。
- **[jit_runtime.md](docs/components/tier3_plugins/jit_runtime.md)**: 実行履歴監視・ホットスポット判定、PC-アドレス変換検索、および 3面キャッシュローテーションを担当する。

## 3. 静的モデル

### 3.1 データ構造
- **`trace_compiler.cxx`**: C++フロントエンドがWASM命令をデコードし、選択したステンシル本体を接続slotで連結して固定長traceへ合成する。成功経路は命令間の転送命令なしでfall-throughする。直接stencilを持たない有効opcodeはRuntime API stencilへlowerする。対応命令数とRuntime API引数record数から定数poolの使用量に上限を設け、traceごとの定数領域はその上限内に収める。WASM検証違反はLoaderが拒否し、ABI不整合は構成時に検出する。
- **`JIT_AssemblyStencilCodegen`**: アセンブリsourceから操作、trace entry/exit、terminal result、共通chain dispatcherをビルド時にraw binaryとmetadataへ変換する責務を表す。実行時trace builderは生成済みraw binaryを固定長bufferへ組み立てる。
- **共通コード領域**: x64 JIT領域には`lir/x64/common.S`から生成したchain dispatcherを配置する。配置offsetはTier 1構成から渡し、C++ ABI probeから得た構造体offsetをassembly macroへ反映する。共通chain dispatcherはopcodeを判定しない。x64の領域サイズとoffsetは [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) および [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) を正本とする。ARMv8-Mのサイズと配置はTBDである。 `{JIT_MultiBuffer_Cache}`
- **操作ステンシル (`lir/x64/operations.S`)**: 算術、local操作、融合操作、Runtime API呼出しをアセンブリで定義する。生成器はPIC raw binaryとsymbol・分岐・即値・命令slot metadataを出力する。実行時trace builderは操作コードを連結し、即値slotを埋め、local memory instructionをエンコードする。ARMv8-Mの物理仕様はTBD。
- **JIT トレースヘッダ (`jit_trace_header`)**: 各ネイティブtraceの先頭に配置する実行時ヘッダである。x64では40バイトで、chain target、chain dispatcherへの相対値、配置サイズ、stack境界値、基本ブロック番号、定数領域offsetを保持する。bodyはヘッダ直後の`+0x28`から始まる。定数領域にはRuntime APIの引数recordを使用順に置き、pool末尾の4-byte相対値からtrace exitがheaderを得る。定数poolにslot数footerは置かない。chain先ではtarget headerのoffsetから`rel`を初期化する。

#### コンパイラ入口
<!-- traceability: {JIT_AssemblyStencilCodegen} {META_ContractImplSplit} -->
JitRuntimeはLoader所有のWASMコードと関数実行metadataから適格な基本ブロックを選ぶ。
C++フロントエンドは命令をデコードし、ステンシルと定数を固定長作業領域へ記録する。
trace builderは各operationの成功link slotを除去して、次の操作本体を同じ位置へ配置する。
slotはbuild artifact上の接続markerであり、実行traceには残らない。
operation本体にreturnやスタック復元を置かず、成功経路を次の操作本体へ直接fall-throughさせる。
trace entryのABI prologueとtrace単位のterminal resultだけが外部呼出し境界を持つ。
Runtime API引数は定数領域へ配置し、数値即値とlocal offsetはコードslotへ焼き込んで完成したtraceをJIT cacheへ登録する。
単体コンパイラ入力と状態検査はQA専用ライブラリで扱う。

### 3.2 内部ブロック図
```mermaid
flowchart TD
    subgraph JIT_Compiler_Core
        Pipeline[jit_pipeline]
        Engine[Native Stencil Linker]
        NativeCompiler[C++ x64 Trace Compiler]
    end

    subgraph Runtime_Interface
        Runtime[jit_runtime]
        Cache[Active Code Cache]
    end

    Pipeline --> Engine
    NativeCompiler -->|decode and select operation stencils| Engine
    Engine -->|link native stencil body and constant pool| Cache
    Pipeline -->|register entry| Runtime
```

### 3.3 主要なクラス・構造体・定数

#### コンパイル単位とインタープリタ協調方針
<!-- traceability: {JIT_RuntimeAPI} {LowLatencyJIT} {PositionIndependentCode} {SimpleJITArchitecture} -->
- **関数/モジュール一括コンパイルの禁止**: 関数全体やモジュール全体の事前一括コンパイルは行わない。
 - **ベーシックブロック/トレース単位コンパイル**: カードマーキング表で HOT（`10`）に達した直線命令列（基本ブロック / トレース）のみを対象とする。スケジューラのidle時等にC++ trace builderがbuild済みstencilを組み立てる。
- **制御フロー境界**: 制御終端命令そのものはトレース本体で実行しない。chain dispatcherは同一深さの直線後続traceと、loaderが解決した静的な前方`BR`のtarget traceへ接続できる。chain終端traceのmetadataにある`frame_depth`をC++ Interpreter dispatcherがtrace復帰後に適用する。`BR_IF`、`BR_TABLE`、後方`BR`、`BLOCK`、`LOOP`、`IF`、`ELSE`、`END`、call、return、および未接続の分岐はC++ Interpreter handlerが処理する。後方分岐数もhandlerが記録するため、yieldしきい値を迂回する直接loop back-edge chainは作らない。
- **実行ABI**: Interpreter handlerは`ctx`, `ip*`, `sp*`, `rel*`の4引数で`void`を返す。JIT trace entryも同じ4引数で`bool`を返す。正常完了時は未接続chain targetに到達した終端dispatcherだけがmetadata pointerを一時的に`ctx.rel`へ渡し、`body_end_ip`を`ctx.ip`へ設定する。先頭3引数は実行状態、`rel`は定数領域を指す`const uint8_t*`である。JITの`rel`はtrace固有定数領域を順次読むため、各trace入口およびchain先で領域先頭へ設定する。ローカル基点は`ctx.local_base`から取得する。Interpreter dispatcherは`true`後にmetadataからstack・control frame・Interpreter `rel`を確定し、終端命令を処理して次のBasicBlockを解決する。`false`はtrapまたはCPS exitを示す。

##### 3.3.1 現行x64の制御終端処理とchain dispatcher
<!-- traceability: {JIT_AssemblyStencilCodegen} {JIT_LazyChaining} {PositionIndependentCode} -->

現行x64実行系では制御終端命令をトレース本体から除外する。`BR_IF`、`BR_TABLE`、後方`BR`、`BLOCK`、`LOOP`、`IF`、`ELSE`、`END`、call、returnはC++ Interpreter handlerが処理し、条件値、スタック巻き戻し、動的な制御更新、遷移先決定を行う。戻り値を残さない静的な前方`BR`だけはchain可能である。loaderが関数開始からのframe depthを各blockに確定し、各traceの境界metadataへ記録する。C++ Interpreter dispatcherはchain終端metadataの`frame_depth`から期待する制御フレーム終端を求め、trace関数復帰後に制御スタックサイズをその境界と整合させる。後方分岐数はC++ branch handlerが記録する。Interpreterが設定を所有し、C++ dispatcherは `FB_CONF_INTERPRETER_YIELD_THRESHOLD` に従って続行またはyieldする。

trace chainは、互換な直線後続または静的前方`BR`のtarget traceが常駐する場合に、共通コード領域のchain dispatcherがTraceヘッダからtarget bodyを読み、そこへtail-jumpする経路である。未接続chain targetに到達した終端dispatcherだけが境界metadata pointerを`ctx.rel`へ一時保存し、`body_end_ip`を`ctx.ip`へ設定して`true`を返す。C++ Interpreter dispatcherがmetadataのframe-depthを適用する。C++ handler実行後にC++ dispatcherが次traceを検索・起動する遷移はchainではない。chain dispatcherはopcodeごとのhandlerを共通化せず、分岐条件や分岐命令を実行しない。

##### 3.3.2 ARMv8-Mの物理仕様（TBD）

ARMv8-M向け命令Stencil、ABI、Trace header、共通コード領域とchain dispatcher、メモリ保護方式はすべてTBDとする。未確定項目は [`jit_stencil_catalog.md`](docs/specs/jit_stencil_catalog.md) に集約する。

##### 3.3.3 Runtime API ステンシル
<!-- traceability: {JIT_RuntimeAPI} -->

C++ trace compilerは操作を直接実行するassembly stencilか、共通コード領域のRuntime API tableを呼ぶ`runtime_api` stencilへlowerする。Runtime API呼出しもJIT trace bodyの一操作であり、インタープリタへ処理を渡すフォールバックではない。

APIを呼ぶ操作では、コンパイラがopcodeに対応するAPI table index、元の命令PC、共有stackの前後状態、および操作別即値をtrace末尾の定数poolへ出力する。Runtime APIが第4引数の`rel`から即値を読む契約を保つため、このrecordはpoolに置く。`operations.S`の`runtime_api` stencilは共通コード領域からAPIを取得して呼び出す。ローカル操作はRuntime APIを呼ばない。WASM型とcompact byte offsetから型・操作別の単一テンプレートを選び、x64命令エンコーダがローカルbase registerと`disp8`をtraceへ直接書く。どちらもtraceへコンパイルされ、`ctx.ip`を進めない。Runtime APIを呼ぶ操作では、APIがtrapやruntime境界を記録しなければstencilは後続の合成済みoperationへfall-throughする。APIがCPS exitを記録した場合だけtrace共通abort経路へ移り、C++ dispatcherがそのexit種別を処理する。

Interpreterでも同じRuntime APIを使う。Interpreter側のopcode handlerは統一された`(ctx, instruction_ip, sp, rel)` prototypeを持ち、テンプレート`h_runtime_api`がopcode固有のRuntime APIを呼んでからCPS継続へ渡す。JIT stencilから呼ぶAPIはCPSを実行せず、次のoperationを選択しない。

| 命令経路 | 実行方法 | 次の処理 |
| :--- | :--- | :--- |
| 直接assembly variantがある | 型・offset・融合形に対応するstencilをtraceへ合成する | 次のstencilへfall-throughする |
| ローカル操作 | 型・操作別テンプレートを合成し、x64命令エンコーダがmemory instructionと`disp8`を書き込む | 次のstencilへfall-throughする |
| 直接assembly variantがない有効opcode | `runtime_api` stencilが共通API tableから対応するAPIを呼ぶ | 通常継続なら同じJIT traceの次のstencilへ進む。trapまたは境界exitならtraceを終了する |

Loaderが受理した各opcodeは直接assembly variant、直接生成経路、またはJIT Runtime API tableのいずれかでtraceへlowerする。`0xFC`の対応サブオペコード集合は [`wasm_instruction_set.md`](docs/specs/wasm_instruction_set.md) が定め、集合外はWASM命令セットの非対応機能としてLoaderが拒否する（{WasmFCSubset}）。

**ABI 規約と境界チェック (`GOTCHA-JITC-01`, `03`, `05`)**:
- **スタック状態**: すべてのWASM値を共有オペランド領域へ命令ごとに確定する。基本ブロック終端、API境界、トラップ時に別の値キャッシュを書き戻す処理は設けない。 `{ExecutionContext_Layout}`
- **呼出し境界 ({GOTCHA-JITC-01}, `03`)**: Interpreter handlerとJIT trace entryは`ctx`, `ip`, `sp`, `rel`の4引数を受け取る。`rel`は定数プールポインタである。assembly sourceはSysV AMD64とWindows x64の引数レジスタを明示し、ABI probeが関数型と構造体offsetを照合する。ARMv8-MのABIはTBDとする。 <!-- definition: {GOTCHA-JITC-01} -->
- **Runtime API呼出し**: opcodeからJIT Runtime API wrapperへの対応表とfunction pointer tableはTier 3 JITが所有する。Interpreter opcode handler表から構築しない。JIT stencilは対応APIを共通領域から呼び、API後に次の操作をディスパッチしない。
- **継続先と境界検証 (`GOTCHA-JITC-05`)**: trace chain先はtrace headerの`chain_target_addr`で表す。trace builderは操作本体の成功link slotを後続operationのbytesで置き換える。成功経路のslot命令は完成traceから除かれ、operationごとのreturnやエピローグはない。abort slotだけをtrace共通abort resultへ接続する。trace末尾poolには操作間deltaを置かない。コンパイラは出力範囲のoffsetとサイズ、操作内分岐の再配置を検証する。ARMv8-Mの配置方式はTBDとする。

#### ABIと共有値
<!-- traceability: {JIT_RegisterMapping} {ContextPointerRegister} {EnvironmentPointer} {PositionIndependentCode} -->
Interpreter opcode handlerとJIT trace entryは`ctx`, `ip`, `sp`, `rel`の4引数を使う。先頭3引数が実行状態、`rel`が定数プールポインタである。Interpreter handlerは`void`を返し、JIT trace entryは`bool`を返す。正常完了時は未接続chain targetに到達した終端dispatcherだけがmetadata pointerを一時的に`ctx.rel`へ渡し、`body_end_ip`を`ctx.ip`へ設定する。x64の関数型と構造体配置は [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) を正本とする。assembly sourceはSysV AMD64とWindows x64の引数レジスタへ直接合わせる。ARMv8-MのABIはTBDとする。

実行状態の3論理引数はInterpreterとJIT traceで `ctx`, `ip`, `sp` の順に共通し、各native entryの第4引数は定数領域ポインタ`rel`である。assembly sourceは対象x64 ABIの物理引数配置を明示し、callを含むRuntime API stencilは対象ABIの保存規則に従う。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| operation symbol | trace compile時に命令の型・offset・fusion結果で選択するステンシル名 | fixed array entry | build時にraw binary化したステンシル本体をtraceへ連結する |
| trace link metadata | 各stencilの成功slot、abort slot、trace entryのstart/exit slotと局所分岐 | build artifact | 成功slotを次の操作本体で置き換え、必要な局所分岐を再配置する |
| operation source | x64操作、trace entry/exit、共通chain dispatcherのアセンブリ定義 | build-time source | 共有ライブラリへ埋め込まず、ビルド時にraw binaryへ変換する |
| 位置独立性 (PIC) | 任意アドレス・キャッシュバンクで再コンパイル不要で動作 | 設計制約 | x64ではプロセス絶対アドレスを命令列へ埋め込まず、トレースヘッダ、共通領域オフセット、基底相対アドレス、`rel32` 分岐を用いる。ARMv8-Mの方式はTBD |

##### 操作状態の受け渡し
<!-- traceability: {JIT_RegisterMapping} {CPS_3Args} -->
JIT trace entryは`ctx`, `ip`, `sp`, `rel`を受け取る。
InterpreterはWASM値を共有オペランドスタックに保持する。
各操作stencilは`ctx`, `ip`, `sp`, `rel`を対象ABIの引数レジスタから受け取り、次のstencilへ同じレジスタでstateを渡す。
アセンブリ操作はcaller-save scratch registerだけを一時値に使う。
trace builderは各成功link slotを除いて後続operationを配置するため、合成後の成功経路に操作間call/jumpはない。
生成器は各operation stencilに`ret`、スタック操作、Runtime API stencil以外のcallがないことを検査し、操作単位のエピローグ生成を拒否する。
trapを検出したoperationはabort link slotから一つのtrace共通terminal resultへ移る。
trace entry wrapperはtrace bodyの実行結果を復元する。未接続chain targetに到達した場合だけ終端dispatcherが末尾境界metadata pointerを`ctx.rel`へ置き、`body_end_ip`を`ctx.ip`に設定して`true`を返す。trap/CPS exitではtrace entryが`false`を返す。Interpreter dispatcherは正常復帰後にmetadataから実行状態を確定し、終端命令と次BasicBlockを処理する。
ARMv8-MのABIはTBDである。

#### トレース境界不変条件とスタックフレーム整合性 (Trace Boundary Invariants)
<!-- traceability: {LowLatencyJIT} {PositionIndependentCode} {JIT_RuntimeAPI} -->
JIT トレースとインタープリタが共有オペランド領域上で相互運用するため、トレース境界不変条件（`{TraceBoundaryInvariant}`）を含む以下の4つの不変条件を厳格に保持する。JITからC++ Interpreter handlerへ戻る境界では共有オペランド領域と実行コンテキストを同期する（{GOTCHA-JITC-03}）。 <!-- definition: {TraceBoundaryInvariant} --> <!-- definition: {GOTCHA-JITC-03} -->

1. **基本ブロック入口の共有スタック契約**:
   - トレース入口の`sp`はBasicBlock開始時点の共有オペランドスタック位置を指す。WASM検証で有効なBasicBlock入口値は、トレースの先行命令で生成された値と同じく有効な入力である。
   - コンパイラは相対stack effectを追跡する。入口以前から存在する値を消費する操作で相対深さが負になっても、コンパイル拒否の理由にしない。読み書き位置は入口`sp`と現在の相対深さから生成し、結果の`sp`を共有領域へ反映する。
   - 直接stencilを持たない有効opcodeはRuntime API stencilから処理する。
2. **トレース境界でのメモリ同期不変条件 (Memory Synchronization at Trace Boundary)**:
- x64では、後続traceが共通コード領域のchain dispatcher経由で実行される場合、現在traceが確定した共有状態を次traceが引き継ぐ。C++ Interpreter handlerへ戻る境界では共有オペランド領域と実行コンテキストを同期する。ARMv8-Mの値保持方法と同期手順はTBDである。
3. **制御フロー・コール境界のインタープリタ委譲不変条件 (Control & Call Delegation Invariant)**:
- `BR`, `BR_IF`, `BR_TABLE`、構文デリミタ（`BLOCK`, `LOOP`, `ELSE`, `END`）、コール、およびreturnの終端命令は、トレース本体から除外し、InterpreterのC++ dispatcherから対応handlerへ渡す。handler後の次trace検索とtrace実行もInterpreter dispatcherが行う。RuntimeEngineはyieldなどの実行境界を進める。
- C++ dispatcherへ制御を渡した`BR` / `BR_IF` / `BR_TABLE`は対応handlerが制御frameとoperand stackを更新する。静的前方`BR`のtarget traceが直接chainされる場合、chain終端metadataに記録した最終frame深さをC++ dispatcherがtrace復帰後に適用する。C++ dispatcherは後方分岐カウンタがInterpreter所有のyieldしきい値へ届くまで、遷移先の常駐traceまたはC++ handlerを続けて実行する。このhandler後のC++ dispatcher継続はchainではない。
- chainは同一frame深さの直線後続traceが常駐する場合に限り、trace末尾から共通コード領域のassembly生成chain dispatcherへ移り、dispatcherがTraceヘッダのtarget bodyへtail-jumpする経路である。未接続時は終端dispatcherだけが現在trace末尾の境界metadata pointerを`ctx.rel`へ置き、`body_end_ip`を`ctx.ip`に設定して`true`でtrace関数を終了する。opcode別の分岐処理を共通dispatcherに持ち込まず、制御handlerを飛ばさない。
- C++ dispatcherはC++ Interpreter単独実行にも使う。しきい値はInterpreterが所有し、単独実行とHybrid JITで同じ `FB_CONF_INTERPRETER_YIELD_THRESHOLD` を使う。traceが未接続の場合は前方分岐を含む各分岐が対応handlerを経由し、トラップ・外部呼出し・関数完了は必要な早期境界としてRuntimeEngineへ返す。常駐targetへ接続した静的前方`BR`はchain dispatcherがtargetへ直接移る。
4. **共有スタック範囲不変条件 (Shared Stack Range Invariant)**:
   - Interpreterは全WASM値を共有オペランド領域に保持する。JITは定数畳込みや隣接命令の融合で実行時に不要な中間値を除去できる。融合後も生存する値は共有オペランド領域に置く。
   - コンパイラは、トレースが実際に使用する最大スタックワード数を `stack_words` としてヘッダに記録する。Runtime API operationが要求する入力・結果の領域も含める。融合により、この値はWASM意味論上の最大スタック深さ以下になることがある。
   - 入口以前から存在する値へのアクセスはWASM検証済みのstack shapeに基づき、トレース中に追加で確保する領域は共有スタック上限内へ書き込む。

#### JIT トレース物理メモリレイアウト (`jit_trace_header`)
<!-- traceability: {JIT_LazyChaining} {SimpleJITArchitecture} {PositionIndependentCode} -->
物理配置は対象ごとのABI契約へ委譲する。Windows x64およびSystem V AMD64の16バイト配置は [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) に定義し、ARMv8-Mの物理配置と命令列はTBDである。これらの配置を一つの共通ヘッダとして扱ってはならない。

#### ネイティブトレースコンパイラ (`trace_compiler.cxx`)
<!-- traceability: {META_ZeroCostAbstraction} {JIT_MultiBuffer_Cache} {Runtime_BumpAllocator} -->
本実装はClang 17以降でビルドするx64 trace builderである。
`generate_assembly.py`は本番C++ ABI probeから構造体offsetを得て、`operations.S`、`trace.S`、`common.S`をPIC objectへ変換する。
生成器は`objcopy`で`.text`を抽出し、GOT参照と予期しないrelocationを検査する。
実行時のC++ trace builderはraw binaryのentry/exit/resultと選択済みステンシルを固定長出力へ連結する。
operationの成功link slotを次のstencil本体で置き換え、slotの命令自体を完成traceから除く。
operation stencil内の条件分岐はdisassembly metadataを使い、合成後の位置に合わせて再配置する。
現行実装は数値即値を生成slotへtrace compile時に書き込む。
local offsetは固定長instruction slotへx64 memory instructionとしてエンコードする。
`rel` poolを使うのはRuntime API引数recordとtrace header参照である。
数値即値を`rel`から読む方式は現行trace compilerにない。
追加する場合は、コードサイズと実行時のロード・カーソル更新コストを代表traceで比較する。
traceは次opcodeをfetchせず、次操作を間接dispatchしない。
JIT共有ライブラリはbuild時に生成したassembly byte arrayとC++ runtimeだけで動作する。
Clang integrated assemblerはx64操作、trace entry/exit、common dispatcherのraw binary生成にbuild時だけ使う。
実行時trace compilerはWASMをdecodeしてoperation templateを選び、埋め込みraw binaryの即値slotを埋め、local instructionをエンコードして固定長traceへ組み立てる。

操作対応と融合候補は、ClangでC23のプロファイル用WASMゲスト [`suite.c`](experiments/pysim/benchmarks/profile/guest/suite.c) を `--target=wasm32 -mcpu=mvp -O2` でコンパイルし、`wasm-objdump` が復号した命令列のn-gramから選ぶ。再現用の [`analyze_wasm_ngrams.py`](experiments/pysim/benchmarks/profile/guest/analyze_wasm_ngrams.py) は同じCソースをコンパイルし、関数境界をまたがず2〜4命令のn-gramと即値分布を集計する。Clang 21.1.8で得た28関数・6460命令では、`local.get i32.const i32.add` が305回、`i32.const i32.add` が382回、`i32.const i32.mul` が83回、`i32.const i32.and` が60回現れた。内訳では加算即値`1` / `-1` が97 / 21回、乗算即値`3` / `5` が1 / 3回、AND即値`255` / `65535` が11 / 2回であった。これはコンパイル済みモジュール内の静的出現数であり、実行時頻度ではない。

Clang-WASM n-gramの出現数は、操作LIRと融合規則を選ぶ根拠として使う。
即値ごとに異なるx64ステンシルvariantは作らない。
即値は単一ステンシル内のpatch slotへtrace compile時に書き込むため、実行時に`rel`から読まない。
operation間に接続用jump/callは置かない。成功link slotを次のstencil bytesで置き換え、連続配置した本体へfall-throughする。
加算0、乗算1、シフト量0などの恒等演算はC++フロントエンドが除去する。
`i32.const; i32.*`融合では、演算即値を命令slotへ焼き込む。
`drop`はコードを出さず、定数`i32.eqz`はtrace生成時に評価する。

`local.get`、`local.set`、`local.tee`はoperationとWASM値型に対応するstencilへlowerする。trace compilerはLoaderが確定したcompact word offsetを命令エンコーダへ渡し、エンコーダがlocal memory instructionへ直接書き込む。offsetごとのstencil variantは作らない。
trace compilerは関数引数を含む論理ローカル数を16以下に制限する。
各ローカルは最大2 wordなので、最後のlocalの開始位置はbyte offset 120以下となり、x64 signed `disp8`に収まる。
エンコーダはWASM local indexをLoaderのcompact offsetへ変換し、offsetをlocal memory instructionへ直接書き込む。
local indexやoffsetを実行時に`rel`から読み出さず、local Runtime APIやopcode dispatchも使わない。
i64/f64は2 word幅として処理する。
`i32.const; local.set/tee`融合は、即値slotとlocal instruction slotを一度に埋める。
`local.get, local.get, i32.*`融合は2つのlocal offsetをそれぞれ命令エンコーダで焼き込む。
C++ local get/set/tee Runtime API経路は設けない。

 命令列を短くする融合候補として空スタックからの`local.get, local.get, i32.*`を実装する。第1localを共有スタックへロードし、第2localを融合アセンブリstencilのmemory operandにする。C23プロファイルguestからClang 21.1.8で生成したWASMには、対応する二項演算のn-gramが静的に85件含まれた。この数は融合候補を選ぶ根拠であり、build時に出力する個々のstencilの機械語命令数を示さない。機械語サイズや命令数は生成stencilごとに測定する。

`i32.const, local.set`融合（{GOTCHA-JITC-08}）は即値を命令へ埋め込み、localへ書く。融合で定数pushを除去するため、この操作は`sp`も`rel`も変更しない。`i32.const, local.tee`融合は同じ即値をlocalと共有スタックへ書く。 <!-- definition: {GOTCHA-JITC-08} -->

1トレースは最大64 WASM命令、bodyとtrace末尾定数領域を合わせた出力領域1,536バイト、共有stack深さ128 wordを上限とする。WASM数値即値とlocal offsetはtrace codeに直接埋め込む。定数領域はRuntime API引数recordと、最後のtrace header参照4 byteを保持する。64命令と最大129件のrecord stagingで必要となる領域は出力領域内に収まる。ヘッダは定数領域の先頭offsetだけを保持し、slot数fieldを持たない。C++フロントエンドは復号済み命令列、operation symbol列、Runtime API用定数recordを固定長配列に一時保持する。最大blobはheader 40バイトとbody/pool領域1,536バイトの合計1,576バイトである。定数record stagingは最大129件である。

PySIMの `BumpAllocator` は実行用bufferとLoader由来のtyped backing viewを管理し、JIT managerが借用期間中それらを保持してネイティブ側へ渡す。x64 compilerは実行コードregion内のtrace出力領域へassembly生成コード、命令エンコード済みlocal access、即値、Runtime API用定数poolを構成する。decode済み命令列、operation symbol列、最大129件の定数record staging、stack modelは固定長配列で保持する。実行可能コード領域とアリーナ上のbufferは別管理とする。

trace単位コード生成はx64実装に限って定義する。ARMv8-M向けLIR、ABI、trace配置、relocation形式はTBDである。

## 4. 動的モデル

### 4.1 アルゴリズム
<!-- traceability: {JIT_AssemblyStencilCodegen} {JIT_RuntimeAPI} -->
 1. **トレース解析とコード生成**: Tier 3 JIT拡張はLoader所有WASMコードと関数metadataを内部C++コンパイラへ渡す。C++コンパイラは命令列を復号し、operation LIR symbolと即値を選ぶ。Clang integrated assemblerがbuild時に生成したstencilをC++ trace builderが連結する。成功link slotを次のstencil本体に置換し、命令間に転送命令のないfall-through列を作る。
   - 関数引数を含む論理ローカル数が16以下なら、operationとWASM型に対応するローカル操作stencilを選ぶ。
   - WASM数値即値を命令へ埋め込み、local memory instructionの`disp8`へoffsetをエンコードする。
   - 直接stencilを持たない有効opcodeは対応するRuntime API stencilへlowerする。trace形成上限と定数pool容量は対応する命令列を収容する。
2. **キャッシュ配置と定数pool**: JIT runtimeがtrace header、native stencil body、trace末尾定数領域をcacheへ配置する。WASM数値即値とlocal offsetはコードへ直接埋め込み、poolはRuntime API引数recordと最後のtrace header相対値に使う。操作間継続deltaは生成しない。chain targetだけを実行時trace targetとしてheaderへ記録する。
3. **実行可能メモリの確定**: x64の実行可能バッファ管理が書込み・実行権限の切替と必要な同期を行う。ARMv8-Mの権限機構、命令キャッシュ同期、バリア命令はTBDである。
4. **制御終端の処理**: trace bodyは制御終端命令を実行しない。未接続traceでは対応するC++ Interpreter handlerが条件、control frame、遷移先を確定する。C++ dispatcherは設定された後方分岐数へ達するまで次の常駐traceまたはhandlerを続ける。
5. **trace chain**: 同一深さの直線後続またはloaderが解決した静的前方`BR`のtarget traceが常駐する場合、trace末尾から共通コード領域のchain dispatcherへ進む。dispatcherは必要な制御frame深さを更新し、trace headerのtarget bodyへtail-jumpする。C++ handler後にC++ dispatcherが別traceを選ぶ遷移はchainではない。

#### JIT トレース検索 & 3面キャッシュ代謝オーケストレーション
<!-- traceability: {JIT_MultiBuffer_Cache} {JIT_OldestOnly_Promote} -->
3段JIT検索および連続8KBキャッシュ領域の管理は[`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md)を正本とする。コンパイラコアは生成したトレースの登録と命令同期を同コンポーネントへ委譲する。

#### トレース・チェイニング（共通コード領域のchain dispatcher経由）
<!-- traceability: {JIT_LazyChaining} {JIT_RuntimeAPI} -->
Interpreter dispatcherが最初のJIT traceを選んだときは通常の4引数C ABIでtrace entryを呼び出す。同一frame深さの直線後続traceが常駐する場合、trace末尾は共通chain dispatcherへtail-jumpする。dispatcherは現trace headerの`chain_target_addr`を読み、target headerの`const_rel_offset`から次traceの`rel`を設定してtarget bodyへtail-jumpする。targetが未接続なら終端dispatcherだけがmetadata pointerを`ctx.rel`へ渡し、`body_end_ip`を`ctx.ip`へ設定して`true`で終了する。Interpreter dispatcherは`false`をtrap/CPS exitとして扱い、`true`の後にmetadataから共有実行状態を確定する。

分岐opcodeのhandler実行とC++ dispatcher内で次の常駐traceを選ぶ遷移は、このmachine-code chainとは別である。共通chain dispatcherはopcodeを検査せず、命令ごとの条件評価とhandler呼出しを集約しない。chain回数を数える指標は共通chain dispatcherからtarget bodyへ移った回数とし、C++ handler後のdispatcher遷移を含めない。

#### ホットスポット検出とコンパイル待ち列
ホットスポット記録、しきい値、コンパイル待ち列、コンパイル時期、キャッシュ登録は [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) が正本である。本コンポーネントは受け取った適格なtraceをx64機械語へ変換し、登録情報を返す。

#### トレース生成と登録
<!-- traceability: {GOTCHA-JITC-01} {JIT_AssemblyStencilCodegen} {JIT_LazyChaining} -->

```mermaid
flowchart TD
    Start(["HOT trace request"]) --> Scan["C++ x64 compiler scans WASM trace"]
    Scan --> Supported{"All instructions supported?"}
    Supported -- "No" --> Fallback["Keep interpreter execution"]
    Supported -- "Yes" --> Emit["Emit x64 header and body"]
    Emit --> Register["Runtime registers body and resolves chain targets"]
    Register --> Protect["x64 executable buffer commits code"]
    Protect --> Ready(["Trace can be entered by C++ dispatcher"])
```

### 4.2 状態遷移図
<!-- traceability: {JIT_LazyChaining} {JIT_ReverseCompilationOrder} {GLOBAL_PeriodicTask} {GLOBAL_IdleDetection} -->
```mermaid
stateDiagram-v2
    state "C++ dispatcher lookup" as Lookup
    state "x64 trace body" as Trace
    state "C++ Interpreter handler" as Handler
    state "Common-code chain dispatcher" as Chain
    state "Runtime boundary" as Boundary

    [*] --> Lookup
    Lookup --> Trace: resident trace
    Lookup --> Handler: no resident trace
    Trace --> Chain: eligible straight-line successor
    Chain --> Trace: resident target body
    Chain --> Boundary: no target
    Trace --> Boundary: Runtime API exit, trap, completion
    Handler --> Lookup: continue below yield threshold
    Handler --> Boundary: trap, call, completion, threshold
    Boundary --> [*]
```

### 4.3 内部シーケンス
<!-- traceability: {JIT_LazyChaining} {JIT_RuntimeAPI} -->
#### trace実行と制御終端処理
```mermaid
sequenceDiagram
    participant R as RuntimeEngine
    participant I as Interpreter C++ dispatcher
    participant C as Cache
    participant T as x64 trace body
    participant D as Common-code chain dispatcher
    participant H as C++ Interpreter handler
    participant O as COOS

    I->>C: lookup current BasicBlock trace
    alt Resident trace
        C-->>I: trace entry
        I->>T: enter trace
        T->>D: transfer for eligible straight-line successor
        D->>T: tail-jump to target body
    else No resident trace
        I->>H: execute handler at current PC
        H-->>I: update shared state and branch count
    end
    I->>I: continue until threshold or an early boundary
    I-->>R: return execution-boundary status
    R->>O: return yield request at configured boundary
```

## 5. インターフェース定義

### 5.1 公開API
<!-- traceability: {JIT_AssemblyStencilCodegen} {TraceBoundaryInvariant} {PositionIndependentCode} {ExecutionContext_Layout} {GOTCHA-JITC-03} {JIT_MultiBuffer_Cache} -->
本コンポーネントの公開境界は、Tier 3 JIT 拡張から既存C++コンパイラ入口へ渡すWASMコード範囲と出力領域である。ABIは [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) を正本とする。

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 適格なWASM命令列からraw stencilを連結し、trace単位のネイティブ命令列を生成する。 |
| 入力 | ローダ所有WASMコードのアドレス・範囲と、ロード時に確定した型・フレーム情報。 |
| 出力 | 生成コードと、JIT拡張のキャッシュ登録に必要なトレース情報。 |
| 事前条件 | 入力WASMがLoaderの検証を通過し、呼出しABIと固定作業領域が有効であること。入口stack値を消費するopcodeや直接stencilを持たない有効opcodeもAPI経路を含めてtraceへlowerする。 |
| 事後条件 | 対象ABIに従うコードと、トレース境界で共有状態を同期する情報を返す。 |
| エラー時の挙動 | WASM検証違反はLoaderが拒否する。ABI不整合は構成時に検出する。 |

キャッシュの初期化、エントリ検索、カード状態、コンパイル待ち列、およびコンパイル時期はJIT拡張が所有する。これらをC++コンパイラへ公開する管理APIは定義しない。

### 5.2 URI/IPCインターフェース
<!-- traceability: {META_ConfigurableSystem} -->
本コンポーネントは vSoC の内部ライブラリであり、直接のIPCインターフェースは持たない。

## 6. 制約達成の方策

### 6.1 性能制約と方策
<!-- traceability: {JIT_AssemblyStencilCodegen} {JIT_RegisterMapping} {JIT_MultiBuffer_Cache} -->
- **目標**: コンパイルレイテンシを最小化し、WAMRインタープリタを上回る実行速度を実現。
- **方策**:
     - **trace単位native composition**: C++はcompile時だけopcodeを復号してLIR variantを選ぶ。操作ごとのreturn/エピローグを生成せず、成功slotを後続のraw stencil本体で置換する。完成traceはoperation間call/jump、次opcode fetch、indirect dispatchを持たない。恒等演算はtrace構築前に除去する。
    - **レジスタ割り当て**: assembly sourceは対象ABIの引数レジスタを明示し、Runtime API stencilは対象ABIの呼出し保存規則を守る。
    - **実行時検索との分離**: コード生成後の検索方式とキャッシュ管理は [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) に従う。

### 6.2 安全性制約と方策
<!-- traceability: {PositionIndependentCode} {MemoryBoundaryCheck} {FastAddressCheck} {SimpleJITArchitecture} -->
- **目標**: 不正なコード実行および W^X 違反の防止。
- **方策**:
    - **位置独立コード**: 生成コードを位置独立とし、配置場所の自由度を確保。
    - `Cache Capacity Check`: コード生成時にキャッシュ溢れを厳密にチェックし、溢れた場合は 3面リングローテーションにより Oldest バンクを破棄して再利用する。これはキャッシュ容量管理であり、（ゲストメモリアクセスの隔離）とは別の関心事である。
    - **メモリ境界検査 ({GOTCHA-JITC-04})**: <!-- definition: {GOTCHA-JITC-04} --> x64の対応済みゲストメモリアクセス命令は、load/store前に幅を含めた境界を検査し、範囲外をWASM trapへ変換する。拒否時はメモリと演算対象の値を変更しない。アドレスをマスク等で巡回させて継続すると境界外アクセスを隠すため、Wrappingによる継続は禁止する。生成命令列の検証証拠はx64実装テストを参照する。ARMv8-Mの命令列と検査方式はTBDである。
    - **W^X 保護**: x64の実行可能バッファは書込みと実行を同時許可しない。ARMv8-Mの保護機構、属性遷移、命令キャッシュ同期はTBDである。形式モデル `formal/jit_cache_model.py` は抽象W^X状態遷移を検証し、物理機構は主張しない。

## 7. 形式検証・テスト仕様との対応

独立コンセプト層は、検証因子・成果物マトリクスの責務判定に従って対象外とする。
生成x64コードの演算結果と命令列は、C++ native compilerを通す実行テストで検証する。W^X、キャッシュ状態、チェイン寿命の抽象不変条件は形式モデルで検証する。
ARMv8-Mのコンパイラと実機検証はTBDである。

### 7.1 検証対象の不変条件
- **位置独立性 (PIC)**: x64コードが任意のキャッシュバンクで再コンパイル不要で動作すること。ARMv8-Mの位置独立性と分岐範囲はTBD（`TEST-INT-40`, `TEST-JITC-40`）。
    - **トレース境界状態**: 融合後も生存するWASM値は共有オペランドスタックに保持する。x64ではchain先も同じスタック状態を引き継ぐ。ARMv8-Mのポインタ配置とスタック位置の更新はTBDである（`TEST-INT-41`, `TEST-JITC-52`）。
- **W^X メモリ保護**: 抽象状態モデルとx64の実行可能バッファテストで書込み・実行の排他を確認する。ARMv8-Mの物理実装と実機検証はTBDである。

### 7.2 テスト仕様書との連携
本コンポーネントの単体テストケースは [`jit_compiler_test_spec.md`](docs/qa/tier3_plugins/jit_compiler_test_spec.md) を正本として定義する。3面キャッシュの直交表は [`jit_runtime_test_spec.md`](docs/qa/tier3_plugins/jit_runtime_test_spec.md) を正本とする。


## 8. 設計判断と参考実装

### 8.1 設計判断 (ADR)
<!-- traceability: {ADR_ScalableCodeOffset} {ADR_SafeQueuingOnHotMiss} {JIT_LazyChaining} -->

**Trace境界の値返却 (`{GOTCHA-JITC-07}`)**: <!-- definition: {GOTCHA-JITC-07} -->
WASM値は共有オペランドスタックに残し、JIT traceの`bool`戻り値には使わない。通常完了では未接続chain targetの終端dispatcherだけが境界metadata pointerを`ctx.rel`へ置き、`body_end_ip`を`ctx.ip`に設定して`true`を返す。trapまたはCPS exitでは`false`を返すため、Interpreter dispatcherは状態判定を先に行う。共通chain dispatcher経由では同じ共有スタックを引き継ぎ、entry保存処理を重ねない。

- **決定事項**:
  - **背景**: 現行x64参照構成のコードキャッシュは8KBである。生成コードは可変長であり、すべてのtraceの開始位置が一定の命令アライメントを満たす保証はない。
  - **選択肢**:
    - 案1: バイト単位のオフセットを保持する。現行キャッシュ容量と任意のtrace開始位置を表現できる。
    - 案2: 一定アライメントを前提にシフトして圧縮する。現行の可変長配置には追加の整列が必要になる。
  - **結論**: 現行構成では案1を採用する。
  - **適用範囲**: 64KBを超えるキャッシュへの拡張表現とARMv8-Mの物理配置はTBDとする。現行構成の決定からシフト圧縮や512KB対応を保証しない。
- **決定事項**:
  - **背景**: ホットスポット検出で同一PCのコンパイル要求が重複すると、固定キューの容量と処理予算を消費する。
  - **結論**: 未処理の同一PCを二重登録しない。通常は固定キューへ登録してInterpreterで実行し、コンパイル時期はJIT Runtimeの契約に従う。
  - **適用範囲**: 現行のevictionではカードを`UNEXECUTED`へ戻し、再度hot判定を得る。これは暫定方策である。未使用traceの再コンパイルを抑止する条件と回復条件は未決とする。キュー満杯時の同期処理は通常予算の例外としてJIT Runtimeで定める。
