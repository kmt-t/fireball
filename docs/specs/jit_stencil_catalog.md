# JIT ステンシルテンプレート・カタログ物理仕様書 (JIT Stencil Template Catalog) {VERIFY_LLM} {VERIFY_FORMAL}
<!-- evidence:
     formal: formal/jit_stencil_epilogue_model.py
-->

## 1. 概要と基本思想
<!-- traceability: {JIT_CopyAndPatch} {JIT_ZeroCompileCostTheorem} {JIT_RegisterMapping} {META_ZeroCostAbstraction} -->
本仕様書は、Fireball Copy-and-Patch JIT コンパイラが実行時にコード結合およびパッチ適用を行うための **事前コンパイル済み Thumb-2 ネイティブ命令テンプレート（Stencil）** の完全な物理カタログである。

ビルド時に Clang 17（`-target arm-none-eabi -mcpu=cortex-m33 -mthumb -O2`）で生成されたバイナリ列とプレースホルダ（穴: Relocation Slots）のオフセット、および多次元レジスタバリアント（スタックキャッシュ深度 `R3=TOS / R4=NOS / R5=NNOS`、`R0=ctx`、`R1=SP`、`R2=local_base`、`R8=mem_base / R9=mem_size` ピン留め、`R10=safepoint`、および AAPCS 準拠 Callee-saved 退避 `R4-R6, R8-R11`、Frame Pointer `R7`）を一意に定義する。 `{JIT_CopyAndPatch}` `{JIT_ZeroCompileCostTheorem}` `{JIT_RegisterMapping}` `{META_ZeroCostAbstraction}`

---

## 2. プレースホルダ（穴 / Relocations）の種類とパッチ規約
<!-- traceability: {JIT_CopyAndPatch} {PositionIndependentCode} -->

| リロケーション型 | ビット幅 / 形式 | 説明 | パッチ処理 |
| :--- | :--- | :--- | :--- |
| **`RELOC_IMM32_MOVW_MOVT`** | 32-bit (2x 16-bit) | 32-bit 即値を `MOVW` (下位16bit) + `MOVT` (上位16bit) 命令ペアに書き込む。 | `encode_movw_movt_imm16(insn0, imm[15:0]); encode_movw_movt_imm16(insn1, imm[31:16])` (Thumb-2 即値ビットフィールド展開) |
| **`RELOC_REL24_BRANCH`** | 24-bit (Thumb-2 `B.W`) | JIT トレース内および前方/後方ラベルへの相対ジャンプオフセット。 | `delta = target - (pc + 4); patch_b_w(insn, delta)` |
| **`RELOC_IMM8_OFFSET`** | 8-bit | 構造体メンバオフセットまたはローカル配列オフセット。 | `insn[7:0] |= offset` |
| **`RELOC_API_POINTER`** | 32-bit (Literal Pool) | ランタイム関数アドレス（`vsoc_memory_grow` 等）をリテラルプールへ書き込み。 | `*(uint32_t*)(cache_ptr + offset) = func_addr` |

---

## 3. ステンシル・カタログ (Thumb-2 Stencil Catalog)

### 3.1 プロローグ & エピローグ・ステンシル (Prologue, Epilogue & Spill Flush)
<!-- traceability: {ContextPointerRegister} {EnvironmentPointer} {JIT_RuntimeAPI_Fallback} {ADR_TosCacheAsymmetry} {JIT_LazyChaining} -->

トレース境界には、性質の異なる 2 種類のエントリと 2 種類のエグジットが存在し、両者を混同してはならない。

- **新規エントリ（`STENCIL_PROLOGUE_FULL` を通過）**: インタープリタ・ディスパッチャから `exec_trace` 関数ポインタ経由で呼び出される場合。CPS 4引数ディスパッチ規約（`R0=ctx, R1=sp, R2=local_base, R3=tos`）に基づいて呼び出され、真の AAPCS 呼び出し境界を跨ぐため、Callee-saved レジスタ（`R4-R6, R8-R11, LR`）の退避を行う。`R3: tos` はそのまま JIT スタックキャッシュ `TOS` として活用される。
- **チェイン・エントリ（`STENCIL_PROLOGUE_FULL` の直後のオフセット、プロローグをスキップ）**: 常駐先行トレースからの直接分岐（`{JIT_LazyChaining}` によりバックパッチされた `B.W` またはヘッダ動的ジャンプ `BX r12`）で入ってくる場合。先行トレースのレジスタ状態（`R3=TOS / R4=NOS / R5=NNOS` のキャッシュ値含む）がそのまま生きているため、退避・再ロードは不要かつ有害。
- **AAPCS 準拠終了エピローグ（`STENCIL_EPILOGUE_FLUSH_D1`/`D2`）**: 後続の常駐トレースが存在しない、またはこのトレースがチェインの終端である場合。基本ブロック末尾でプッシュされたスタックキャッシュ（`R3: TOS`、Depth 2 では `R4: NOS` も）をオペランドスタック（`[R1, #offset]`）へ確実に書き戻し（Flush）、コンテキスト `R0` の `ip`（`+0x00`）および `sp_offset`（`+0x0C`）を同期した上で、Callee-saved レジスタを `POP` 復元してリターンする（`{JITC-GOTCHA-07}`）。
- **直接チェイン分岐（エピローグなし）**: `{JIT_LazyChaining}` により後続トレースが常駐と解決済みの場合、上記エピローグの代わりに後続トレースのチェイン・エントリへのジャンプ（動的ヘッダ参照 `BX r12`）を配置する。フラッシュも `POP` も発生せず、レジスタは分岐を跨いでそのまま生き続ける。

#### `STENCIL_PROLOGUE_FULL` (Callee-saved 全域退避 + LR、新規エントリ専用)
- **入力状態**: CPS 4引数規約 (`R0=ctx, R1=sp, R2=local_base, R3=tos`)
- **出力状態**: Callee-saved 退避完了、JIT スタックキャッシュ `R3=TOS`
- **Thumb-2 命令列**:
  ```asm
  push.w {r4-r6, r8-r11, lr} ; [Offset 0x00] AAPCS 準拠 Callee-saved 退避
  ```
- **バイナリ列 (4 Bytes)**: `2D E9 70 4F`
- チェイン・エントリはこのステンシルの直後（トレース先頭 + 4 Bytes）のオフセットを指し、このステンシル自体を経由しない。先行トレースからレジスタ状態（`R3-R5`）が直接引き継がれる。

#### `STENCIL_EPILOGUE_FLUSH_D1` (TOS 書き戻し + Callee-saved 復元 & リターン)
- **Thumb-2 命令列**:
  ```asm
  str   r3, [r1, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (TOS 書き戻し)
  pop.w {r4-r6, r8-r11, pc} ; [Offset 0x02] Callee-saved 復元 & リターン
  ```
- **バイナリ列 (6 Bytes)**: `0B 60 BD E8 70 8F`

#### `STENCIL_EPILOGUE_FLUSH_D2` (TOS & NOS 書き戻し + Callee-saved 復元 & リターン)
- **Thumb-2 命令列**:
  ```asm
  str   r3, [r1, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (TOS 書き戻し)
  str   r4, [r1, #0x04]    ; [Offset 0x02] RELOC_IMM8_OFFSET (NOS 書き戻し)
  pop.w {r4-r6, r8-r11, pc} ; [Offset 0x04] Callee-saved 復元 & リターン
  ```
- **バイナリ列 (8 Bytes)**: `0B 60 4C 60 BD E8 70 8F`

#### `STENCIL_DYNAMIC_CHAIN_EXIT_D1` (ヘッダ参照動的チェイン分岐 & インタープリタ復帰エピローグ)
- **概要**: コードの自己書き換え（インプレースパッチ）を行わず、自身のトレースヘッダ内のデータフィールド `chain_target_addr`（+0x0C）の解決状態（非ゼロかゼロか）に応じて動的に分岐する。
- **Thumb-2 命令列**:
  ```asm
  ldr.w r12, [pc, #-offset_to_header_target] ; 自身のヘッダ chain_target_addr (+0x0C) をロード
  cbz   r12, <interp_fallback>               ; 未解決 (0) の場合はエピローグへフォールスルー
  bx    r12                                  ; 解決済みの場合は後続トレース（プロローグ直後）へ直接ジャンプ！
<interp_fallback>:
  str   r3, [r1, #0x00]                      ; TOS 書き戻し (Flush)
  pop.w {r4-r6, r8-r11, pc}                  ; Callee-saved 復元 & リターン（インタープリタ復帰）
  ```
- **特徴**:
  - **初期状態（未チェイン）**: ヘッダの `chain_target_addr` は `0`。`cbz` でフォールスルーし、TOS 書き戻しと `POP PC` で安全にインタープリタへ戻る。
  - **チェイン確立時**: 後続トレースがコンパイルされた際、ランタイムは先行トレースヘッダのデータフィールド（`chain_target_addr`）に後続トレースのネイティブアドレスを書き込むだけ（命令コードの書き換えなし、MPU W^X 切り替え不要）。次回実行時からは `bx r12` によりエピローグおよび後続プロローグを完全にスキップしてネイティブ直行する。
  - **アンリンク時**: ヘッダの `chain_target_addr` を `0` にリセットするだけで即座にインタープリタ復帰へと安全に戻る。

#### `STENCIL_FALLBACK_FLUSH_D1` (TOS 書き戻し + Callee-saved 復元 $\to$ インタープリタ末尾ジャンプ)
- **Thumb-2 命令列**:
  ```asm
  str   r3, [r1, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (TOS 書き戻し)
  pop.w {r4-r6, r8-r11, lr} ; [Offset 0x02] Callee-saved 復元
  bx    r12                ; [Offset 0x06] R12 のハンドラアドレスへ直接ジャンプ
  ```
- **バイナリ列 (8 Bytes)**: `0B 60 BD E8 70 4F 60 47`

#### `STENCIL_FALLBACK_FLUSH_D2` (TOS & NOS 書き戻し $\to$ インタープリタ末尾ジャンプ)
- **Thumb-2 命令列**:
  ```asm
  str   r3, [r1, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (TOS 書き戻し)
  str   r4, [r1, #0x04]    ; [Offset 0x02] RELOC_IMM8_OFFSET (NOS 書き戻し)
  pop.w {r4-r6, r8-r11, lr} ; [Offset 0x04] Callee-saved 復元
  bx    r12                ; [Offset 0x08] R12 のハンドラアドレスへ直接ジャンプ
  ```
- **バイナリ列 (10 Bytes)**: `0B 60 4C 60 BD E8 70 4F 60 47`

#### `STENCIL_EXTERNAL_CALL_STUB` (外部 AAPCS C/C++ 関数呼出境界)
- **Thumb-2 命令列**:
  ```asm
  push.w {r0-r3, r12, lr} ; [Offset 0x00] 32-bit Caller-saved 退避 (4 Bytes)
  bl     0x00000000       ; [Offset 0x04] RELOC_REL24_BRANCH (外部C関数, 4 Bytes)
  pop.w  {r0-r3, r12, lr} ; [Offset 0x08] 32-bit Caller-saved 復元 (4 Bytes)
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
- **入力状態**: Cache Depth 1 (`R3 = TOS`)
- **出力状態**: Cache Depth 0
- **Thumb-2 命令列**:
  ```asm
  cmp  r3, #0             ; [Offset 0x00] 条件判定
  bne.w 0x00000000        ; [Offset 0x02] RELOC_REL24_BRANCH (真なら分岐)
  ```
- **バイナリ列 (6 Bytes)**: `00 2B 40 F0 00 80`

#### `STENCIL_SELECT_DEPTH_3` (`0x1B` 3値選択: c, val2, val1)
- **入力状態**: Cache Depth 3 (`R3 = cond`, `R4 = val2`, `R5 = val1`)
- **出力状態**: Cache Depth 1 (`R3 = (cond != 0 ? val1 : val2)`)
- **Thumb-2 命令列**:
  ```asm
  cmp  r3, #0
  it   ne
  movne r4, r5
  mov  r3, r4
  ```
- **バイナリ列 (8 Bytes)**: `00 2B 18 BF 2C 46 23 46`

---

### 3.3 定数ロード系ステンシル (Constants)
<!-- traceability: {JIT_CopyAndPatch} {ADR_TosCacheAsymmetry} -->

#### `STENCIL_I32_CONST_D0` (`0x41` i32.const バリアント: スタック空 Depth 0 $\to$ R3)
- **Thumb-2 命令列**:
  ```asm
  movw r3, #0x0000        ; [Offset 0x00] RELOC_IMM32_MOVW_MOVT (LO)
  movt r3, #0x0000        ; [Offset 0x04] RELOC_IMM32_MOVW_MOVT (HI)
  ```
- **バイナリ列 (8 Bytes)**: `40 F2 00 03 C0 F2 00 03`

#### `STENCIL_I32_CONST_D1` (`0x41` i32.const バリアント: 既存TOS退避 Depth 1 $\to$ R4=旧TOS, R3=新TOS)
- **Thumb-2 命令列**:
  ```asm
  mov  r4, r3             ; [Offset 0x00] 旧TOSをNOSへ退避
  movw r3, #0x0000        ; [Offset 0x02] RELOC_IMM32_MOVW_MOVT (LO)
  movt r3, #0x0000        ; [Offset 0x06] RELOC_IMM32_MOVW_MOVT (HI)
  ```
- **バイナリ列 (10 Bytes)**: `1C 46 40 F2 00 03 C0 F2 00 03`

#### `STENCIL_I64_CONST_D0` (`0x42` i64.const 64-bit 即値 $\to$ R3:R4)
- **Thumb-2 命令列**:
  ```asm
  movw r3, #0x0000        ; [Offset 0x00] RELOC_IMM32_MOVW_MOVT (LO32 LO)
  movt r3, #0x0000        ; [Offset 0x04] RELOC_IMM32_MOVW_MOVT (LO32 HI)
  movw r4, #0x0000        ; [Offset 0x08] RELOC_IMM32_MOVW_MOVT (HI32 LO)
  movt r4, #0x0000        ; [Offset 0x0C] RELOC_IMM32_MOVW_MOVT (HI32 HI)
  ```
- **バイナリ列 (16 Bytes)**: `40 F2 00 03 C0 F2 00 03 40 F2 00 04 C0 F2 00 04`

---

### 3.4 変数アクセス系ステンシル (Local & Global Variables)
<!-- traceability: {ContextPointerRegister} {JIT_RegisterMapping} -->

#### `STENCIL_LOCAL_GET_D0` (`0x20` Depth 0 $\to$ R3)
- **入力状態**: Cache Depth 0 (または直前命令のキャッシュ状態に依存)
- **出力状態**: Cache Depth 1 (`R3 = TOS` にローカル変数値をロード)
- **Thumb-2 命令列**:
  ```asm
  ldr  r3, [r2, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (local_base R2 からロード -> R3: TOS)
  ```
- **バイナリ列 (2 Bytes)**: `13 68`

#### `STENCIL_LOCAL_SET_D1` (`0x21` R3 $\to$ Local)
- **入力状態**: Cache Depth 1 (`R3 = TOS`)
- **出力状態**: Cache Depth 0（値はローカル変数へ退避・消費されスタックからポップされる）
- **Thumb-2 命令列**:
  ```asm
  str  r3, [r2, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (R3: TOS を local_base R2 へストア)
  ```
- **バイナリ列 (2 Bytes)**: `13 60`

#### `STENCIL_LOCAL_TEE_D1` (`0x22` R3 $\to$ Local, R3 維持)
- **入力状態**: Cache Depth 1 (`R3 = TOS`)
- **出力状態**: Cache Depth 1 (`R3 = TOS` を維持したままローカル変数へ複製ストア)
- **Thumb-2 命令列**:
  ```asm
  str  r3, [r2, #0x00]    ; [Offset 0x00] RELOC_IMM8_OFFSET (R3: TOS を local_base R2 へストア、TOSはR3に残す)
  ```
- **バイナリ列 (2 Bytes)**: `13 60`

#### `STENCIL_GLOBAL_GET_D0` (`0x23` execution_context globals_base 経由ロード)
- **入力状態**: Cache Depth 0
- **出力状態**: Cache Depth 1 (`R3 = TOS` にグローバル変数値をロード)
- **Thumb-2 命令列**（`R12` は AAPCS Intra-call スクラッチで、この1ステンシル内でのみ globals_base ポインタを保持する）:
  ```asm
  ldr.w r12, [r0, #0x30]  ; [Offset 0x00] ctx->globals_base ロード (execution_context +0x30)
  ldr.w r3, [r12, #0x00]  ; [Offset 0x04] RELOC_IMM8_OFFSET (global[N] ロード -> R3: TOS)
  ```
- **バイナリ列 (8 Bytes)**: `D0 F8 30 C0 DC F8 00 30`

#### `STENCIL_GLOBAL_SET_D1` (`0x24` execution_context globals_base 経由ストア)
- **入力状態**: Cache Depth 1 (`R3 = TOS`)
- **出力状態**: Cache Depth 0（値はグローバル変数へ退避・消費されスタックからポップされる）
- **Thumb-2 命令列**:
  ```asm
  ldr.w r12, [r0, #0x30]  ; [Offset 0x00] ctx->globals_base ロード (execution_context +0x30)
  str.w r3, [r12, #0x00]  ; [Offset 0x04] RELOC_IMM8_OFFSET (R3: TOS を global[N] へストア)
  ```
- **バイナリ列 (8 Bytes)**: `D0 F8 30 C0 CC F8 00 30`

---

### 3.5 整数算術 & 論理演算ステンシル (32-bit Integer Arithmetic & Logic)
<!-- traceability: {JIT_CopyAndPatch} {META_ZeroCostAbstraction} -->

| WASM 命令 | Stencil 名 | 入力状態 | 出力状態 | Thumb-2 命令列 | バイナリ列 (Hex) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `i32.add` (`0x6A`) | `STENCIL_I32_ADD_D2` | R3=TOS, R4=NOS | R3=TOS | `adds r3, r4, r3` | `E3 18` |
| `i32.sub` (`0x6B`) | `STENCIL_I32_SUB_D2` | R3=TOS, R4=NOS | R3=TOS | `subs r3, r4, r3` | `E3 1A` |
| `i32.mul` (`0x6C`) | `STENCIL_I32_MUL_D2` | R3=TOS, R4=NOS | R3=TOS | `mul r3, r4, r3` | `04 FB 03 F3` |
| `i32.div_s` (`0x6D`) | `STENCIL_I32_DIV_S_D2` | R3=TOS, R4=NOS | R3=TOS | `cbz r3, <trap>; cmp r4, #0x80000000; it eq; cmpeq r3, #-1; beq <trap>; sdiv r3, r4, r3` (0除算・INT_MIN/-1時はインタープリタへトラップ) | `00 B1 ... 94 FB F3 F3` |
| `i32.div_u` (`0x6E`) | `STENCIL_I32_DIV_U_D2` | R3=TOS, R4=NOS | R3=TOS | `cbz r3, <trap>; udiv r3, r4, r3` (0除算時はインタープリタへトラップ) | `00 B1 B4 FB F3 F3` |
| `i32.rem_s` (`0x6F`) | `STENCIL_I32_REM_S_D2` | R3=TOS, R4=NOS | R3=TOS | `cbz r3, <trap>; sdiv r12, r4, r3; mls r3, r12, r3, r4` (ARM MLS: $Rd(r3) = Ra(r4) - Rn(r12) \times Rm(r3)$) | `00 B1 94 FB F3 FC 0C FB 13 43` |
| `i32.rem_u` (`0x70`) | `STENCIL_I32_REM_U_D2` | R3=TOS, R4=NOS | R3=TOS | `cbz r3, <trap>; udiv r12, r4, r3; mls r3, r12, r3, r4` (ARM MLS: $Rd(r3) = Ra(r4) - Rn(r12) \times Rm(r3)$) | `00 B1 B4 FB F3 FC 0C FB 13 43` |

※ ARMv8-M Architecture Reference Manual 規定：`MLS Rd, Rn, Rm, Ra` 命令の動作は $Rd = Ra - (Rn \times Rm)$ である。したがって `mls r3, r12, r3, r4` は $Rd(r3) = Ra(r4) - Rn(r12) \times Rm(r3)$（$被除数 - 商 \times 除数 = 剰余$）を正しく算出する（検証仕様: [jit_compiler_test_spec.md](docs/components/tier3_jit/tests/jit_compiler_test_spec.md) `JITC-GOTCHA-06` を参照）。
※ 16-bit Thumb-2 命令（`adds r3, r4, r3` 等）はリトルエンディアンバイト列（`E3 18` 等）として格納される。実機エミュレータ検証（[`jit_trace_execution_verifier.py`](docs/components/tier3_jit/concepts/jit_trace_execution_verifier.py)）にて全ステンシルの動作整合性を検証済みである。
| `i32.and` (`0x71`) | `STENCIL_I32_AND_D2` | R3=TOS, R4=NOS | R3=TOS | `ands r3, r4, r3` | `23 40` |
| `i32.or` (`0x72`) | `STENCIL_I32_OR_D2` | R3=TOS, R4=NOS | R3=TOS | `orrs r3, r4, r3` | `23 43` |
| `i32.xor` (`0x73`) | `STENCIL_I32_XOR_D2` | R3=TOS, R4=NOS | R3=TOS | `eors r3, r4, r3` | `63 40` |
| `i32.shl` (`0x74`) | `STENCIL_I32_SHL_D2` | R3=TOS, R4=NOS | R3=TOS | `lsl.w r3, r4, r3` | `04 FA 03 F3` |
| `i32.shr_s` (`0x75`)| `STENCIL_I32_SHR_S_D2` | R3=TOS, R4=NOS | R3=TOS | `asr.w r3, r4, r3` | `44 FA 03 F3` |
| `i32.shr_u` (`0x76`)| `STENCIL_I32_SHR_U_D2` | R3=TOS, R4=NOS | R3=TOS | `lsr.w r3, r4, r3` | `24 FA 03 F3` |
| `i32.rotl` (`0x77`) | `STENCIL_I32_ROTL_D2` | R3=TOS, R4=NOS | R3=TOS | `rsb r12, r3, #32; ror.w r3, r4, r12` | `C3 F1 20 0C 64 FA 0C F3` |
| `i32.rotr` (`0x78`) | `STENCIL_I32_ROTR_D2` | R3=TOS, R4=NOS | R3=TOS | `ror.w r3, r4, r3` | `64 FA 03 F3` |
| `i32.clz` (`0x67`) | `STENCIL_I32_CLZ_D1` | R3=TOS | R3=TOS | `clz r3, r3` | `B3 FA 83 F3` |
| `i32.ctz` (`0x68`) | `STENCIL_I32_CTZ_D1` | R3=TOS | R3=TOS | `rbit r3, r3; clz r3, r3` | `93 FA A3 F3 B3 FA 83 F3` |

---

### 3.6 整数比較演算ステンシル (32-bit Integer Comparisons)
<!-- traceability: {JIT_CopyAndPatch} -->

| WASM 命令 | Stencil 名 | 入力状態 | 出力状態 | Thumb-2 命令列 | バイナリ列 (Hex) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `i32.eqz` (`0x45`) | `STENCIL_I32_EQZ_D1` | R3=TOS | R3=TOS | `cmp r3, #0; it eq; moveq r3, #1; it ne; movne r3, #0` | `00 2B 08 BF 01 23 18 BF 00 23` |
| `i32.eq` (`0x46`) | `STENCIL_I32_EQ_D2` | R3=TOS, R4=NOS | R3=TOS | `cmp r4, r3; it eq; moveq r3, #1; it ne; movne r3, #0` | `9C 42 08 BF 01 23 18 BF 00 23` |
| `i32.ne` (`0x47`) | `STENCIL_I32_NE_D2` | R3=TOS, R4=NOS | R3=TOS | `cmp r4, r3; it ne; moveq r3, #1; it eq; moveq r3, #0` | `9C 42 18 BF 01 23 08 BF 00 23` |
| `i32.lt_s` (`0x48`)| `STENCIL_I32_LT_S_D2` | R3=TOS, R4=NOS | R3=TOS | `cmp r4, r3; it lt; movlt r3, #1; it ge; movge r3, #0` | `9C 42 B8 BF 01 23 A8 BF 00 23` |
| `i32.lt_u` (`0x49`)| `STENCIL_I32_LT_U_D2` | R3=TOS, R4=NOS | R3=TOS | `cmp r4, r3; it lo; movlo r3, #1; it hs; movhs r3, #0` | `9C 42 38 BF 01 23 28 BF 00 23` |
| `i32.gt_s` (`0x4A`)| `STENCIL_I32_GT_S_D2` | R3=TOS, R4=NOS | R3=TOS | `cmp r4, r3; it gt; movgt r3, #1; it le; movle r3, #0` | `9C 42 C8 BF 01 23 D8 BF 00 23` |
| `i32.gt_u` (`0x4B`)| `STENCIL_I32_GT_U_D2` | R3=TOS, R4=NOS | R3=TOS | `cmp r4, r3; it hi; movhi r3, #1; it ls; movls r3, #0` | `9C 42 88 BF 01 23 98 BF 00 23` |
| `i32.le_s` (`0x4C`)| `STENCIL_I32_LE_S_D2` | R3=TOS, R4=NOS | R3=TOS | `cmp r4, r3; it le; movle r3, #1; it gt; movgt r3, #0` | `9C 42 D8 BF 01 23 C8 BF 00 23` |
| `i32.le_u` (`0x4D`)| `STENCIL_I32_LE_U_D2` | R3=TOS, R4=NOS | R3=TOS | `cmp r4, r3; it ls; movls r3, #1; it hi; movhi r3, #0` | `9C 42 98 BF 01 23 88 BF 00 23` |
| `i32.ge_s` (`0x4E`)| `STENCIL_I32_GE_S_D2` | R3=TOS, R4=NOS | R3=TOS | `cmp r4, r3; it ge; movge r3, #1; it lt; movlt r3, #0` | `9C 42 A8 BF 01 23 B8 BF 00 23` |
| `i32.ge_u` (`0x4F`)| `STENCIL_I32_GE_U_D2` | R3=TOS, R4=NOS | R3=TOS | `cmp r4, r3; it hs; movhs r3, #1; it lo; movlo r3, #0` | `9C 42 28 BF 01 23 38 BF 00 23` |

---

### 3.7 メモリアクセス系ステンシル (Linear Memory Load & Store with Boundary Protection)
<!-- traceability: {MemoryBoundaryCheck} {FastAddressCheck} {JIT_RegisterMapping} -->

すべてのロード/ストア命令は、`R9 = mem_size`（`execution_context.mem_size` [R0, #0x2C] からロード。`{FastAddressCheck}` が要求するのはサイズ比較の単一命令であり、マスクではない — `requirement_list.md` 参照）に対する `CMP` + `BHS.W` の境界チェックを経て、`R8 = mem_base`（`[R0, #0x28]` からロード）ピン留めバリアントによりアクセスされる（`R1`/`R2` ではない——`R1` は `sp`、`R2` は `local_base`）。`CMP addr, r9` の直後の `BHS.W <trap>` は、アドレスが `mem_size` 以上（符号なし）ならトレースのトラップテール（インタープリタへのフォールバック）へ即座に分岐する——実際のロード/ストアはこの分岐が不成立の場合にのみ実行される。境界チェックはロード/ストアの副作用（メモリアクセスそのもの）より必ず先に評価されるため、トラップ経路には巻き戻すべき副作用が存在しない。`mem_size` に2の冪の制約はなく、部分ページ（例: 8KB, 12KB, 16KB）・単一 64KB ページ・複数 64KB ページ（`N * 64KB`）のいずれも同一の比較一つで判定できる。

`BHS.W` の分岐先オフセットはコンパイル時には未確定（トレースのトラップテールは、通常の出口エピローグの後にレイアウトされるため、エピローグ全体が生成し終わるまでアドレスが決まらない）。JIT エンジン（`jit_copy_patch_concept.py` の `compile_trace()`）はプレースホルダのオフセット `0` で `BHS.W` を発行しつつ、その命令のバイト位置を記録しておき、トレース末尾にトラップテール（基本ブロック末尾フラッシュ + `fallback_interp`）を生成し終えた後、記録しておいた全ての `BHS.W` を実アドレスへバックパッチする（2パス発行 + バックパッチ。検証仕様: [jit_compiler_test_spec.md](docs/components/tier3_jit/tests/jit_compiler_test_spec.md) `JITC-GOTCHA-04`, `JITC-GOTCHA-05` を参照）。

> [!NOTE]
> **JITホットパスとインタープリタ/vMMIO経路の境界チェックは統一されている**: JITステンシル（本節）とインタープリタ/vMMIO側（[`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md)）は、どちらも同一の比較ベース境界チェック（マスクなし）を用い、境界外アクセスは必ずトラップしてインタープリタへフォールバックする。境界外アドレスを黙って範囲内へ折り畳んで処理を継続する（Address Wrapping）ことは許容されない。インタープリタがトラップ元の WASM PC から復旧できないと判断した場合は、ゲストタスクを停止してよい。`{MemoryBoundaryCheck}` `{vMMIO_TrapAndEmulate}`

| WASM 命令 | Stencil 名 | 入力状態 | 出力状態 | Thumb-2 命令列 (`R8=mem_base, R9=mem_size`) | バイナリ列 (Hex) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `i32.load` (`0x28`) | `STENCIL_I32_LOAD_R8` | R3=addr | R3=val | `cmp r3, r9; bhs.w <trap>; ldr.w r3, [r8, r3]` | `4B 45` + `BHS.W`(reloc) + `58 F8 03 30` |
| `i32.load8_s` (`0x2C`)| `STENCIL_I32_LOAD8_S_R8` | R3=addr | R3=val | `cmp r3, r9; bhs.w <trap>; ldrsb.w r3, [r8, r3]` | `4B 45` + `BHS.W`(reloc) + `18 F9 03 30` |
| `i32.load8_u` (`0x2D`)| `STENCIL_I32_LOAD8_U_R8` | R3=addr | R3=val | `cmp r3, r9; bhs.w <trap>; ldrb.w r3, [r8, r3]` | `4B 45` + `BHS.W`(reloc) + `18 F8 03 30` |
| `i32.load16_s` (`0x2E`)| `STENCIL_I32_LOAD16_S_R8` | R3=addr | R3=val | `cmp r3, r9; bhs.w <trap>; ldrsh.w r3, [r8, r3]` | `4B 45` + `BHS.W`(reloc) + `38 F9 03 30` |
| `i32.load16_u` (`0x2F`)| `STENCIL_I32_LOAD16_U_R8` | R3=addr | R3=val | `cmp r3, r9; bhs.w <trap>; ldrh.w r3, [r8, r3]` | `4B 45` + `BHS.W`(reloc) + `38 F8 03 30` |
| `i32.store` (`0x36`) | `STENCIL_I32_STORE_R8` | R3=val, R4=addr | (なし) | `cmp r4, r9; bhs.w <trap>; str.w r3, [r8, r4]` | `4C 45` + `BHS.W`(reloc) + `48 F8 04 30` |
| `i32.store8` (`0x3A`) | `STENCIL_I32_STORE8_R8` | R3=val, R4=addr | (なし) | `cmp r4, r9; bhs.w <trap>; strb.w r3, [r8, r4]` | `4C 45` + `BHS.W`(reloc) + `08 F8 04 30` |
| `i32.store16` (`0x3B`)| `STENCIL_I32_STORE16_R8` | R3=val, R4=addr | (なし) | `cmp r4, r9; bhs.w <trap>; strh.w r3, [r8, r4]` | `4C 45` + `BHS.W`(reloc) + `28 F8 04 30` |
| `memory.size` (`0x3F`)| `STENCIL_MEM_SIZE_D0` | (なし) | R3=pages | `ldr.w r3, [r0, #0x2C]` (`execution_context.mem_size`) | `D0 F8 2C 30` |

`cmp r3, r9`/`cmp r4, r9` のバイト列（`4B 45`/`4C 45`）は 16-bit Thumb-2 `CMP Rn, Rm` **T2** エンコーディング（`0x4500 | (N << 7) | (rm << 3) | (rn & 7)`、`N`は`rn`がR8以上のときに1）から導出する——`R9`はハイレジスタのため、低レジスタ同士でしか使えないT1エンコーディング（`0x4280 | (rm << 3) | rn`）は使えない（`jit_assembler_constexpr_concept.py` の `cmp_reg_t2` を正本とする）。`BHS.W <trap>` は 32-bit Thumb-2 条件分岐（`Cond.HS = 0b0010`）で、オフセットはバックパッチされるまで確定しないためリテラルのバイト列を持たない（`jit_copy_patch_concept.py` の `_MEMORY_OP_ADDR_REG` / `oob_branch_fixups` を正本とする）。

### 3.8 トレース内レジスタバリアント (Register Variants) と `variant_id`

<!-- traceability: {JIT_RegisterMapping} -->

各ステンシル名の末尾 `_dN` は、その命令が実行される時点でオペランドスタックキャッシュに何個の値が常駐しているか（= これから読み書きするレジスタの組）を表す**レジスタバリアント**であり、`jit_trace_header.variant_id`（8bit、[`jit_compiler.md`](docs/components/tier3_jit/jit_compiler.md) の ``jit_compiler.md` (Trace Header)` 参照）と同じ ID 空間を共有する。**この軸は同一トレース内部（intra-trace）で連続する命令間のレジスタ引き継ぎに関するものであり、トレース境界をまたぐチェイニング（`{JIT_LazyChaining}`）とは無関係である**——トレース境界は常にメモリ（オペランドスタック上の正準アドレス）経由でスピル/リロードされ、レジスタ内容を熱いまま引き継ぐことはない（[`jit_compiler.md`](docs/components/tier3_jit/jit_compiler.md) の `{JIT_LazyChaining}` を正本とする）。同一トレース内で、将来のステンシルバリアント動的選択が連続する命令間で異なるレジスタ配置を選ぶ場合（下記 NOTE 参照）にのみ、この `variant_id` を使った引き継ぎ互換性の判定とグルー挿入が意味を持つ。

| `variant_id` | 名称 | レジスタ占有状態 | 該当ステンシル |
| :---: | :--- | :--- | :--- |
| `0` | Depth 0 (Empty) | キャッシュなし。次の命令がゼロから値を生成する。 | `i32_const_d0`, `i64_const_d0`, `local_get_d0`, `global_get_d0`, `memory_size_d0` |
| `1` | Depth 1 (TOS) | `R3` = TOS のみ常駐。 | `i32_const_d1`, `local_set_d1`, `local_tee_d1`, `global_set_d1`, `br_if_d1`, `i32_eqz_d1`, `i32_clz_d1`, `i32_ctz_d1` |
| `2` | Depth 2 (TOS+NOS) | `R3` = TOS, `R4` = NOS が常駐。現行の唯一の物理レジスタ割当。 | `i32_add_d2` 等すべての2項算術・比較ステンシル |
| `3` | Depth 3 (TOS+NOS+NNOS) | `R3`/`R4`/`R5` の3値が常駐。`mem_base`/`mem_size` は `R8`/`R9` に分離されているため、メモリアクセス系ステンシルを含むトレースとも**両立できる**。 | `select_d3` |

メモリアクセス系ステンシル（`*_r8`、`{MemoryBoundaryCheck}`）はこの4段階のバリアント軸そのものではなく、Depth 1/2 の上に重ねて `R8=mem_base`/`R9=mem_size` を追加で要求する直交した制約である（ロード系は Depth 1 の `R3` をアドレスとして再利用、ストア系は Depth 2 の `R3=val, R4=addr` をそのまま用いる）。`R8`/`R9` は Depth 0-3 のいずれとも重ならないため、メモリアクセスは全バリアントと自由に組み合わせられる。

#### ローカル変数アクセスの基底ポインタと静的オフセット畳み込み (`ContextPointerRegister`)
<!-- traceability: {ContextPointerRegister} -->
ローカル変数アクセス（`local.get`/`local.set`/`local.tee`）は、JIT 専用のローカル変数基底レジスタ（`R2 = local_base`）経由（`[R2, #offset]`）として解決される。追加のベースレジスタを消費することなく極小フットプリントで実行可能である。`R2` は `local_base`（`local_param`）として JIT トレース内で固定される役割レジスタであり、`R3 = tos` および `mem_base`/`mem_size`（`R8`/`R9`）と同様に他ステンシルのスクラッチ用途と衝突させない（`jit_copy_patch_concept.py` を正本とする。※レジスタ分離検証は [jit_compiler_test_spec.md](docs/components/tier3_jit/tests/jit_compiler_test_spec.md) `JITC-GOTCHA-01` を参照）。

> [!NOTE]
> **現状は静的割当であり、動的なバリアント選択はまだ実装されていない**: `jit_copy_patch_concept.py` の `compile_trace()` は WASM 命令ごとに1つの固定ステンシルしか持たず（例: `i32.const` は常に特別処理で `R4` へ直接書き込み、`i32_const_d0`/`i32_const_d1` のどちらのステンシルも実際には参照しない）、実行時のキャッシュ深度に応じて `_d0`/`_d1`/`_d2` を動的に選び分けるロジックはまだ存在しない。したがって同一トレース内で連続する命令のレジスタ配置が食い違う状況も現状は発生しない。上表の `variant_id` は、(1) 将来その動的選択を実装する際の ID 体系、および (2) その際に必要となる命令間引き継ぎ互換性判定・グルー挿入（`_order_register_moves`/`emit_variant_reconciliation_glue` を参照、`jit_copy_patch_concept.py` 内の再利用可能なユーティリティとして検証済み実装が既に存在する）の両方に使われる、正本の割当表である。

---

### 3.9 64ビット整数・浮動小数点のランタイムヘルパー委譲方針
<!-- traceability: {Libgcc_Runtime_Helper} {JIT_RuntimeAPI_Fallback} -->

`i64` の除算・剰余・ビットシフト、および `f32`/`f64` 浮動小数点演算は、32-bit MCU（ARMv8-M / Cortex-M33）において `libgcc`（`__divdi3`, `__adddf3`, `__muldf3` 等）を呼び出すコードを生成する必要がある。

JIT コンパイラは、これら複雑な命令に対してインラインステンシルを展開せず、**ランタイムヘルパー関数呼び出しスタブ（`fireball_rt_*` / `{JIT_RuntimeAPI_Fallback}`）を生成して委譲**する。これにより、JIT ステンシルカタログを極小サイズ（ROM 予算 8KB）に保ち、FPU 有無のビルド差異をランタイムヘルパー関数内部に局所化する。
