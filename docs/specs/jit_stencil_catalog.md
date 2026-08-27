# JIT ステンシルテンプレート・カタログ物理仕様書 (JIT Stencil Template Catalog)

## 1. 概要と基本思想
<!-- traceability: {JIT_CopyAndPatch} {JIT_ZeroCompileCostTheorem} {JIT_RegisterMapping} {META_ZeroCostAbstraction} -->
本仕様書は、Fireball Copy-and-Patch JIT コンパイラが実行時にコード結合およびパッチ適用を行うための **事前コンパイル済み Thumb-2 ネイティブ命令テンプレート（Stencil）** の完全な物理カタログである。

ビルド時に Clang 17（`-target arm-none-eabi -mcpu=cortex-m33 -mthumb -O2`）で生成されたバイナリ列とプレースホルダ（穴: Relocation Slots）のオフセット、および多次元レジスタバリアント（スタックキャッシュ深度 TOS/NOS、`R3` コンテキスト・スピル、Callee-saved 任意割当プール `R4-R6, R8-R11`、AAPCS 準拠 Frame Pointer `R7`）を一意に定義する。 `{JIT_CopyAndPatch}` `{JIT_ZeroCompileCostTheorem}` `{JIT_RegisterMapping}` `{META_ZeroCostAbstraction}`

---

## 2. プレースホルダ（穴 / Relocations）の種類とパッチ規約
<!-- traceability: {JIT_CopyAndPatch} {PositionIndependentCode} -->

| リロケーション型 | ビット幅 / 形式 | 説明 | パッチ処理 |
| :--- | :--- | :--- | :--- |
| **`RELOC_IMM32_MOVW_MOVT`** | 32-bit (2x 16-bit) | 32-bit 即値を `MOVW` (下位16bit) + `MOVT` (上位16bit) 命令ペアに書き込む。 | `insn0[15:0] |= imm[15:0]`, `insn1[15:0] |= imm[31:16]` |
| **`RELOC_REL24_BRANCH`** | 24-bit (Thumb-2 `B.W`) | JIT トレース内および前方/後方ラベルへの相対ジャンプオフセット。 | `delta = target - (pc + 4); patch_b_w(insn, delta)` |
| **`RELOC_IMM8_OFFSET`** | 8-bit | 構造体メンバオフセットまたはローカル配列オフセット。 | `insn[7:0] |= offset` |
| **`RELOC_API_POINTER`** | 32-bit (Literal Pool) | ランタイム関数アドレス（`vsoc_memory_grow` 等）をリテラルプールへ書き込み。 | `*(uint32_t*)(cache_ptr + offset) = func_addr` |

---

## 3. ステンシル・カタログ (Thumb-2 Stencil Catalog)

### 3.1 プロローグ & エピローグ・ステンシル (Prologue, Epilogue & Spill Flush)
<!-- traceability: {ContextPointerRegister} {EnvironmentPointer} {JIT_RuntimeAPI_Fallback} {ADR_TosCacheAsymmetry} -->

エピローグおよびインタープリタ脱出（フォールバック）では、トレース実行中にレジスタへバインドされた値のうち、**ダーティ（変更済み）なスピル変数（TOS/NOS、レジスタ常駐ローカル変数、SPオフセット等）を統合スタックメモリへ `STR` で確実に書き戻した（Flush / Writeback）上で**、Callee-saved レジスタを `POP` 復元してジャンプする。

#### `STENCIL_PROLOGUE_FULL` (Callee-saved 全域退避 + LR)
- **Thumb-2 命令列**:
  ```asm
  push.w {r4-r6, r8-r11, lr} ; [Offset 0x00] AAPCS 準拠 Callee-saved 退避
  ```
- **バイナリ列 (4 Bytes)**: `2D E9 70 4F`

#### `STENCIL_EPILOGUE_FLUSH_D1` (TOS 書き戻し + Callee-saved 復元 & リターン)
- **Thumb-2 命令列**:
  ```asm
  str   r4, [r1, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (TOS 書き戻し)
  pop.w {r4-r6, r8-r11, pc} ; [Offset 0x02] Callee-saved 復元 & リターン
  ```
- **バイナリ列 (6 Bytes)**: `0C 60 BD E8 70 8F`

#### `STENCIL_EPILOGUE_FLUSH_D2` (TOS & NOS 書き戻し + Callee-saved 復元 & リターン)
- **Thumb-2 命令列**:
  ```asm
  str   r4, [r1, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (TOS 書き戻し)
  str   r5, [r1, #0x04]    ; [Offset 0x02] RELOC_IMM8_OFFSET (NOS 書き戻し)
  pop.w {r4-r6, r8-r11, pc} ; [Offset 0x04] Callee-saved 復元 & リターン
  ```
- **バイナリ列 (8 Bytes)**: `0C 60 4D 60 BD E8 70 8F`

#### `STENCIL_FALLBACK_FLUSH_D1` (TOS 書き戻し + Callee-saved 復元 $\to$ インタープリタ末尾ジャンプ)
- **Thumb-2 命令列**:
  ```asm
  str   r4, [r1, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (TOS 書き戻し)
  pop.w {r4-r6, r8-r11, lr} ; [Offset 0x02] Callee-saved 復元
  bx    r12                ; [Offset 0x06] R12 のハンドラアドレスへ直接ジャンプ
  ```
- **バイナリ列 (8 Bytes)**: `0C 60 BD E8 70 4F 60 47`

#### `STENCIL_FALLBACK_FLUSH_D2_LOCALS` (TOS/NOS + ダーティ Local 変数書き戻し $\to$ インタープリタ末尾ジャンプ)
- **Thumb-2 命令列**:
  ```asm
  str   r4, [r1, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (TOS 書き戻し)
  str   r5, [r1, #0x04]    ; [Offset 0x02] RELOC_IMM8_OFFSET (NOS 書き戻し)
  str   r8, [r1, #0x08]    ; [Offset 0x04] RELOC_IMM8_OFFSET (ダーティ local[0] 書き戻し)
  str   r9, [r1, #0x0C]    ; [Offset 0x06] RELOC_IMM8_OFFSET (ダーティ local[1] 書き戻し)
  pop.w {r4-r6, r8-r11, lr} ; [Offset 0x08] Callee-saved 復元
  bx    r12                ; [Offset 0x0C] R12 のハンドラアドレスへ直接ジャンプ
  ```
- **バイナリ列 (14 Bytes)**: `0C 60 4D 60 88 60 C9 60 BD E8 70 4F 60 47`

#### `STENCIL_EXTERNAL_CALL_STUB` (外部 AAPCS C/C++ 関数呼出境界)
- **Thumb-2 命令列**:
  ```asm
  push {r0-r3, r12, lr}   ; [Offset 0x00] Caller-saved 退避
  bl   0x00000000         ; [Offset 0x04] RELOC_REL24_BRANCH (外部C関数)
  pop  {r0-r3, r12, lr}   ; [Offset 0x08] Caller-saved 復元
  ```
- **バイナリ列 (12 Bytes)**: `2D E9 0F 50 00 F0 00 F8 BD E8 0F 50`

---

### 3.2 制御フロー系ステンシル (Control Flow)
<!-- traceability: {ThreadedInterpreter} {JIT_RuntimeAPI_Fallback} -->

#### `STENCIL_UNREACHABLE` (`0x00`)
- **Thumb-2 命令列**: `bkpt #0x00`
- **バイナリ列 (2 Bytes)**: `00 BE`

#### `STENCIL_NOP` (`0x01`)
- **Thumb-2 命令列**: (0 Byte - コンパイル時に完全消去)

#### `STENCIL_BR` (`0x0C` 無条件ジャンプ)
- **Thumb-2 命令列**:
  ```asm
  b.w  0x00000000         ; [Offset 0x00] RELOC_REL24_BRANCH
  ```
- **バイナリ列 (4 Bytes)**: `00 F0 00 B8`

#### `STENCIL_BR_IF_DEPTH_1` (`0x0D` 条件分岐: TOS != 0)
- **入力状態**: Cache Depth 1 (`R4 = TOS`)
- **出力状態**: Cache Depth 0
- **Thumb-2 命令列**:
  ```asm
  cmp  r4, #0             ; [Offset 0x00] 条件判定
  bne.w 0x00000000        ; [Offset 0x02] RELOC_REL24_BRANCH (真なら分岐)
  ```
- **バイナリ列 (6 Bytes)**: `00 2C 00 F0 00 80`

#### `STENCIL_SELECT_DEPTH_3` (`0x1B` 3値選択: c, val2, val1)
- **入力状態**: Cache Depth 3 (`R4 = cond`, `R5 = val2`, `R6 = val1`)
- **出力状態**: Cache Depth 1 (`R4 = (cond != 0 ? val1 : val2)`)
- **Thumb-2 命令列**:
  ```asm
  cmp  r4, #0
  it   ne
  movne r5, r6
  mov  r4, r5
  ```
- **バイナリ列 (8 Bytes)**: `00 2C 18 BF 35 46 2C 46`

---

### 3.3 定数ロード系ステンシル (Constants)
<!-- traceability: {JIT_CopyAndPatch} {ADR_TosCacheAsymmetry} -->

#### `STENCIL_I32_CONST_D0` (`0x41` Depth 0 $\to$ R4)
- **Thumb-2 命令列**:
  ```asm
  movw r4, #0x0000        ; [Offset 0x00] RELOC_IMM32_MOVW_MOVT (LO)
  movt r4, #0x0000        ; [Offset 0x04] RELOC_IMM32_MOVW_MOVT (HI)
  ```
- **バイナリ列 (8 Bytes)**: `40 F2 00 04 C0 F2 00 04`

#### `STENCIL_I32_CONST_D1` (`0x41` Depth 1 $\to$ R5=旧TOS, R4=新TOS)
- **Thumb-2 命令列**:
  ```asm
  mov  r5, r4             ; [Offset 0x00] 旧TOSをNOSへ退避
  movw r4, #0x0000        ; [Offset 0x02] RELOC_IMM32_MOVW_MOVT (LO)
  movt r4, #0x0000        ; [Offset 0x06] RELOC_IMM32_MOVW_MOVT (HI)
  ```
- **バイナリ列 (10 Bytes)**: `A5 46 40 F2 00 04 C0 F2 00 04`

#### `STENCIL_I64_CONST_D0` (`0x42` 64-bit 即値 $\to$ R4:R5)
- **Thumb-2 命令列**:
  ```asm
  movw r4, #0x0000        ; [Offset 0x00] RELOC_IMM32_MOVW_MOVT (LO32 LO)
  movt r4, #0x0000        ; [Offset 0x04] RELOC_IMM32_MOVW_MOVT (LO32 HI)
  movw r5, #0x0000        ; [Offset 0x08] RELOC_IMM32_MOVW_MOVT (HI32 LO)
  movt r5, #0x0000        ; [Offset 0x0C] RELOC_IMM32_MOVW_MOVT (HI32 HI)
  ```
- **バイナリ列 (16 Bytes)**: `40 F2 00 04 C0 F2 00 04 40 F2 00 05 C0 F2 00 05`

---

### 3.4 変数アクセス系ステンシル (Local & Global Variables)
<!-- traceability: {ContextPointerRegister} {JIT_RegisterMapping} -->

#### `STENCIL_LOCAL_GET_D0` (`0x20` Depth 0 $\to$ R4)
- **Thumb-2 命令列**:
  ```asm
  ldr  r4, [r1, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (local_offset)
  ```
- **バイナリ列 (2 Bytes)**: `0C 68`

#### `STENCIL_LOCAL_SET_D1` (`0x21` R4 $\to$ Local)
- **Thumb-2 命令列**:
  ```asm
  str  r4, [r1, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET
  ```
- **バイナリ列 (2 Bytes)**: `0C 60`

#### `STENCIL_LOCAL_TEE_D1` (`0x22` R4 $\to$ Local, R4 維持)
- **Thumb-2 命令列**:
  ```asm
  str  r4, [r1, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (TOSはR4に残す)
  ```
- **バイナリ列 (2 Bytes)**: `0C 60`

#### `STENCIL_GLOBAL_GET_D0` (`0x23` Env globals_base 経由ロード)
- **Thumb-2 命令列**:
  ```asm
  ldr.w r3, [r2, #0x08]   ; [Offset 0x00] env->globals_base ロード (vsoc_runtime +0x08)
  ldr.w r4, [r3, #0x00]   ; [Offset 0x04] RELOC_IMM8_OFFSET (global[N] ロード)
  ```
- **バイナリ列 (8 Bytes)**: `D2 F8 08 30 D3 F8 00 40`

#### `STENCIL_GLOBAL_SET_D1` (`0x24` Env globals_base 経由ストア)
- **Thumb-2 命令列**:
  ```asm
  ldr.w r3, [r2, #0x08]   ; [Offset 0x00] env->globals_base ロード (vsoc_runtime +0x08)
  str.w r4, [r3, #0x00]   ; [Offset 0x04] RELOC_IMM8_OFFSET (global[N] ストア)
  ```
- **バイナリ列 (8 Bytes)**: `D2 F8 08 30 C3 F8 00 40`

---

### 3.5 整数算術 & 論理演算ステンシル (32-bit Integer Arithmetic & Logic)
<!-- traceability: {JIT_CopyAndPatch} {META_ZeroCostAbstraction} -->

| WASM 命令 | Stencil 名 | 入力状態 | 出力状態 | Thumb-2 命令列 | バイナリ列 (Hex) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `i32.add` (`0x6A`) | `STENCIL_I32_ADD_D2` | R4=TOS, R5=NOS | R4=TOS | `adds r4, r5, r4` | `2C 19` |
| `i32.sub` (`0x6B`) | `STENCIL_I32_SUB_D2` | R4=TOS, R5=NOS | R4=TOS | `subs r4, r5, r4` | `2C 1B` |
| `i32.mul` (`0x6C`) | `STENCIL_I32_MUL_D2` | R4=TOS, R5=NOS | R4=TOS | `mul r4, r5, r4` | `05 FB 04 F4` |
| `i32.div_s` (`0x6D`) | `STENCIL_I32_DIV_S_D2` | R4=TOS, R5=NOS | R4=TOS | `sdiv r4, r5, r4` | `95 FB F4 F4` |
| `i32.div_u` (`0x6E`) | `STENCIL_I32_DIV_U_D2` | R4=TOS, R5=NOS | R4=TOS | `udiv r4, r5, r4` | `B5 FB F4 F4` |
| `i32.rem_s` (`0x6F`) | `STENCIL_I32_REM_S_D2` | R4=TOS, R5=NOS | R4=TOS | `sdiv r3, r5, r4; mls r4, r3, r4, r5` | `95 FB F4 F3 03 FB 14 54` |
| `i32.rem_u` (`0x70`) | `STENCIL_I32_REM_U_D2` | R4=TOS, R5=NOS | R4=TOS | `udiv r3, r5, r4; mls r4, r3, r4, r5` | `B5 FB F4 F3 03 FB 14 54` |
| `i32.and` (`0x71`) | `STENCIL_I32_AND_D2` | R4=TOS, R5=NOS | R4=TOS | `ands r4, r5, r4` | `2C 40` |
| `i32.or` (`0x72`) | `STENCIL_I32_OR_D2` | R4=TOS, R5=NOS | R4=TOS | `orrs r4, r5, r4` | `2C 43` |
| `i32.xor` (`0x73`) | `STENCIL_I32_XOR_D2` | R4=TOS, R5=NOS | R4=TOS | `eors r4, r5, r4` | `6C 40` |
| `i32.shl` (`0x74`) | `STENCIL_I32_SHL_D2` | R4=TOS, R5=NOS | R4=TOS | `lsl.w r4, r5, r4` | `05 FA 04 F4` |
| `i32.shr_s` (`0x75`)| `STENCIL_I32_SHR_S_D2` | R4=TOS, R5=NOS | R4=TOS | `asr.w r4, r5, r4` | `25 FA 04 F4` |
| `i32.shr_u` (`0x76`)| `STENCIL_I32_SHR_U_D2` | R4=TOS, R5=NOS | R4=TOS | `lsr.w r4, r5, r4` | `15 FA 04 F4` |
| `i32.rotl` (`0x77`) | `STENCIL_I32_ROTL_D2` | R4=TOS, R5=NOS | R4=TOS | `rsb r3, r4, #32; ror.w r4, r5, r3` | `C4 F1 20 03 35 FA 03 F4` |
| `i32.rotr` (`0x78`) | `STENCIL_I32_ROTR_D2` | R4=TOS, R5=NOS | R4=TOS | `ror.w r4, r5, r4` | `35 FA 04 F4` |
| `i32.clz` (`0x67`) | `STENCIL_I32_CLZ_D1` | R4=TOS | R4=TOS | `clz r4, r4` | `B4 FA 84 F4` |
| `i32.ctz` (`0x68`) | `STENCIL_I32_CTZ_D1` | R4=TOS | R4=TOS | `rbit r4, r4; clz r4, r4` | `94 FA A4 F4 B4 FA 84 F4` |

---

### 3.6 整数比較演算ステンシル (32-bit Integer Comparisons)
<!-- traceability: {JIT_CopyAndPatch} -->

| WASM 命令 | Stencil 名 | 入力状態 | 出力状態 | Thumb-2 命令列 | バイナリ列 (Hex) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `i32.eqz` (`0x45`) | `STENCIL_I32_EQZ_D1` | R4=TOS | R4=TOS | `cmp r4, #0; it eq; moveq r4, #1; it ne; movne r4, #0` | `00 2C 08 BF 01 24 18 BF 00 24` |
| `i32.eq` (`0x46`) | `STENCIL_I32_EQ_D2` | R4=TOS, R5=NOS | R4=TOS | `cmp r5, r4; it eq; moveq r4, #1; it ne; movne r4, #0` | `A5 42 08 BF 01 24 18 BF 00 24` |
| `i32.ne` (`0x47`) | `STENCIL_I32_NE_D2` | R4=TOS, R5=NOS | R4=TOS | `cmp r5, r4; it ne; movne r4, #1; it eq; moveq r4, #0` | `A5 42 18 BF 01 24 08 BF 00 24` |
| `i32.lt_s` (`0x48`)| `STENCIL_I32_LT_S_D2` | R4=TOS, R5=NOS | R4=TOS | `cmp r5, r4; it lt; movlt r4, #1; it ge; movge r4, #0` | `A5 42 B8 BF 01 24 A8 BF 00 24` |
| `i32.lt_u` (`0x49`)| `STENCIL_I32_LT_U_D2` | R4=TOS, R5=NOS | R4=TOS | `cmp r5, r4; it lo; movlo r4, #1; it hs; movhs r4, #0` | `A5 42 38 BF 01 24 28 BF 00 24` |
| `i32.gt_s` (`0x4A`)| `STENCIL_I32_GT_S_D2` | R4=TOS, R5=NOS | R4=TOS | `cmp r5, r4; it gt; movgt r4, #1; it le; movle r4, #0` | `A5 42 C8 BF 01 24 D8 BF 00 24` |
| `i32.gt_u` (`0x4B`)| `STENCIL_I32_GT_U_D2` | R4=TOS, R5=NOS | R4=TOS | `cmp r5, r4; it hi; movhi r4, #1; it ls; movls r4, #0` | `A5 42 88 BF 01 24 98 BF 00 24` |
| `i32.le_s` (`0x4C`)| `STENCIL_I32_LE_S_D2` | R4=TOS, R5=NOS | R4=TOS | `cmp r5, r4; it le; movle r4, #1; it gt; movgt r4, #0` | `A5 42 D8 BF 01 24 C8 BF 00 24` |
| `i32.le_u` (`0x4D`)| `STENCIL_I32_LE_U_D2` | R4=TOS, R5=NOS | R4=TOS | `cmp r5, r4; it ls; movls r4, #1; it hi; movhi r4, #0` | `A5 42 98 BF 01 24 88 BF 00 24` |
| `i32.ge_s` (`0x4E`)| `STENCIL_I32_GE_S_D2` | R4=TOS, R5=NOS | R4=TOS | `cmp r5, r4; it ge; movge r4, #1; it lt; movlt r4, #0` | `A5 42 A8 BF 01 24 B8 BF 00 24` |
| `i32.ge_u` (`0x4F`)| `STENCIL_I32_GE_U_D2` | R4=TOS, R5=NOS | R4=TOS | `cmp r5, r4; it hs; movhs r4, #1; it lo; movlo r4, #0` | `A5 42 28 BF 01 24 38 BF 00 24` |

---

### 3.7 メモリアクセス系ステンシル (Linear Memory Load & Store with Boundary Protection)
<!-- traceability: {MemoryBoundaryCheck} {FastAddressCheck} {JIT_RegisterMapping} -->

すべてのロード/ストア命令は、`R6 = mem_size`（`vsoc_runtime.mem-size`。`{FastAddressCheck}` が要求するのはサイズ比較の単一命令であり、マスクではない — `requirement_list.md` 参照）に対する `CMP` + `BHS.W` の境界チェックを経て、`R3 = mem_base` ピン留めバリアントによりアクセスされる。`CMP addr, r6` の直後の `BHS.W <trap>` は、アドレスが `mem_size` 以上（符号なし）ならトレースのトラップテール（インタープリタへのフォールバック）へ即座に分岐する——実際のロード/ストアはこの分岐が不成立の場合にのみ実行される。境界チェックはロード/ストアの副作用（メモリアクセスそのもの）より必ず先に評価されるため、トラップ経路には巻き戻すべき副作用が存在しない。`mem_size` に2の冪の制約はなく、部分ページ（例: 8KB, 12KB, 16KB）・単一 64KB ページ・複数 64KB ページ（`N * 64KB`）のいずれも同一の比較一つで判定できる。

`BHS.W` の分岐先オフセットはコンパイル時には未確定（トレースのトラップテールは、通常の出口エピローグの後にレイアウトされるため、エピローグ全体が生成し終わるまでアドレスが決まらない）。JIT エンジン（`jit_copy_patch_concept.py` の `compile_trace()`）はプレースホルダのオフセット `0` で `BHS.W` を発行しつつ、その命令のバイト位置を記録しておき、トレース末尾にトラップテール（ダーティスピルのフラッシュ + `fallback_interp`）を生成し終えた後、記録しておいた全ての `BHS.W` を実アドレスへバックパッチする（2パス発行 + バックパッチ）。

> [!NOTE]
> **JITホットパスとインタープリタ/vMMIO経路の境界チェックは統一されている**: JITステンシル（本節）とインタープリタ/vMMIO側（[`runtime_vmmio.md`](../components/tier2_runtime/runtime_vmmio.md)）は、どちらも同一の比較ベース境界チェック（マスクなし）を用い、境界外アクセスは必ずトラップしてインタープリタへフォールバックする。境界外アドレスを黙って範囲内へ折り畳んで処理を継続する（Address Wrapping）ことは許容されない。インタープリタがトラップ元の WASM PC から復旧できないと判断した場合は、ゲストタスクを停止してよい。`{MemoryBoundaryCheck}` `{vMMIO_TrapAndEmulate}`

| WASM 命令 | Stencil 名 | 入力状態 | 出力状態 | Thumb-2 命令列 (`R3=mem_base, R6=mem_size`) | バイナリ列 (Hex) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `i32.load` (`0x28`) | `STENCIL_I32_LOAD_R3` | R4=addr | R4=val | `cmp r4, r6; bhs.w <trap>; ldr.w r4, [r3, r4]` | `A4 42` + `BHS.W`(reloc) + `53 F8 04 40` |
| `i32.load8_s` (`0x2C`)| `STENCIL_I32_LOAD8_S_R3` | R4=addr | R4=val | `cmp r4, r6; bhs.w <trap>; ldrsb.w r4, [r3, r4]` | `A4 42` + `BHS.W`(reloc) + `13 F9 04 40` |
| `i32.load8_u` (`0x2D`)| `STENCIL_I32_LOAD8_U_R3` | R4=addr | R4=val | `cmp r4, r6; bhs.w <trap>; ldrb.w r4, [r3, r4]` | `A4 42` + `BHS.W`(reloc) + `13 F8 04 40` |
| `i32.load16_s` (`0x2E`)| `STENCIL_I32_LOAD16_S_R3` | R4=addr | R4=val | `cmp r4, r6; bhs.w <trap>; ldrsh.w r4, [r3, r4]` | `A4 42` + `BHS.W`(reloc) + `33 F9 04 40` |
| `i32.load16_u` (`0x2F`)| `STENCIL_I32_LOAD16_U_R3` | R4=addr | R4=val | `cmp r4, r6; bhs.w <trap>; ldrh.w r4, [r3, r4]` | `A4 42` + `BHS.W`(reloc) + `33 F8 04 40` |
| `i32.store` (`0x36`) | `STENCIL_I32_STORE_R3` | R4=val, R5=addr | (なし) | `cmp r5, r6; bhs.w <trap>; str.w r4, [r3, r5]` | `B5 42` + `BHS.W`(reloc) + `43 F8 05 40` |
| `i32.store8` (`0x3A`) | `STENCIL_I32_STORE8_R3` | R4=val, R5=addr | (なし) | `cmp r5, r6; bhs.w <trap>; strb.w r4, [r3, r5]` | `B5 42` + `BHS.W`(reloc) + `03 F8 05 40` |
| `i32.store16` (`0x3B`)| `STENCIL_I32_STORE16_R3` | R4=val, R5=addr | (なし) | `cmp r5, r6; bhs.w <trap>; strh.w r4, [r3, r5]` | `B5 42` + `BHS.W`(reloc) + `23 F8 05 40` |
| `memory.size` (`0x3F`)| `STENCIL_MEM_SIZE_D0` | (なし) | R4=pages | `ldr.w r4, [r2, #0x04]` (`env->mem_size`) | `D2 F8 04 40` |

`cmp r4, r6`/`cmp r5, r6` のバイト列（`A4 42`/`B5 42`）は 16-bit Thumb-1 `CMP Rn, Rm` エンコーディング（`0x4280 | (rm << 3) | rn`）から導出。`BHS.W <trap>` は 32-bit Thumb-2 条件分岐（`Cond.HS = 0b0010`）で、オフセットはバックパッチされるまで確定しないためリテラルのバイト列を持たない（`jit_copy_patch_concept.py` の `_MEMORY_OP_ADDR_REG` / `oob_branch_fixups` を正本とする）。

### 3.8 トレース内レジスタバリアント (Register Variants) と `variant_id`

<!-- traceability: {JIT_RegisterMapping} -->

各ステンシル名の末尾 `_dN` は、その命令が実行される時点でオペランドスタックキャッシュに何個の値が常駐しているか（= これから読み書きするレジスタの組）を表す**レジスタバリアント**であり、`jit_trace_header.variant_id`（8bit、[`jit_compiler.md` 3.3](../components/tier3_jit/jit_compiler.md)「JIT トレースヘッダ」参照）と同じ ID 空間を共有する。**この軸は同一トレース内部（intra-trace）で連続する命令間のレジスタ引き継ぎに関するものであり、トレース境界をまたぐチェイニング（`{JIT_LazyChaining}`）とは無関係である**——トレース境界は常にメモリ（統合スタック上の正準アドレス）経由でスピル/リロードされ、レジスタ内容を熱いまま引き継ぐことはない（[`jit_compiler.md` 8 節「トレース境界とチェイニングの安全性」](../components/tier3_jit/jit_compiler.md)を正本とする）。同一トレース内で、将来のステンシルバリアント動的選択が連続する命令間で異なるレジスタ配置を選ぶ場合（下記 NOTE 参照）にのみ、この `variant_id` を使った引き継ぎ互換性の判定とグルー挿入が意味を持つ。

| `variant_id` | 名称 | レジスタ占有状態 | 該当ステンシル |
| :---: | :--- | :--- | :--- |
| `0` | Depth 0 (Empty) | キャッシュなし。次の命令がゼロから値を生成する。 | `i32_const_d0`, `i64_const_d0`, `local_get_d0`, `global_get_d0`, `memory_size_d0` |
| `1` | Depth 1 (TOS) | `R4` = TOS のみ常駐。 | `i32_const_d1`, `local_set_d1`, `local_tee_d1`, `global_set_d1`, `br_if_d1`, `i32_eqz_d1`, `i32_clz_d1`, `i32_ctz_d1` |
| `2` | Depth 2 (TOS+NOS) | `R4` = TOS, `R5` = NOS が常駐。現行の唯一の物理レジスタ割当。 | `i32_add_d2` 等すべての2項算術・比較ステンシル（3.5, 3.6 節） |
| `3` | Depth 3 (TOS+NOS+NNOS) | `R4`/`R5`/`R6` の3値が常駐。`R6` を使うため、メモリアクセス系ステンシル（`R6=mem_size` 常駐）を含むトレースとは**同時に成立し得ない**。 | `select_d3` |

`3.7` のメモリアクセス系ステンシル（`*_r3`）はこの4段階のバリアント軸そのものではなく、Depth 1/2 の上に重ねて `R3=mem_base`/`R6=mem_size` を追加で要求する直交した制約である（ロード系は Depth 1 の `R4` をアドレスとして再利用、ストア系は Depth 2 の `R4=val, R5=addr` をそのまま用いる）。

#### `local_param` (`local_base`) ピン留めバリアント（直交軸）

<!-- traceability: {ContextPointerRegister} -->

[`master_physical_design.md` §3 NOTE](../architecture/master_physical_design.md) の通り、`local_base`（フレーム基底、`local_param` とも呼ぶ）は同一トレースが再帰呼び出しや異なる呼び出し深さから共有されうる場合、統合スタック上の絶対位置が毎回異なる実行時値となり、コンパイル時定数（`R1` からの静的オフセット）には畳み込めない。この場合 `local_base` はトレース入口でコンテキスト構造体から都度ロードされ、レジスタにピン留めされる必要がある——`mem_base` と同じ **`R3`** に割り当てる（`{FastAddressCheck}` の `R3=mem_base` ピン留めと同様、Caller-saved スクラッチをトレース単位で用途固定する運用）。この場合、ローカル変数アクセスは `LDR r4, [r3, #slot_offset]` のように `local_base` レジスタ経由の間接参照へ変わり、上表 3.4 節の `[r1, #offset]` 直接参照とは異なるステンシルバリアントになる。

**`mem_base` との排他性**: `R3` は1トレース内で `mem_base` と `local_param` のどちらか一方にしか使えない。したがって「`local_base` が非畳み込み（再帰・共有呼び出し深さ）」かつ「メモリアクセスを含む」トレースは、両方を同時に `R3` へピン留めできず現行の物理レジスタ割当では成立しない——このようなトレースはコンパイル対象から除外し、インタープリタ実行に委ねる（Copy-and-Patch のコンパイル可否判定に新しい除外条件を追加する必要がある。概念コードにはまだ実装されていない）。

`mem_base`/`mem_size` と同様、`local_param` はトレース入口で一度だけフレッシュにロードされ、トレース内では変化しない値であるため、命令間引き継ぎを判定する `variant_id`（後述）の対象には含めない——含めるのは TOS/NOS/NNOS の Depth 0-3 のみである。ただし、`R3` の用途（`mem_base` / `local_param` / 未使用）はトレース自身がどのステンシル（`[r1,#off]` 直接参照 or `[r3,#off]` 間接参照）を選ぶかに関わる別軸であり、上表と合わせて「そのトレースが使用可能なレジスタ予算」を決定する。

> [!NOTE]
> **`local_param` ピン留めも動的選択は未実装**: 現行の `compile_trace()` の `local.get`/`local.set`/`local.tee` は常に `[r1, #off]` 直接参照であり（`R1=stack_bot` がそのまま `local_base=0` として畳み込まれる前提）、`local_base` を `R3` にロードして間接参照するパスも、上記の `mem_base` との排他性チェックも概念コードにはまだ存在しない。同一トレースの再利用（再帰・共有呼び出し深さ）を跨ぐケースが実装されるまでは、この軸は表上の予約のみである。

> [!NOTE]
> **現状は静的割当であり、動的なバリアント選択はまだ実装されていない**: `jit_copy_patch_concept.py` の `compile_trace()` は WASM 命令ごとに1つの固定ステンシルしか持たず（例: `i32.const` は常に特別処理で `R4` へ直接書き込み、`i32_const_d0`/`i32_const_d1` のどちらのステンシルも実際には参照しない）、実行時のキャッシュ深度に応じて `_d0`/`_d1`/`_d2` を動的に選び分けるロジックはまだ存在しない。したがって同一トレース内で連続する命令のレジスタ配置が食い違う状況も現状は発生しない。上表の `variant_id` は、(1) 将来その動的選択を実装する際の ID 体系、および (2) その際に必要となる命令間引き継ぎ互換性判定・グルー挿入（`_order_register_moves`/`emit_variant_reconciliation_glue` を参照、`jit_copy_patch_concept.py` 内の再利用可能なユーティリティとして検証済み実装が既に存在する）の両方に使われる、正本の割当表である。
