# Fireball マスター物理設計仕様書 (Master Physical Design) {VERIFY_LLM}

## 1. 概要と基本思想 (Overview & Philosophy)
<!-- traceability: {META_AI_Native_Dev} {META_3TierSeparation} {META_ZeroCostAbstraction} -->
本仕様書は、Fireball Hypervisor における**「マスター物理設計（Master Physical Design）」**を定義する正本である。

抽象的な要求語彙や設計方針（Why/What）がどのようにマイコンの物理ハードウェア（CPUレジスタ、SRAM、Thumb-2命令、MPU）上に具象化されるかを一意に確定させ、読み手や実装者の脳内にブレのない強固な物理実像（Mental Model）を結像させることを目的とする。 `{META_AI_Native_Dev}` `{META_3TierSeparation}` `{META_ZeroCostAbstraction}`

---

## 2. 6大物理コアメカニズム (The 6 Physical Pillars)

Fireball の実行コアは、以下の 6 つの明確に命名された物理メカニズムによって構成される。

```
+---------------------------------------------------------------------------------------------------+
|                                  FIREBALL MASTER PHYSICAL DESIGN                                  |
+---------------------------------------------------------------------------------------------------+
|  [Pillar 1] 統合スタックフレーム・モデル (Unified Stack Frame Model)                              |
|             └─ 基底 stack_bot (R1), ボトム常駐 execution_context, インラインフレーム/ローカル/オペランド  |
+---------------------------------------------------------------------------------------------------+
|  [Pillar 2] 3段直接 JIT 検索パイプライン (3-Stage Direct JIT Lookup Pipeline)                     |
|             └─ Card Marking (O(1)) -> Entry Group Index (O(1)) -> flat_map_view Binary Search     |
+---------------------------------------------------------------------------------------------------+
|  [Pillar 3] 3面世代交代回転コードキャッシュ (3-Bank Generational Rotating Code Cache)             |
|             └─ Bank 0 (Active) <-> Bank 1 (Warm) <-> Bank 2 (Oldest) + 最古限定昇格 + MPU W^X     |
+---------------------------------------------------------------------------------------------------+
|  [Pillar 4] 対称直接ハンドオフ・エンジン (Symmetric Direct Handoff Engine)                        |
|             └─ 純粋同期ランデブー (容量0) + スケジューラバイパス 対称遷移 (Symmetric Transfer)     |
+---------------------------------------------------------------------------------------------------+
|  [Pillar 5] 折りたたみXOR TLB ＆ 平坦ページ表 (Folding XOR TLB & FlatMap Page Table)               |
|             └─ 20-bit VPN Folding XOR (16 entries) + flat_map_view PTE FlatMap                    |
+---------------------------------------------------------------------------------------------------+
|  [Pillar 6] 有界ゼロコピー・ランデブー・メールボックス (Bounded Zero-Copy Rendezvous Mailbox)     |
|             └─ Revoke -> Enqueue -> Grant (TCBポインタ置換によるゼロコピー所有権移転)              |
+---------------------------------------------------------------------------------------------------+
```

---

### 2.1 Pillar 1: 統合スタックフレーム・モデル (Unified Stack Frame Model)
<!-- traceability: {ContextPointerRegister} {MemoryBoundaryCheck} {ThreadedInterpreter} -->
- **物理実体**: 単一の連続した固定長メモリバッファ（2KB〜4KB）。
- **物理レイアウト**:
  1. **スタックボトム (`+0x00`)**: `execution_context` 構造体が固定配置される（SP長 `sp_offset`、フレームオフセット `frame_offset`、スタック境界 `sp_boundary`、ハンドラテーブル参照等を保持——リニアメモリ基底 `mem_base` は `vsoc_runtime`（`R2: env`）側が所有する。§3.2 参照）。
  2. **スタック中間〜トップ**: `CallFrame`（親フレームオフセット、戻り先PC）、`Function Locals`（ローカル変数スロット）、`Operand Stack`（計算用オペランド）、`ControlFrames`（ブロック・ループ分岐情報）が単一の配列上にインラインで積層される。
- **レジスタ渡し規約**:
  - `R1: stack_bot`（スタックボトム基底ポインタ）が全バイトコードハンドラおよび JIT トレースへ不変で渡される。
  - ハンドラ間でのスタックポインタ（SP）のレジスタ受け渡しは行わず、スタック長はコンテキスト内の `sp_offset` で管理される。
- **物理的利点**: 複数の独立したスタックバッファ（フレーム用、ローカル用、オペランド用）の管理オーバーヘッドを完全に排除し、Caller-saved レジスタ `R3` を一時計算用スクラッチとして解放する。 `{ContextPointerRegister}`

---

### 2.2 Pillar 2: 3段直接 JIT 検索パイプライン (3-Stage Direct JIT Lookup Pipeline)
<!-- traceability: {SimpleJITArchitecture} {JIT_MultiBuffer_Cache} {FlatViewNarrowing} {META_FlatMapIndexed} {META_BinarySearch} -->
- **物理実体**: 密な状態ビット配列、粗索引固定配列、およびソート済みキー・値ペア配列の 3 段物理パイプライン。
- **検索の物理ステップ**:
  1. **Stage 1 (カードマーキング表: `bit_view<2>`) [$O(1)$]**:
     - WASM PC から `pc >> card_shift` でカードインデックスを算出し、2-bit 状態表を $O(1)$ で直接ロード・マスク判定する。
     - 状態が `COMPILED`（3）でない場合は、1 回のメモリアクセスで即座にインタープリタ継続（Fast Exit）。
  2. **Stage 2 (JITエントリグループインデックス) [$O(1)$]**:
     - `COMPILED` の場合、`pc >> entry_group_shift` により固定長粗索引配列を参照し、JITエントリ表の探索区間 `[first, last]` を $O(1)$ で切り出す。
  3. **Stage 3 (JITエントリ表: `flat_map_view`) [$O(\log n)$]**:
     - 切り出された狭い探索区間（`flat_map_view`）に対してのみ二分探索を実行し、ネイティブ実行アドレス（`exec_trace`）を特定する。
- **物理的利点**: 全件二分探索のキャッシュミスと比較回数を最小化し、WASM 実行ループ内での検索オーバーヘッドを極限まで圧縮する。 `{FlatViewNarrowing}` `{META_BinarySearch}`

---

### 2.3 Pillar 3: 3面世代交代回転コードキャッシュ (3-Bank Generational Rotating Code Cache)
<!-- traceability: {JIT_MultiBuffer_Cache} {JIT_OldestOnly_Promote} {SimpleJITArchitecture} -->
- **物理実体**: 物理的に等小な 3 つのメモリバンク（例: 2KB × 3 = 6KB）。
- **3面の物理的役割**:
  - `Bank 0 (Active)`: 新規 JIT コンパイルコードおよび Oldest からの昇格コードを格納する書き込み対象バンク。
  - `Bank 1 (Warm)`: 1 世代前のコードを保持する観測バンク。**無償観測期間 (Observation Window)** として昇格コピーを行わずにそのまま実行する。
  - `Bank 2 (Oldest)`: 2 世代前のコードを保持する破棄直前バンク。ここでヒットした Hot コードのみを新 Active バンクへ **昇格コピー (Promote)** する。
- **MPU W^X 保護遷移**:
  - コンパイル時: 対象バンクを `RW + XN`（書込可・実行不可）に設定。
  - パッチ完了時: `__DSB(); __ISB();` を発行し、`RO + X`（読取専用・実行可）に切り替える。
- **局所再チェイニング＆アンリンク機構 (Inbound Chain Index Table)**:
  - バンクごとに被チェイン元 JIT エントリインデックスを保持する固定長配列 `bank_inbound_chains[3]` を配備。
  - ローテーション時、全 JIT エントリを走査（全舐め）することなく、Oldest バンクがパージされ新 Active へ再利用される直前に、そのバンクを指す被チェイン元のみを $O(k)$ で参照し、ターゲットが昇格済みの場合は再チェイニング、完全破棄の場合のみインタープリタ復帰スタブにアンパッチする（Warm $\to$ Oldest 期間中はチェイン実行を維持）。
- **インライン JIT トレースヘッダ (Inline Trace Header)**:
  - キャッシュ内の各トレース先頭に 16 バイト固定長のメタデータヘッダ（`jit_trace_header`）をインライン配置。$O(1)$ ゼロルックアップ逆引きと高速な直接再チェイニングを実現。詳細な物理メモリレイアウトおよびオフセット定義は [`../components/tier3_jit/jit_engine_copy_patch.md`](../components/tier3_jit/jit_engine_copy_patch.md) を参照。
- **物理的利点**: 実行時カウンタや動的メモリ確保（malloc）を一切使わず、純粋な 3面リングローテーション、最古限定昇格、および $O(k)$ 局所再チェイニング・アンリンクにより、断片化ゼロ・全走査ゼロの高速な動的代謝を実現する。 `{JIT_MultiBuffer_Cache}` `{JIT_OldestOnly_Promote}` `{JIT_LazyChaining}`

---

### 2.4 Pillar 4: 対称直接ハンドオフ・エンジン (Symmetric Direct Handoff Engine)
<!-- traceability: {ADR_RendezvousChannel} {CSP_Handoff} {DirectContextSwitch} -->
- **物理実体**: バッファを持たない（容量 0）同期ランデブースロットと、C++20 コルーチンの対称遷移（Symmetric Transfer）機構。
- **物理的遷移フロー**:
  1. **送信側待機**: 受信側が未待機の場合、送信タスクは値ポインタをチャネルスロットに登録し、自身を `SUSPENDED_CSP` にしてサスペンドする。
  2. **ランデブー成立**: 受信側タスクが `recv()` を呼び出した瞬間、スロット内の値ポインタを直接読み取る（ゼロコピー転送）。
  3. **対称遷移スイッチ**: 受信側の `await_suspend` が、スケジューラを完全にバイパスして送信タスクの `std::coroutine_handle` を直接リターンする。
  4. **レジスタ直結切り替え**: CPU はスタックフレームを消費することなく、Tail Call（末尾呼び出し）によって送信タスクの実行コンテキストへ直接ジャンプする（スタック深度 $O(1)$）。
- **物理的利点**: OS スケジューラのキュー走査オーバーヘッドを排し、ハードウェア割り込みに匹敵する極小レイテンシのタスク間通信を実現する。 `{CSP_Handoff}` `{DirectContextSwitch}`

---

### 2.5 Pillar 5: 折りたたみXOR TLB ＆ 平坦ページ表 (Folding XOR TLB & FlatMap Page Table)
<!-- traceability: {FastAddressCheck} {META_RestrictedPhysicalAccess} {LowLatencyLookup} -->
- **物理実体**: 16 エントリのダイレクトマップ TLB 配列と、ソート済み VPN-PTE 平坦配列（`flat_map_view`）。
- **物理的変換フロー**:
  1. **Fast-path (Bit 31 = 0)**: ゲスト RAM アクセス。ベースポインタ加算とマスク演算のみで 1 サイクル変換。
  2. **vMMIO-path (Bit 31 = 1)**: 仮想ページ番号 (VPN: `[31:12]`, 20 bits) に対し、4-bit Folding XOR（`VPN[3:0] ^ VPN[7:4] ^ VPN[11:8] ^ VPN[15:12] ^ VPN[19:16]`）を計算。
  3. **TLB 直接参照**: 得られた 4-bit インデックスで 16 エントリ TLB を照合。一致すれば $O(1)$ で物理アドレス/ハンドラを即座に取得。
  4. **TLB ミス時**: ソート済み PTE FlatMap 配列（`flat_map_view`）を二分探索し、TLB エントリを更新。
- **物理的利点**: 階層型ページテーブルの多段ポインタ参照を排除し、極小の 16 エントリ配列で vMMIO トラップを高効率に処理する。 `{FastAddressCheck}` `{LowLatencyLookup}`

---

### 2.6 Pillar 6: 有界ゼロコピー・ランデブー・メールボックス (Bounded Zero-Copy Rendezvous Mailbox)
<!-- traceability: {IPC_ZeroCopy} {TypeSafeMessaging} {Challenge_IpcQueueStarvation} -->
- **物理実体**: IPC Router が管理する静的サービスチャネル配列と、TCB 内のメッセージポインタスロット。
- **物理的所有権移転シーケンス**:
  1. **Revoke (剥奪)**: 送信元タスクの TCB からメッセージバッファの所有権フラグを無効化。
  2. **Enqueue (登録)**: IPC Router のサービスメールボックススロットへバッファのポインタ・サイズ・URI を登録。
  3. **Grant (付与)**: 受信側サービスハンドラの TCB へメッセージバッファの所有権を付与。
- **物理的利点**: メッセージペイロードの memcpy を一切行わず、ポインタの再バインドのみでプロセス間通信を完結させる。 `{IPC_ZeroCopy}`

---

## 3. 物理レジスタ＆ABI規約 (Physical Register & ABI Map)
<!-- traceability: {ContextPointerRegister} {EnvironmentPointer} {JIT_RegisterMapping} {ADR_TosCacheAsymmetry} -->

ARM Cortex-M33 (ARMv8-M Mainline) における物理レジスタの厳格な役割分担：

| 物理レジスタ | AAPCS 規約 | Fireball インタープリタ | Fireball JIT トレース (トレース単位任意割当) | 役割と不変条件 |
| :--- | :--- | :--- | :--- | :--- |
| **`R0`** | Argument 1 / Scratch | `ip` (WASM PC) | `ip` (WASM PC) | 継続渡し（CPS）第1引数。現在実行中のバイトコード位置。 |
| **`R1`** | Argument 2 / Scratch | `stack_bot` | `stack_bot` | 継続渡し（CPS）第2引数。統合スタックボトム基底ポインタ `{ContextPointerRegister}`。 |
| **`R2`** | Argument 3 / Scratch | `env` | `env` | 継続渡し（CPS）第3引数。ランタイム環境ポインタ `{EnvironmentPointer}`。 |
| **`R3`** | Argument 4 / Scratch | `scratch` (解放) | **`local_param`** | **Caller-saved スクラッチ / スピル**。`local_base`（フレーム基底）が再帰・共有呼び出し深さによりコンパイル時定数へ畳み込めない場合、トレース入口でコンテキスト構造体からロードしてピン留めする（[`jit_stencil_catalog.md` 3.8](../specs/jit_stencil_catalog.md)「`local_param` ピン留めバリアント」を正本とする）。`mem_base`/`mem_size`（`R8`/`R9`）や各ステンシルの一時スクラッチ（`R12`）とは別レジスタのため、メモリアクセスや他の演算ステンシルと衝突しない。`local.get`/`set`/`tee` の現行ステンシル（`docs/specs/wasm_instruction_set.md` 3.3）は `R1（stack_bot）` からの単一の即値オフセットのみで実装され `R3` を参照しないため、`local_param` ピン留め自体は現行の概念コードにはまだ実装されていない。 |
| **`R4`** | Callee-saved | (保全) | **`Assignable Pool 0` (TOS等)** | **役割任意割当レジスタ 0**。スタックトップキャッシュ (TOS)、コンテキストスピル、ローカル変数等からトレース単位でバインド。 |
| **`R5`** | Callee-saved | (保全) | **`Assignable Pool 1` (NOS等)** | **役割任意割当レジスタ 1**。スタック次段キャッシュ (NOS) 等。 |
| **`R6`** | Callee-saved | (保全) | **`Assignable Pool 2`（select 使用時は NNOS）** | **役割任意割当レジスタ 2**。`select_d3` ステンシルでは3値目（NNOS）を保持し、それ以外のトレースでは他の役割任意割当に用いる。`mem_size` は `R9` に分離されているため、メモリアクセスを含むトレースとも両立する。 |
| **`R7`** | **Frame Pointer (FP)** | **FP (不可侵)** | **FP (不可侵)** | **AAPCS 標準フレームポインタ**。デバッガ・スタックアンワインドのため不変。 |
| **`R8`** | Callee-saved | (保全) | **`Assignable Pool 3`（メモリアクセス時は `mem_base`）** | **役割任意割当レジスタ 3**。メモリアクセス系ステンシルでは常に `mem_base`（`vsoc_runtime.mem_base` からロード）を保持し、メモリアクセスを含まないトレースでは高頻度ローカル変数 (`local[0]`)、ループカウンタ等に用いる。 |
| **`R9`** | Callee-saved | (保全) | **`Assignable Pool 4`（メモリアクセス時は `mem_size`）** | **役割任意割当レジスタ 4**。メモリアクセス系ステンシルでは `vsoc_runtime.mem_size`（境界比較の上限値、`{FastAddressCheck}` は `CMP addr, mem_size; BHS __trap` の比較命令——マスク演算は使わない）を保持し、メモリアクセスを含まないトレースでは高頻度ローカル変数 (`local[1]`)、補助ポインタ等に用いる。 |
| **`R10`** | Callee-saved | (保全) | **`Assignable Pool 5`** | **役割任意割当レジスタ 5**。セーフポイント監視フラグ (`safepoint_flag`) 等。 |
| **`R11`** | Callee-saved | (保全) | **`Assignable Pool 6`** | **役割任意割当レジスタ 6**。拡張レジスタキャッシュ。 |
| **`R12 (IP)`**| Intra-Call Scratch | scratch | **一時スクラッチ** | リンカ・スタブ用スクラッチに加え、`global.get`/`global.set`（globals_base ポインタ）、`i32.rem_s`/`i32.rem_u`/`i32.rotl`（除算・回転量の一時値）ステンシル内でのみ値を保持する使い捨てスクラッチ。呼び出しをまたいで保持されないため、`R3`（`local_param`）と異なりトレース単位のピン留めは行わない。 |
| **`R13 (SP)`**| Stack Pointer | C++ Core SP | C++ Core SP | C++ コア実行用スタックポインタ。 |
| **`R14 (LR)`**| Link Register | Return Address | Return Address | 関数呼び出し戻り先アドレス。 |
| **`R15 (PC)`**| Program Counter | CPU PC | CPU PC | 命令ポインタ。 |

> [!NOTE]
> **トレース単位のレジスタバインディングとステンシル・バリアント選択**:
> JIT コンパイラはトレース解析時に、`R4-R6, R8-R11`（Callee-saved 計7本）に対する役割マップ（TOS/NOS/NNOS、メモリアクセス時は `R8`/`R9` を `mem_base`/`mem_size` に固定、それ以外では local 変数・ループカウンタ等）を決定する。`R3` は Caller-saved の `local_param` 専用（メモリアクセス系ステンシルとは非衝突）、`R12` は各ステンシル内でのみ生存する使い捨てスクラッチであり、いずれもこの役割マップの対象外である。トレース突入時に使用する Callee-saved レジスタを `PUSH`（必要に応じて初期ロード）、脱出（リターンまたはインタープリタフォールバック）時に**ダーティなスピル変数（TOS/NOS、レジスタ常駐ローカル変数、SPオフセット等）を統合スタックへ `STR` で確実に書き戻した上で `POP` 復元** することで、インタープリタとゼロ再構築で相互遷移しつつ、トレース内部を純粋なレジスタマシンとして超高速実行する。ステンシルはレジスタ割当バインディングに応じた事前コンパイル済みバリアントを選択して結合される。
>
> `local_base`（フレーム基底）は同一トレースが再帰呼び出しや異なる呼び出し深さから共有されうるため、統合スタック上の絶対位置が毎回異なる実行時値であり、トレース入口でコンテキスト構造体から毎回ロードする必要がある——コンパイル時定数には畳み込めない。一方、各ローカル変数スロットや現在のオペランドスタック深さ（`sp_offset`）は `local_base` からの静的に決まる相対オフセットに過ぎないため、Copy-and-Patch のパッチ適用時に即値として書き込まれ、独立したプールロールを持たない。`sp_offset` はトレース脱出時にのみ算出されコンテキスト構造体へ書き戻される。 `{ADR_TosCacheAsymmetry}`

### 3.1 AAPCS 非スクラッチ完全準拠と外部 C/C++ 関数呼び出し境界
<!-- traceability: {ContextPointerRegister} {EnvironmentPointer} {JIT_RegisterMapping} -->

Fireball は外部の AAPCS 準拠 C/C++ 関数（WASI ホストコール、vMMIO トラップハンドラ、HAL ドライバ、浮動小数点ヘルパー等）を頻繁に呼び出すため、非スクラッチレジスタおよび呼び出し境界に関して以下の **厳格な AAPCS 準拠ルール** を適用する：

1. **非スクラッチレジスタ (`R4-R6, R7, R8-R11, R13`) の完全保全 (Callee-saved Preserved)**:
   - インタープリタおよび JIT トレースは、自身が使用する `R4-R6, R8-R11` を **必ず関数プロローグで `PUSH` し、エピローグで `POP` して呼び出し元（Caller）の値を完全保全** する。
   - `R7 (FP)` は AAPCS 標準フレームポインタとして不可侵であり、デバッガやスタックアンワインドの整合性を保証する。
   - `R13 (SP)` は C++ コア実行用スタックポインタであり、外部関数呼び出し時には AAPCS 規定に従い **必ず 8 バイト境界（Double-word aligned）** に整列される。
2. **外部 AAPCS 関数呼び出し時の Caller-saved 退避境界**:
   - 外部 AAPCS 準拠の C/C++ 関数は、Caller-saved スクラッチレジスタ（`R0-R3, R12, LR`）を自由に上書き・破壊する。
   - JIT トレースまたはインタープリタから外部 C/C++ 関数を `BL`/`BLX` で呼び出す際は：
     - ① 継続渡し引数 `(R0: ip, R1: stack_bot, R2: env)` および `R3: local_param`（ピン留めしているトレースの場合）をスタック、または Callee-saved レジスタ（`R4-R11`）へ退避する。
     - ② AAPCS 規約に従い引数を `R0-R3` にセットして関数を呼び出す。
     - ③ 関数復帰時、`R4-R11`（Callee-saved）に保持されている JIT スタックキャッシュやコンテキスト変数は AAPCS により **完全に維持** されているため、Caller-saved を復元して即座に高速実行を継続する。

---

### 3.2 メモリ常駐構造体の物理バイトオフセット (Physical Byte Offsets of Memory-Resident Structures)
<!-- traceability: {ContextPointerRegister} {EnvironmentPointer} {MemoryBoundaryCheck} -->

`R1`（`execution_context`）および `R2`（`vsoc_runtime`）が指す構造体のフィールドは、複数のドキュメント（インタープリタ、JIT ステンシルカタログ）から `[R1, #offset]` / `[r2, #offset]` の形で直接参照される。これらの数値がドキュメントごとに独立に決め打ちされると、シフト命令のビット配置バグと同種の——一方だけが更新されて他方が追従しない——サイレントなドリフトが発生する。本節をこれらオフセットの単一正本とする。フィールドの型・名称の正本は各コンポーネントの `wit/*.wit` を参照。

#### `execution_context`（`R1: stack_bot` 起点、計16バイト）
正本: [`runtime_interpreter.md` 3.3](../components/tier2_runtime/runtime_interpreter.md) / [`execution_context.wit`](../components/tier2_runtime/wit/execution_context.wit)

| オフセット | フィールド | 型 | 役割 |
| :--- | :--- | :--- | :--- |
| `+0x00` | `sp_offset` | u32 | オペランドスタック頂点オフセット |
| `+0x04` | `frame_offset` | u32 | アクティブな call-frame 開始オフセット |
| `+0x08` | `sp_boundary` | u32 | スタックオーバーフロー検知上限オフセット |
| `+0x0C` | `handler_table` | u32 | 通常/デバッグ用ハンドラテーブルポインタ |

#### `call_frame`（統合スタック上、各フレーム先頭からの相対オフセット、計20バイト）
正本: [`runtime_interpreter.md` 3.3](../components/tier2_runtime/runtime_interpreter.md) / [`execution_context.wit`](../components/tier2_runtime/wit/execution_context.wit)

| オフセット | フィールド | 型 |
| :--- | :--- | :--- |
| `+0x00` | `parent_frame_offset` | u32 |
| `+0x04` | `return_pc` | u32 |
| `+0x08` | `local_var_offset` | u32 |
| `+0x0C` | `function_index` | u32 |
| `+0x10` | `saved_stack_len` | u32 |

#### `control_frame`（統合スタック上、各フレーム先頭からの相対オフセット、計16バイト）
正本: [`runtime_interpreter.md` 3.3](../components/tier2_runtime/runtime_interpreter.md) / [`execution_context.wit`](../components/tier2_runtime/wit/execution_context.wit)

| オフセット | フィールド | 型 |
| :--- | :--- | :--- |
| `+0x00` | `label_pc` | u32 |
| `+0x04` | `exec_trace_ptr` | u32（関数ポインタ） |
| `+0x08` | `saved_stack_len` | u32 |
| `+0x0C` | `result_arity` | u16 |
| `+0x0E` | `loop_flag` | u8 |
| `+0x0F` | （予約、4バイトアライメント） | — |

#### `vsoc_runtime`（`R2: env` 起点、計12バイト）
正本: [`runtime_vsoc.md` 3.3](../components/tier2_runtime/runtime_vsoc.md) / [`vsoc_runtime.wit`](../components/tier2_runtime/wit/vsoc_runtime.wit)

| オフセット | フィールド | 型 | 役割 |
| :--- | :--- | :--- | :--- |
| `+0x00` | `mem_base` | u32（アドレス値） | ゲストリニアメモリ開始アドレス |
| `+0x04` | `mem_size` | u32 | ゲストリニアメモリ有効バイト数。`{FastAddressCheck}` の境界比較 `CMP addr, mem_size; BHS __trap` に直接使う（マスクは使わない、2の冪制約もない） |
| `+0x08` | `globals_base` | u32（アドレス値） | グローバル変数配列開始アドレス |

---

## 4. ドキュメント体系上の位置づけ (System Integration)
<!-- traceability: {META_3TierSeparation} -->
本マスター物理設計書は、各コンポーネント設計書（Tier 1 Core/Interface, Tier 2 Runtime, Tier 3 JIT/Platform）の上位に位置し、具象的なデータレイアウトおよび制御遷移の正本アンカーとして機能する。各設計書は本仕様書で定義された 6 大物理コアメカニズムの名称およびレジスタ規約に厳格に準拠しなければならない。
