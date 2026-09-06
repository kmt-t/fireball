# WASM 命令セット物理仕様書 (Supported WASM Instruction Set) {VERIFY_FORMAL}
<!-- evidence:
     formal: formal/wasm_control_flow_model.py
     test: tests/wasm_instruction_set_test_spec.md
-->

## 1. 概要と適用方針
<!-- traceability: {ThreadedInterpreter} {JIT_CopyAndPatch} {Wasm32Only} {META_ZeroCostAbstraction} -->
本仕様書は、Fireball Hypervisor（インタープリタおよび Copy-and-Patch JIT コンパイラ）がサポートする **WASM MVP (v1, 32-bit)** 命令セットの物理マトリクスを定義する正本である。

全バイトコードは Cortex-M33（ARMv8-M）ターゲットにおける `__fastcall` 継続渡し（CPS）4引数シグネチャ（`R0: ctx`, `R1: sp`, `R2: local_base`, `R3: tos`）ハンドラ、および JIT Stencil テンプレート（同じ `R0`〜`R3` の CPS 引数マッピングを共有し、加えて Callee-saved 任意割当プール `R4-R6, R8-R11`（`R4`: TOS 次段キャッシュ NOS、`R5`: NNOS、メモリアクセス時は `R8`/`R9` を `mem_base`/`mem_size` に固定）、`R12`: 一時スクラッチ）へのマッピングを一意に確定する。基本ブロック末尾では、スタックがプッシュされた場合に `TOS, NOS, NNOS` をスタック（`[R1, #offset]`）へフラッシュし、コンテキスト `R0` の `ip`（+0x00）および `sp_offset`（+0x0C）を同期する。 `{ThreadedInterpreter}` `{JIT_CopyAndPatch}` `{Wasm32Only}` `{META_ZeroCostAbstraction}`

---

## 2. 非サポート機能 (Explicit Non-Goals)
<!-- traceability: {Wasm32Only} {GLOBAL_StrictMemoryLimit} -->
32KB〜64KB RAM の極小組込み環境における決定論的リアルタイム性と極小フットプリントを維持するため、以下の WASM 拡張仕様は明示的にサポート対象外（Non-Goal）とし、ロード時にデコードエラー（`ERR_WASM_UNSUPPORTED_FEATURE`）として即座に拒否する：
- **Wasm64 / Memory64 / Table64**: 64-bit アドレス空間・テーブル（完全除外 `{Wasm32Only}`）。
- **SIMD / Vector (`0xFD` プレフィックス)**: 128-bit ベクトル命令（Cortex-M33 非搭載）。
- **Threads / Atomics (`0xFE` プレフィックス)**: 共有メモリ・アトミック命令（CSP ランデブー通信で代替）。
- **Garbage Collection (GC) / Reference Types (`externref`, `funcref`)**: 動的GCヒープを排除。
- **Exception Handling (EH)**: テーブル駆動例外ハンドリング。
- **Tail Call Optimization (`return_call`, `return_call_indirect`)**: MVP 範囲外。

---

## 3. WASM MVP オプコード物理マトリクス

### 3.1 制御フロー命令 (Control Flow)
<!-- traceability: {ThreadedInterpreter} {JIT_RuntimeAPI_Fallback} {ContextPointerRegister} -->

| Opcode | 命令名 | スタック遷移 | インタープリタ実装 (`__fastcall` CPS) | JIT Stencil 提供 | 物理動作・備考 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x00` | `unreachable` | `[] -> []` | トラップハンドラへジャンプ | あり (Direct Trap) | `BKPT #0` またはトラップルーチン呼出 |
| `0x01` | `nop` | `[] -> []` | `ip + 1` へ継続渡し | あり (Eliminated) | JIT 時は命令生成をスキップ（0 byte） |
| `0x02` | `block` | `[] -> []` | `control_frame` をスタックへプッシュ | あり (Label Bind) | 分岐先ラベルの記録のみ |
| `0x03` | `loop` | `[] -> []` | ループ先頭 PC を記録してプッシュ | あり (Label Bind) | 後方ジャンプ先ターゲット |
| `0x04` | `if` | `[i32] -> []` | 条件判定 $\to$ 偽なら else/end へ分岐 | あり (Conditional Branch) | `CBZ` / `CBNZ` または `BNE` |
| `0x05` | `else` | `[] -> []` | 対応する end の直後へ無条件ジャンプ | あり (Unconditional Branch) | `B.W <end_label>` |
| `0x0B` | `end` | `[] -> []` | `control_frame` をポップ | あり (Label Target) | スコープ終了ラベル |
| `0x0C` | `br` | `[] -> []` | 指定深度のラベルへ無条件ジャンプ | あり (Branch) | `B.W <target_label>` |
| `0x0D` | `br_if` | `[i32] -> []` | TOS $\ne 0$ ならラベルへジャンプ | あり (Branch Cond) | `CMP r4, #0; BNE.W <target>` |
| `0x0E` | `br_table` | `[i32] -> []` | テーブルインデックス分岐 | あり (Jump Table) | `TBB` / `TBH` テーブル分岐 |
| `0x10` | `call` | `[t1*] -> [t2*]`| `call_frame` を積んで関数呼出 | フォールバック (Runtime API / Interp Fallback) | JIT 複雑度低減のため `vsoc_call_function` へ委譲 |
| `0x11` | `call_indirect`| `[t1*, i32] -> [t2*]`| 関数テーブル照合 $\to$ 間接呼出 | フォールバック (Runtime API / Interp Fallback) | 型シグネチャ照合＋ `vsoc_call_indirect` へ委譲 |

---

### 3.2 パラメトリック命令 (Parametric)
<!-- traceability: {ContextPointerRegister} -->

| Opcode | 命令名 | スタック遷移 | インタープリタ実装 | JIT Stencil 提供 | 物理動作・備考 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x1A` | `drop` | `[t] -> []` | SP オフセットを 1 減算 | あり (Register Drop) | TOS キャッシュを破棄または NOS 昇格 |
| `0x1B` | `select` | `[t, t, i32] -> [t]` | 条件に応じて 2 値から 1 つを選択 | あり (IT / CSEL) | Cortex-M33 `IT` ブロックまたは `MOVNE` |

---

### 3.3 変数アクセス命令 (Variable Access)
<!-- traceability: {ContextPointerRegister} {JIT_RegisterMapping} -->

| Opcode | 命令名 | スタック遷移 | インタープリタ実装 | JIT Stencil 提供 | 物理動作・備考 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x20` | `local.get` | `[] -> [t]` | ローカル配列 `[local_base + idx]` をロード | あり (Direct LDR / Mov) | `LDR r4, [r2, #offset]`（`r2=local_base` 起点の静的オフセット畳み込み——`{ContextPointerRegister}` `{JIT_RegisterMapping}` 参照） |
| `0x21` | `local.set` | `[t] -> []` | ローカル配列 `[local_base + idx]` へストア | あり (Direct STR / Mov) | `STR r4, [r2, #offset]` |
| `0x22` | `local.tee` | `[t] -> [t]` | ローカルへ保存しつつスタックに残す | あり (STR & Keep) | `STR r4, [r2, #offset]` (TOS維持) |
| `0x23` | `global.get` | `[] -> [t]` | グローバル配列 `[execution_context.globals_base + idx]` ロード | あり (LDR via globals_base) | `LDR.W r12, [r1, #0x28]; LDR.W r4, [r12, #glob_off]`（`{ExecutionContext_Layout}` 参照） |
| `0x24` | `global.set` | `[t] -> []` | グローバル配列へストア | あり (STR via globals_base) | `LDR.W r12, [r1, #0x28]; STR.W r4, [r12, #glob_off]` |

---

### 3.4 メモリアクセス命令 (Memory Access - 32-bit Linear Memory)
<!-- traceability: {MemoryBoundaryCheck} {FastAddressCheck} {PositionIndependentCode} -->

すべてのメモリアクセスは、リニアメモリ基底（`mem_base`）加算とアライメント・境界チェックを伴う。

| Opcode | 命令名 | スタック遷移 | インタープリタ実装 | JIT Stencil 提供 | 物理動作・備考 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x28` | `i32.load` | `[i32] -> [i32]` | 境界チェック（比較+トラップ） $\to$ 32-bit ロード | あり (LDR.W) | `CMP r4, r9; BHS.W <trap>; LDR r4, [r8, r4]` (`r8=mem_base, r9=mem_size`) |
| `0x29` | `i64.load` | `[i32] -> [i64]` | 境界チェック（比較+トラップ） $\to$ 64-bit ロード | あり (LDRD) | `CMP r4, r9; BHS.W <trap>; LDRD r4, r5, [r8, r4]` |
| `0x2A` | `f32.load` | `[i32] -> [f32]` | 境界チェック（比較+トラップ） $\to$ 単精度ロード | あり (VLDR.32) | `CMP r4, r9; BHS.W <trap>; VLDR s0, [r8, r4]` (FPU搭載時) |
| `0x2B` | `f64.load` | `[i32] -> [f64]` | 境界チェック（比較+トラップ） $\to$ 倍精度ロード | あり (VLDR.64) | `CMP r4, r9; BHS.W <trap>; VLDR d0, [r8, r4]` (FPv5搭載時) |
| `0x2C` | `i32.load8_s`| `[i32] -> [i32]` | 境界チェック（比較+トラップ） $\to$ 符号拡張 8-bit ロード | あり (LDRSB) | `CMP r4, r9; BHS.W <trap>; LDRSB r4, [r8, r4]` |
| `0x2D` | `i32.load8_u`| `[i32] -> [i32]` | 境界チェック（比較+トラップ） $\to$ ゼロ拡張 8-bit ロード | あり (LDRB) | `CMP r4, r9; BHS.W <trap>; LDRB r4, [r8, r4]` |
| `0x2E` | `i32.load16_s`| `[i32] -> [i32]`| 境界チェック（比較+トラップ） $\to$ 符号拡張 16-bit ロード | あり (LDRSH) | `CMP r4, r9; BHS.W <trap>; LDRSH r4, [r8, r4]` |
| `0x2F` | `i32.load16_u`| `[i32] -> [i32]`| 境界チェック（比較+トラップ） $\to$ ゼロ拡張 16-bit ロード | あり (LDRH) | `CMP r4, r9; BHS.W <trap>; LDRH r4, [r8, r4]` |
| `0x36` | `i32.store` | `[i32, i32] -> []` | 境界チェック（比較+トラップ） $\to$ 32-bit メモリストア | あり (STR.W) | `CMP r5, r9; BHS.W <trap>; STR r4, [r8, r5]` (`r4=val, r5=addr`) |
| `0x37` | `i64.store` | `[i32, i64] -> []` | 境界チェック（比較+トラップ） $\to$ 64-bit メモリストア | あり (STRD) | `CMP r4, r9; BHS.W <trap>; STRD r5, r6, [r8, r4]`（値ペア高位語は `mem_base`/`mem_size` と衝突しない `r6` を使う） |
| `0x38` | `f32.store` | `[i32, f32] -> []` | 境界チェック（比較+トラップ） $\to$ 単精度メモリストア | あり (VSTR.32) | `CMP r4, r9; BHS.W <trap>; VSTR s0, [r8, r4]` |
| `0x39` | `f64.store` | `[i32, f64] -> []` | 境界チェック（比較+トラップ） $\to$ 倍精度メモリストア | あり (VSTR.64) | `CMP r4, r9; BHS.W <trap>; VSTR d0, [r8, r4]` |
| `0x3A` | `i32.store8` | `[i32, i32] -> []` | 境界チェック（比較+トラップ） $\to$ 8-bit メモリストア | あり (STRB) | `CMP r5, r9; BHS.W <trap>; STRB r4, [r8, r5]` |
| `0x3B` | `i32.store16`| `[i32, i32] -> []` | 境界チェック（比較+トラップ） $\to$ 16-bit メモリストア | あり (STRH) | `CMP r5, r9; BHS.W <trap>; STRH r4, [r8, r5]` |
| `0x3F` | `memory.size`| `[] -> [i32]` | 現在のリニアメモリページ数を返す | あり (LDR via execution_context.mem_size) | `LDR.W r4, [r1, #0x24]` |
| `0x40` | `memory.grow`| `[i32] -> [i32]` | リニアメモリ拡張 (ランタイムAPI呼出) | あり (Runtime Call) | `BL vsoc_memory_grow` |

---

### 3.5 整数算術・論理・比較命令 (Integer Arithmetic, Logic & Comparison)
<!-- traceability: {JIT_CopyAndPatch} {META_ZeroCostAbstraction} -->

| Opcode | 命令名 | スタック遷移 | インタープリタ実装 | JIT Stencil 提供 | 物理動作 (Cortex-M33 Thumb-2) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x41` | `i32.const` | `[] -> [i32]` | 即値を TOS へプッシュ | あり (MOVW / MOV) | `MOVW r4, #imm16; MOVT r4, #imm16` |
| `0x42` | `i64.const` | `[] -> [i64]` | 64-bit 即値をプッシュ | あり (2x MOV) | 2 レジスタへロード |
| `0x45` | `i32.eqz` | `[i32] -> [i32]` | $x == 0$ 判定 | あり (CMP & IT) | `CMP r4, #0; IT EQ; MOVEQ r4, #1; IT NE; MOVNE r4, #0` |
| `0x46` | `i32.eq` | `[i32, i32] -> [i32]` | $a == b$ 判定 | あり (CMP & IT) | `CMP r5, r4; IT EQ; MOVEQ r4, #1; IT NE; MOVNE r4, #0` |
| `0x47` | `i32.ne` | `[i32, i32] -> [i32]` | $a \ne b$ 判定 | あり (CMP & IT) | `CMP r5, r4; IT NE; MOVNE r4, #1; IT EQ; MOVEQ r4, #0` |
| `0x48` | `i32.lt_s` | `[i32, i32] -> [i32]` | 符号付き $a < b$ | あり (CMP & LT) | `CMP r5, r4; IT LT; MOVLT r4, #1; IT GE; MOVGE r4, #0` |
| `0x49` | `i32.lt_u` | `[i32, i32] -> [i32]` | 符号なし $a < b$ | あり (CMP & LO) | `CMP r5, r4; IT LO; MOVLO r4, #1; IT HS; MOVHS r4, #0` |
| `0x67` | `i32.clz` | `[i32] -> [i32]` | 先頭連続ゼロビット数 | あり (CLZ) | `CLZ r4, r4` |
| `0x68` | `i32.ctz` | `[i32] -> [i32]` | 末尾連続ゼロビット数 | あり (RBIT & CLZ) | `RBIT r4, r4; CLZ r4, r4` |
| `0x69` | `i32.popcnt`| `[i32] -> [i32]` | 立っているビット数 | あり (Inline SW) | 算術アルゴリズム展開 |
| `0x6A` | `i32.add` | `[i32, i32] -> [i32]` | 加算 | あり (ADDS / ADD) | `ADDS r4, r5, r4` |
| `0x6B` | `i32.sub` | `[i32, i32] -> [i32]` | 減算 | あり (SUBS / SUB) | `SUBS r4, r5, r4` |
| `0x6C` | `i32.mul` | `[i32, i32] -> [i32]` | 乗算 | あり (MUL) | `MUL r4, r5, r4` |
| `0x6D` | `i32.div_s` | `[i32, i32] -> [i32]` | 符号付き除算 (0除算トラップ)| あり (SDIV) | 0判定 $\to$ `SDIV r4, r5, r4` |
| `0x6E` | `i32.div_u` | `[i32, i32] -> [i32]` | 符号なし除算 (0除算トラップ)| あり (UDIV) | 0判定 $\to$ `UDIV r4, r5, r4` |
| `0x71` | `i32.and` | `[i32, i32] -> [i32]` | ビット論理積 | あり (ANDS / AND) | `ANDS r4, r5, r4` |
| `0x72` | `i32.or` | `[i32, i32] -> [i32]` | ビット論理和 | あり (ORRS / ORR) | `ORRS r4, r5, r4` |
| `0x73` | `i32.xor` | `[i32, i32] -> [i32]` | ビット排他論理和 | あり (EORS / EOR) | `EORS r4, r5, r4` |
| `0x74` | `i32.shl` | `[i32, i32] -> [i32]` | 左シフト | あり (LSL.W, 3オペランド) | `LSL.W r4, r5, r4` |
| `0x75` | `i32.shr_s` | `[i32, i32] -> [i32]` | 算術右シフト | あり (ASR.W, 3オペランド) | `ASR.W r4, r5, r4` |
| `0x76` | `i32.shr_u` | `[i32, i32] -> [i32]` | 論理右シフト | あり (LSR.W, 3オペランド) | `LSR.W r4, r5, r4` |
| `0x77` | `i32.rotl` | `[i32, i32] -> [i32]` | 左循環シフト | あり (RSB & ROR.W) | `RSB r12, r4, #32; ROR.W r4, r5, r12` |
| `0x78` | `i32.rotr` | `[i32, i32] -> [i32]` | 右循環シフト | あり (ROR.W, 3オペランド) | `ROR.W r4, r5, r4` |

---

### 3.6 64ビット整数・浮動小数点命令と Libgcc ランタイムヘルパー
<!-- traceability: {Libgcc_Runtime_Helper} {JIT_RuntimeAPI_Fallback} {ThreadedInterpreter} -->

32ビット極小組み込みマイコン（ARM Cortex-M33 等）において、64ビット整数除算・剰余・ビットシフトや、単精度・倍精度浮動小数点（`f32`/`f64`）演算は、ハードウェア命令が存在しないか、あるいはコンパイラランタイムライブラリ（`libgcc` の `__divdi3`, `__udivdi3`, `__adddf3`, `__muldf3`, `__fixdfsi` 等）を呼び出すコードが生成される。

Fireball では、これらの命令をインライン展開で肥大化させず、**ランタイムヘルパー関数 / 専用ハンドラ経由（`{Libgcc_Runtime_Helper}` / `{JIT_RuntimeAPI_Fallback}`）で統一的にディスパッチ**する。

| Opcode 群 | カテゴリ / 代表命令名 | スタック遷移 | インタープリタ実装 | JIT Stencil 方針 (`{JIT_RuntimeAPI_Fallback}`) | 物理動作・Libgcc 連携 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x79`〜`0x8A` | **i64 算術・論理・シフト** (`i64.add`, `i64.sub`, `i64.mul`, `i64.div_s/u`, `i64.rem_s/u`, `i64.shl`, `i64.shr_s/u`, `i64.rotl/r`) | `[i64, i64] -> [i64]` | C++ `int64_t` / `libgcc` 呼び出し | ランタイムヘルパー呼び出し (`fireball_rt_i64_*`) | `__divdi3`, `__udivdi3`, `__moddi3`, `__umoddi3`, `__ashldi3` 等の呼出 |
| `0x51`〜`0x5A` | **i64 比較命令** (`i64.eqz`, `i64.eq`, `i64.ne`, `i64.lt_s/u`, `i64.gt_s/u`, `i64.le_s/u`, `i64.ge_s/u`) | `[i64, i64] -> [i32]` | 64-bit 比較ハンドラ | ランタイムヘルパー呼び出し (`fireball_rt_i64_cmp`) | 上位・下位 32-bit ワード順次比較 |
| `0x8B`〜`0x98` | **f32 単精度浮動小数点** (`f32.add`, `f32.sub`, `f32.mul`, `f32.div`, `f32.sqrt`, `f32.min`, `f32.max`, `f32.ceil/floor/trunc/nearest`) | `[f32, f32] -> [f32]` | C++ `float` / ハードウェア FPU / soft-float | FPU 命令またはランタイムヘルパー | FPU 搭載時は単精度命令、非搭載時は `libgcc` soft-float |
| `0x99`〜`0xA6` | **f64 倍精度浮動小数点** (`f64.add`, `f64.sub`, `f64.mul`, `f64.div`, `f64.sqrt`, `f64.min`, `f64.max`, `f64.ceil/floor/trunc/nearest`) | `[f64, f64] -> [f64]` | C++ `double` / `libgcc` soft-float | ランタイムヘルパー呼び出し (`fireball_rt_f64_*`) | `__adddf3`, `__subdf3`, `__muldf3`, `__divdf3` 等の呼出 |
| `0x5B`〜`0x66` | **f32/f64 浮動小数点比較** (`f32/f64.eq`, `ne`, `lt`, `gt`, `le`, `ge`) | `[f*, f*] -> [i32]` | IEEE 754 準拠比較 | FPU 比較またはランタイムヘルパー | `__eqdf2`, `__ltdf2`, `__gtdf2` 等の呼出 |
| `0xA7`〜`0xBF` | **型変換・再解釈命令** (`i32.wrap_i64`, `i64.extend_i32_*`, `i32/i64.trunc_f*`, `f32/f64.convert_i*`, `reinterpret`) | `[t1] -> [t2]` | 型変換・ビット再解釈ハンドラ | 単純変換はインライン、切捨/変換はヘルパー | `__fixsfsi`, `__fixdfdi`, `__floatsisf`, `__floatdidf` 等の呼出 |
