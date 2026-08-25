# JIT ステンシルテンプレート・カタログ物理仕様書 (JIT Stencil Template Catalog)

## 1. 概要と基本思想
<!-- traceability: {JIT_CopyAndPatch} {JIT_ZeroCompileCostTheorem} {JIT_RegisterMapping} {META_ZeroCostAbstraction} -->
本仕様書は、Fireball Copy-and-Patch JIT コンパイラが実行時にコード結合およびパッチ適用を行うための **事前コンパイル済み Thumb-2 ネイティブ命令テンプレート（Stencil）** の物理カタログである。

ビルド時に Clang 17（`-target arm-none-eabi -mcpu=cortex-m33 -mthumb -O2`）で生成されたバイナリ列とプレースホルダ（穴: Relocation Slots）のオフセット、および多次元レジスタバリアント（スタックキャッシュ深度 TOS/NOS、`R3` コンテキスト・スピル、Callee-saved 任意割当プール）を一意に定義する。 `{JIT_CopyAndPatch}` `{JIT_ZeroCompileCostTheorem}` `{JIT_RegisterMapping}` `{META_ZeroCostAbstraction}`

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

### 3.1 定数ロード系ステンシル (`i32.const`)
<!-- traceability: {JIT_CopyAndPatch} {ADR_TosCacheAsymmetry} -->

#### `STENCIL_I32_CONST_DEPTH_0` (スタック空 $\to$ R4 にロード)
- **入力状態**: Cache Depth 0 (レジスタキャッシュなし)
- **出力状態**: Cache Depth 1 (`R4 = TOS`)
- **Thumb-2 命令列**:
  ```asm
  movw r4, #0x0000        ; [Offset 0x00] RELOC_IMM32_MOVW_MOVT (下位16bit)
  movt r4, #0x0000        ; [Offset 0x04] RELOC_IMM32_MOVW_MOVT (上位16bit)
  ```
- **バイナリ列 (8 Bytes)**: `40 F2 00 04 C0 F2 00 04`

#### `STENCIL_I32_CONST_DEPTH_1` (R4 に TOS $\to$ R4 を R5 (NOS) へ移し、R4 に新即値ロード)
- **入力状態**: Cache Depth 1 (`R4 = TOS`)
- **出力状態**: Cache Depth 2 (`R4 = TOS`, `R5 = NOS`)
- **Thumb-2 命令列**:
  ```asm
  mov  r5, r4             ; [Offset 0x00] 旧 TOS を NOS へ退避
  movw r4, #0x0000        ; [Offset 0x02] RELOC_IMM32_MOVW_MOVT (下位16bit)
  movt r4, #0x0000        ; [Offset 0x06] RELOC_IMM32_MOVW_MOVT (上位16bit)
  ```
- **バイナリ列 (10 Bytes)**: `A5 46 40 F2 00 04 C0 F2 00 04`

---

### 3.2 整数演算系ステンシル (`i32.add`, `i32.sub`, `i32.mul`)
<!-- traceability: {JIT_CopyAndPatch} {JIT_RegisterMapping} -->

#### `STENCIL_I32_ADD_DEPTH_2` (両オペランドが R4, R5 にキャッシュ)
- **入力状態**: Cache Depth 2 (`R4 = TOS (b)`, `R5 = NOS (a)`)
- **出力状態**: Cache Depth 1 (`R4 = TOS (a + b)`)
- **Thumb-2 命令列**:
  ```asm
  adds r4, r5, r4         ; [Offset 0x00] R4 = R5 + R4 (1 サイクル)
  ```
- **バイナリ列 (2 Bytes)**: `6C 19`

#### `STENCIL_I32_ADD_DEPTH_1` (片方が R4、もう片方は統合スタックからロード)
- **入力状態**: Cache Depth 1 (`R4 = TOS (b)`, スタック上に `a`)
- **出力状態**: Cache Depth 1 (`R4 = TOS (a + b)`)
- **Thumb-2 命令列**:
  ```asm
  ldr  r3, [r1, #-4]!     ; [Offset 0x00] スタックから a を R3 へポップ
  adds r4, r3, r4         ; [Offset 0x02] R4 = R3 + R4
  ```
- **バイナリ列 (4 Bytes)**: `51 F8 04 3D 1C 19`

#### `STENCIL_I32_SUB_DEPTH_2`
- **入力状態**: Cache Depth 2 (`R4 = TOS (b)`, `R5 = NOS (a)`)
- **出力状態**: Cache Depth 1 (`R4 = TOS (a - b)`)
- **Thumb-2 命令列**:
  ```asm
  subs r4, r5, r4         ; [Offset 0x00] R4 = R5 - R4
  ```
- **バイナリ列 (2 Bytes)**: `AC 1B`

---

### 3.3 メモリアクセス系ステンシル (`i32.load`, `i32.store`)
<!-- traceability: {MemoryBoundaryCheck} {FastAddressCheck} {JIT_RegisterMapping} -->

#### `STENCIL_I32_LOAD_R3_MEMBASE` (`R3` に `mem_base` がピン留めされているバリアント)
- **入力状態**: Cache Depth 1 (`R4 = TOS (guest_addr)`), `R3 = mem_base`
- **出力状態**: Cache Depth 1 (`R4 = TOS (loaded_val)`)
- **Thumb-2 命令列**:
  ```asm
  ldr.w r4, [r3, r4]      ; [Offset 0x00] R4 = *(uint32_t*)(mem_base + guest_addr)
  ```
- **バイナリ列 (4 Bytes)**: `53 F8 04 40`

#### `STENCIL_I32_STORE_R3_MEMBASE` (`R3` に `mem_base` がピン留めされているバリアント)
- **入力状態**: Cache Depth 2 (`R4 = TOS (val)`, `R5 = NOS (guest_addr)`), `R3 = mem_base`
- **出力状態**: Cache Depth 0 (すべて消費)
- **Thumb-2 命令列**:
  ```asm
  str.w r4, [r3, r5]      ; [Offset 0x00] *(uint32_t*)(mem_base + guest_addr) = val
  ```
- **バイナリ列 (4 Bytes)**: `43 F8 05 40`

---

### 3.4 ローカル変数アクセス系ステンシル (`local.get`, `local.set`)
<!-- traceability: {ContextPointerRegister} {JIT_RegisterMapping} -->

#### `STENCIL_LOCAL_GET_IMM8` (ローカル配列オフセットを即値パッチ)
- **入力状態**: Cache Depth 0, `R1 = stack_bot`
- **出力状態**: Cache Depth 1 (`R4 = TOS`)
- **Thumb-2 命令列**:
  ```asm
  ldr  r4, [r1, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (frame.local_offset + idx*4)
  ```
- **バイナリ列 (2 Bytes)**: `04 68` (パッチ前)

#### `STENCIL_LOCAL_SET_DEPTH_1`
- **入力状態**: Cache Depth 1 (`R4 = TOS`), `R1 = stack_bot`
- **出力状態**: Cache Depth 0
- **Thumb-2 命令列**:
  ```asm
  str  r4, [r1, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET
  ```
- **バイナリ列 (2 Bytes)**: `04 60` (パッチ前)

---

### 3.5 プロローグ & エピローグ・ステンシル (Prologue & Epilogue)
<!-- traceability: {ContextPointerRegister} {EnvironmentPointer} {JIT_RuntimeAPI_Fallback} {ADR_TosCacheAsymmetry} -->

#### `STENCIL_TRACE_PROLOGUE_FULL` (使用する Callee-saved レジスタを退避)
- **Thumb-2 命令列**:
  ```asm
  push {r4-r6, r8-r11, lr} ; [Offset 0x00] AAPCS 準拠 Callee-saved 退避
  ```
- **バイナリ列 (4 Bytes)**: `2D E9 F0 4F`

#### `STENCIL_TRACE_EPILOGUE_RETURN` (Callee-saved 復元 & リターン)
- **Thumb-2 命令列**:
  ```asm
  pop  {r4-r6, r8-r11, pc} ; [Offset 0x00] Callee-saved 復元 & 呼出元へリターン
  ```
- **バイナリ列 (4 Bytes)**: `BD E8 F0 8F`

#### `STENCIL_TRACE_FALLBACK_INTERP` (インタープリタへ直接末尾ジャンプ)
- **Thumb-2 命令列**:
  ```asm
  pop  {r4-r6, r8-r11, lr} ; [Offset 0x00] Callee-saved 復元
  bx   r12                ; [Offset 0x04] R12 に保持されたインタープリタ次ハンドラへ BX
  ```
- **バイナリ列 (6 Bytes)**: `BD E8 F0 4F 60 47`
