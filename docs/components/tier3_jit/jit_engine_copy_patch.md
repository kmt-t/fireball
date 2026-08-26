# Copy-and-Patch Engine コンポーネント設計書

## 1. コンセプト
<!-- traceability: {JIT_CopyAndPatch} {JIT_ZeroCompileCostTheorem} {LowLatencyJIT} {SinglePassCompilation} -->
Copy-and-Patch Engine は、WASM 命令に対応する事前生成されたネイティブコードテンプレートを結合・修正することで、ネイティブ実行バイナリを高速に生成する JIT コンパイラの核心部である。レジスタ割り当てや命令選択などの計算コストの高い最適化をビルド時にオフロードし、実行時は単純なメモリコピーと特定箇所への定数書き込み（パッチ）のみを行うことで、「Zero Compile Cost」を目指す。 `{JIT_CopyAndPatch}` `{JIT_ZeroCompileCostTheorem}` `{LowLatencyJIT}` `{SinglePassCompilation}`

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} {JIT_CopyAndPatch} -->
本コンポーネントは **Tier 3 (詳細リーフコンポーネント: Leaf Component)** に属し、JIT コンパイラ (`jit_compiler.md`) から分解された事前生成ネイティブ命令テンプレートのコピー＆パッチ結合エンジンを担当する。 `{META_3TierSeparation}` `{JIT_CopyAndPatch}`

## 3. 静的モデル

### 3.1 データ構造
- **`CopyAndPatchEngine`**: テンプレートの選択、コピー、およびパッチ適用を一括して行う主要クラス。
- **命令テンプレート**: パッチ用の「穴」を含むネイティブ命令列の雛形。
- **パッチ定義**: テンプレート内の修正箇所のメタデータ。

### 3.2 内部ブロック図
```mermaid
graph TD
    Queue[Compile Queue] -->|Pop PC| Engine[CopyAndPatchEngine]
    Engine -->|Write| Cache[Active Code Cache]
    Const[constexpr Assembler] -.->|Generate| Engine
```

### 3.3 主要なクラス・構造体・配列・定数


#### コピーアンドパッチエンジン（CopyAndPatchEngine）クラス
テンプレートの解決とバイナリ操作をカプセル化する。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| テンプレート辞書 | WASM命令に対応するJITテンプレートの検索索引 | アクセス辞書 | `jit_template_map` |
| アセンブラ参照 | 実行時に補助的な命令生成を行う場合のインターフェイス | 構造体への参照 | [`constexpr_assembler`](jit_assembler_constexpr.md) (非所有) |

#### 命令テンプレート（jit_template）
<!-- traceability: {JIT_RegisterMapping} {ContextPointerRegister} {EnvironmentPointer} {ADR_TosCacheAsymmetry} -->
WASM命令に対応するネイティブバイナリの雛形。インタープリタの `opcode_handler` と完全整合する `__fastcall` CPS 3引数呼び出し規約（`R0`: `ip`, `R1`: `stack_bot`, `R2`: `env`）に基づいて設計される。スタックボトム渡しにより `R3`（Caller-saved）および `R4-R6, R8-R11`（Callee-saved 計7本）をトレース単位の任意割当プール（スタックトップキャッシュ TOS/NOS/NNOS、コンテキストスピル mem_base/local_base、ローカル変数スロット、ループカウンタ）として活用できる。`local_base` は呼び出し元ごとに絶対位置が異なる実行時値であるためトレース入口で毎回ロードされるが、`sp_offset`（オペランドスタック深さ）はそこからの静的既知オフセットとしてパッチ適用時に即値化されるため、独立したレジスタ役割は持たない。JIT コンパイラはトレース解析結果に応じて最適なテンプレートバリアントを選択・結合する。 `{JIT_RegisterMapping}` `{ContextPointerRegister}` `{EnvironmentPointer}` `{ADR_TosCacheAsymmetry}`

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 命令バイナリ | ネイティブ命令列の実体（[JIT ステンシルカタログ](../../specs/jit_stencil_catalog.md) 準拠） | バイナリビュー | ROM参照 |
| パッチ箇所数 | テンプレート内で修正（パッチ）が必要なスロットの数 | エントリ数 | 8/16bit |
| パッチ情報 | 各パッチ位置のオフセットと修正方法（即値/分岐/APIポインタ）を定義する情報の配列 | バイナリビュー | [JIT ステンシルカタログ §2](../../specs/jit_stencil_catalog.md) |
| レジスタ規約 | JIT トレースとインタープリタ間で共有される物理レジスタ規約および Callee-saved プール | 規約定義 | [マスター物理設計書 §3](../../architecture/master_physical_design.md) 準拠 (`R0-R2: CPS`, `R3: spill`, `R4-R6, R8-R11: assignable pool`, `R7: FP`) |

#### JIT トレース物理メモリレイアウト (JIT Trace Header & Binary Layout)
<!-- traceability: {JIT_MultiBuffer_Cache} {JIT_LazyChaining} {SimpleJITArchitecture} -->
JIT キャッシュ（2KB バンク）内に書き込まれる各コンパイル済みトレースは、**先頭に 16 バイト固定長のメタデータヘッダ（JITエントリ: `jit_trace_header`）を持ち、直後（`+0x10`）からネイティブ Thumb-2 命令列が展開される** インライン物理レイアウトをとる。

```text
+---------------------------------------------------------------------------------------------------+
| JIT トレース物理メモリレイアウト (4-byte アライン)                                                |
+---------------------------------------------------------------------------------------------------+
| [Trace Header / JIT Entry Metadata] (固定長 16 Bytes: sizeof(jit_trace_header))                  |
|  +0x00: uint32_t head_wasm_pc      -- トレース開始 WASM PC (逆引き/デバッグ照合用)               |
|  +0x04: uint16_t trace_byte_size   -- ヘッダ含むトレース全体の総物理バイトサイズ                  |
|  +0x06: uint8_t  flags             -- 状態フラグ (0x01: PROMOTED, 0x02: LOOP_HEADER)              |
|  +0x07: uint8_t  variant_id        -- ステンシルバリアント/TOSレジスタ割り当て状態 ID             |
|  +0x08: uint32_t chain_next_pc     -- 直結チェイン先 WASM PC (0: インタープリタ復帰)             |
|  +0x0C: uint32_t chain_target_addr -- チェイン先ネイティブアドレス (初期値: ディスパッチャスタブ) |
+---------------------------------------------------------------------------------------------------+
| [Native Executable Code] (Thumb-2 機械語命令列, 実行エントリ = trace_base + 0x10 | 1)            |
|  +0x10: PUSH.W {r4-r6, r8-r11, lr} -- Callee-saved レジスタ退避                                   |
|  +0x14: [Copied & Patched Stencils]-- WASM 命令群のネイティブ展開                                  |
|         - Immediate loads / ALU / Memory loads                                                    |
|         - Loop Safepoint Poll (CBZ / LDR)                                                         |
|         - Dirty Spill Flush to stack_bot                                                          |
|  +0xXX: POP.W {r4-r6, r8-r11, lr}  -- Callee-saved レジスタ復元                                   |
|  +0xYY: LDR R12, [PC, #chain_slot] -- +0x0C の chain_target_addr をロード                         |
|  +0xZZ: BX R12                     -- チェイン先またはインタープリタ復帰スタブへ末尾ジャンプ      |
+---------------------------------------------------------------------------------------------------+
```

- **Zero-Lookup ヘッダ逆引き**: 実行中のネイティブ命令ポインタ `pc_native` から、`header = (pc_native & ~1) - offset` により $O(1)$・メモリアクセス 0 回でメタデータ（WASM PC, チェインスロット）を逆引きできる。
- **高速再チェイニング**: キャッシュローテーション時の再チェイニング／アンリンクは、コードバイナリをスキャンすることなく、先頭ヘッダの `+0x0C`（`chain_target_addr`）を直接書き換えるだけで完了する。

## 4. 動的モデル

### 4.1 アルゴリズム
<!-- traceability: {LowLatencyJIT} {JIT_CopyAndPatch} {JIT_RuntimeAPI_Fallback} {ContextPointerRegister} {VERIFY_FORMAL} -->

#### トレースコンパイル手順
1. **トレース解析とレジスタ役割バインディング決定**: トレース内の命令構成（メモリ/ローカル/スタック/ループ）に基づき、`R3` および Callee-saved レジスタ群（`R4-R6, R8-R11`）への最適な役割バインディング（TOS/NOS/NNOS, mem_base, local_base, local変数, ループカウンタ）を決定する。
2. **テンプレート・バリアント選択**: 命令およびレジスタバインディング状態に対応する `jit_template`（Stencil Variant）を取得する。
3. **ヘッダ配置 & プロローグ生成**: キャッシュの先頭に 16 バイト固定長の [`jit_trace_header`](jit_compiler.md)（WASM PC、トレースサイズ、チェイン先アドレス等）を構築・配置し、続く領域（`+0x10`）に使用する Callee-saved レジスタの `PUSH` および初期値ロード（`LDR`）を展開して、テンプレートの命令列をコピーする。
4. **パッチ適用 & AAPCS 外部関数呼び出し境界**:
    - 命令内に含まれる即値（定数）をテンプレートの指定位置に書き込む。
    - ランタイムAPIのアドレスをパッチする。
    - 分岐命令の相対オフセットを計算してパッチする。
    - **外部 AAPCS 関数（WASI/vMMIO等）呼び出し**: Caller-saved レジスタ `(R0-R3, R12, LR)` を退避し、SP を 8 バイト境界に整列して `BL` 発行、復帰後に Caller-saved を復元するスタブをインライン展開。Callee-saved レジスタ（`R4-R11`）は AAPCS により安全に保全される。
5. **インタープリタ継続渡し整合 (CPS / __fastcall Tail Call & AAPCS Callee-saved 保全)**:
    - JIT トレースの出口やフォールバック箇所では、レジスタ R0〜R2 に最新の `(ip, stack_bot, env)` を載せ、ダーティなレジスタ（TOS/NOS、ローカル変数）を統合スタックへ書き戻した上で Callee-saved レジスタを `POP` 復元し、インタープリタの次命令ハンドラへ直接末尾ジャンプ（`BX`）する。コンテキストの再構築は発生しない。 `{JIT_RuntimeAPI_Fallback}` `{ADR_TosCacheAsymmetry}`
    - **トレース入口**では逆に、プロローグで Callee-saved レジスタを `PUSH` 退避し、統合スタック上の値をレジスタへロードしてからトレース本体に入る。Callee-saved レジスタの生存区間は単一トレース内部に閉じており、呼び出し元の値は AAPCS 規約通り完全に保全される。 `{ADR_TosCacheAsymmetry}`
6. **ポインタ更新**: キャッシュの使用済みサイズを更新する。

#### Copy-and-Patch JIT フルセット・コンセプトコード (`concepts/jit_copy_patch_concept.py`)
```python
class MPUAttribute:
    RO_X = "RO_X"      # Read-Only + Executable (Native Execution)
    RW_XN = "RW_XN"    # Read-Write + Non-Executable (Patching)


class MPUFault(Exception):
    pass


class CopyPatchJITEngine:
    def __init__(self, cache_size: int = 1024):
        self.code_cache = ["NOP"] * cache_size
        self.mpu_attr = MPUAttribute.RO_X
        self.barrier_flushes = 0
        self.current_write_pos = 0

        # R0=ip, R1=stack_bot, R2=env are shared with the interpreter and are never
        # written by a trace. R3=scratch. R4=TOS / R5=NOS are trace-local: the prologue
        # fills them from the unified stack, the epilogue flushes them back.
        # SP is never touched (no C stack frame).
        self.stencils = {
            "prologue": ["LDR R4, [R1, #__TOS_OFF__]", "LDR R5, [R1, #__NOS_OFF__]"],
            "i32_const": ["STR R5, [R1, #__SPILL_OFF__]", "MOV R5, R4",
                          "MOVW R4, #__IMM_LO__", "MOVT R4, #__IMM_HI__"],
            "i32_add": ["ADD R4, R5, R4", "LDR R5, [R1, #__FILL_OFF__]"],
            "epilogue": ["STR R4, [R1, #__TOS_OFF__]", "STR R5, [R1, #__NOS_OFF__]", "BX R3"],
        }

    def begin_jit_patch(self):
        """Switches JIT Code Cache MPU attribute to RW + XN (W^X Protection)."""
        self.mpu_attr = MPUAttribute.RW_XN

    def commit_jit_patch(self):
        """Restores MPU attribute to RO + X and issues __DSB(); __ISB(); barriers."""
        assert self.mpu_attr == MPUAttribute.RW_XN
        self.mpu_attr = MPUAttribute.RO_X
        self.barrier_flushes += 1  # Hardware barrier sync

    def write_instruction(self, offset: int, instruction: str):
        if self.mpu_attr != MPUAttribute.RW_XN:
            raise MPUFault("W^X VIOLATION: Attempted write to non-writable code memory")
        self.code_cache[offset] = instruction

    def compile_basic_block(self, wasm_ops: list[tuple[str, object]]) -> tuple[int, int]:
        """Batches Stencil copy & relocation patching inside a single W^X transaction."""
        start_offset = self.current_write_pos

        # 1. Begin W^X Transaction (RW + XN)
        self.begin_jit_patch()

        # 2. Emit Prologue
        for inst in self.stencils["prologue"]:
            self.write_instruction(self.current_write_pos, inst)
            self.current_write_pos += 1

        # 3. Emit WASM Ops with Relocation Patching
        for op, arg in wasm_ops:
            if op == "i32.const":
                imm = int(arg)
                self.write_instruction(self.current_write_pos, f"MOVW R0, #{imm & 0xFFFF}")
                self.write_instruction(self.current_write_pos + 1, f"MOVT R0, #{(imm >> 16) & 0xFFFF}")
                self.write_instruction(self.current_write_pos + 2, "STR R0, [SP, #0]")
                self.current_write_pos += 3
            elif op == "i32.add":
                for inst in self.stencils["i32_add"]:
                    self.write_instruction(self.current_write_pos, inst)
                    self.current_write_pos += 1

        # 4. Emit Epilogue
        for inst in self.stencils["epilogue"]:
            self.write_instruction(self.current_write_pos, inst)
            self.current_write_pos += 1

        # 5. Commit W^X Transaction (RO + X + Barriers)
        self.commit_jit_patch()

        return (start_offset, self.current_write_pos - start_offset)
```

### 4.2 状態遷移図
本コンポーネントはステートレスなプロセッサとして動作するため、状態遷移は省略する。

### 4.3 内部シーケンス
```mermaid
sequenceDiagram
    participant J as JIT Compiler
    participant E as Engine
    participant R as Resolver
    participant A as Applicator
    participant C as Cache

    J->>E: Compile(WASM_PC)
    E->>R: Resolve(Instruction)
    R-->>E: Template
    E->>A: Apply(Template, Context)
    A->>C: Copy Binary
    A->>C: Patch Holes (Immediates, etc.)
    A-->>E: Done
    E-->>J: Native Entry Address
```

## 5. インターフェイス定義

### 5.1 公開API


#### トレースコンパイル（compile_trace）

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | WASM命令列をネイティブコードへ変換し、キャッシュへ書き込む。 |
| シグネチャ | `auto compile_trace(uint32_t pc, uint8_t* dest, size_t dest_size, const RuntimeContext* runtime) noexcept -> int32_t` |
| 引数 | `pc`: コンパイル開始位置を示す WASM PC<br>`dest`: ネイティブコードの書き込み先バッファの先頭ポインタ<br>`dest_size`: 書き込み先バッファの最大サイズ（バイト単位。バッファオーバーランを防止）<br>`runtime`: 実行環境ポインタ（WASM実行時のメモリ配置やコンテキスト情報を保持） |
| 戻り値 | `int32_t`（成功時は生成・書き込みしたネイティブコードの総 `バイト数`（正の値）を返し、バッファ不足や非対応命令などの失敗時は負のエラーコード（例：`-1` = バッファ不足、`-2` = 未サポート命令）を返す） |

## 6. 制約達成の方策

### 6.1 性能制約と最優先設計方針
<!-- traceability: {LowLatencyJIT} {JIT_CopyAndPatch} {JIT_RuntimeAPI_Fallback} -->
- **最優先設計方針**: 本コンパイラは、コンパイルレイテンシの最小化を最優先の設計目標とする。最適化のほとんどはビルド時に事前に行われており、実行時のオーバーヘッドを極限まで低減させる。 `{LowLatencyJIT}`
- **Copy-and-Patchによる時間短縮**: `{JIT_CopyAndPatch}` により、コンパイル時にレジスタ割り当てやアセンブル処理を実行せず、事前アセンブルされた命令テンプレートを単純コピー・穴埋め（パッチ）するだけにすることで、コンパイル時間を理論上の最速値まで圧縮する。 `{JIT_CopyAndPatch}`
- **複雑なエッジケースのオフロード**: ランタイムAPIフォールバック `{JIT_RuntimeAPI_Fallback}` により、JITエンジン自体のロジックを肥大化させず、複雑な浮動小数点演算や例外エミュレーションなどをヘルパー関数呼び出しに落とし込み、コンパイルパスを単一（Single-Pass）で超高速に完結させる。 `{JIT_RuntimeAPI_Fallback}`

### 6.2 3層分離設計 (3-Tier Separation) とハーネスパターン (Static Dependency Inversion)
<!-- traceability: {META_3TierSeparation} {META_StaticDI} {GLOBAL_ComponentHarness} -->
- **3層構造における役割**: 本コンポーネントは、システムアーキテクチャにおける「Tier 3 (詳細リーフコンポーネント: Leaf Component)」として位置付けられる。上位のファサードである [`jit_compiler`](jit_compiler.md) (Tier 3) が定義するインターフェイスと、「Tier 1」に属する全体的なシステムコンフィグから、完全に独立した具体的なマシンコード生成・バイナリ操作の実装に特化する。 `{META_3TierSeparation}`
- **ハーネスパターンによる依存関係の逆転 (Zero Virtual Overhead)**: 仮想関数（vtable）や動的ディスパッチに伴う実行時仮想化オーバーヘッドを一切排除するため、上位の統合サブシステムである [`runtime_vsoc`](../tier2_runtime/runtime_vsoc.md) (Tier 2) との接続は、POD 構造体 `vsoc_harness` (`{META_StaticDI}`) による静的依存性逆転（Dependency Inversion）を用いて行われる。JITエンジンは上位層の内部状態に直接依存せず、ハーネスから提供される関数シグネチャおよび引数ポインタ（`RuntimeContext*` 等）のみを介して疎結合に結合される。 `{META_3TierSeparation}` `{META_StaticDI}` `{GLOBAL_ComponentHarness}`
