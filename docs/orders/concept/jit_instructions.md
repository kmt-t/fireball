# JITコンパイラ命令セット定義 (constexprアセンブラ用)

## 1. 概要
FireballのJITコンパイラは、`constexpr`アセンブラを用いて命令テンプレートを生成する。本ドキュメントでは、Copy-and-Patch方式において必要となる最小限の命令セットを、ターゲットアーキテクチャ（ARMv8M, RISC-V32, x64）ごとに定義する。

## 2. レジスタ割り当てと呼び出し規約 `{JIT_RegisterMapping}`

### 2.1 固定レジスタ割り当て
各アーキテクチャにおいて、WASMの実行状態を保持するために以下のレジスタを固定的に割り当てる。

| 役割 | ARMv8M | RISC-V32 | x64 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| **Context** | `r10` | `s1 (x9)` | `r14` | `jit_context` 構造体へのポインタ |
| **StackTop** | `r11` | `s2 (x18)` | `r15` | WASMオペランドスタックのトップポインタ |
| **WASM_PC** | `r12` | `s3 (x19)` | `r12` | 現在実行中のWASM命令のPC |

### 2.2 ランタイムAPI呼び出し規約 (`__fastcall` 準拠)
ランタイムAPIは `void` 戻り値であり、結果は `Context` 内に直接書き込まれる。引数は以下の3つをレジスタ経由で渡す。

| 引数順序 | ARMv8M | RISC-V32 | x64 (Win) | 内容 |
| :--- | :--- | :--- | :--- | :--- |
| **第1引数 (Arg1)** | `r0` | `a0` | `rcx` | プログラムカウンタ (PC) |
| **第1引数 (Arg2)** | `r1` | `a1` | `rdx` | オペランドスタック Top |
| **第4引数 (Arg3)** | `r2` | `a2` | `r8` | 実行コンテキスト (Context) |

## 3. 抽出命令リスト

### 3.1 算術・論理演算 (i32)
WASMの基本演算に対応する命令。

| WASM命令 | ARMv8M | RISC-V32 | x64 |
| :--- | :--- | :--- | :--- |
| `i32.add` | `ADD` | `ADD` | `ADD` |
| `i32.sub` | `SUB` | `SUB` | `SUB` |
| `i32.mul` | `MUL` | `MUL` | `IMUL` |
| `i32.div_s` | `SDIV` | `DIV` | `IDIV` |
| `i32.div_u` | `UDIV` | `DIVU` | `DIV` |
| `i32.and` | `AND` | `AND` | `AND` |
| `i32.or` | `ORR` | `OR` | `OR` |
| `i32.xor` | `EOR` | `XOR` | `XOR` |
| `i32.shl` | `LSL` | `SLL` | `SHL` |
| `i32.shr_s` | `ASR` | `SRA` | `SAR` |
| `i32.shr_u` | `LSR` | `SRL` | `SHR` |

### 3.2 オペランドスタック操作
スタックトップ（`StackTop`レジスタ）を介したデータの読み書き。

| 操作 | ARMv8M | RISC-V32 | x64 |
| :--- | :--- | :--- | :--- |
| **Load (i32)** | `LDR r0, [r11, #-4]!` | `lw a0, -4(s2); addi s2, s2, -4` | `mov eax, [r15-4]; sub r15, 4` |
| **Store (i32)** | `STR r0, [r11], #4` | `sw a0, 0(s2); addi s2, s2, 4` | `mov [r15], eax; add r15, 4` |

### 3.3 PCインクリメント
各命令のコンパイル時に、WASMの命令サイズ分だけ `WASM_PC` レジスタを更新する。

| アーキテクチャ | 命令例 |
| :--- | :--- |
| **ARMv8M** | `ADD r12, r12, #imm` |
| **RISC-V32** | `addi s3, s3, imm` |
| **x64** | `add r12, imm` |

### 3.4 ランタイムAPI呼び出し `{JIT_RuntimeAPI_Fallback}`
複雑な命令や、比較・分岐ロジックを内包するC++関数を呼び出す。

1.  **引数ロード**: スタックトップから `Arg1`, へロード。`Arg2` に `WASM_PC`、`Arg3` に `Context` をセット。
2.  **呼び出し**: 関数アドレスをレジスタにロードして分岐。
3.  **レジスタ同期**: API側で更新された `StackTop` を `Context` からレジスタへリロードする。

| アーキテクチャ | 命令例 (Call & Reload) |
| :--- | :--- |
| **ARMv8M** | `LDR r4, =addr; BLX r4; LDR r11, [r10, #offset_stack_top]` |
| **RISC-V32** | `lui t0, %hi(addr); jalr ra, %lo(addr)(t0); lw s2, offset_stack_top(s1)` |
| **x64** | `mov r11, addr; call r11; mov r15, [r14 + offset_stack_top]` |

## 4. パッチ対象（Holes）の定義

Copy-and-Patchにおいて実行時に書き換える箇所。

1.  **Immediate**: `i32.const` 等の即値、およびPCインクリメントの `imm`。
2.  **Runtime Address**: ランタイム関数の絶対アドレス。

## 5. 実装上の注意

- **x64 (Windows)**: `__fastcall` (または x64 calling convention) では、呼び出し前にスタックに 32バイトのシャドウスペースを確保する必要がある点に注意。
- **レジスタ保存**: ランタイムAPI呼び出し前後で、固定割り当てレジスタ（Context, StackTop, WASM_PC）が破壊されないよう、C++側の関数宣言に適切な属性（`__attribute__((pcs("aapcs")))` 等）を付与するか、JIT側で退避を行う。
