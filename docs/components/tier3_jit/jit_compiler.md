# JIT コンパイラ コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM} {VERIFY_BENCHMARK}
<!-- evidence:
     formal: formal/jit_cache_model.py
     benchmark: benchmarks/zero_runtime_overhead_bench.py
     concept: concepts/jit_copy_patch_concept.py
     test: tests/jit_compiler_test_spec.md
-->

## 1. コンセプト
<!-- traceability: {LowLatencyJIT} {JIT_CopyAndPatch} {JIT_ZeroCompileCostTheorem} {SimpleJITArchitecture} {JIT_Encoder} {PositionIndependentCode} {SinglePassCompilation} -->
JIT Compiler は、WASMバイトコードを実行時にネイティブコードへ変換し、実行速度を向上させる。Execution Engine (`executor`) の一部として、インタープリタと一対の実行エンジンとして機能する。極小リソース環境（RAM 32KB〜64KB）において、コンパイルコストを極小化する「Zero Compile Cost」方針に基づき、最適化を省いた高速な **Copy-and-Patch** 方式を採用する。命令テンプレートは C++ `constexpr` アセンブラによりビルド時に確定され、実行時は単純なメモリコピーと特定箇所への定数書き込み（パッチ）のみを行う。 `{LowLatencyJIT}` `{JIT_CopyAndPatch}` `{JIT_ZeroCompileCostTheorem}` `{SimpleJITArchitecture}` `{JIT_Encoder}` `{PositionIndependentCode}` `{SinglePassCompilation}`

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} {JIT_CopyAndPatch} -->
本コンポーネントは **Tier 3 (詳細リーフコンポーネント: Leaf Component)** に属し、vSoC (`runtime_vsoc.md`) から分解された JIT コンパイルパイプライン、事前生成テンプレートのコピー＆パッチ結合、および C++ `constexpr` 命令エンコードを担当する。ランタイム側のエントリ検索・キャッシュ管理・ホットスポット検出は [`jit_runtime.md`](jit_runtime.md) が担当する。 `{META_3TierSeparation}` `{JIT_CopyAndPatch}`

### 2.1 JIT サブシステムのデコンポジション
<!-- traceability: {JIT_Encoder} {JIT_CopyAndPatch} -->
JITサブシステムは、以下の2つの独立した設計書に責務を分離して構成される。

- **[JIT Compiler (コード生成コア)](jit_compiler.md)**: 命令テンプレートを用いたネイティブコード生成（Copy-and-Patch Engine）および静的な命令エンコード DSL（constexpr Assembler）。 `{JIT_Encoder}` `{JIT_CopyAndPatch}`
- **[JIT Runtime (ランタイム管理)](jit_runtime.md)**: 実行履歴監視・ホットスポット判定（Hotspot Detector）、PC-アドレス変換検索（JIT Entry Index）、および 3面キャッシュローテーション。 `{SimpleJITArchitecture}` `{JIT_MultiBuffer_Cache}`

## 3. 静的モデル

### 3.1 データ構造
- **`CopyAndPatchEngine`**: WASM命令に対応するネイティブ命令テンプレートを選択・コピーし、即値・分岐先・APIポインタをパッチ適用するクラス。
- **`constexpr_assembler`**: C++の `constexpr` 機能を活用し、ビルド時に Thumb-2 / RISC-V 命令バイナリを型安全に静的生成する DSL。
- **命令テンプレート (`jit_template`)**: パッチスロットを含むネイティブ命令列の雛形（[JIT ステンシルカタログ](../../specs/jit_stencil_catalog.md) 準拠）。
- **JIT トレースヘッダ (`jit_trace_header`)**: キャッシュに書き込まれる各ネイティブトレースの先頭（`+0x00`）に配置される 16 バイト固定長のメタデータ構造体。

### 3.2 内部ブロック図
```mermaid
graph TD
    subgraph JIT_Compiler_Core
        Pipeline[jit_pipeline]
        Engine[CopyAndPatchEngine]
        ConstAsm[constexpr Assembler]
    end

    subgraph Runtime_Interface
        Runtime[jit_runtime]
        Cache[Active Code Cache]
    end

    Pipeline --> Engine
    ConstAsm -.->|build-time template generation| Engine
    Engine -->|write native code + patch| Cache
    Pipeline -->|register entry| Runtime
```

### 3.3 主要なクラス・構造体・定数

#### コンパイル単位とインタープリタ協調方針
<!-- traceability: {LowLatencyJIT} {SimpleJITArchitecture} {PositionIndependentCode} -->
- **関数/モジュール一括コンパイルの完全禁止**: 極小リソース環境（RAM 32KB〜64KB）におけるコンパイル遅延とメモリ消費をゼロ化するため、関数全体やモジュール全体の事前一括コンパイルは一切行わない。
- **純粋ベーシックブロック/トレース単位コンパイル**: 2-bit カードテーブル（カードマーキング表）で HOT（`10`）に達した直線命令列（基本ブロック / トレース）のみを、スケジューラのアイドル時（`idle_hook` 等）に Copy-and-Patch により 1 トレースずつオンデマンド生成する。
- **複雑命令のハンドラ直接委譲ポリシー (`{JIT_RuntimeAPI_Fallback}`)**:
  - **インライン展開対象（Primitive Inline Ops）**: `i32.const`, `local.get`, `local.set`, `i32` 算術・論理・シフト・単純比較など、固定長ステンシルで完結する高頻度・低複雑度の直線演算のみを JIT ステンシルとしてキャッシュに展開する。
  - **ハンドラ直接呼び出し/フォールバック対象（Complex Delegated Ops）**:
    1. 制御フロー・フレーム遷移: `BR`, `BR_IF`, `BR_TABLE`, `IF`, `ELSE`, `CALL`, `CALL_INDIRECT`, `RETURN`
    2. メモリ操作・システム連携: `memory.grow`, `memory.copy`, `memory.fill`, WASI/ホストシステムコール
    3. 複雑演算・例外検査 (`{Libgcc_Runtime_Helper}`): `f32`/`f64` 浮動小数点、64-bit 複雑数学（`libgcc` の `__divdi3` / `__adddf3` 等）、トラップ検査を伴う境界処理
    これら実装・検証が複雑化する命令は JIT 側で独自生成せず、**インタープリタの命令ハンドラ（`_HANDLERS[opcode]`）を直接呼び出すか、ランタイムヘルパーへフォールバックして委譲**する。これにより JIT ROM サイズを極小化（数KB）し、保守性と堅牢性を最大化する。
- **ハンドラ互換ディスパッチ**: JIT トレースエントリポイントは、インタープリタの命令ハンドラ（`opcode_handler`）と完全に同一の C/C++ 関数シグネチャを持ち、ディスパッチテーブルから直接呼び出しが可能である。

**ABI 規約と境界チェック・バックパッチング (`JITC-GOTCHA-01`〜`05`)**:
- **レジスタ整合性 (`JITC-GOTCHA-01`, `02`, `03`)**: JIT トレースとインタープリタは `__fastcall`（R0=IP, R1=stack_bot, R2=local_base, R3=tos）により共通の物理レジスタ規約を保持する。トレース生成時はホストアーキテクチャ（ARM/x64）の不変条件（呼び出し側退避レジスタの保全、スタックアライメント境界）を厳格に維持する。
- **境界チェックとバックパッチング (`JITC-GOTCHA-04`, `05`)**: トレース末尾の直接ジャンプ（チェイニング）およびインタープリタへの脱出境界において、PC の境界検査を必ず先行させる。前方参照ブロックへのジャンプオフセットは、コード生成完了後にバックパッチングにより不可分に書き換えられ、未解決ジャンプによる迷走実行を完全に防止する。
- **ARM MLS 命令のオペランド配置順序 (`JITC-GOTCHA-06`)**: ARM Thumb-2 の積和減算命令 `MLS Rd, Rn, Rm, Ra`（$Rd = Ra - Rn \times Rm$）を生成する際、減算の引かれる数（アキュムレータ）が第4オペランド $Ra$ に配置されるハードウェア仕様を遵守し、通常の乗算命令（$Rn, Rm$）との取り違えによる計算誤りを防ぐ。

#### コピーアンドパッチエンジン（CopyAndPatchEngine）クラス
<!-- traceability: {JIT_RegisterMapping} {ContextPointerRegister} {EnvironmentPointer} {ADR_TosCacheAsymmetry} {PositionIndependentCode} -->
テンプレートの解決とバイナリ操作をカプセル化する。インタープリタの `opcode_handler` と完全整合する `__fastcall` CPS 4引数呼び出し規約（`R0: ip`, `R1: stack_bot`, `R2: local_base`, `R3: tos`）に基づいて設計される。`env`（`vsoc_runtime`）は独立引数レジスタとしては廃止され、`stack_bot` が指す `execution_context` 内に完全内包される（ADR-INTERP-03）。

```c
// インタープリタ命令ハンドラおよび JIT トレース共通の C 呼び出し規約
// コメントは「実機 ARM AAPCS レジスタ / 実機 RISC-V ABI レジスタ / x86-64 ホストシミュレータ __fastcall レジスタ」の対応を示す。
// この4本は呼び出し境界でのみ使われ、jit_stencil_catalog.md のトレース本体内 assignable pool
// (ARM R4-R6, R8-R11 / RISC-V s1-s7) とは物理レジスタが重ならない別の割り当てである。
typedef int64_t (*opcode_handler_t)(
    uint32_t ip,            // ARM R0 / RISC-V a0 / x86-64 RCX: WASM プログラムカウンタ (head_pc)
    void*    stack_bot,     // ARM R1 / RISC-V a1 / x86-64 RDX: 実行コンテキスト (execution_context @ stack bottom, mem_base/size内包)
    void*    local_base,    // ARM R2 / RISC-V a2 / x86-64 R8:  ローカル変数配列基底ポインタ
    uint32_t tos            // ARM R3 / RISC-V a3 / x86-64 R9:  スタックトップ値 (Top of Stack)
);
```

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| テンプレート辞書 | WASM命令に対応するJITテンプレートの検索索引 | アクセス辞書 | `jit_template_map` |
| 命令テンプレート | WASM命令に対応するネイティブバイナリの雛形 | バイナリビュー | ROM参照（[JIT ステンシルカタログ](../../specs/jit_stencil_catalog.md) 準拠。Thumb-2 のみを収録し、RISC-V の物理ステンシルは別カタログとして今後定義する） |
| 位置独立性 (PIC) | 任意アドレス・キャッシュバンクで再コンパイル不要で動作 | 設計制約 | 絶対アドレス埋め込み禁止。`local_base` 相対、`stack_bot` 相対、`rel32` 相対分岐のみ `{PositionIndependentCode}` |

##### 物理レジスタマッピング一覧表
<!-- traceability: {JIT_RegisterMapping} {AAPCS_FastCall} -->
JIT トレースとインタープリタは呼び出し境界において CPS 4引数規約を共有し、トレース内部では assignable pool を用いることで物理競合を防止する（`JITC-GOTCHA-01`）。

| アーキテクチャ | 物理レジスタ | 規約上の役割 / CPS引数 | トレース内部での用途 | 退避・保護責務 |
| :--- | :--- | :--- | :--- | :--- |
| **ARM (Thumb-2)** | `R0` | `ip` (WASM PC) | 呼び出し境界引数 | Caller-saved |
| | `R1` | `stack_bot` (実行コンテキスト) | 呼び出し境界引数（`mem_base/size` ピン留め起点） | Caller-saved |
| | `R2` | `local_base` (ローカル配列基底) | 呼び出し境界引数 | Caller-saved |
| | `R3` | `tos` (Top of Stack) | 呼び出し境界引数（トレース開始時に一度読まれるのみ）。トレース内部では未使用——VM のオペランドスタックとは無関係 | Caller-saved |
| | `R4` | - | `TOS` (Top of Stack 最上段キャッシュ) | Callee-saved |
| | `R5` | - | `NOS` (Next on Stack 次段キャッシュ) | Callee-saved (境界でメモリへ同期、ダーティな場合) |
| | `R6` | - | `NNOS` (次々段スタックキャッシュ) | Callee-saved (境界でメモリへ同期、ダーティな場合) |
| | `R7` | `FP` (フレームポインタ) | 不可侵 | システム固定 |
| | `R8` | - | `mem_base` (ゲストリニアメモリ基底) | Callee-saved |
| | `R9` | - | `mem_size` (ゲストリニアメモリ長) | Callee-saved |
| | `R10` | - | `safepoint` (ポーリングフラグ) | Callee-saved |
| | `R11` | - | 汎用アサイナブルレジスタ | Callee-saved |
| **RISC-V** | `a0`〜`a3` | `ip, stack_bot, local_base, tos` | 呼び出し境界引数 (CPS 4引数) | Caller-saved |
| | `s1`〜`s7` | - | トレース内部アサイナブルプール (`s1: NOS`, `s4: mem_base`, `s5: mem_size`) | Callee-saved |
| | `s0/fp` | `FP` (フレームポインタ) | 不可侵 | システム固定 |

#### トレース境界不変条件とスタックフレーム整合性 (Trace Boundary Invariants)
<!-- traceability: {LowLatencyJIT} {PositionIndependentCode} {JIT_RuntimeAPI_Fallback} -->
JIT トレースとインタープリタが同一の UnifiedStack 上でシームレスに相互運用するため、以下の 3 つの不変条件を厳格に保持する：

1. **スタック自己完結性不変条件 (Stack Self-Containment Invariant)**:
   - JIT コンパイル対象とする BasicBlock は、**命令走査中の累積スタック深さが 0 未満（`stack_depth < 0`）に落ちない自己完結ブロックのみ**とする。
   - 先頭で `local.set` や二項演算が先行し、呼び出し元のオペランドスタック上の値を前提とするブロックは JIT 化せず、インタープリタがスタック整合性を保持して安全に実行する。
2. **トレース境界でのメモリ同期不変条件 (Memory Synchronization at Trace Boundary)**:
   - トレース境界（トレース終了時、分岐時、ハンドラ呼び出し時、Safepoint 到達時）では、レジスタ上のキャッシュされた値のうちスタックトップ（TOS `R4`）はそのままレジスタに残し、スタック次段キャッシュ（NOS `R5`）以降のダーティな値および更新されたローカル変数を確実にメモリ（`stack_bot` / `local_base` 配列）へスピル（書き戻し）し、未確定のレジスタ状態を次のブロックやインタープリタへ持ち越さない。
3. **制御フロー・コール境界のインタープリタ委譲不変条件 (Control & Call Delegation Invariant)**:
   - `BR`, `BR_IF`, `BR_TABLE`, `IF`, `CALL`, `CALL_INDIRECT`, `RETURN` 等の制御命令は JIT トレース内に含めず、その直前で BasicBlock を終端する。スタック巻き戻し（`_do_branch`）やコールフレーム（`call_frame`）生成はインタープリタ（または専用チェイニングハンドラ）に委譲する。

#### JIT トレース物理メモリレイアウト (`jit_trace_header`)
<!-- traceability: {JIT_LazyChaining} {SimpleJITArchitecture} {PositionIndependentCode} -->
JIT キャッシュ内に書き込まれる各トレースは、**先頭に 16 バイト固定長のメタデータヘッダを持ち、直後（`+0x10`）からネイティブ命令列（PIC Code Stream）が展開される**。エントリポイントは `trace_base + 0x10`。

| オフセット | フィールド名 | 型 | 説明 |
| :--- | :--- | :--- | :--- |
| `+0x00` | `head_wasm_pc` | `uint32_t` | トレース開始 UnifiedPC（`(func_index << 16) \| bytecode_offset`） |
| `+0x04` | `trace_byte_size` | `uint16_t` | ヘッダ含むトレース全体の総物理バイトサイズ |
| `+0x06` | `flags` | `uint8_t` | 状態フラグ（`0x01: PROMOTED`, `0x02: LOOP_HEADER`） |
| `+0x07` | `variant_id` | `uint8_t` | ステンシルバリアント／TOSレジスタ割り当て状態 ID |
| `+0x08` | `chain_next_pc` | `uint32_t` | 直結チェイン先 UnifiedPC |
| `+0x0C` | `chain_target_addr` | `uint32_t` | チェイン先ネイティブアドレス（初期値: 復帰スタブ） |
| `+0x10` | コードストリーム | 可変長 | ネイティブ Thumb-2 / RISC-V 命令列（PIC Code Stream） |

#### `constexpr_assembler` (DSL)
<!-- traceability: {JIT_Encoder} {META_ZeroCostAbstraction} -->
ビルド時に Thumb-2 / RISC-V 命令バイナリを静的エンコードし、実行時の命令生成オーバーヘッドを完全排除する。

| 構造体 | 機能 | ビット幅 |
| :--- | :--- | :--- |
| `fireball::arm::add_imm` | Thumb-2 即値加算命令エンコーダ | 32bit |
| `fireball::arm::ldr_imm` | Thumb-2 即値ロード命令エンコーダ | 32bit |
| `fireball::riscv::i_type`| RISC-V I-Type 命令エンコーダ | 32bit |

## 4. 動的モデル

### 4.1 アルゴリズム
<!-- traceability: {JIT_CopyAndPatch} {JIT_RuntimeAPI_Fallback} {SinglePassCompilation} -->
1. **トレース解析 & テンプレート選択**: WASM PC から始まる基本ブロックを 1 パス走査し、対応する事前生成ステンシルテンプレートを選択する。
2. **メモリコピー & パッチ適用**: アクティブキャッシュへテンプレート命令列をコピーし、即値オペランドや相対分岐オフセットをインプレースでパッチする。
3. **AAPCS 境界フォールバック**: 複雑な命令やホスト関数呼び出しはランタイム API 呼び出しスタブを生成してフォールバックする。 `{JIT_RuntimeAPI_Fallback}`
4. **命令キャッシュ同期**: パッチ完了後、`__DSB()` および `__ISB()` バリアを発行して命令キャッシュを同期する。
5. **インタープリタ連携とハンドラ直接呼び出し (Low-Overhead Interop & Direct Handler Call)**:
   - JIT トレースとインタープリタの命令ハンドラ（`opcode_handler`）は完全に同一の CPS 4引数呼び出し規約（`R0: ip, R1: stack_bot, R2: local_base, R3: tos`）を共有する。
   - JIT トレースは直線的な算術・ローカル変数演算に専念し、複雑な制御フロー（`BR`, `BR_IF`, `BR_TABLE`, `CALL`, `RETURN`, `IF`）やホストシステムコールに達した際は、**JIT 内で複雑なジャンプ処理を重複実装せず、直接インタープリタのハンドラテーブル（`handler_table[opcode]`）へ末尾ジャンプ（Tail Jump / `BX`）するか、戻り値 `next_ip` を返却してインタープリタへ即座にフォールバック**する。
   - レジスタ規約が完全一致しているためコンテキスト再構築コストはゼロであり、JIT の軽量性（Zero Compile Cost）と完全な制御フロー安全性を両立する。 `{JIT_RuntimeAPI_Fallback}` `{ADR_TosCacheAsymmetry}`

#### JIT トレース検索 & 3面キャッシュ代謝オーケストレーション
<!-- traceability: {JIT_MultiBuffer_Cache} {JIT_OldestOnly_Promote} -->
3段直接 JIT 検索および 3面キャッシュローテーションの詳細は、ランタイム管理の正本である `{JIT_MultiBuffer_Cache}` を参照すること。コンパイラコアは生成されたネイティブトレースの登録と命令同期を `{JIT_MultiBuffer_Cache}` に委譲する。

#### トレース・チェイニング（連鎖実行）と専用分岐ハンドラ分離
<!-- traceability: {JIT_LazyChaining} -->
検索オーバーヘッドを排除し、ネイティブコード同士を直接接続（チェイニング）するため、**純粋インタープリタ用のジャンプハンドラと、JIT トレースから呼び出される専用チェイニングハンドラ（`jit_chain_branch_handler`）を明確に分離**する。

```mermaid
graph TD
    JITTrace[JIT Trace Exit / Branch] --> JITHdr[JIT-Specific Chaining Handler]
    JITHdr --> Lookup{Target in JIT Cache?}
    Lookup -->|Hit: Active/Warm| Patch[In-place Patch chain_target_addr + Tail Call BX]
    Patch --> TargetTrace[Target JIT Trace Native Exec]
    Lookup -->|Miss: Uncompiled| Fallback[Record Hotspot + Return to Interpreter Loop]

    Interp[Interpreter Step] --> InterpHdr[Pure Interpreter Branch Handler]
    InterpHdr --> InterpLoop[Direct _do_branch -> Next Opcode Dispatch]
```

1. **ハンドラの責務分離**:
   - **純粋インタープリタ用ハンドラ (`_h_br` 等)**: 単純にスタックを巻き戻して次の WASM PC を算出し、ディスパッチループへ戻る（JIT 探索やパッチのオーバーヘッドが完全ゼロ）。
   - **JIT 専用チェイニングハンドラ (`jit_chain_branch_handler`)**: JIT トレースから呼び出され、分岐先 `UnifiedPC` を解決した上で JIT キャッシュを照会する。
2. **オンデマンド・インプレースパッチ（Lazy Chaining）**:
   - 分岐先が既にコンパイル済みであれば、呼び出し元トレースの末尾スロット（`chain_target_addr`）をターゲットのネイティブアドレスへ書き換え、被チェイン逆引きテーブル（`inbound_chains`）へ登録する。
   - インタープリタディスパッチループを介さず、**そのままターゲットのネイティブトレースへ直接 tail-call（`BX`）してネイティブ実行を継続**する。
3. **未コンパイル時の遅延昇格**:
   - 分岐先が未コンパイルの場合のみ、HistoryRing に分岐先 PC を記録した上でインタープリタ復帰スタブ経由でインタープリタへ戻る。次回以降ホット化してコンパイルされた際にチェイニングが確立される。
4. **局所再チェイニングとアンリンク（O(k) Bounded Re-chaining & Unlinking）**: チェイニング確立時にターゲットの属するバンクの **被チェイン逆引きテーブル（`inbound_chains`）** にソースの JIT エントリインデックスを登録する。ターゲットが Active $\to$ Warm $\to$ Oldest へ推移する間はキャッシュ内のコードは依然として有効に常駐しているため、チェイニングは維持され JIT 実行が継続する。**Oldest バンクがパージされ新 Active へローテートするまさにその瞬間**、破棄される Oldest バンクの `inbound_chains` に登録された被チェインエントリ（$k$ 件）のみを直接参照する。
    - **ターゲットが Oldest-Only Promotion 等により Active/Warm へ昇格（Promote）している場合**: チェインスロットを昇格先のアドレスへ **再チェイニング（Re-chaining）** し、昇格先バンクの `inbound_chains` へ登録を移譲する（インタープリタへフォールバックさせず、ネイティブ直接チェイン実行を維持）。
    - **ターゲットが昇格せず完全にキャッシュアウト（Evict）する場合のみ**: チェインスロットをインタープリタ復帰スタブにアンパッチする。
    これにより、全走査オーバーヘッド $O(N)$ を完全排除しつつ、生存トレース間のネイティブ実行効率を最大化する。 `{JIT_LazyChaining}`
5. **制御コードスキップ表（Control Skip Table）と直接チェイニング連携**:
   - **制御構文デリミタの読み飛ばし**: WASM 基本ブロック末尾の制御命令（`BLOCK`, `LOOP`, `IF`, `ELSE`, `END` 等）は、先行ブロックの実行完了と後続ブロックの先頭命令の間に位置する。JIT ネイティブ実行同士を直接チェイニング（`chain_next`）する際、先行ブロック終端 PC（delimiter PC）から制御構文を読み飛ばしたフォールスルー先（fallthrough head PC）を即座に解決する必要がある。
   - **スキップ表の事前生成と非所有ビュー借用 (`ReadOnlyRadixBinaryTreeStorage` / `RadixBinaryTreeView`)**: モジュールロード時（`prepare_module`）に全関数の基本ブロック境界を走査し、`delimiter_pc -> fallthrough_head_pc` の対応関係を `ReadOnlyRadixBinaryTreeStorage` に格納する。ランタイムエンジンは非所有ビューである `RadixBinaryTreeView`（`control_skip_tree`）をゼロコピーで借用保持する。
   - **キーのバイトオーダー反転 (`bswap32`) による Radix Table 圧縮**: PC（16bit 関数インデックス + 16bit 命令オフセット）は上位ビットが関数番号に偏るため、キーのバイトオーダーを `bswap32` で反転して命令オフセットの変化を高エントロピーな最上位ビットに射影する。これにより、わずか 17 要素（68 バイト）の極小 Radix Table（`radix_shift = 28`）で $O(1)$ スキップ先境界解決を実現する。
   - **双方向チェイニング解決フロー**:
     - **後方チェイニング (Backward Chaining)**: 新規トレース登録時、`succ = trace.next_pc` を `control_skip_tree.find(bswap32(succ))` で解決し、スキップ先が Active/Warm に常駐していれば `trace.chain_next = succ` を即座に接続する。
     - **前方チェイニング (Forward Chaining)**: キャッシュ常駐トレース `resident_t` の `res_succ = resident_t.next_pc` を同様にスキップ解決し、新登録トレースの `head_pc` と一致すれば `resident_t.chain_next = trace.head_pc` をインプレースパッチする。

#### 統合 Tiered ランタイムエンジン・コンセプトコード (`../tier2_runtime/concepts/runtime_engine_concept.py`)
インタープリタ実行、2-bit Hotspot 検出、Copy-and-Patch JIT コンパイル、3面マルチバッファキャッシュ（Active/Warm/Oldest）、および MPU W^X 保護プロトコルを統合した自己完結実行シミュレーションは [`../tier2_runtime/concepts/runtime_engine_concept.py`](../tier2_runtime/concepts/runtime_engine_concept.py) を参照。

#### ホットスポット判定 (yield 時)
<!-- traceability: {JIT_LazyChaining} -->
1. **履歴走査**: インタープリタの実行サイクル中に記録、蓄積された「実行履歴バッファ」を走査する。
2. **状態更新**: カードマーキング表の状態が「頻出」に達した命令オフセットを「コンパイル待ち列」（固定容量 LIFO キュー、`jit_runtime.md` §3.1）に投入する。容量に達した時点でバッチコンパイル（下記）を即座に実行して空にするため、この固定容量を上回ることはない。 `{GLOBAL_Policy_Memory}`
3. **遅延チェイニング制御**: ホットスポットと判定されてコンパイルキューへ投入されたトレースは、JITコードの末尾においてインタープリタ実行環境へ正しく復帰（遷移制御）するためのディスパッチャ・スタブが初期値としてチェイニング（連結）され、遅延チェイニングを実現する。 `{JIT_LazyChaining}`

#### バッチコンパイル (周期実行またはアイドル時)
<!-- traceability: {JIT_ReverseCompilationOrder} {GLOBAL_PeriodicTask} {GLOBAL_IdleDetection} -->
1. **キューの取得**: 「コンパイル待ち列」から対象の命令オフセットを**逆順（LIFO）**で取り出す。 `{JIT_ReverseCompilationOrder}`
2. **コンパイル実行**: 後続のトレースを先にコンパイルすることで、先行するトレースのリンク時（Patching 時）にターゲットが既にキャッシュ内に存在する確率を上げ、即時チェイニングを実現する。
3. **補足**: COOSの `register_periodic_callback` または `set_idle_hook` により実行される。これにより、実行スレッドのブロッキング時間を抑える。 `{GLOBAL_PeriodicTask}` `{GLOBAL_IdleDetection}`


#### Copy-and-Patch ステンシル結合 & バックパッチング手順（手順アクティビティ図）
<!-- traceability: {JITC-GOTCHA-01} {JITC-GOTCHA-02} {JITC-GOTCHA-05} {JIT_CopyAndPatch} -->
BasicBlock 走査、事前コンパイル済みステンシルのコピー、即値・レジスタパッチ、およびトレース末尾バックパッチングの決定論的手順を示す。

```mermaid
flowchart TD
    Start(["Begin JIT Compilation of BasicBlock"]) --> InitEmit["Emit 16-byte jit_trace_header at trace_base"]
    InitEmit --> LoopOps["Fetch Next WASM Opcode in Block"]

    LoopOps --> SelectStencil["Select Precompiled Thumb-2 Stencil from ROM Catalog"]
    SelectStencil --> CopyBytes["Copy Stencil Binary Bytes to JIT Code Cache"]
    CopyBytes --> RelocImm{"Stencil has Relocation Holes (Immediates / Offsets)?"}

    RelocImm -- "Yes" --> PatchReloc["In-place Patch Constants (e.g. imm_lo, imm_hi, slot_offset)"]
    RelocImm -- "No" --> CheckLast{"Last Opcode in BasicBlock?"}
    PatchReloc --> CheckLast

    CheckLast -- "No" --> LoopOps
    CheckLast -- "Yes" --> EmitExit["Emit Trace Boundary Guard & Register Spill Sequence"]
    EmitExit --> Backpatch["Backpatch Relative Branch Offsets (B/BL/BX) to Exit Stub"]
    Backpatch --> FinalizeSize["Write total trace_byte_size into Header (+0x04)"]
    FinalizeSize --> Complete(["JIT Machine Code Ready for W^X Commit"])
```

### 4.2 状態遷移図
<!-- traceability: {JIT_LazyChaining} {JIT_ReverseCompilationOrder} {GLOBAL_PeriodicTask} {GLOBAL_IdleDetection} -->
```mermaid
stateDiagram-v2
    state "Interpreting" as Interp
    state "Detecting (at yield)" as Detect
    state "Background (Idle/Periodic)" as Background
    state "Compiling" as Compile

    [*] --> Interp
    Interp --> Detect: yield / trap
    Detect --> Background: Queue populated
    Detect --> Interp: No hotspot
    Background --> Compile: Trigger
    Compile --> Background: Done
    Background --> Interp: Task Wakeup
```

### 4.3 内部シーケンス
<!-- traceability: {JIT_LazyChaining} {JIT_ReverseCompilationOrder} {GLOBAL_PeriodicTask} {GLOBAL_IdleDetection} -->
#### JITコンパイルおよび検索シーケンス
サイクル全体を駆動するのは常に vSoC (V) であり、Interpreter (I) はディスパッチされた側の実行エンジンとして現れるだけで、履歴処理やキャッシュ検索を自ら開始することはない（`{Interpreter_LazyJITSwitch}`）。
```mermaid
sequenceDiagram
    participant V as vSoC
    participant D as Detector
    participant E as Engine
    participant C as Cache
    participant S as JIT Searcher
    participant I as Interpreter

    Note over V, S: co_yield 時のバッチ処理（vSoC が駆動）
    V->>D: Process History Buffer
    D->>D: Update card marking table
    D->>E: Push HOT PC to Queue
    E->>C: Copy Template & Patch
    E->>S: Register Entry (PC, Offset)
    
    Note over V, S: 実行時の検索（vSoC の step() から毎回呼び出す）
    V->>S: Lookup(PC)
    alt Card state != COMPILED
        S-->>V: Fallback (Fast Exit)
        V->>I: exec_trace(pc) -- インタープリタへディスパッチ
    else Card state == COMPILED
        S->>S: Search Bank 0 (Active)
        alt Active Hit
            S-->>V: Native Code Address
        else Active Miss
            S->>S: Search Bank 1 (Warm)
            alt Warm Hit
                Note over S: Observation window - no promotion copy
                S-->>V: Native Code Address
            else Warm Miss
                S->>S: Search Bank 2 (Oldest)
                alt Oldest Hit
                    S->>S: Promote to new Active (Copy)
                    S-->>V: Native Code Address
                else Oldest Miss
                    S->>S: Enqueue PC in LIFO queue, card stays COMPILED
                    S-->>V: Fallback (Return NULL)
                    V->>I: exec_trace(pc) -- インタープリタへディスパッチ
                end
            end
        end
    end
```

## 5. インターフェース定義

### 5.1 公開API
外部から利用可能なオブジェクト指向APIを定義する。

#### 初期化（initialize）
<!-- traceability: {META_ConfigurableSystem} -->

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | コードキャッシュ領域、管理テーブル、およびカードマーキング表の初期化を行う。 |
| シグネチャ | `initialize(ctx: 可変参照, config: const参照) -> 結果型` |
| 引数 | `ctx`: JITコンテキスト (`jit_context`) への可変参照<br>`config`: JIT構成 (`jit_config`) への読取専用参照 |
| 戻り値 | 結果型 (成功時は空、エラー時はエラーコード) |
| 事前条件 | 設定パラメータが一貫しており、静的に確保されたメモリの範囲を超えていないこと。 |
| 事後条件 | カードマーキング表がクリアされ、キャッシュが空の状態になる。 |
| 不変条件 | 実行中に `config` の値を変更してはならない。 |
| エラー時の挙動 | メモリ割り当ての不備がある場合はエラーを返す。 |
| 補足 | `{META_ConfigurableSystem}` の方針に基づき、基本的にはブート時に一度だけ呼び出される。 |

#### トレース検索（lookup_trace）
<!-- traceability: {META_ConfigurableSystem} -->

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 指定されたWASMプログラムカウンタ(PC)に対応する、コンパイル済みのネイティブコードの実行アドレスを高速に検索する。 |
| シグネチャ | `lookup_trace(pc: address) -> result<address, bool>` |
| 補足 | カードマーキング表の状態が `COMPILED` でない場合は即座に失敗を返す。その後、`harness` 経由でエントリ索引を検索する。本機能は、ヘッダファイルで定義されたマクロ（`FB_CONF_JIT_CACHE_SIZE`等）に基づき、システムのメモリマップや検索範囲等のパラメータが固定された状態で動作する。 `{META_ConfigurableSystem}` |

#### カード状態取得（get_card_state）
<!-- traceability: {META_ConfigurableSystem} -->

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 指定したPCが属するカードの状態（2-bit）を取得する。 |
| シグネチャ | `get_card_state(pc: address) -> u8` |
| 補足 | 本機能は、コンパイル時に固定されたカード境界シフト値（`FB_CONF_JIT_CARD_SHIFT`等）のマクロ定義に基づき、PC値からカードインデックスへの変換を高速に行う。 `{META_ConfigurableSystem}` |

#### 検索範囲取得（get_search_range）
<!-- traceability: {META_ConfigurableSystem} {FlatViewNarrowing} {META_BinarySearch} -->

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | JITエントリグループインデックスを用いて、指定されたWASM PCに対応する探索区間を $O(1)$ で `fireball::flat_map_view` へ絞り込む。生の添字対ではなくビューを返すことで、呼び出し側が区間を誤った配列と組み合わせる余地をなくす（`{FlatViewNarrowing}` を参照）。該当グループが存在しない場合は空ビューを返す。 `{FlatViewNarrowing}` `{META_BinarySearch}` |
| シグネチャ | `get_search_range(bank_idx: u8, pc: address) -> flat_map_view<u32, code_offset>` |
| 補足 | 本機能は、ヘッダファイルで定義されたJITエントリグループサイズおよび最大登録件数のマクロ定数（`FB_CONF_JIT_ENTRY_GROUP_SHIFT`等）に基づき、二分探索範囲をコンパイル時に静的に制限して計算する。 `{META_ConfigurableSystem}` |

#### バッチコンパイル処理（process_batch_compile）
<!-- traceability: {META_ConfigurableSystem} -->

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | vSoC が収集した履歴を基にコンパイルを実行する。 |
| シグネチャ | `process_batch_compile(ctx: 可変参照, harness: 構造体への参照) -> void` |
| 引数 | `ctx`: JITコンテキスト への可変参照<br>`harness`: JITハーネス への参照 |
| 戻り値 | void |
| 補足 | vSoC が `co_yield` を発行する際に呼び出され、アイドル時間等を活用して処理される（`co_yield` の判定・発行はインタープリタや `executor` 自身ではなく vSoC が行う）。 |

### 5.2 URI/IPCインターフェース
<!-- traceability: {META_ConfigurableSystem} -->
本コンポーネントは vSoC の内部ライブラリであり、直接のIPCインターフェースは持たない。

## 6. 制約達成の方策

### 6.1 性能制約と方策
<!-- traceability: {JIT_CopyAndPatch} {JIT_RegisterMapping} -->
- **目標**: コンパイルレイテンシを最小化し、WAMRインタープリタを上回る実行速度を実現。
- **方策**: 
    - `{JIT_CopyAndPatch}`: 複雑な最適化を省き、テンプレートコピーのみでコンパイルを完了。
    - `{JIT_RegisterMapping}`: `Context`, `StackTop`, `WASM_PC` を物理レジスタに固定し、メモリアクセスを削減。
    - `Card Marking (O(1)) + Binary Search`: カードマーキング表による $O(1)$ 事前フィルタと二分探索により、高速な検索を実現。

### 6.2 安全性制約と方策
<!-- traceability: {PositionIndependentCode} {MemoryBoundaryCheck} {FastAddressCheck} {SimpleJITArchitecture} -->
- **目標**: 不正なコード実行および W^X 違反の防止。
- **方策**: 
    - `{PositionIndependentCode}`: 生成コードを位置独立とし、配置場所の自由度を確保。
    - `Cache Capacity Check`: コード生成時にキャッシュ溢れを厳密にチェックし、溢れた場合は 3面リングローテーションにより Oldest バンクを破棄して再利用する。これはキャッシュ容量管理であり、`{MemoryBoundaryCheck}`（ゲストメモリアクセスの隔離）とは別の関心事である。 `{SimpleJITArchitecture}`
    - `{MemoryBoundaryCheck}`: 生成コードに埋め込むゲストメモリアクセスの境界チェック。`FastAddressCheck` のサイズ比較命令（`CMP addr, mem_size; BHS.W <trap>`、マスクは使わない）により、ゲストリニアメモリ範囲外へのロード/ストアを検出した時点でインタープリタへのフォールバックへトラップする（境界外アドレスを黙って折り畳んで継続することはない）。 `{MemoryBoundaryCheck}` `{FastAddressCheck}`
    - `MPU W^X 保護`: Cortex-M33 PMSAv8 MPU を用い、JIT パッチ書き込み時は `RW+XN`、ネイティブ実行時は `RO+X` に切り替え、`__DSB(); __ISB();` メモリ・命令同期バリアを発行する。書き込みと実行の同時許可（RWX）を物理的に排除する。`formal/jit_cache_model.py` により変異検査付き形式モデルとして検証。

## 7. 形式検証・テスト仕様との対応

### 7.1 検証対象の不変条件
- **位置独立性 (PIC)**: 生成された Thumb-2 / RISC-V バイナリが絶対アドレスに依存せず、任意のキャッシュバンクで再コンパイル不要で動作すること（`INT-40`, `JITC-40`）。
- **トレース境界メモリ同期**: トレースの真の脱出（後続の常駐トレースへ直接チェインしない場合）時に、キャッシュ中のスタックトップ（`R4: TOS`）・次段（`R5: NOS`）およびローカル変数がメモリへ確実に同期されること。直接チェイン分岐（`{JIT_LazyChaining}`）ではレジスタ状態がそのまま後続トレースへ引き継がれるため、この同期は発生しない（`INT-41`, `JITC-52`）。
- **W^X メモリ保護**: JIT パッチ書き込み時の `RW+XN` と実行時の `RO+X` の分離（`jit_cache_model.py`, `JITC-42`）。

### 7.2 テスト仕様書との連携
本コンポーネントの単体テストケース（JITC-01〜JITC-53, JITC-GOTCHA-01〜06）は、[`tests/jit_compiler_test_spec.md`](tests/jit_compiler_test_spec.md) を正本として定義する。なお、3面キャッシュの検索・昇格・代謝の組み合わせ直交表は、ランタイム管理のテスト仕様書 [`tests/jit_runtime_test_spec.md`](tests/jit_runtime_test_spec.md) を正本とする。

## 8. 設計判断 (ADR)
<!-- traceability: {ADR_ScalableCodeOffset} {ADR_SafeQueuingOnHotMiss} {ADR_TosCacheAsymmetry} {JIT_LazyChaining} {JITC-GOTCHA-07} -->

- **決定事項**: `{ADR_TosCacheAsymmetry}`
  - **背景**: JIT トレースはスタックマシンである WASM のオペランドを `R4`/`R5` に TOS/NOS としてキャッシュすると大きく速くなるが、インタープリタのオプコードハンドラは AAPCS 引数レジスタ `R0`〜`R3` を CPS 境界の呼び出し引数 `(ip, stack_bot, local_base, tos)` で使い切っており、2段目のスタック値（NOS）を保持する余地がない。両者は `__fastcall` CPS シグネチャを共有するため、この差をどう扱うかを決める必要がある。
  - **選択肢と評価**:
    - 案1: CPS を 4 引数化（`ip, stack_bot, env, tos`）し、インタープリタ側も TOS をレジスタ保持する。遷移コストは真にゼロになるが、`{ContextPointerRegister}` の統合スタック化でせっかく解放した `R3` スクラッチを再び失い、全ハンドラが TOS 不変条件の維持義務を負う。
    - 案2: JIT からも `R4`/`R5` を廃し、両者ともオペランドを統合スタックのメモリ上でのみ扱う。記述は最も単純になるが、スタックマシンに対する唯一かつ最大の最適化余地を捨てることになり、`{LowLatencyJIT}`（WAMR 超え）の達成が困難になる。
    - 案3: 非対称を許容し、JIT トレース内部でのみ `R4`/`R5` を TOS/NOS として使用する。トレース脱出時にダーティ値を統合スタックへ書き戻す。
  - **結論**: 案3を採用する。
  - **評価**: 「低オーバーヘッド」の根拠を **コンテキスト再構築がゼロであること** に限定し、トレース脱出時のダーティな TOS/NOS（`R4`/`R5`）の書き戻し（`STR` × 2）を明示的な有界極小コストとして仕様に記載する。JIT トレースは複数 WASM 命令にまたがるため、この 2 命令はトレース長で償却され、トレース内部で得られる TOS/NOS キャッシュの利得を下回る。インタープリタは `R4`/`R5` について何の不変条件も負わない（callee-saved として通常どおり扱う）ため、ハンドラ実装の複雑度も増えない。
  - **トレース境界の2種類のエントリと2種類のエグジット**: この設計は現在 `R4-R6, R8-R11`（計7本）のトレース単位任意割当プールへ一般化されているが、境界の性質は「真の脱出/新規進入」と「直接チェイン」の2系統に分かれ、混同してはならない（`docs/specs/jit_stencil_catalog.md` 3.1）。
    - **新規エントリ / 真の脱出**: インタープリタ・ディスパッチャから初めて呼び出される場合は Callee-saved 全域退避のプロローグを通過する。対応する真の脱出（後続の常駐トレースが存在しない、またはこのトレースがチェインの終端）では、ダーティなスタックキャッシュ（`R4`/`R5`）を `stack_bot` 相対の正準アドレスへ `STR` で書き戻した上で Callee-saved レジスタを `POP` 復元してリターンする。呼び出し規約上の戻り値レジスタは一切経由しない——VM のオペランドスタック状態と C/AAPCS の戻り値には何の関係もない（`{JITC-GOTCHA-07}`）。
    - **チェイン・エントリ / 直接チェイン分岐**: `{JIT_LazyChaining}` によって後続トレースが常駐と解決済みの場合、真の脱出の代わりに後続トレースのチェイン・エントリ（後続トレース自身のプロローグ直後のオフセット）への直接分岐（`B.W`、バックパッチ）を配置する。フラッシュも `POP` も発生せず、レジスタ状態（`R4-R6` のキャッシュ値を含む）は分岐を跨いでそのまま生き続ける。後続側もチェイン・エントリではプロローグを経由しないため、両者を合わせても Callee-saved の退避・復元は連結全体でちょうど1回ずつしか発生しない——1つの通常のトレースと同じ AAPCS 収支に収まる。したがって連結された2トレースが異なるバリアントを選んでいる場合はこの経路を使えず、`{ADR_TosCacheAsymmetry}` の通り真の脱出（メモリ経由の受け渡し）にフォールバックする。
  - **ローカル変数アクセスの静的オフセット畳み込み (`ContextPointerRegister`)**: 各関数フレームにおけるローカル変数のアドレスは、スタックボトムから `frame_offset + local_offset + idx * 4` として定まる。JIT コンパイル（Copy-and-Patch）は同一関数フレームのコンテキスト下で行われるため、この合成オフセットはトレース生成時に即値定数としてステンシルにパッチ（`[R1, #offset]`）される。これにより、実行時に追加のベースレジスタ（`local_base`）を消費することなく、固定のスタックボトム基底ポインタ `{ContextPointerRegister}`（`R1: stack_bot`）から直接1命令でアクセスできる。`sp_offset` はトレース内部で独立したレジスタ役割を持たず、トレース脱出時にのみコンテキスト構造体の `sp_offset` フィールドへ書き戻される。

- **決定事項**: `{ADR_ScalableCodeOffset}`
  - **背景**: 16ビットの `code_offset` をそのまま使用すると、コードキャッシュが64KBに制限される。将来的に外部メモリ等を活用してキャッシュを拡張（例：512KB）する場合、このビット幅がボトルネックとなる。
  - **選択肢**:
    - 案1: `code_offset` を32ビットにする。エントリは `flat_map_view<u32, code_offset>`（キー: PC 4バイト + 値: `code_offset`）であるため、値が16ビット(2バイト)から32ビット(4バイト)になるとエントリ1件は6バイトから8バイトへ増加し、エントリテーブルのメモリ消費は約33%増加する。
    - 案2: 命令アライメント (`code_align_shift`) を利用してビットシフトして保持する。
  - **結論**: 案2を採用。 `actual_offset >> code_align_shift` を保持する。
  - **評価**: これにより、エントリテーブルのサイズを維持したまま、アライメントに応じたスケーラビリティを確保できる。最大キャッシュサイズは `65535 << code_align_shift` となる。
- **決定事項**: `{ADR_SafeQueuingOnHotMiss}`
  - **背景**: `COMPILED` 状態のカードで検索ミスが発生した場合、その場で同期コンパイルを行うか、キューイングするか。
  - **結論**: `Compile Queue` にプッシュし、インタープリタへフォールバックする。
  - **理由**: 同期コンパイルは実行ループ内での予測不可能なレイテンシ（ジッタ）の原因となるため。
