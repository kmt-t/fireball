# Interpreter コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}
<!-- evidence:
     formal: formal/vsoc_state_model.py
     formal: formal/interpreter_stack_model.py
     concept: concepts/interpreter_concept.py
     test: docs/qa/tier2_runtime/runtime_interpreter_test_spec.md
-->

## 1. コンセプト
<!-- traceability: {ThreadedInterpreter} {LowLatencyJIT} {InterpreterContextStackless} {EnvironmentPointer} {DirectBytecodeExecution} -->
Interpreter は、WASM命令をスレッドインタープリタ方式で実行する。低レイテンシかつ小フットプリントでゲストを動作させる。本コンポーネントは Execution Engine (`executor`) の一部として設計する。JITと実行状態を完全に共有する。周辺コンポーネントへの参照は Environment Pointer (`vsoc_runtime* env`) を介して型安全に行う。

組み込み環境の極小メモリ制約（`{GLOBAL_Policy_Memory}`）を遵守する。WASM命令は Flash や ROM 上のバイト列（`const uint8_t* ip`）から直接フェッチ（`*ip++`）する。中間命令オブジェクト（`Instr`）は生成しない。命令ごとの二分探索マップ（`FlatMapView`）も一切使用しない。即値（LEB128 等）はその場でポインタから直接デコードする。次の命令アドレスは単なるポインタ加算（`ip += len`）で決定する。

制御構文（`block`, `loop`, `if`）の飛び先は静的解決された軽量制御表（`control_map`）を参照する。この制御表はモジュールロード時に一度だけ構築される。これにより命令実行に伴うヒープ確保や探索オーバーヘッドを完全ゼロ（$O(1)$）とする（`{GOTCHA-INTP-05}`）。

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} -->
本コンポーネントは **Tier 2 (分解されたサブコンポーネント: Decomposed Subcomponent)** に属する。vSoC (`runtime_vsoc.md`) から分解されたサブコンポーネントである。WASM バイトコードの逐次実行を担当する。JIT との共用実行コンテキスト管理も担当する。

## 3. 静的モデル

### 3.1 データ構造
- **`Interpreter`**: WASM命令の実行、コンテキスト管理、外部環境との連携をカプセル化した主要クラスである。
- **`execution_context`**: 仮想CPUレジスタ、3本の値スタック情報、リニアメモリ情報、JITヘルパーを保持する構造体（計152バイト）である。
- **`OperandStack`（オペランドスタック）**: WASM のオペランド値のみを保持する固定容量スタックである。コールチェーン全体を貫く1本の連続バッファとして動作する。呼び出しを跨いでもスタックは連続して配置される。
- **`LocalStack`（ローカル変数スタック）**: コールチェーン全体で共有する固定容量の raw 32ビットスロット配列である。論理ローカル1個につき `WASM_LOCAL_ALIGNMENT_BYTES` の固定スロットを割り当てる。アクセス先は `local_base + slot * WASM_LOCAL_ALIGNMENT_BYTES` から直接計算する。
- **`control_frame` スタック**: `block`/`loop`/`if` の入れ子構造を管理する固定容量スタックである。
- **`call_frame_stack`**: 関数ごとの実行メタデータを保持する独立した固定容量スタックである。各descriptorは`LocalStack`内のフレーム開始位置を保持し、`LocalStack`にはローカル値だけを置く。
- **`interpreter_config`**: 3本のスタック容量やyield閾値などの不変な構成情報である。

### 3.2 内部ブロック図
```mermaid
graph TD
    Ctx["execution_context<br/>各スタックの領域/境界/オフセットを保持"]

    subgraph Operand_Stack_Memory["OperandStack: 固定容量バッファ1"]
        Op0["呼出元のオペランド群 (Caller)"]
        Op1["呼出先のオペランド群 (Callee)<br/>呼出元の頂点に連続して開始"]
    end

    subgraph Local_Stack_Memory["LocalStack: 固定容量バッファ2"]
        Loc0["raw 32-bit ローカル値: caller"]
        Loc1["raw 32-bit ローカル値: callee"]
    end

    subgraph Call_Frame_Memory["CallFrameStack: 固定容量メタデータ領域"]
        Frame0["caller descriptor<br/>LocalStack開始位置を保持"]
        Frame1["callee descriptor<br/>LocalStack開始位置を保持"]
    end

    subgraph Control_Frame_Memory["ControlFrameStack: 固定容量バッファ3"]
        Ctrl0["制御フレーム 0"]
        Ctrl1["制御フレーム 1"]
    end

    Engine[Interpreter Engine] -- R0: ctx --> Ctx
    Ctx -. "オフセットで参照" .-> Op1
    Ctx -. "オフセットで参照" .-> Loc1
    Ctx -. "オフセットで参照" .-> Ctrl1
    Engine -. "descriptorを管理" .-> Frame1
    Op0 -- "下から上へ成長・関数を跨いで連続" --> Op1
    Loc0 -- "下から上へ成長" --> Loc1
    Frame0 -- "下から上へ成長" --> Frame1
    Ctrl0 -- "下から上へ成長" --> Ctrl1
```
3本の値スタックは互いに独立した固定容量バッファであり、それぞれ自身の容量に対してオーバーフローを検知する。`call_frame_stack`は値スタックとは別の固定容量メタデータ領域であり、descriptorの容量も独立して検査する。`OperandStack`は関数呼び出しを跨いで連続するため、戻り値を呼出元へコピーする必要がない。calleeの戻り値は共有`OperandStack`に残り、call helperがdescriptorをpopしてcallerの実行を再開する。

### 3.3 主要なクラス・構造体・配列・定数

#### インタープリタ（Interpreter）クラス
依存関係（vSoC環境等）と実行に必要なテーブルをカプセル化する。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 統合コンテキスト | 実行コンテキストおよびリニアメモリ情報（ctx） | 構造体への参照 | `execution_context` (非所有) |
| ハンドラテーブル | 命令ハンドラへのジャンプテーブル | テーブルポインタ | 関数ポインタの配列 |

#### 実行コンテキスト（execution_context）
<!-- traceability: {PositionIndependentCode} {ContextPointerRegister} {MemoryBoundaryCheck} {EnvironmentPointer} {AAPCS_FastCall} -->
WASMゲストの全実行状態を管理する。JIT/Interpreter 共通の仮想CPUレジスタ群として設計する。

**独立した固定構造体としての配置**:
`execution_context` は、単体の固定サイズ構造体（計152バイト）である。`OperandStack`、`LocalStack`、`control_frame` のいずれにもインライン配置されない。ハンドラ呼び出しの第1引数（`R0: ctx`）として渡される。`R1` はオペランドスタックポインタ（`sp`、第2引数）とする。`R2` はカレントの `call_frame` のローカル変数配列先頭を指す `local_base`（第3引数）とする。`R3` はオペランドスタックのスタックトップ値 `tos`（第4引数）として直接引き回す。

複雑命令をCへ委譲する場合の命令別関数ポインタは、JIT専用コンテキストメンバ `jit_helper_ptrs[]` に置く。JITコードは対象要素を `ctx` 相対で読み、直接末尾ジャンプする。インタープリタの命令ディスパッチはこのヘルパーを呼ばない。基本ブロック末尾では、常に `TOS, NOS, NNOS` を命令テンプレートのvariantで判定した個数だけオペランドスタック（`[R1, #offset]`）へフラッシュする。その後、コンテキスト `R0` の `ip`（`+0x00`）および `sp_offset`（`+0x0C`）を書き換えて状態を完全同期する。

##### CPS 4引数 仮想CPUレジスタ（ディスパッチ境界での引数受渡し）

| 項目名 | 機能と役割 | 型分類 | 物理レジスタ |
| :--- | :--- | :--- | :--- |
| 実行コンテキスト基底 | `execution_context` 構造体自身へのポインタ | 物理レジスタ | `R0: ctx` (`ContextPointerRegister`) |
| オペランドスタックポインタ | `OperandStack` 現在のスタックトップ位置を指すポインタ | 物理レジスタ | `R1: sp` (`AAPCS_FastCall` 準拠) |
| ローカル変数基底 | カレントコールフレームのローカル変数配列基底ポインタ | 物理レジスタ | `R2: local_base` (`AAPCS_FastCall` 準拠) |
| スタックトップ (TOS) | `OperandStack` 最上位値（スタックトップ）を直接保持するレジスタ | 物理レジスタ | `R3: tos` (`AAPCS_FastCall` 準拠) |

##### `execution_context` 物理メモリレイアウト（計152バイト）

`[R0, #0x00]`〜`[R0, #0x97]` に配置される固定構造体メモリフィールド：

| 項目名 | 機能と役割 | 型分類 | サイズ・オフセット |
| :--- | :--- | :--- | :--- |
| プログラムカウンタ (ip) | 現在または復帰時の WASM PC | 統一オフセット | 4バイト (`[R0, #0x00]`) |
| SP領域開始位置 (sp_base) | `OperandStack` バッファ先頭アドレス | アドレス | 4バイト (`[R0, #0x04]`) |
| SP領域終端位置 (sp_limit) | `OperandStack` バッファ終端アドレス（オーバーフロー検知用） | アドレス | 4バイト (`[R0, #0x08]`) |
| SPオフセット (sp_offset) | `OperandStack` 現在の頂点オフセット/アドレス | 長さ/アドレス | 4バイト (`[R0, #0x0C]`) |
| ローカル変数領域開始位置 (local_base_addr) | `LocalStack` バッファ先頭アドレス | アドレス | 4バイト (`[R0, #0x10]`) |
| ローカル変数領域終端位置 (local_limit_addr) | `LocalStack` バッファ終端アドレス（オーバーフロー検知用） | アドレス | 4バイト (`[R0, #0x14]`) |
| ローカル変数オフセット (local_offset) | `LocalStack`上の次の空きraw 32-bit word位置 | ワードオフセット | 4バイト (`[R0, #0x18]`) |
| 制御フレーム領域開始位置 (cf_base_addr) | `control_frame` バッファ先頭アドレス | アドレス | 4バイト (`[R0, #0x1C]`) |
| 制御フレーム領域終了位置 (cf_limit_addr) | `control_frame` バッファ終端アドレス（オーバーフロー検知用） | アドレス | 4バイト (`[R0, #0x20]`) |
| 制御フレームオフセット (cf_offset) | `control_frame` 現在の頂点オフセット/深さ | 長さ/オフセット | 4バイト (`[R0, #0x24]`) |
| リニアメモリ開始アドレス (mem_base) | ゲストリニアメモリの開始アドレス | メモリアドレス | 4バイト (`[R0, #0x28]`) |
| リニアメモリサイズ (mem_size) | ゲストリニアメモリの有効バイト数（境界チェック比較用） | メモリサイズ | 4バイト (`[R0, #0x2C]`) |
| グローバル変数領域開始位置 (globals_base) | WASM global 配列の開始アドレス | メモリアドレス | 4バイト (`[R0, #0x30]`) |
| グローバル変数領域終端位置 (globals_limit) | WASM global 配列の終端アドレス | メモリアドレス | 4バイト (`[R0, #0x34]`) |
| ハンドラテーブル (handler_table) | 命令ハンドラへのジャンプテーブルポインタ | テーブルポインタ | 4バイト (`[R0, #0x38]`) |
| 予約パディング (reserved) | 8バイトアライメント調整用の予約パディング | パディング | 4バイト (`[R0, #0x3C]`) |
| JITヘルパー (jit_helper_ptrs) | 命令ごとのCヘルパー関数ポインタ配列 | 関数ポインタ配列 | 88バイト (`[R0, #0x40]`〜`[R0, #0x97]`) |

`execution_context` 構造体の実体は、計152バイトの固定長メモリである。内訳は15個の32ビットフィールド、4バイトの予約領域、および11個の64ビット `jit_helper_ptrs` である。3本のスタック（OperandStack、LocalStack、control_frame）の開始・終端・オフセットを対称なフィールドとして保持する。この設計により、いずれか1本のスタックの伸縮が他の記録位置へ影響することを物理的に排除する。JITの複雑処理の委譲先は、対象命令のメンバから直接参照する。コード領域へプロセスアドレスを直接埋め込むことはしない。バイトオフセットの物理配置は `{ExecutionContext_Layout}` に従う。

**TOS レジスタキャッシングとスタック同期不変条件 (`{GOTCHA-INTP-01}`)**:
オペランドスタックの最上位要素（TOS: Top-of-Stack）は、常に物理レジスタ `R3: tos` に常駐させる。これによりメモリアクセス回数を半減させ、スタック操作命令（`i32.add`, `local.get` 等）の実行性能を最大化する。各命令ハンドラの入口では、直前の演算結果が `R3` に保持されている。ハンドラは必要に応じて第2オペランドのみをスタックバッファからポップする。関数呼び出し、外部システムコール、JIT 遷移などの境界では、TOS レジスタの値をメインスタック配列へ書き戻して同期する。

TOS および NOS（次段TOS、`NTOS`）はライトバック・キャッシュとして機能する。実行中はレジスタ上の TOS/NOS を正本として扱う。各命令ごとに Native スタックへライトスルーすることは禁止する。`sp` はキャッシュされていないスタック領域の次の書き込み位置を示す。実行境界では dirty な値を TOS、NOS の順序を保って一括 spill する。その後に Native スタックのサイズ（`sp_offset`）を更新する。インタープリタと JIT の相互移行、外部呼出し、トラップ処理のいずれにおいても同じ spill 規約を適用する。キャッシュ値を不当に破棄したり、`tos=0` で代用したりしてはならない。

**統一プログラムカウンタによる複数モジュール線形化 (`{GOTCHA-INTP-04}`)**:
モジュール間を跨ぐ相互関数呼び出しでは、統一 PC（Unified PC: `(func_index << 16) | bytecode_offset`）を採用する。モジュール相対オフセットではなく、システム全体で一意に決定される値とする。これにより複数モジュールが共存する環境下でも、PC の単一比較のみで分岐先コードブロックを特定できる。JIT トレースのモジュール横断インライン化を極低オーバーヘッドで実現する。

#### コールフレーム（call_frame descriptor）
<!-- traceability: {PositionIndependentCode} {ContextPointerRegister} {MemoryBoundaryCheck} {EnvironmentPointer} {AAPCS_FastCall} -->
`call_frame` は、関数インデックス、コード、制御マップ、環境、および`LocalStack`の開始raw-word位置を結び付ける実行時descriptorであり、独立した`call_frame_stack`に格納する。`frame_offset`は`LocalStack`のuint32ワード単位である。calleeのローカル値は現在の`local_offset`から確保し、descriptorに保存した開始位置から参照する。`LocalStack`の固定長`uint32_t`配列へdescriptor、戻りPC、型情報を混在させない。論理ローカルは`WASM_LOCAL_ALIGNMENT_BYTES`固定スロットで保持する。値の有効ワードは関数シグネチャに従う。i32/f32は1ワード、i64/f64は2ワードを占有する。

`local.get`, `local.set`, `local.tee` は型を解釈しない。local index から `slot * WASM_LOCAL_ALIGNMENT_BYTES` を直接計算する。必要な 1 または 2 ワードをオペランドスタックとの間で生コピーする。型付き演算や ABI 境界の読み書きのみが、既知の型に応じて値を解釈する。オペランドスタックはコール境界を跨いで連続する。`call`, `call_indirect`, import, host call, 関数復帰は、常に Interpreter/RuntimeEngine 境界で処理する。JIT トレースが `call_frame` の push/pop や host call helper の呼出しを代行することはない。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 関数インデックス | 所有するWASM関数のメタデータを特定 | インデックス | 32bit符号なし |
| コード参照 | 現在実行するWASM命令列 | 非所有参照 | Flash/ROM上のコードを参照 |
| 制御マップ | block/loop/if の静的な飛び先表 | 非所有参照 | ロード時に構築した表を共有 |
| LocalStack開始スロット | 当該関数のローカル値の先頭 | 32bitオフセット | 固定長ローカル配列内の物理スロット位置 |
| ローカルスロット | local index ごとの固定領域 | `WASM_LOCAL_ALIGNMENT_BYTES` 固定 | `local_base + local_index * WASM_LOCAL_ALIGNMENT_BYTES` から直接計算 |

呼出し時はdescriptorを`call_frame_stack`へ積み、引数を含むローカル値を`LocalStack`の現在位置から確保して`local_offset`を次の空き位置へ進める。復帰時はcallee descriptorをpopし、`LocalStack`を保存済み`frame_offset`まで一括で戻して`local_offset`を同期する。ABIへ渡す値配列にはdescriptor、型タグや可変長コンテナを含めない。 `{CallFrame_Layout}`

#### 制御フレーム（control_frame）
<!-- traceability: {PositionIndependentCode} {ContextPointerRegister} {MemoryBoundaryCheck} {EnvironmentPointer} {AAPCS_FastCall} -->
`control_frame` は、`block/loop/if` 命令によるネスト構造とジャンプ先を管理する。専用の固定容量バッファへ積む。`loop/block/if` の分岐は JIT トレースが直接解決できる（`{TraceBoundaryInvariant}`, `{JIT_RuntimeAPI_Fallback}`）。そのため、構文の開始や終了に対応するフレームの積み下ろしを JIT が代行しない場合がある。`control_frame` は他のスタックと物理的に独立している。したがって積み下ろし漏れが生じても、他のスタックの記録位置が乱れることはない。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 構造種別 | `block`/`loop`/`if` の識別値 | 列挙値 | 32bit符号なし (フレーム先頭 `+0x00`) |
| ブロック開始PC | 構造化ブロックの開始PC | オフセット | 32bit符号なし (`+0x04`) |
| 対応終了PC | 対応する`end`または分岐対応位置 | オフセット | 32bit符号なし (`+0x08`) |
| 保存済みスタック長 | ブロック開始時点の`OperandStack`の高さ | 長さ | 32bit符号なし (`+0x0C`) |
| 結果アリティ | このブロックが戻す値の数（スタック pruningに使用） | 整数 | 16bit符号なし (`+0x10`)。`+0x12`〜`+0x13`は末尾パディング |

`control_frame` は計20バイト（`+0x00`〜`+0x13`）である。物理配置はPySimの`ControlFrameNative`および`wasm_interop.hxx`と一致し、`{ControlFrame_Layout}` に従う。

#### 制御フレーム整合性とリーク防止不変条件 (Control Frame Integrity Invariant)
<!-- traceability: {InterpreterContextStackless} {PositionIndependentCode} -->
ネスト制御構造においてフレームスタックの不整合を完全に防止するため、以下の不変条件を厳格に保持する：

1. **`IF` 条件不成立時のフレーム整合性とリーク防止 (`{GOTCHA-INTP-03}`)**:
   - `if` 命令でスタック条件が偽（`cond == 0`）かつ `else` 節が存在しない場合、制御フレームスタックに無駄なブロックフレームを積んではならない。
   - 条件偽判定の瞬間に、直ちに対応する `END` 命令の直後へ PC をスキップさせる。
   - これにより、未ポップの不要フレームが残留してスタックオーバーフローを起こすフレームリークを防止する。
2. **分岐脱出時のフレーム Pruning と TOS 復元 (`{GOTCHA-INTP-02}`)**:
   - `br / br_if / br_table` で `depth` 個のフレームを脱出する際、対象フレームより外側のフレームを確実にポップする。
   - オペランドスタック長を、対象フレームの開始時スタック高（`stack_height`）へ巻き戻す。
   - ブロックが戻り値を持つ場合は、分岐命令実行時に最上位に積まれていた戻り値のみを退避する。
   - プルーニング完了後に、正確にスタック頂点（TOS レジスタ）へ復元する。
   - 脱出先が `loop` の場合はループ本体先頭へ巻き戻してフレームを維持する。`block / if` の場合はフレームをポップしてブロック終端直後へ遷移する。
3. **JIT 代行分岐脱出でのフレーム内容不正利用防止 (`{GOTCHA-INTP-06}`, ADR-INTERP-03)**:
   - JIT トレースが分岐条件を直接解決した場合、フレームの積み下ろしは行われない。
   - 以前インタープリタが積んだフレームが回収されずに残留することがある。
   - したがって `br`, `br_if`, `else` の分岐先解決では、フレームスタックの内容（`kind`, ラベルPC, 結果アリティ, 保存済みスタック長）を信用しない。
   - 制御構造の入れ子は静的な性質である。モジュールロード時に各ベーシックブロックの分岐先（`next_pc` / `loops_to`）としてあらかじめ解決しておく。
   - 実行時はその解決済みの値を直接使用する。フレームスタックを都度たどって再計算しない。
   - フレームスタックの深さ切り詰めは安全策としてのみ機能させる。JIT が `END` を代行し続けることでスタックが無制限に伸びるのを防ぐ。

#### 分岐脱出時のフレームプルーニングと TOS 復元手順（手順アクティビティ図）
<!-- traceability: {GOTCHA-INTP-01} {GOTCHA-INTP-02} {GOTCHA-INTP-03} {CallFrame_Layout} -->
`br / br_if / br_table` 命令によるネスト脱出時に、中間フレームを破棄しつつ戻り値を TOS レジスタへ復元する手順を示す。フレームスタックが信頼できる純粋インタープリタ実行経路、または静的解析結果がないフォールバック経路で使用する。JIT トレース境界を跨ぐ場面（`{GOTCHA-INTP-06}`）では深さ探索を行わず、事前解決済みのラベルPCや `exec_trace` を直接使用する。

```mermaid
flowchart TD
    Start(["br / br_if / br_table 実行"]) --> CheckCond{"条件は真か (br_if のみ)"}
    CheckCond -- "いいえ" --> NextPC(["次の命令へ PC を進める"])
    CheckCond -- "はい / 無条件" --> FetchTarget["指定深度の対象制御フレームを取得"]

    FetchTarget --> CheckArity{"対象ブロックの戻り値数 > 0 か"}
    CheckArity -- "はい" --> SaveVal["スタック頂点 / TOS から戻り値を退避"]
    CheckArity -- "いいえ" --> Prune["指定深度の中間制御フレームをポップ"]

    SaveVal --> Prune
    Prune --> RewindStack["スタック高さを対象フレームの退避値へ巻き戻す"]
    RewindStack --> CheckLoop{"対象フレームは LOOP か"}

    CheckLoop -- "はい" --> LoopBranch["PC をループ先頭へ設定 (フレーム維持)"]
    CheckLoop -- "いいえ" --> BlockBranch["対象フレームをポップし PC を END 直後へ設定"]

    LoopBranch --> RestoreVal{"戻り値の退避があったか"}
    BlockBranch --> RestoreVal

    RestoreVal -- "はい" --> SetTOS["退避値を TOS レジスタ / スタック頂点へ復元"]
    RestoreVal -- "いいえ" --> Dispatch
    SetTOS --> Dispatch(["clang::musttail で次の命令へディスパッチ"])
```

#### インタープリタ構成（interpreter_config）
<!-- traceability: {META_ConfigurableSystem} -->
インタープリタの動作パラメータを定義する。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| `OperandStack` 容量 | `OperandStack` バッファの総バイト数 | バイト数 | 32bit符号なし (`FB_CONF_INTERP_OPSTACK_SIZE`) |
| `LocalStack` 容量 | `LocalStack` バッファの総バイト数 | バイト数 | 32bit符号なし (`FB_CONF_INTERP_LOCALSTACK_SIZE`) |
| `control_frame` スタック容量 | `control_frame` バッファの総バイト数 | バイト数 | 32bit符号なし (`FB_CONF_INTERP_CTRLSTACK_SIZE`) |
| Yield 閾値 | 次の yield までに実行を許可する命令（トレース）数 | 回数 | 32bit符号なし |

#### オプコードハンドラ / トレース実行（opcode_handler / exec_trace）
<!-- traceability: {JIT_RuntimeAPI_Fallback} {ContextPointerRegister} {EnvironmentPointer} {JIT_RegisterMapping} {ADR_TosCacheAsymmetry} {AAPCS_FastCall} -->
命令ハンドラおよびJITトレースは、継続渡し（CPS）と `__fastcall` 呼び出し規約による同一の4引数入口を持つ。実行コンテキストポインタ（`ctx`）、オペランドスタックポインタ（`sp`）、ローカル変数基底（`local_base`）、スタックトップ値（`tos`）を物理レジスタで引き継ぐ。

インタープリタハンドラは次回呼び出し用の4引数とトラップ状態を結果として返す。次のPCは `ctx` に保持する。JITトレースは末尾ジャンプで継続し、結果レコードを返さない。

命令実行中のWASMトラップは、例外を送出せず、継続引数とトラップ情報を含む結果として返す。トラップを受け取った実行器は全アクティブフレームを破棄して実行結果を確定する。以後の命令は実行しない。同期的な公開APIは、確定済みトラップを正常な戻り値（特に`None`）と混同せず、呼び出し側へ明示する。これはハンドラ内部の実行経路とは分離する。追加仕様の一覧と判定項目は [runtime_interpreter_test_spec.md](docs/qa/tier2_runtime/runtime_interpreter_test_spec.md#追加gotcha一覧マージ判定用) に集約する。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 実行シグネチャ | インタープリタ命令ハンドラの継続渡し（CPS）4引数シグネチャ | 関数ポインタ | `handler_result (__fastcall *)(execution_context* __restrict__ ctx, uint32_t* __restrict__ sp, uint32_t* __restrict__ local_base, uint32_t tos) noexcept` |
| ハンドラ結果 | 次の継続引数とトラップ状態 | 固定結果レコード | `ctx`、`sp`、`local_base`、`tos`、`trap_code`。次のPCは `ctx->ip` に格納し、`trap_code == 0` を正常、非0をトラップとする |
| JITトレース入口 | インタープリタと同一の4引数入口を持つ末尾継続 | 関数ポインタ | `void (__fastcall *)(execution_context* __restrict__ ctx, uint32_t* __restrict__ sp, uint32_t* __restrict__ local_base, uint32_t tos) noexcept` |
| レジスタ割り当て | ARM AAPCS / `__fastcall` 引数レジスタマッピング | 物理レジスタ | `R0`: `ctx`, `R1`: `sp`, `R2`: `local_base`, `R3`: `tos` (`AAPCS_FastCall` 準拠) |

WASM オプコードごとのスタック遷移およびハンドラ実装マトリクスは `{ThreadedInterpreter}` を参照する。

**スタックトップキャッシュ (`R3: tos`) のレジスタ受け渡しと対称性**:
`env` は `execution_context`（`R0`）に内包されるため、独立した引数レジスタを消費しない。CPS 第4引数 `R3` はスタックトップ値（`tos`）を直接引き渡す。これによりインタープリタと JIT は、トレース境界において `R3: tos` でスタックトップ値を対称に直接引き渡せる。トレース境界でのメモリ PUSH/POP アクセスを最小化する。

JIT トレース内では `R4` が NOS（スタック次段キャッシュ）、`R5` が NNOS（スタック第3段キャッシュ）として割り当てられる。基本ブロック末尾では、常に `TOS, NOS, NNOS` をvariantで判定した個数だけオペランドスタック（`[R1, #offset]`）へフラッシュする。コンテキスト `R0` の `ip`（`+0x00`）および `sp_offset`（`+0x0C`）を書き換えて状態を完全同期する。

## 4. 動的モデル

### 4.1 アルゴリズム
<!-- traceability: {ThreadedInterpreter} {JIT_RuntimeAPI_Fallback} {Interpreter_LazyJITSwitch} {LowLatencyJIT} {SimpleJITArchitecture} {Challenge_ApproximateYield} {Debug_Integrated} {ContextPointerRegister} {ADR_TosCacheAsymmetry} {ADR_TraceBoundaryYield} -->
- **Threaded Dispatch with Continuation Passing Style (CPS)**:
  - 命令ハンドラを連鎖させるテーブルディスパッチ方式で分岐コストを極小化する。
  - ハンドラ関数型は `handler_result __fastcall(ctx, sp, local_base, tos)` に統一する。結果レコードで次の4引数とトラップ状態を返す。
  - JITトレースの関数型は `void __fastcall(...) noexcept` とする。
  - `ctx` (R0), `sp` (R1), `local_base` (R2), `tos` (R3) のホットな変数を `__fastcall` 引数レジスタ上で保持・更新する。
  - 3本のスタック情報、リニアメモリ情報、ハンドラテーブルを `execution_context` 内で直接管理する。
  - JITが複雑処理をCへ委譲する際の命令別関数ポインタを `jit_helper_ptrs[]` に保持する。
  - 非制御命令では `[[clang::musttail]]` による直接末尾ジャンプ（Direct-Threaded Code）を行う。レジスタ上の引数をそのまま次のハンドラへ継続渡しする。
- **JIT コードとの完全な呼び出し規約整合 (Low-Overhead Interop)**:
  - JIT コンパイラが生成するネイティブトレース（`exec_trace`）も同一の `__fastcall` CPS 4引数シグネチャに従う。
  - **インタープリタから JIT への遷移**: レジスタ上の `(ctx, sp, local_base, tos)` を AAPCS 準拠開始プロローグへ渡す。プロローグがcallee-savedレジスタを退避し、入口レジスタ割り当てを整えた後にJIT本体へ進む。インタープリタから内部chain entryへ直接入ってはならない。
  - **JIT からインタープリタへのフォールバック (OSR / Exit)**: 未サポート命令やトラップ、トレース終端に達した場合に発生する。終了エピローグで `TOS, NOS, NNOS` をスタックへフラッシュする。コンテキスト `R0` の `ip` および `sp_offset` を書き戻す。その後、`BX r12` 等でインタープリタへ末尾ジャンプする。構造体への退避・復元やレジスタ再配置は一切発生しない。
- **WASM命令とRuntime API / Libgcc ヘルパー連携 (`Libgcc_Runtime_Helper`)**:
  - 各命令ハンドラはスタックボトム相対でオペランドとスタック長を更新する。
  - ハードウェア支援がない64ビット整数演算や浮動小数点演算は、専用ランタイムヘルパー（`fireball_rt_*`）経由で実行する。これによりFPUの有無や soft-float 差異を透過的に吸収する。
- **ジャンプの高速化 (exec_trace)**:
  - 制御命令によるジャンプ先を `control_frame` 内の `exec_trace` に保持する。
  - この値はロード時の静的解析結果から書き込まれる。
  - 深さ相対の分岐命令はその都度フレームを辿り直さず、事前計算済みの値をそのまま使用する（`{GOTCHA-INTP-06}`）。
- **スタック Pruning (Label Arity対応)**:
  - `br` 命令等の実行時、ジャンプ先の `control_frame` に記録された結果アリティに基づき、スタック上のオペランドを残してスタック長を保存済みスタック長まで巻き戻す。
- **JIT更新戦略（判定主体は常に vSoC）**:
  - 制御命令や関数境界は、インタープリタにとって制御を vSoC へ返す境界である。
  - インタープリタは次に実行すべき WASM PC を返すだけであり、JIT キャッシュの参照は行わない。
  - vSoC の `step()` が制御復帰ごとに PC に対応する `exec_trace` を JIT キャッシュから引き直す。JIT 済みであればネイティブコードへ、未コンパイルであればインタープリタへディスパッチする。
- **関数復帰の番兵**:
  - JITは `return` 命令や専用RETURN sentinelを生成しない。
  - JITトレースは `return` 直前で終了して共有状態を同期する。
  - インタープリタの return ハンドラが callee の `CallFrame` をポップする。
  - トップレベル復帰のみ専用の RETURN sentinel PC を生成し、RuntimeEngine が実行完了を判定する。
- **Hotspot検知と JIT候補ビットマップによるバイパス (`JIT_CandidateBitmap`)**:
  - インタープリタはトレース開始時の PC を戻り値としてのみ vSoC に伝える。
  - HOT 判定を行うのは vSoC 側の責務である。
  - `jit_candidate_bitmap` において該当ブロック先頭 PC が候補外（`0`）の場合、vSoC は追跡登録をバイパスする。コンパイル効果のないブロックの追跡オーバーヘッドを完全排除する。
- **トレース境界での協調的Yield (`ADR_TraceBoundaryYield`)**:
  - インタープリタは命令ごとの精密なカウンタ評価や中断を行わない。
  - トレースの切れ目でのみインタープリタの命令ハンドラが呼び出し元（vSoC）へ制御を返す。
  - `yield_threshold` の判定と `co_yield` の発行は vSoC 自身が行う。
  - インタープリタはコルーチンではなく単なる関数である。トレース境界での自然なレジスタ・スタック整合によりステート退避を極小化する。
- **デバッグ・プロファイラフック**:
  - 命令実行前後でブレークポイント判定、実行時PC頻度サンプリング、メモリ/レジスタの動的アサーション検証を行い、Debugger/Profiler に制御を委譲する。

#### WASM インタープリタのコンセプトコード
実行可能な概念モデルは [`interpreter_concept.py`](docs/components/tier2_runtime/concepts/interpreter_concept.py) に分離する。本文書には実装言語のコードを埋め込まず、WASM実行契約と固定レイアウトのみを規定する。

#### 統合 Tiered ランタイムエンジン・コンセプトコード
インタープリタ実行、Hotspot検出、Copy-and-Patch JIT、3面キャッシュ、MPU W^X を統合した自己完結実行シミュレーションは [`runtime_engine_concept.py`](docs/components/tier2_runtime/concepts/runtime_engine_concept.py) を参照する。

### 4.2 状態遷移図
<!-- traceability: {ThreadedInterpreter} {JIT_RuntimeAPI_Fallback} {Interpreter_LazyJITSwitch} {LowLatencyJIT} {SimpleJITArchitecture} {Challenge_ApproximateYield} {Debug_Integrated} -->
```mermaid
stateDiagram-v2
    [*] --> Ready
    Ready --> Running: step
    Running --> Ready: yield
    Running --> Debugging: breakpoint
    Debugging --> Running: resume
    Running --> Trap: trap
    Trap --> Ready: handled
```

### 4.3 内部シーケンス
<!-- traceability: {ThreadedInterpreter} {JIT_RuntimeAPI_Fallback} {Interpreter_LazyJITSwitch} {LowLatencyJIT} {SimpleJITArchitecture} {Challenge_ApproximateYield} {Debug_Integrated} -->
#### Interpreter 実行シーケンス
```mermaid
sequenceDiagram
    autonumber
    participant V as vSoC
    participant I as Interpreter
    participant D as Debugger
    participant R as Runtime API

    V->>I: run_step(ctx)
    Note over I,D: デバッグ有効時のみ境界で検査
    opt デバッグ有効
        I->>D: pre_check(ctx)
        D-->>I: continue
    end
    loop トレース内実行 (Direct-Threaded)
        I->>I: dispatch(opcode) via [[clang::musttail]]
        opt 64bit/浮動小数点等の委譲
            I->>R: call(fireball_rt_*)
            R-->>I: result
        end
    end
    Note over I: 制御命令 / トレース境界でループ脱出
    opt デバッグ有効
        I->>D: post_check(ctx)
        D-->>I: continue
    end
    I-->>V: return Result (SUCCESS / TRAP)
```

## 5. インターフェース定義

### 5.1 公開API
外部から利用可能なオブジェクト指向APIを定義する。

#### 初期化（initialize）

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 命令ハンドラテーブルのセットアップやスタック領域の確保等、実行エンジンの初期状態を構築する。 |
| シグネチャ | `initialize(config: const参照) -> 結果型` |
| 引数 | `config`: インタープリタ構成 (`interpreter_config`) への読取専用参照 |
| 戻り値 | 結果型 (成功時は空、失敗時はエラー情報) |
| 事前条件 | 設定値がシステム制限（メモリサイズ等）に適合していること。 |
| 事後条件 | `opcode_handler_table` が正しく配置される。 |
| 不変条件 | 初期化後に設定値を変更できないこと。 |
| エラー時の挙動 | メモリ確保失敗時は初期化を中断し、エラー値を返す。 |
| 補足 | デバッグモードが指定された場合は `debug_handler_table` を使用するように構成する。 |

#### 実行ステップ (`run_step`)
<!-- traceability: {META_RecoveryStrategy} -->

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | WASM命令を1トレース分実行し、実行コンテキストを更新する。 |
| シグネチャ | `run_step(ctx: 可変参照) -> 結果型` |
| 引数 | `ctx`: 実行コンテキスト (`execution_context`) への可変参照 |
| 戻り値 | 結果型 (正常終了時は SUCCESS、トラップ発生時はリカバリー戦略カテゴリ `recovery-strategy-category` ) |
| 補足 | 必要に応じて内部的に JIT コードへのジャンプを行い、JIT/Interpreter を透過的に切り替える。 |

#### 割り込みイベント同期 (`sync_interrupts`)
<!-- traceability: {META_RecoveryStrategy} -->

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | Safepointで受け取った汎用 `interrupt-event` を、実行コンテキストの保留イベント領域へ反映する。 |
| シグネチャ | `sync_interrupts(ctx: 可変参照, event: interrupt-event) -> void` |
| 引数 | `ctx`: 実行コンテキスト (`execution_context`) への可変参照<br>`event`: `vector_id`、`source_id`、`cause_code`、`payload0`、`payload1` の固定5ワード |
| 戻り値 | void (なし) |
| 期待する結果 | `ctx` 内の保留イベントが更新され、vSoCのSafepoint処理で反映される。 |
| 事前条件 | `ctx` が有効な `execution_context` を指していること。 |
| 事後条件 | フラグがアトミックに書き込まれる。 |
| 不変条件 | 実行中の命令ハンドラから安全に参照可能であること。 |
| エラー時の挙動 | 未登録イベントやFIFO満杯はCOOS側でドロップされる。vSoCから渡された不正なイベントは `recovery-strategy: ignore` とし、ゲスト実行コンテキストの破壊を防ぐ。 |
| 補足 | vSoCのSafepoint配送を補助するだけで、ゲスト関数の階層ディスパッチやWASIポーリングは担当しない。 |

#### CPSハンドラ・ネイティブ実験境界 (`cps_interpreter`)
<!-- traceability: {ThreadedInterpreter} {ContextPointerRegister} {InterpreterContextStackless} -->

[`interpreter_cps.py`](experiments/pysim/tier2_runtime/interpreter_cps.py) は、通常Python実装とCythonネイティブCPSチェインを切り替える互換入口である。命令意味論と既存の `_h_*` 本体は `interpreter.py` に残し、`cps_chain.pyx` が4引数C関数ポインタ表からPythonハンドラを呼び、基本ブロック終端でない場合だけ `[[clang::musttail]]` 継続呼出しを行う。ジャンプ・分岐命令はチェインせず、既存インタープリタの境界処理へ戻る。AO-Benchはこの入口を経由し、`--native-cps` 指定時にネイティブ経路を必須化する。

ネイティブCPSはWASMバイナリデコーダの代替ではなく、既存WASM命令ハンドラの実行連鎖を検証する実験境界である。目的は、Python互換境界を保ったまま、実インタープリタ全175ハンドラを対象に `(ctx, sp, local_base, tos)` のC関数ポインタチェイン、基本ブロック境界、分岐、ループ、トラップを同一のAO-Benchで検証することである。

ネイティブ経路は通常命令のハンドラ末尾から次の関数ポインタへ継続し、Clangの `[[clang::musttail]]` を実際に通す。分岐・呼出し・戻りは基本ブロック境界としてチェインを戻し、既存のフレーム処理へ接続する。ビルドは任意であり、未ビルドでも通常Python経路の動作は変わらない。

### 5.2 URI/IPCインターフェース
<!-- traceability: {META_RecoveryStrategy} -->
本コンポーネントは vSoC の内部ライブラリとして利用され、直接のIPCインターフェースは持たない。

### 5.3 関連コンポーネントとの連携
<!-- traceability: {META_RecoveryStrategy} -->
| コンポーネント | 連携内容 | 参照データ構造 |
| :--- | :--- | :--- |
| **WASM Loader** | WASMバイナリの索引情報（関数、命令、即値）の提供 | [`runtime_loader.md`](docs/components/tier2_runtime/runtime_loader.md#モジュールビューmodule_view) |
| **JIT Compiler** | ホットスポット情報の共有と実行エンジンの切り替え | `execution_context`, 履歴バッファ |
| **Debugger** | ブレークポイント判定と実行状態の可視化 | `debug_handler_table`, `execution_context` |
| **vSoC** | 実行制御（step）と協調型マルチタスク（yield）の管理 | `execution_context` |

## 6. 制約達成の方策

### 6.1 性能制約と方策
<!-- traceability: {ThreadedInterpreter} -->
- **目標**: WAMRインタープリタを上回る実行速度を達成する。
- **方策**: 直接末尾呼び出しによる分岐削減と、ホットスポット検出による JIT 移行を組み合わせる。

### 6.2 メモリ制約と方策
<!-- traceability: {ThreadedInterpreter} -->
- **目標**: 64KB RAM環境で動作可能とする。
- **方策**: `execution_context` と `call_frame` を最小化し、スタック領域を固定サイズ化する。

### 6.3 安全性制約と方策
<!-- traceability: {META_FaultIsolation} {MemoryBoundaryCheck} -->
- **目標**: ゲストの暴走を確実に隔離する。
- **方策**: `sp_boundary` と `memory_size` による境界チェックを実施する。Safepointでの `interrupt-event` 保留処理により安全な割り込み処理を行う。

## 7. 形式検証 (Formal Verification)
<!-- traceability: {ThreadedInterpreter} {InterpreterContextStackless} {PositionIndependentCode} -->
本コンポーネントのスタック整合性および実行状態遷移は、Python `pyModelChecking` を用いた形式検証モデルによって数学的に証明されている。 `{VERIFY_FORMAL}`

### 7.1 スタック境界とプルーニング不変条件モデル (`formal/interpreter_stack_model.py`)
3つの独立スタック（`OperandStack`, `LocalStack`, `control_frame`）の境界独立性を検証する。分岐脱出（`br` / `br_if`）時のスタックプルーニング不変条件も検証する。
- **検証特性 (CTL/LTL)**:
  - 相互独立性: いずれか1本のスタックの伸長・収縮が、他スタックの境界やオフセットを侵食しない。
  - プルーニング正当性: 分岐脱出時にスタック高さが対象フレームの退避値へ巻き戻される。かつ結果アリティ分の戻り値が正しく保存される。
  - フレームリーク防止: `if` 条件不成立時に無効なブロックフレームがスタックへ残留しない（`{GOTCHA-INTP-03}`）。
- **変異検査 (`guards=False`)**: ガード条件を意図的に無効化した変異体を作成する。Kripke 構造上で特性式が反証されることを確認済みである。

### 7.2 状態遷移と実行境界モデル (`formal/vsoc_state_model.py`)
インタープリタ実行、トレース境界での Yield 判定、OSR フォールバック、トラップ処理の決定論的遷移を検証する。
- **検証特性 (CTL/LTL)**:
  - 活性 (Liveness): 実行可能状態から有限ステップでディスパッチまたは Yield へ到達する。
  - 安全性 (Safety): 不正命令や範囲外アクセスが発生した場合、即座に Trap 状態へ遷移し、後続命令を実行しない。
  - レジスタ同期: JIT とインタープリタの境界において、コンテキストレジスタ（R0〜R3）の状態が完全同期される。

## 8. 設計判断 (ADR)

### ADR-INTERP-01: トレース境界での協調的 Yield (`{ADR_TraceBoundaryYield}`)

- **ステータス**: 承認 (Approved)
- **コンテキスト**:
  COOS 協調型マルチタスク環境において、ゲスト WASM のインタープリタ実行を中断する粒度と Safepoint ポーリング頻度を設計する。インタープリタ自身はコルーチンにしない。インタープリタは vSoC から呼ばれ、値を返して終了する関数とする。協調的中断（`co_yield`）を判断・発行する責務は常に呼び出し元の vSoC に置く。
- **決定事項**:
  インタープリタの命令ハンドラは、命令ごとの精密なイベント検査やカウンタ更新を行わない。トレースの切れ目（基本ブロック末尾、ループ境界、関数呼出・復帰、または JIT 脱出境界）でのみ vSoC へ制御を返す。vSoC は戻り値を受け取るたびに Yield 判定と JIT キャッシュ再判定（`{Interpreter_LazyJITSwitch}`）を行う。
- **根拠とトレードオフ**:
  1. **ディスパッチ性能の最大化**: 命令ハンドラ内での条件分岐を排除し、`[[clang::musttail]]` による高速ダイレクトスレッド実行を維持する。
  2. **レジスタ・スタック整合性の保証**: トレース境界では TOS レジスタと3本の独立スタックが自然に整合するため、複雑なステート退避が不要となる。
  3. **有界レイテンシ**: 組み込み WASM の基本ブロック長は通常数命令から数十命令である。トレース境界での yield でリアルタイム応答性を満たす。
  4. **責務の分離**: インタープリタは実行のみを担当する。JIT キャッシュやスケジューリング判断は vSoC の責務とする。
- **影響範囲**:
  - `runtime_interpreter.md`, `runtime_vsoc.md`, `os_coos.md`, `jit_compiler.md`

### ADR-INTERP-02: i64 / f32 / f64 の Libgcc ランタイムヘルパー連携 (`{Libgcc_Runtime_Helper}`)

- **ステータス**: 承認 (Approved)
- **コンテキスト**:
  32ビット組み込み CPU において、64ビット整数演算および浮動小数点演算を実行する際、コンパイラ組み込みランタイムライブラリ（`libgcc`）のヘルパー関数を呼び出す必要がある。
- **決定事項**:
  `i64`, `f32`, `f64` 演算命令は、インタープリタおよび JIT の双方で実行する。専用のランタイムヘルパー関数（`fireball_rt_*`）経由で実行する（`{Libgcc_Runtime_Helper}`）。
- **根拠とトレードオフ**:
  1. **JIT ステンシルの軽量化**: 複雑な演算ルーチンを JIT ステンシル内にインライン展開しない。ランタイムヘルパー呼び出しに委譲する。これにより JIT ROM サイズ予算（8KB）を維持する。
  2. **ハードウェア差異の隠蔽**: FPU 搭載環境と非搭載環境のビルド切り替えをヘルパー実装内に局所化する。
  3. **保守性と検証容易性**: `libgcc` との ABI 境界がハンドラ単位で隔離され、テストおよび形式検証が容易になる。
- **影響範囲**:
  - `runtime_interpreter.md`, `jit_compiler.md`, `wasm_instruction_set.md`, `jit_stencil_catalog.md`

### ADR-INTERP-03: 制御フレームを専用スタックへ分離

- **ステータス**: 承認 (Approved)
- **コンテキスト**:
  当初の設計では全スタック要素を単一の統合バッファへ混在させていた。しかし制御命令の分岐は JIT トレースがインタープリタを介さずに直接解決できる。このとき制御フレームの積み下ろしは代行されない。同居しているとオペランドスタックの位置関係が壊れる危険があった。
- **決定事項**:
  `control_frame` を `LocalStack` および `OperandStack` と完全に切り離す。専用の固定容量バッファへ配置する。3本のスタック長は互いに独立して管理する。
- **根拠とトレードオフ**:
  1. **JIT 動作時の物理的安全性**: JIT トレースが `control_frame` を操作しなくても、専用領域へ分離されているためオペランドスタックの値や位置が乱れることはない。
  2. **静的分岐先解決の採用**: フレームの残留が生じても論理的破綻を防ぐ。分岐先解決はモジュールロード時の静的解析結果（事前計算済みのラベルPC）を直接使用する（`{GOTCHA-INTP-06}`）。
  3. **メモリ管理の明確化**: 3本の固定容量バッファに分離し、それぞれ個別にオーバーフローを検知する。
  4. **コールフレームとの責務差**: 関数呼び出しは常にインタープリタへ戻る境界である。本問題はループやブロック特有のものである。
- **影響範囲**:
  - `runtime_interpreter.md`, `jit_runtime.md`

### ADR-INTERP-04: オペランドスタックを LocalStack から分離

- **ステータス**: 承認 (Approved)
- **コンテキスト**:
  `call_frame` とローカル変数、オペランドスタックが同居している場合、関数復帰時に呼び出し先のフレームを飛び越えて戻り値をコピーする必要があった。
- **決定事項**:
  オペランドスタックを独立した固定容量バッファ（`OperandStack`）へ分離する。コールチェーン全体を1本の連続スタックとして貫く。
- **根拠とトレードオフ**:
  1. **戻り値コピーの完全排除**: 呼び出し先の頂点は呼び出し元の頂点の直後に位置する。関数復帰時、結果値はすでに呼び出し元が継続すべき位置に存在する。
  2. **コールフレーム構造の縮小**: オフセット計算が静的になり、フレーム構造体を縮小できる。
  3. **独立した境界検査**: 3本のスタックが独立してオーバーフローを検知する。
  4. **メモリ予算のトレードオフ**: バッファが分かれるため個別の容量設計を要するが、安全性を優先する。
- **影響範囲**:
  - `runtime_interpreter.md`
