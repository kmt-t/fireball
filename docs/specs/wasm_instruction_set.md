# WASM 命令セット物理仕様書 (Supported WASM Instruction Set) {VERIFY_FORMAL}
<!-- evidence:
     formal: formal/wasm_control_flow_model.py
     formal: formal/wasm_bulk_memory_model.py
     test: docs/qa/specs/wasm_instruction_set_test_spec.md
-->

## 1. 概要と適用方針
<!-- traceability: {ThreadedInterpreter} {JIT_CopyAndPatch} {Wasm32Only} {WasmFCSubset} {META_ZeroCostAbstraction} -->
本仕様書は、wasm32単一リニアメモリのWASM Core 1.0 MVPを基礎とし、`0xFC`のうち飽和型浮動小数点→整数変換8命令と`memory.copy`/`memory.fill`だけを追加した命令意味論とインタープリタ動作を定義する。その他の`0xFC`サブオペコードは非対応とする。ARMv8-M向けJITの物理命令列・レジスタ・ABI・メモリ保護方式はTBDとし、本書で確定しない。

x64で確認したInterpreter/JIT間の4論理引数契約と物理ABIは [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) を参照する。ARMv8-Mでは論理引数の物理配置、callee-save規則、値キャッシュ、トレース境界同期をすべてTBDとする。

---

## 2. 非サポート機能 (Explicit Non-Goals)
<!-- traceability: {Wasm32Only} {WasmFCSubset} {GLOBAL_StrictMemoryLimit} -->
本プロジェクトのWASM Core 1.0 MVPと選択拡張の範囲を明確にする。以下の機能はサポート対象外（Non-Goal）とし、ロード時にデコードエラー（`ERR_WASM_UNSUPPORTED_FEATURE`）として拒否する。対象プラットフォームのメモリ容量とは切り離して定める。
- **Wasm64 / Memory64 / Table64**: 64-bit アドレス空間・テーブル（完全除外 ）。
- **SIMD / Vector (`0xFD` プレフィックス)**: 128-bit ベクトル命令（本実装の対象外）。
- **Threads / Atomics (`0xFE` プレフィックス)**: 共有メモリ・アトミック命令（CSP ランデブー通信で代替）。
- **Garbage Collection (GC) / Reference Types (`externref`, `funcref`)**: 動的GCヒープを排除。
- **Exception Handling (EH)**: テーブル駆動例外ハンドリング。
- **Tail Call Optimization (`return_call`, `return_call_indirect`)**: MVP 範囲外。
- **その他の`0xFC`命令**: `memory.init`、`data.drop`、table操作、および本書で列挙しない全サブオペコード。

---

## 3. WASMオプコード物理マトリクス

### 3.1 制御フロー命令 (Control Flow)
<!-- traceability: {ThreadedInterpreter} {JIT_RuntimeAPI_Fallback} {ContextPointerRegister} -->

| Opcode | 命令名 | スタック遷移 | インタープリタ実装（継続渡し4論理引数） | ARMv8-M JIT mapping (TBD) | ARMv8-M physical behavior (TBD) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x00` | `unreachable` | `[] -> []` | トラップハンドラへジャンプ | TBD | TBD |
| `0x01` | `nop` | `[] -> []` | `ip + 1` へ継続渡し | TBD | TBD |
| `0x02` | `block` | `[] -> []` | 制御ブロックの復帰情報を記録 | TBD | TBD |
| `0x03` | `loop` | `[] -> []` | ループ先頭 PC を記録してプッシュ | TBD | TBD |
| `0x04` | `if` | `[i32] -> []` | 条件判定 $\to$ 偽なら else/end へ分岐 | TBD | TBD |
| `0x05` | `else` | `[] -> []` | 対応する end の直後へ無条件ジャンプ | TBD | TBD |
| `0x0B` | `end` | `[] -> []` | 制御ブロックの復帰情報を取り除く | TBD | TBD |
| `0x0C` | `br` | `[] -> []` | 指定深度のラベルへ無条件ジャンプ | TBD | TBD |
| `0x0D` | `br_if` | `[i32] -> []` | TOS $\ne 0$ ならラベルへジャンプ | TBD | TBD |
| `0x0E` | `br_table` | `[i32] -> []` | テーブルインデックス分岐 | TBD | TBD |
| `0x10` | `call` | `[t1*] -> [t2*]`| 関数呼出し記述子を積んで関数を呼び出す | TBD | TBD |
| `0x11` | `call_indirect`| `[t1*, i32] -> [t2*]`| 関数テーブル照合 $\to$ 間接呼出 | TBD | TBD |

---

### 3.2 パラメトリック命令 (Parametric)
<!-- traceability: {ContextPointerRegister} -->

| Opcode | 命令名 | スタック遷移 | インタープリタ実装 | ARMv8-M JIT mapping (TBD) | ARMv8-M physical behavior (TBD) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x1A` | `drop` | `[t] -> []` | SP オフセットを 1 減算 | TBD | TBD |
| `0x1B` | `select` | `[t, t, i32] -> [t]` | 条件に応じて 2 値から 1 つを選択 | TBD | TBD |

---

### 3.3 変数アクセス命令 (Variable Access)
<!-- traceability: {ContextPointerRegister} -->

| Opcode | 命令名 | スタック遷移 | インタープリタ実装 | ARMv8-M JIT mapping (TBD) | ARMv8-M physical behavior (TBD) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x20` | `local.get` | `[] -> [t]` | ローカル配列 `[local_base + idx]` をロード | TBD | TBD |
| `0x21` | `local.set` | `[t] -> []` | ローカル配列 `[local_base + idx]` へストア | TBD | TBD |
| `0x22` | `local.tee` | `[t] -> [t]` | ローカルへ保存しつつスタックに残す | TBD | TBD |
| `0x23` | `global.get` | `[] -> [t]` | モジュール実行情報のグローバル値配列から`idx`番目をロード | TBD | TBD |
| `0x24` | `global.set` | `[t] -> []` | グローバル配列へストア | TBD | TBD |

---

### 3.4 メモリアクセス命令 (Memory Access - 32-bit Linear Memory)
<!-- traceability: {MemoryBoundaryCheck} {FastAddressCheck} {PositionIndependentCode} -->

すべてのメモリアクセスは、ゲスト論理アドレスに対するアライメント・境界検査を伴う。アクセス時は借用されたホスト基点へ検査済み論理アドレスを加える。ゲスト論理アドレスとホスト基点は異なる値である。

| Opcode | 命令名 | スタック遷移 | インタープリタ実装 | ARMv8-M JIT mapping (TBD) | ARMv8-M physical behavior (TBD) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x28` | `i32.load` | `[i32] -> [i32]` | 開始・終端 (`addr+3`) の境界チェック（比較+トラップ） $\to$ 32-bit ロード | TBD | TBD |
| `0x29` | `i64.load` | `[i32] -> [i64]` | 開始・終端 (`addr+7`) の境界チェック（比較+トラップ） $\to$ 64-bit ロード | TBD | TBD |
| `0x2A` | `f32.load` | `[i32] -> [f32]` | 開始・終端 (`addr+3`) の境界チェック（比較+トラップ） $\to$ 単精度ロード | TBD | TBD |
| `0x2B` | `f64.load` | `[i32] -> [f64]` | 開始・終端 (`addr+7`) の境界チェック（比較+トラップ） $\to$ 倍精度ロード | TBD | TBD |
| `0x2C` | `i32.load8_s`| `[i32] -> [i32]` | 境界チェック（比較+トラップ） $\to$ 符号拡張 8-bit ロード | TBD | TBD |
| `0x2D` | `i32.load8_u`| `[i32] -> [i32]` | 境界チェック（比較+トラップ） $\to$ ゼロ拡張 8-bit ロード | TBD | TBD |
| `0x2E` | `i32.load16_s`| `[i32] -> [i32]`| 開始・終端 (`addr+1`) の境界チェック（比較+トラップ） $\to$ 符号拡張 16-bit ロード | TBD | TBD |
| `0x2F` | `i32.load16_u`| `[i32] -> [i32]`| 開始・終端 (`addr+1`) の境界チェック（比較+トラップ） $\to$ ゼロ拡張 16-bit ロード | TBD | TBD |
| `0x36` | `i32.store` | `[i32, i32] -> []` | 開始・終端 (`addr+3`) の境界チェック（比較+トラップ） $\to$ 32-bit メモリストア | TBD | TBD |
| `0x37` | `i64.store` | `[i32, i64] -> []` | 開始・終端 (`addr+7`) の境界チェック（比較+トラップ） $\to$ 64-bit メモリストア | TBD | TBD |
| `0x38` | `f32.store` | `[i32, f32] -> []` | 開始・終端 (`addr+3`) の境界チェック（比較+トラップ） $\to$ 単精度メモリストア | TBD | TBD |
| `0x39` | `f64.store` | `[i32, f64] -> []` | 開始・終端 (`addr+7`) の境界チェック（比較+トラップ） $\to$ 倍精度メモリストア | TBD | TBD |
| `0x3A` | `i32.store8` | `[i32, i32] -> []` | 境界チェック（比較+トラップ） $\to$ 8-bit メモリストア | TBD | TBD |
| `0x3B` | `i32.store16`| `[i32, i32] -> []` | 開始・終端 (`addr+1`) の境界チェック（比較+トラップ） $\to$ 16-bit メモリストア | TBD | TBD |
| `0x3F` | `memory.size`| `[] -> [i32]` | 現在のリニアメモリページ数を返す | TBD | TBD |
| `0x40` | `memory.grow`| `[i32] -> [i32]` | リニアメモリ拡張 (ランタイムAPI呼出) | TBD | TBD |

### 3.4.1 選択された`0xFC`メモリ操作 ({WasmFCSubset})
<!-- traceability: {WasmFCSubset} {VDMA} {MemoryBoundaryCheck} -->

命令列では`0xFC`の後にサブオペコードを符号なしLEB128で置く。`memory.copy`は宛先・元のmemory index、`memory.fill`はmemory indexを即値として持ち、いずれも単一メモリのindex `0`だけを受理する。即値の省略、非ゼロindex、または不正LEB128はロード時エラーとする。

| Encoding | 命令名 | スタック遷移 | 意味論と実行経路 |
| :--- | :--- | :--- | :--- |
| `0xFC 0x0A` | `memory.copy` | `[i32 dst, i32 src, i32 len] -> []` | リニアアドレス同士はCPUでコピーする。片方以上がDYNAMIC・SHM・PASSTHROUGHなら内部vDMAへ委譲する。完了とCPU可視性の確認後に次命令へ進む。コピー結果はmemmove意味論を保つ。 |
| `0xFC 0x0B` | `memory.fill` | `[i32 dst, i32 value, i32 len] -> []` | 全範囲を先に検査し、`value`の下位8 bitでCPU書込みする。vDMAオフロードは行わない。 |

`dst`、`src`、`len`は符号なし32-bit値として扱う。Fireballアドレス空間でBit 31が`0`のコピー端点はリニアメモリのbyte offsetとし、`offset <= memory_size`かつ`len <= memory_size - offset`を加算オーバーフローなしで検査する。Bit 31が`1`の端点はFC=13（DYNAMIC）、FC=14（SHM）、FC=15（PASSTHROUGH）だけを受理し、他のFCはvMMIOアクセス違反とする。vMMIO端点のPTE、権限、所有権および範囲はvMMIO共通アクセスゲートで検査する。いずれかの端点が不正ならコピーを開始せず、対象メモリを部分変更しない。`len == 0`でも各端点を検査する。

リニアメモリ同士のコピーは重複時を含めてCPU memmoveで実行する。どちらか一方でもvMMIO端点を含む場合は内部vDMAサービスへ委譲する。サービスは両端点を検証してから転送し、コピー元の元データを保つ。転送完了とCPU可視性を確認してから次命令へ進む。

内部vDMA委譲はguest importの`fireball:host/vdma.start`やvMMIOレジスタを介さず、ランタイムのコピーサービスを使う。内部の同期・非同期と待機経路は、[`runtime_vsoc.md`](docs/components/tier2_runtime/runtime_vsoc.md)の転送対象別契約に従う。

---

### 3.5 整数算術・論理・比較命令 (Integer Arithmetic, Logic & Comparison)
<!-- traceability: {JIT_CopyAndPatch} {META_ZeroCostAbstraction} -->

| Opcode | 命令名 | スタック遷移 | インタープリタ実装 | ARMv8-M JIT mapping (TBD) | ARMv8-M physical behavior (TBD) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x41` | `i32.const` | `[] -> [i32]` | 即値を TOS へプッシュ | TBD | TBD |
| `0x42` | `i64.const` | `[] -> [i64]` | 64-bit 即値をプッシュ | TBD | TBD |
| `0x45` | `i32.eqz` | `[i32] -> [i32]` | $x == 0$ 判定 | TBD | TBD |
| `0x46` | `i32.eq` | `[i32, i32] -> [i32]` | $a == b$ 判定 | TBD | TBD |
| `0x47` | `i32.ne` | `[i32, i32] -> [i32]` | $a \ne b$ 判定 | TBD | TBD |
| `0x48` | `i32.lt_s` | `[i32, i32] -> [i32]` | 符号付き $a < b$ | TBD | TBD |
| `0x49` | `i32.lt_u` | `[i32, i32] -> [i32]` | 符号なし $a < b$ | TBD | TBD |
| `0x67` | `i32.clz` | `[i32] -> [i32]` | 先頭連続ゼロビット数 | TBD | TBD |
| `0x68` | `i32.ctz` | `[i32] -> [i32]` | 末尾連続ゼロビット数 | TBD | TBD |
| `0x69` | `i32.popcnt`| `[i32] -> [i32]` | 立っているビット数 | TBD | TBD |
| `0x6A` | `i32.add` | `[i32, i32] -> [i32]` | 加算 | TBD | TBD |
| `0x6B` | `i32.sub` | `[i32, i32] -> [i32]` | 減算 | TBD | TBD |
| `0x6C` | `i32.mul` | `[i32, i32] -> [i32]` | 乗算 | TBD | TBD |
| `0x6D` | `i32.div_s` | `[i32, i32] -> [i32]` | 符号付き除算 (0除算トラップ)| TBD | TBD |
| `0x6E` | `i32.div_u` | `[i32, i32] -> [i32]` | 符号なし除算 (0除算トラップ)| TBD | TBD |
| `0x6F` | `i32.rem_s` | `[i32, i32] -> [i32]` | 符号付き剰余 (0除算トラップ)| TBD | TBD |
| `0x70` | `i32.rem_u` | `[i32, i32] -> [i32]` | 符号なし剰余 (0除算トラップ)| TBD | TBD |
| `0x71` | `i32.and` | `[i32, i32] -> [i32]` | ビット論理積 | TBD | TBD |
| `0x72` | `i32.or` | `[i32, i32] -> [i32]` | ビット論理和 | TBD | TBD |
| `0x73` | `i32.xor` | `[i32, i32] -> [i32]` | ビット排他論理和 | TBD | TBD |
| `0x74` | `i32.shl` | `[i32, i32] -> [i32]` | 左シフト | TBD | TBD |
| `0x75` | `i32.shr_s` | `[i32, i32] -> [i32]` | 算術右シフト | TBD | TBD |
| `0x76` | `i32.shr_u` | `[i32, i32] -> [i32]` | 論理右シフト | TBD | TBD |
| `0x77` | `i32.rotl` | `[i32, i32] -> [i32]` | 左循環シフト | TBD | TBD |
| `0x78` | `i32.rotr` | `[i32, i32] -> [i32]` | 右循環シフト | TBD | TBD |

---

### 3.6 64ビット整数・浮動小数点命令と Libgcc ランタイムヘルパー
<!-- traceability: {Libgcc_Runtime_Helper} {JIT_RuntimeAPI_Fallback} {ThreadedInterpreter} -->

ARMv8-Mでの64-bit整数・浮動小数点命令の命令選択、libgcc等の依存、helper呼出し規約はすべてTBDである。

| Opcode 群 | カテゴリ / 代表命令名 | スタック遷移 | インタープリタ実装 | ARMv8-M JIT mapping (TBD) | ARMv8-M physical behavior (TBD) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x79`〜`0x8A` | **i64 算術・論理・シフト** (`i64.clz`, `i64.ctz`, `i64.popcnt`, `i64.add`, `i64.sub`, `i64.mul`, `i64.div_s/u`, `i64.rem_s/u`, `i64.and/or/xor`, `i64.shl`, `i64.shr_s/u`, `i64.rotl/r`) | `[i64, i64] -> [i64]` | C++ `int64_t` / `libgcc` 呼び出し | TBD | TBD |
| `0x50`〜`0x5A` | **i64 比較命令** (`i64.eqz`, `i64.eq`, `i64.ne`, `i64.lt_s/u`, `i64.gt_s/u`, `i64.le_s/u`, `i64.ge_s/u`) | `[i64, i64] -> [i32]` | 64-bit 比較ハンドラ | TBD | TBD |
| `0x8B`〜`0x98` | **f32 単精度浮動小数点** (`f32.add`, `f32.sub`, `f32.mul`, `f32.div`, `f32.sqrt`, `f32.min`, `f32.max`, `f32.ceil/floor/trunc/nearest`) | `[f32, f32] -> [f32]` | C++ `float` / ハードウェア FPU / soft-float | TBD | TBD |
| `0x99`〜`0xA6` | **f64 倍精度浮動小数点** (`f64.add`, `f64.sub`, `f64.mul`, `f64.div`, `f64.sqrt`, `f64.min`, `f64.max`, `f64.ceil/floor/trunc/nearest`) | `[f64, f64] -> [f64]` | C++ `double` / `libgcc` soft-float | TBD | TBD |
| `0x5B`〜`0x66` | **f32/f64 浮動小数点比較** (`f32/f64.eq`, `ne`, `lt`, `gt`, `le`, `ge`) | `[f*, f*] -> [i32]` | IEEE 754 準拠比較 | TBD | TBD |
| `0xA7`〜`0xBF` | **型変換・再解釈命令** (`i32.wrap_i64`, `i64.extend_i32_*`, `i32/i64.trunc_f*`, `f32/f64.convert_i*`, `reinterpret`) | `[t1] -> [t2]` | 型変換・ビット再解釈ハンドラ | TBD | TBD |

### 3.7 飽和型浮動小数点→整数変換 ({WasmFCSubset})
<!-- traceability: {WasmFCSubset} {ThreadedInterpreter} {JIT_RuntimeAPI_Fallback} -->

飽和変換は`0xFC` prefixのサブオペコード`0`〜`7`で符号化される。サブオペコードは符号なしLEB128としてデコードする。スタック上の浮動小数点値を整数へ変換して1値を置き換える。全命令で変換自体はtrapを発生させない。

| Subopcode | 命令名 | スタック遷移 | NaN | 有限値・無限大 |
| :---: | :--- | :--- | :--- | :--- |
| `0` | `i32.trunc_sat_f32_s` | `[f32] -> [i32]` | `0` | 0方向に切り捨て、signed 32-bit範囲へ飽和 |
| `1` | `i32.trunc_sat_f32_u` | `[f32] -> [i32]` | `0` | 0方向に切り捨て、unsigned 32-bit範囲へ飽和 |
| `2` | `i32.trunc_sat_f64_s` | `[f64] -> [i32]` | `0` | 0方向に切り捨て、signed 32-bit範囲へ飽和 |
| `3` | `i32.trunc_sat_f64_u` | `[f64] -> [i32]` | `0` | 0方向に切り捨て、unsigned 32-bit範囲へ飽和 |
| `4` | `i64.trunc_sat_f32_s` | `[f32] -> [i64]` | `0` | 0方向に切り捨て、signed 64-bit範囲へ飽和 |
| `5` | `i64.trunc_sat_f32_u` | `[f32] -> [i64]` | `0` | 0方向に切り捨て、unsigned 64-bit範囲へ飽和 |
| `6` | `i64.trunc_sat_f64_s` | `[f64] -> [i64]` | `0` | 0方向に切り捨て、signed 64-bit範囲へ飽和 |
| `7` | `i64.trunc_sat_f64_u` | `[f64] -> [i64]` | `0` | 0方向に切り捨て、unsigned 64-bit範囲へ飽和 |

signed変換の結果範囲は`[-2^(N-1), 2^(N-1)-1]`、unsigned変換の結果範囲は`[0, 2^N-1]`である。負の非整数値は0方向へ切り捨ててから範囲に収める（例: `-0.5`からunsignedへの変換結果は`0`）。`+∞`と上限超過値は整数上限、`-∞`と下限超過値は整数下限（unsignedでは`0`）となる。符号付き/符号なしは命令指定に従い、NaNのpayloadや浮動小数点例外状態は結果へ伝播しない。

本節のWASM規則はインタープリタとJITの共通契約である。JITが専用の正確な実装を持たない場合はインタープリタハンドラへ委譲する。
