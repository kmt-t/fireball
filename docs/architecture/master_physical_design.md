# Fireball マスター物理設計仕様書 (Master Physical Design)

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
  1. **スタックボトム (`+0x00`)**: `execution_context` 構造体が固定配置される（SP長 `sp_offset`、フレームオフセット `frame_offset`、リニアメモリ基底 `mem_base`、セーフポイントフラグ等を保持）。
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
- **物理的利点**: 実行時カウンタや動的メモリ確保（malloc）を一切使わず、純粋な 3面リングローテーションと最古限定昇格により、断片化ゼロの代謝を実現する。 `{JIT_MultiBuffer_Cache}` `{JIT_OldestOnly_Promote}`

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
| **`R3`** | Argument 4 / Scratch | `scratch` (解放) | **`Spill / Scratch` (任意)** | **Caller-saved スクラッチ / スピル**。トレース単位でコンテキスト変数（`mem_base`, `local_base` 等）をピン留め、または一時演算スクラッチ。 |
| **`R4`** | Callee-saved | (保全) | **`Assignable Pool 0` (TOS等)** | **役割任意割当レジスタ 0**。スタックトップキャッシュ (TOS)、コンテキストスピル、ローカル変数等からトレース単位でバインド。 |
| **`R5`** | Callee-saved | (保全) | **`Assignable Pool 1` (NOS等)** | **役割任意割当レジスタ 1**。スタック次段キャッシュ (NOS)、リニアメモリ基底 (`mem_base`) 等。 |
| **`R6`** | Callee-saved | (保全) | **`Assignable Pool 2`** | **役割任意割当レジスタ 2**。メモリマスク (`mem_mask`)、ローカル変数基底 (`local_base`) 等。 |
| **`R7`** | **Frame Pointer (FP)** | **FP (不可侵)** | **FP (不可侵)** | **AAPCS 標準フレームポインタ**。デバッガ・スタックアンワインドのため不変。 |
| **`R8`** | Callee-saved | (保全) | **`Assignable Pool 3`** | **役割任意割当レジスタ 3**。高頻度ローカル変数 (`local[0]`)、ループカウンタ等。 |
| **`R9`** | Callee-saved | (保全) | **`Assignable Pool 4`** | **役割任意割当レジスタ 4**。高頻度ローカル変数 (`local[1]`)、補助ポインタ等。 |
| **`R10`** | Callee-saved | (保全) | **`Assignable Pool 5`** | **役割任意割当レジスタ 5**。セーフポイント監視フラグ (`safepoint_flag`) 等。 |
| **`R11`** | Callee-saved | (保全) | **`Assignable Pool 6`** | **役割任意割当レジスタ 6**。拡張レジスタキャッシュ。 |
| **`R12 (IP)`**| Intra-Call Scratch | scratch | scratch | リンカ・スタブ用スクラッチ。 |
| **`R13 (SP)`**| Stack Pointer | C++ Core SP | C++ Core SP | C++ コア実行用スタックポインタ。 |
| **`R14 (LR)`**| Link Register | Return Address | Return Address | 関数呼び出し戻り先アドレス。 |
| **`R15 (PC)`**| Program Counter | CPU PC | CPU PC | 命令ポインタ。 |

> [!NOTE]
> **トレース単位のレジスタバインディングとステンシル・バリアント選択**:
> JIT コンパイラはトレース解析時に、`R3`（Caller-saved）および `R4-R6, R8-R11`（Callee-saved 計7本）に対する最適な役割マップ（TOS/NOS、mem_base/mask、local変数、ループカウンタ）を決定する。トレース突入時に使用する Callee-saved レジスタを `PUSH`（必要に応じて初期ロード）、脱出時にダーティ書き戻し＋ `POP` することで、インタープリタとゼロ再構築で相互遷移しつつ、トレース内部を純粋なレジスタマシンとして超高速実行する。ステンシルはレジスタ割当バインディングに応じた事前コンパイル済みバリアントを選択して結合される。

---

## 4. ドキュメント体系上の位置づけ (System Integration)
<!-- traceability: {META_3TierSeparation} -->
本マスター物理設計書は、各コンポーネント設計書（Tier 1 Core/Interface, Tier 2 Runtime, Tier 3 JIT/Platform）の上位に位置し、具象的なデータレイアウトおよび制御遷移の正本アンカーとして機能する。各設計書は本仕様書で定義された 6 大物理コアメカニズムの名称およびレジスタ規約に厳格に準拠しなければならない。
