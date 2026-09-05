# vMMIO コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}
<!-- evidence:
     formal: formal/vsoc_state_model.py
     concept: concepts/vmmio_concept.py
     test: tests/runtime_vmmio_test_spec.md
-->

## 1. コンセプト
<!-- traceability: {META_RestrictedPhysicalAccess} {vMMIO_TrapAndEmulate} {PhysicalPassthrough} {DynamicMmap} {UnifiedAccessModel} {FastAddressCheck} {Fast_Path_GPIO} {META_FlatMapIndexed} -->
vMMIO (Virtual Memory-Mapped I/O) は、ホストが仲介するリソース（物理レジスタ（GPIO等）、共有メモリ、システムコール用バッファなど）へのアクセスを統一的に扱うアクセス層である。ホスト-ゲスト間境界を横切るアクセスのうち、vMMIOアドレス空間（Bit 31 == 1、下記 Stage 2/3）を経由するものはすべてこの層で仲介される。一方、ゲスト専用RAM（Stage 1: Bit 31 == 0）はvMMIO管理対象外とし、境界チェックのみで完結する高速バイパス経路を別途持つ（詳細は後述）。

WASM ゲストのリニアメモリは、WebAssembly 標準仕様に準拠して **64KB ページ単位 (65,536 bytes)** でページング・管理・拡張（`memory.grow`）される論理空間（`N * 64KB`）である。ただし、RAM < 64KB の極小組込み環境（Cortex-M 等）に適合するため、物理実装としては **64KB に満たない部分ページ（Sub-64KB / Partial Page: 例 8KB, 16KB）** の割り当てを許容し、境界超過アクセスを即座にトラップする設計をとる。一方、ホスト/デバイス側の vMMIO 領域は **1ページ（4KB）** 単位で管理される。 `{META_RestrictedPhysicalAccess}` `{vMMIO_TrapAndEmulate}` `{PhysicalPassthrough}` `{DynamicMmap}` `{UnifiedAccessModel}`

本アーキテクチャでは、PTE（Page Table Entry）の保存にシステム全体の設計規約（`{META_FlatMapIndexed}`）に準拠した **静的ソート済み配列と、それを引く `fireball::flat_map_view`** を採用し、仮想ページ番号（VPN）から PTE へのマッピングをフラットに保持・管理する。

標準の `std::flat_map` を素のまま用いない理由: C++23 の `std::flat_map` はコンテナアダプタであり、既定の下位コンテナが `std::vector` であるため、そのままでは `{META_NoStdVector}` および `{GLOBAL_Policy_Memory}`（`malloc`/`new` の使用禁止）に抵触する。本プロジェクトは表の実体を静的配列として各コンポーネントが所有し、`fireball::flat_map_view` で引く（`{META_FlatMapIndexed}` を正本とする）。 `{META_NoStdVector}` `{GLOBAL_Policy_Memory}`

FlatMap 単体での探索は $O(\log N)$（またはハッシュ探索）となるが、本アーキテクチャでは手前に **「ダイレクトマップ方式のソフトウェアTLB（16エントリ、完全 $O(1)$ キャッシュ）」** を配置する。JIT 実行やホットな共有メモリ操作などのクリティカルパスでは、大半のアクセス（目標 90% 以上）が TLB キャッシュヒット（$O(1)$）で高速解決されるため、FlatMap 化に伴うテーブル探索の遅延は十分に吸収・容認される。

1. **リニアアドレス空間フィルタ（高速バイパス & 境界チェック）**:
   32ビットゲストアドレスの最上位ビット（Bit 31）が `0` の場合、そのアドレスは vMMIO 管理対象外として、Stage 1（ゲストRAM）への直接アクセスとして高速バイパス（O(1) 処理）を実行する。 `{FastAddressCheck}`
   - **統一境界チェック**: `{FastAddressCheck}` は比較命令ベースの単一の高速境界チェックである（マスクは用いない）。`guest_ram_size`（`vsoc_runtime.mem-size`）と直接比較し、`addr >= guest_ram_size` なら境界外として即座に `ERR_OUT_OF_BOUNDS` トラップを発生させる（Thumb-2: 単一の境界チェック比較命令 `CMP addr, mem_size` とトラップ分岐 `BHS.W __trap`）。マスク方式と異なり `guest_ram_size` に2の冪の制約はなく、部分ページ（例: 8KB, 12KB, 16KB）・単一 64KB ページ・複数 64KB ページ（`N * 64KB`）のいずれも同一の比較一つで判定できる（`vmmio_concept.py` の `VMMIOController.access` を正本とする）。トラップは必須であり、境界外アドレスを黙ってラップアラウンドさせて処理を継続することは許容されない。JIT トレース側（`jit_stencil_catalog.md` §3.7, `jit_copy_patch_concept.py`）も同一の比較+トラップ方式を採り、トラップ発生時はインタープリタへフォールバックする。インタープリタが復旧不能と判断した場合はゲストタスクを停止してよい。
2. **FlatMap PTE 管理**:
   最上位ビット（Bit 31）が `1` のアドレス空間を vMMIO 領域（`0x8000_0000` – `0xFFFF_FFFF`）とする。
   - 仮想ページ番号（VPN = `raw >> 12`）をキーとして、FlatMap（`vmmio_ptes`）に PTE を格納する。
   - 動的 SHM ページや物理パススルーページをフラットに登録・管理できる。
3. **ダイレクトマップ方式ソフトウェアTLB（完全O(1)キャッシュ）**:
   ホットパス高速化のため、一発でインデックスが決まるダイレクトマッピング（ハッシュ方式、16エントリ固定サイズ）を採用する。
   - 仮想ページ番号（20-bit: `vpn = raw >> 12`）の全ビットを 4-bit（16スロット）に均等拡散する Folding XOR Hash `tlb_idx = (vpn ^ (vpn >> 4) ^ (vpn >> 8) ^ (vpn >> 12) ^ (vpn >> 16)) & 15` を算出し、TLBに一撃でアクセスする。ヒット時は権限チェックを通過した後に即時実行する。 `{META_RestrictedPhysicalAccess}`
   - **全ビット拡散**: FC[31:28] や中間ページ番号ビット、下位ページ番号ビットのすべてが 4-bit 幅で折り畳まれるため、FC 間やページ番号の変動に対して TLB スロットが均等に分散する。

vMMIO領域（Stage 2/3）のセキュリティモデルは**PTEに埋め込まれた権限フィールドがゲート**である。アクセス権限は PTE に保持され、ルックアップと権限チェックを1パスで完結させる。ゲストRAM（Stage 1）はPTEを経由せず、`FastAddressCheck` による境界チェックのみをゲートとする別経路である。アクセス特性に応じてセキュリティゲートを以下の3段階に階層化する。 `{META_RestrictedPhysicalAccess}`

1. **Stage 1 (ゲストRAMバイパス)**: ゲスト専用RAM領域（Bit 31 == 0、FC=0..7）。`addr >= guest_ram_size` による比較ベースの単一の高速境界チェック（`FastAddressCheck`）のみで高速処理し、境界外は即座にトラップする。
2. **Stage 2 (静的vMMIO, FC=12)**: コンパイル時にアドレスが確定するコアデバイス（SYSCTL, IPCR, VDMA等）。アドレス `0xC000_0000` は FC=12 に位置する。JIT生成時に許可チェックを行い、許可済みならネイティブコードに直接デバイスキーを埋め込む。
3. **Stage 3 (動的vMMIO, FC=14-15)**: SHM（FC=14, `0xE000_0000`）、PASSTHROUGH（FC=15, `0xF000_0000`）領域のアクセス。TLB または FlatMap を経由して PTE を解決し、エントリの権限フィールドで可否を判定する。

IPC経由のデータ交換は行わない — GPIOのようなsub-µs応答が必要な周辺機器はIPCレイテンシに耐えられないため、このダイレクトアクセスモデルが採用されている。 `{Fast_Path_GPIO}`

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} -->
本コンポーネントは **Tier 2 (分解されたサブコンポーネント: Decomposed Subcomponent)** に属し、vSoC (`runtime_vsoc.md`) から分解された仮想MMIO・デバイスレジスタアクセスおよびメモリ空間マッピングを担当する。 `{META_3TierSeparation}`

## 3. 静的モデル

### 3.1 データ構造
<!-- traceability: {META_Static_Resolution} {META_FlatMapIndexed} -->

リソース制約上、構造体は **ROM（不変）** と **RAM（可変）** に明確に分離する。PTE は FlatMap（ソート済みキー・バリュー配列）で管理し、手前の TLB キャッシュにより高速アクセスを担保する。

| 配置 | 構造体 | 概要 |
| :--- | :--- | :--- |
| ROM | `vmmio_address` | アドレスビットフィールド定義（C++23 ヘルパー構造体、実体なし） |
| ROM/RAM | `vmmio_ptes` | 仮想ページ番号 (VPN) → 32bit PTE の FlatMap（`fireball::flat_map_view<uint32_t, uint32_t>`） |
| RAM | ソフトウェアTLB配列 | `vmmio_tlb_cache[16]` 16エントリのダイレクトマップ型高速TLBキャッシュ配列 |

- **`VmmioController`**: アドレス境界デコード、FlatMap PTE ルックアップ、TLBキャッシュ管理、動的マッピング管理を担う主要クラス。
- **`vmmio_config`**: 静的な領域定義 (`vmmio_static_region`) の不変なテーブル。 `{META_Static_Resolution}`

### 3.2 内部ブロック図
<!-- traceability: {META_Static_Resolution} -->
```mermaid
graph TD
    subgraph vMMIO_Layer
        Filter["MSB Address Filter<br/>Bit 31 == 0 vs 1"]
        Decoder["Address Decoder<br/>FC(31:28) + VPN(31:12) + Offset(11:0)"]
        FlatMap["vmmio_ptes (FlatMap)<br/>Key: VPN -> Value: PTE"]
        TLB["Direct-Mapped TLB (16)<br/>Index = Hash(VPN) & 15"]
        Controller["VmmioController"]
    end

    Controller -- checks addr --> Filter
    Filter -- Bit 31 == 0 --> Bypass[Linear RAM Bypass\nTier 1 RAM Access]
    Filter -- Bit 31 == 1 --> Decoder
    Controller -- checks Cache --> TLB
    TLB -- TLB Hit (O(1)) --> Handler["PTE Handler\n(Syscall or Phys)"]
    TLB -- TLB Miss --> Walk[FlatMap Lookup]
    Walk -- lookup VPN --> FlatMap
    FlatMap -- returns --> WalkResult[Resolved PTE]
    WalkResult -- refills --> TLB
    WalkResult --> Handler
```

### 3.3 主要なクラス・構造体・配列・定数
<!-- traceability: {META_Static_Resolution} -->
vMMIO

#### アドレスフィールド定義 (vmmio_address)
<!-- traceability: {META_Static_Resolution} -->
32ビットゲストアドレスをフィールドに分割する。

| 項目名 | 機能と役割 | 備考（制約、型など） |
| :--- | :--- | :--- |
| RAM Bypass Flag | Bit 31 が 0 のときはゲストRAMアクセス（Tier 1）とし、vMMIOを高速バイパス。 | ビット[31]（1 bit） |
| Function Code | vMMIO 領域時の機能種別（FC）。`vpn >> 16` でも抽出可能。 | ビット[31:28]（4 bits）、16種別 |
| Syscall Metadata / ID | 静的デバイス・Syscall 領域（FC=12）における Syscall ID / サービス識別メタデータ。 | ビット[27:16]（12 bits: `0..4095`） |
| VPN (Virtual Page Number) | 仮想ページ番号（FC + Syscall Metadata + Page Index を包含）。FlatMap のキーおよび TLB のマッチタグ。 | ビット[31:12]（20 bits: `raw >> 12`） |
| Offset | 4KBページ内でのバイトオフセット。PTE 解決後に相対アドレスとして使用。 | ビット[11:0]（12 bits）、4KB |

**アドレスデコード + PTE アクセス擬似コード例**:
```python
class VmmioAddress:
    def __init__(self, raw: int):
        self.raw = raw & 0xFFFFFFFF

    def is_linear(self) -> bool:
        # 最上位ビット(Bit 31)が0ならゲストRAM
        return (self.raw & 0x80000000) == 0

    def fc(self) -> int:
        # Function Code: [31:28]
        return (self.raw >> 28) & 0xF

    def syscall_metadata(self) -> int:
        # Syscall Metadata / Syscall ID: [27:16]
        return (self.raw >> 16) & 0xFFF

    def offset(self) -> int:
        # Offset: [11:0]
        return self.raw & 0xFFF

    def vpn(self) -> int:
        # 20-bit Virtual Page Number (VPN) for TLB and FlatMap Key
        return self.raw >> 12


def lookup_tlb(addr: VmmioAddress) -> int:
    vpn = addr.vpn()
    # 20-bit VPN の Folding XOR Hash（全ビットを4ビット幅に拡散）
    tlb_idx = (vpn ^ (vpn >> 4) ^ (vpn >> 8) ^ (vpn >> 12) ^ (vpn >> 16)) & 15

    if vmmio_tlb_cache[tlb_idx]["vpn"] == vpn:
        return vmmio_tlb_cache[tlb_idx]["pte"]  # TLB Hit!

    # TLB Miss: FlatMap ルックアップを実行
    pte = vmmio_ptes.get(vpn)
    if pte is None:
        raise Exception("UNREGISTERED_PAGE")

    # TLB をリフィル
    vmmio_tlb_cache[tlb_idx] = {"vpn": vpn, "pte": pte}
    return pte


def access_vmmio(addr: VmmioAddress, is_write: bool):
    # 1. リニアアドレスフィルタ
    if addr.is_linear():
        # Tier 1 ゲストRAMアクセス（vMMIOバイパス）
        access_guest_ram(addr.raw, is_write)
        return

    # 2. TLB / ページテーブルルックアップ
    pte = lookup_tlb(addr)

    # 3. 権限チェック (PTE [11:8])
    is_valid = (pte >> 11) & 1
    if not is_valid:
        raise Exception("PAGE_FAULT_INVALID")

    read_allowed = (pte >> 10) & 1
    write_allowed = (pte >> 9) & 1
    if is_write and not write_allowed:
        raise Exception("ACCESS_VIOLATION_WRITE")
    if not is_write and not read_allowed:
        raise Exception("ACCESS_VIOLATION_READ")

    # 4. タイプ別アクセス実行
    is_passthrough = (pte >> 8) & 1
    if is_passthrough == 0:
        # Tier 2 (Static Device) - Syscall モード
        syscall_id = (addr.raw >> 16) & 0xFFF
        dispatch_syscall(syscall_id, addr.offset(), is_write)
    else:
        # Tier 3 (SHM / PASSTHROUGH) - 物理アクセスモード
        if addr.fc() == 14:
            owner_id = pte & 0xFF
            if owner_id != current_task_id:
                raise Exception("ACCESS_VIOLATION_NOT_OWNED")

        phys_page = (pte >> 12) & 0xFFFFF
        phys_addr = (phys_page << 12) | addr.offset()
        access_memory(phys_addr, is_write)
```

**アドレス分解の対応関係**

| アドレス範囲 | MSB | FC | 割り当て用途 |
| :--- | :--- | :--- | :--- |
| `0x0000_0000` – `0x7FFF_FFFF` | 0 | - | ゲスト RAM（WASM線形メモリ）— Tier 1 |
| `0xC000_0000` – `0xC000_FFFF` | 1 | 12 (`0xC`) | Static Devices（SYSCTL, IPCR, VDMA）— Tier 2 |
| `0xE000_0000` – `0xEFFF_FFFF` | 1 | 14 (`0xE`) | SHM（共有メモリ）— Tier 3 |
| `0xF000_0000` – `0xFFFF_FFFF` | 1 | 15 (`0xF`) | PASSTHROUGH（物理アドレス直結）— Tier 3 |

#### コントローラ群 (VmmioController)
<!-- traceability: {META_Static_Resolution} {META_FlatMapIndexed} -->
アドレスデコード・FlatMap PTE ルックアップ・TLBキャッシュ管理をカプセル化する。

| 項目名 | 機能と役割 | 備考（制約、型など） |
| :--- | :--- | :--- |
| FlatMap ページテーブル | 仮想ページ番号 (VPN) → 32bit PTE のマッピング。 | `vmmio_ptes`（`fireball::flat_map_view<uint32_t, uint32_t>`） |
| ソフトウェアTLB（グローバル） | 仮想ページ番号 (VPN) → PTE マッピングをダイレクトマップハッシュでキャッシュ。ホットパスを完全 O(1) に高速化する。 | `vmmio_tlb_cache[16]`（固定16エントリ、ハッシュ結合） |

#### 静的デバイスページテーブルエントリ (vmmio_pte_static)
<!-- traceability: {META_Static_Resolution} -->
Static Devices (Tier 2) 向け。PTE には Device Type やパーミッションフラグ、ハンドラ情報を保持する。

```
32-bit Static Device PTE:
[31:24] Reserved
[23:20] Flags (4 bits):
        [3] Type (FC に対応した値 — FC=12 では 0 = Syscall)
        [2] CACHEABLE (JIT キャッシュ可能)
        [1] WRITE_ENABLED
        [0] READ_ENABLED
[19:16] Device Type (4 bits) — {SYSCTL=0, IPCR=1, VDMA=2, ...}
[15:0]  Reserved
```

**FC=14 (SHM) エントリの仮想化マッピングは、Tier 3 共有メモリマネージャが発火する物理ページイベントの購読を通じて自律的に駆動される（`{VmmioShmDelegation}`）。vMMIO コントローラは共有メモリマネージャにイベントリスナーを登録し、物理ページのライフサイクル通知（割り当て、所有権更新、Revoke、解放）を受けて自身の仮想アドレス空間（VPN: `(0xE000_0000 >> 12) + page_idx`）に対応する PTE 登録・所有権更新・TLB フラッシュを実行する。メモリマネージャ側が vMMIO の内部実装やアドレス体系を直接操作することはなく、クリーンアーキテクチャ（DIP）が維持される。**

#### FlatMap ページテーブル定義
<!-- traceability: {META_FlatMapIndexed} {vMMIO_Isolation} -->
システム全体の共通ポリシー（`{META_FlatMapIndexed}`）に準拠し、PTE の保存には `fireball::flat_map_view<uint32_t, uint32_t>`（キー: VPN = `raw >> 12`、値: 32bit PTE）を採用する。 `{vMMIO_Isolation}`
| 構造体・型定義名 | 構成要素 | 型分類 | 役割と不変条件 |
| :--- | :--- | :--- | :--- |
| `pte_entry` | `vpn` (Key: 仮想ページ番号: 20bit)<br>`pte` (Value: 32bit PTE属性) | 構造体 | ソート済みページテーブルの1レコード |
| `VmmioPteStore` | `std::array<pte_entry, FB_CONF_VMMIO_MAX_PTES>` | 固定長配列 | コンパイル時に静的確保されるPTE実体ストレージ（動的確保ゼロ） |
| `VmmioPteView` | `fireball::flat_map_view<uint32_t, uint32_t>` | ビュー | 二分探索索引を提供する軽量ゼロコピービュー |


#### ハンドラ定義 (vmmio_handler)
読み書きアクセス発生時に呼び出される関数の共通インターフェース。

| 項目名 | 機能と役割 | 備考（制約、型など） |
| :--- | :--- | :--- |
| アクセス形式定義 | 相対オフセットとデータ列（可変バイナリビュー）を引数に取る関数の形式 | `status(offset, span, is_write)` |

## 4. 動的モデル

### 4.1 アルゴリズム

#### 多段アドレスデコード & TLB ルックアップ（手順アクティビティ図）
<!-- traceability: {VMMIO-GOTCHA-01} {VMMIO-GOTCHA-02} {META_Static_Resolution} -->
32ビットゲストアドレスの Guest RAM 高速バイパス、20-bit VPN の Folding XOR による 16 エントリ TLB 探索、および PTE 二分探索の手順を示す。

```mermaid
flowchart TD
    Start(["32-bit Guest Virtual Address"]) --> CheckBit31{"Address Bit 31 == 0?"}

    CheckBit31 -- "Yes (Bit 31 == 0)" --> RAMBypass["VMMIO-GOTCHA-01: Guest RAM Bypass (Tier 1)"]
    RAMBypass --> CalcRAM["Physical Address = guest_ram_base + addr"]
    CalcRAM --> DirectAccess(["Direct O(1) Memory Access (Zero MMU Overhead)"])

    CheckBit31 -- "No (Bit 31 == 1)" --> ExtractVPN["Extract 20-bit VPN (raw >> 12) & Offset (raw & 0xFFF)"]
    ExtractVPN --> FoldingXOR["VMMIO-GOTCHA-02: Hash = ((VPN >> 12) ^ (VPN >> 6) ^ VPN) & 0x0F"]
    FoldingXOR --> ProbeTLB["Probe Direct-Mapped TLB at index [Hash]"]

    ProbeTLB --> TLBHit{"TLB Entry.vpn == VPN?"}
    TLBHit -- "Yes (TLB Hit)" --> ExecHandler["Execute Cached PTE Handler / Offset Add (O(1))"]
    ExecHandler --> Complete(["Memory Access Completed"])

    TLBHit -- "No (TLB Miss)" --> FlatMapWalk["Binary Search VPN in Sorted vmmio_ptes FlatMap (O(log N))"]
    FlatMapWalk --> WalkFound{"PTE Found?"}
    WalkFound -- "No" --> Fault(["Trap: VMMIO Page Fault / Access Denied"])
    WalkFound -- "Yes" --> RefillTLB["Refill TLB Slot [Hash] with Resolved PTE"]
    RefillTLB --> ExecHandler
```

#### 共有メモリ権限剥奪と TLB 即時無効化（責務シーケンス図）
<!-- traceability: {VMMIO-GOTCHA-03} {MEM-GOTCHA-03} {OwnershipTransfer} -->
タスク間共有メモリ転送における、Sender Task、MemoryManager、vMMIO Controller、Receiver Task の責務分離と TLB 即時フラッシュを示す。

```mermaid
sequenceDiagram
    autonumber
    actor Sender as Sender Task
    participant Mem as MemoryManager
    participant vMMIO as vMMIO Controller (PTE & TLB)
    actor Receiver as Receiver Task

    Sender->>Mem: release_shared(shm_id)
    Note over Sender: Relinquishes local ownership
    Mem->>vMMIO: Set PTE.owner_id = FB_TASK_ID_FLIGHT (0xFF)
    vMMIO->>vMMIO: VMMIO-GOTCHA-03: Immediate TLB Flush for Page
    Note over vMMIO: Invalidate TLB slot matching VPN
    vMMIO-->>Mem: Ownership revoked
    Mem-->>Sender: Release committed

    opt Stale access attempt by Sender
        Sender->>vMMIO: Access old memory address
        vMMIO->>vMMIO: TLB Miss -> PTE lookup
        vMMIO-->>Sender: TRAP: Access Denied (owner == FLIGHT)
    end

    Receiver->>Mem: claim_shared(shm_id)
    Mem->>vMMIO: Set PTE.owner_id = Receiver Task ID
    vMMIO-->>Receiver: Shared memory accessible
    Note over Receiver: Safe zero-copy access granted
```
#### アクセスディスパッチシーケンス

ゲストのアドレスアクセスは以下のロジックで解決される。

```mermaid
sequenceDiagram
    participant G as Guest (JIT/Interp)
    participant C as VmmioController
    participant T as Software TLB (Hash)
    participant F as FlatMap (vmmio_ptes)
    participant H as Handler / PhysMem

    G->>C: dispatch_access(addr, buf, is_write)
    C->>C: Check Bit 31 (is_linear)
    alt Bit 31 == 0 (Linear RAM)
        C->>H: access_guest_ram(...)
        H-->>C: data
        C-->>G: ok
    else Bit 31 == 1 (vMMIO Range)
        C->>C: Decode FC, VPN, Offset
        C->>T: hash_lookup(vpn)
        alt TLB Hit
            T-->>C: cached PTE
        else TLB Miss
            C->>F: ptes.find(vpn)
            alt Not Found
                C-->>G: Trap (Unregistered Page / Undefined FC)
            end
            F-->>C: resolved PTE
            C->>T: Refill entry at Hash(vpn) & 15
        end

        C->>C: perm_check(PTE, is_write)
        alt Check Failed
            C-->>G: Trap (Access Violation)
        end

        alt Type == 0 (Syscall)
            C->>H: dispatch_syscall(Syscall_ID, Offset, is_write)
        else Type == 1 (Physical)
            C->>H: Access physical memory (Phys_addr)
        end
        H-->>C: result
        C-->>G: operation-result
    end
```

#### vMMIO フルセット・コンセプトコード

FlatMap ページテーブル、ダイレクトマップ
ソフトウェアTLB、および PTE 権限・所有権検査を含む実行可能なリファレンス実装は
[`concepts/vmmio_concept.py`](concepts/vmmio_concept.py) を正本とする。
仕様書側に複製は置かない（二重管理を避けるため）。

### 4.2 アルゴリズム: 仮想DMA (VDMA)
<!-- traceability: {VDMA} -->
ゲストリニアメモリと vMMIO 空間（または他のメモリ領域）間の高速転送を実現する。 `{VDMA}`

**アクセス方式**: 純粋MMIOトラップ。直接vMMIOアドレスにアクセス可能なゲストはVDMAレジスタへ直接書き込み、アクセス不可なゲスト言語は `fireball_call(VDMA_START)` 経由でホストが代理実行。

1. **転送設定**: ゲストが `REG_VDMA_SRC`, `REG_VDMA_DST`, `REG_VDMA_COUNT` にパラメータを書き込む。
2. **トリガー**: `REG_VDMA_CTRL` の `START` ビットを `1` に書き込む。
3. **実行**:
   - vMMIO ハンドラが物理アドレスを解決（境界チェックを適用）。
   - `std::memcpy` または HAL経由のDMAを用いて一括転送を実行。
4. **完了**: 転送完了後、必要に応じてゲストに仮想割り込み（`IRQ_VDMA_DONE`）を通知する。

### 4.3 仮想デバイスマップ
<!-- traceability: {VDMA} -->
各領域は 4KB 単位で割り当てられる。`vMMIO_BASE = 0x8000_0000` 以上の領域を対象とする。

| アドレス範囲 | FC | デバイス名 | 説明 |
|:---| :--- | :--- | :--- |
| `0xC000_0000` | `12` (`0xC`) | **SYSCTL** | システム制御（Yield, Halt, Syscall等） |
| `0xC000_1000` | `12` (`0xC`) | **IPCR** | IPCルータ連携レジスタ |
| `0xC000_2000` | `12` (`0xC`) | **VDMA** `{VDMA}` | 仮想DMA（バルク転送） |
| `0xE000_0000` – `0xEFFF_FFFF` | `14` (`0xE`) | **SHM** | 共有メモリ（1領域=1ページ） |
| `0xF000_0000` – `0xFFFF_FFFF` | `15` (`0xF`) | **PASSTHROUGH** | 物理アドレス直結 |

PASSTHROUGH アドレス変換:
`物理アドレス = pte.phys_page << 12 | Offset`

### 4.4 SYSCTL レジスタ詳細 (FC=12)
<!-- traceability: {VDMA} -->
| オフセット | レジスタ名 | R/W | 説明 |
| :--- | :--- | :--- | :--- |
| `0x00` | `REG_SYS_CONTROL` | W | `1`: Reset, `2`: Yield, `3`: Halt, `4`: Syscall |
| `0x04` | `REG_SYS_STATUS` | R | システム状態フラグ |
| `0x08` | `REG_IRQ_FLAGS` | R/W | 仮想割り込みフラグ |
| `0x10` | `REG_SYSCALL_ID` | R/W | サービスID |
| `0x14` | `REG_SYSCALL_CMD` | R/W | コマンドID |
| `0x18` | `REG_SYSCALL_ARG0` | R/W | 第1引数 / 戻り値 |
| `0x1C` | `REG_SYSCALL_ARG1` | R/W | 第2引数 |
| `0x20` | `REG_SYSCALL_ARG2` | R/W | 第3引数 |
| `0x24` | `REG_SYSCALL_ARG3` | R/W | 第4引数 |
| `0x28` | `REG_SYSCALL_ARG4` | R/W | 第5引数 |
| `0x2C` | `REG_SYSCALL_ARG5` | R/W | 第6引数 |

### 4.5 VDMA レジスタ詳細 (FC=12)
<!-- traceability: {VDMA} -->
| オフセット | レジスタ名 | R/W | 説明 |
| :--- | :--- | :--- | :--- |
| `0x00` | `REG_VDMA_SRC` | R/W | 転送元アドレス |
| `0x04` | `REG_VDMA_DST` | R/W | 転送先アドレス |
| `0x08` | `REG_VDMA_COUNT` | R/W | 転送バイト数 |
| `0x0C` | `REG_VDMA_CTRL` | W | 制御（Bit0: START） |

`REG_VDMA_SRC` / `REG_VDMA_DST` に指定できるアドレスはゲストRAM（Tier 1）および vMMIO空間（FC=14/15）。SHMアドレス（FC=14）を転送先/元に指定した場合、VDMAハンドラが `dispatch_access` と同一の権限チェック（`owner_id` 検証を含む）を実施する。

### 4.6 共有メモリマッピング (FC=14)
<!-- traceability: {OwnershipTransfer} -->
SHM へのアクセスは **IPCルータ経由でのみ許可される**。ゲストは IPCルータからハンドルを受け取ることによってのみ FC=14 アドレス空間にアクセスできる。SHM の所有権状態は IPCルータが一元管理し（`ipc_router.md` §4.1 所有権移譲モデル準拠）、vMMIO はその状態を執行するのみ。 `{OwnershipTransfer}`

- **SHMハンドル**: `(page_idx << 8) | slot_idx` の識別値。
- **アクセスアドレス**: `0xE000_0000 | (page_idx << 12) | offset_in_page`。

```mermaid
graph LR
    Guest[Guest App] -- Load/Store addr=0xExxx_xxxx --> vMMIO
    vMMIO -- FlatMap / TLB lookup --> Entry[vmmio_pte_tier3\nowner_id]
    Entry -- owner_id == current_task_id --> Phys[Physical Shared Memory]
    Entry -- FB_TASK_ID_FLIGHT or mismatch --> Trap[Trap/Exception]
```

#### ライフサイクル（ipc_router.md §4.1 に従属）
<!-- traceability: {OwnershipTransfer} -->

1. **Alloc (`allocate-shared`)**: COOS / 物理メモリマネージャが SHM 物理ページを確保し、`owner_id` = 送信タスクID で vMMIO に登録する。
2. **Revoke (`shm.release()`)**: 送信側がリソースを手放し、IPCルータが送信タスクの権限を無効化する。`owner_id` を `FB_TASK_ID_FLIGHT`（`0xFF`、移譲中センチネル）にセットし、TLB の該当エントリを無効化する。この時点で送信は完了確約となる。
3. **Rendezvous**: IPCルータが `(sender_role, target_role)` エッジ専用の CSP チャネル上でハンドルを含むメッセージのバッファなし同期ハンドオフを試みる。受信タスクが既に待機していれば即座に、まだ到達していなければ送信タスクが協調スケジューラ上でブロックする。キューが存在しないため、キュー満杯による差し戻しは発生しない。
4. **Grant (`claim(shm-id)`)**: ランデブーが成立した瞬間、IPCルータが `owner_id` を受信タスクIDにセットする。受信タスク側で `claim()` を呼び出すことで有効な `shared-block` ハンドルが取得可能となる。
5. **障害時回復 (`rollback_transfer`)**: 相手タスクが永久に到達しない場合、送信タスクはブロックし続ける。タスク異常終了やタイムアウト等によるフォールト発生時は、物理メモリ層の `rollback_transfer()` により `owner_id` を送信元タスクIDへ復元し、リソースの回収・再利用を行う。

### 4.7 仮想割り込みマッピング
<!-- traceability: {META_ConfigurableSystem} -->
物理割り込みから仮想割り込みIDへのマッピングは**静的1:1**とし、別コンフィグ（`irq_mapping_config`）で定義される。 `{META_ConfigurableSystem}`

- **マッピング方式**: 物理IRQ 1: 仮想IRQ 1。集約しない。
- **ゲスト側確認方式**: ポーリング。ゲストがstep再開時に `REG_IRQ_FLAGS` をチェック。
- **コールバック登録**: Phase1+で検討。
- **設定ファイル**: `vsoc_config` とは分離。`irq_mapping_config` として独立管理。

@see `system_syscall.md` §8.1

### 4.8 ソフトウェアTLB
<!-- traceability: {VDMA} {OwnershipTransfer} {META_ConfigurableSystem} -->
Tier 3 アクセス（FC=14/15）において毎回 FlatMap の二分探索を走らせる遅延を排除するため、仮想ページ番号（VPN = `raw >> 12`）に基づくマッピングを16エントリのダイレクトマップキャッシュに保持する。

- **Guest RAM アクセス時の TLB 完全バイパス (`VMMIO-GOTCHA-01`)**:
  **設計理由と不変条件**: 最上位ビットが 0 のアドレス空間（`0x0000_0000`〜`0x7FFF_FFFF`）はゲスト RAM 専用領域である。全メモリアクセスの 99% 以上を占める最頻パスにおいて毎回 TLB ルックアップやハッシュ計算を行うと、実行性能が致命的に劣化する。そのため、最上位ビットが 0 のアクセスは TLB を一切参照せず、直接ゲストベースアドレス加算＋サイズ境界検査のみで即時メモリアクセスを完結させる。TLB は最上位ビットが 1 の vMMIO / ペリフェラル領域にのみ適用される。
- **Folding XOR ハッシュによる機能コード（FC）の均等分散 (`VMMIO-GOTCHA-02`)**:
  - キー（VPN）: `raw >> 12`（20-bit）
  - HASH / インデックス計算: `tlb_idx = (vpn ^ (vpn >> 4) ^ (vpn >> 8) ^ (vpn >> 12) ^ (vpn >> 16)) & 15`
  - **設計理由と不変条件**: 単純なビットマスク（`vpn & 15`）や剰余を用いると、同一オフセットを持つ異なる機能コード（FC=14 SHM と FC=15 PASSTHROUGH など）が同一スロットに衝突し、TLB スラッシング（頻繁な追い出し）が発生する。上位 4-bit の FC フィールドから下位ページインデックスまでの全 20 ビットを 4-bit 幅で均等に折りたたんで XOR 合成することで、異なるデバイス領域間でのキャッシュ競合を極小化し、16 スロットの利用効率を最大化する。
- **アクセス権限剥奪（Revoke）時の TLB 即時無効化 (`VMMIO-GOTCHA-03`)**:
  - **設計理由と不変条件**: 共有メモリブロックの送信（`shm.release()`）や権限剥奪トランザクションにおいて、ページテーブル上の所有者 ID を `FB_TASK_ID_FLIGHT` へ変更する際、該当 VPN に対応する TLB エントリを直ちに無効化（フラッシュ）しなければならない。TLB の無効化を怠ると、キャッシュが残存している間に古いタスクからデータが読み書き可能となり、所有権移譲プロトコルの安全性（ゼロコピー手渡しと二重所有防止）が破壊される。
- **キャッシュ更新 & 押し出し (Eviction & Refill)**:
  TLBミス時に FlatMap から取得した PTE を `vmmio_tlb_cache[tlb_idx]` に上書き（同一ハッシュに別のアドレスが割り当てられた場合は以前のエントリを自動無効化・上書きする完全O(1)方式）。

- **権限チェック**:
  TLBは探索ルートのスキップのみをキャッシュする。権限の検証（PTEの読み書きパーミッション、Owner IDなど）は、TLBヒット時も含めて毎回インラインで実施され、TLBヒットが安全性の検査をバイパスすることはない。

## 5. インターフェース定義

### 5.1 公開API
外部から利用可能なオブジェクト指向APIを定義する。

#### フック登録 (`register-hook`)

<!-- traceability: {vMMIO_TrapAndEmulate} -->

本節が正本の実装。vSoC の同名APIは `harness.vmmio` 越しにここへ転送する薄いラッパーであり、`vsoc_context`/`vsoc_harness` は本APIの引数ではない。

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 既に定義（ROM）されている領域に対して、ホスト側のハンドラの実装アドレスを紐づける。 |
| シグネチャ | `register-hook(hook-id: hook-category, handler-addr: mem-address) -> operation-result` |
| 引数と役割 | `ctx`: vmmio_context<br>`hook-id`: 対象の領域カテゴリ（FC/ページ番号等の組み合わせを識別）<br>`handler-addr`: ハンドラ関数の物理アドレス |
| 事前条件 | `hook-id` が `fireball.wit` で定義された有効なIDであること。未登録であること。 |
| 事後条件 | フックレジストリにエントリが追加される。 |
| 不変条件 | アドレスマップ定義自体は変更されない。 |
| エラー時の挙動 | 無効なIDの場合はエラーを返す。二重登録は拒否する。 |
| 期待する結果 | 正常：フックが登録され、以降のアクセスで呼び出される。 |

#### アクセスディスパッチ (`dispatch-access`)

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | vSoC 実行エンジンからトラップされたメモリアクセスを高速RAMバイパス判定し、vMMIOアドレスの場合はTLB及びFlatMapでPTEを解決しつつ権限検証の上でハンドラや物理レイヤへディスパッチする。 |
| シグネチャ | `dispatch-access(addr: mem-address, buffer: list<u8>, is-write: bool) -> operation-result` |
| 引数と役割 | `ctx`: vmmio_context<br>`addr`: アクセス先アドレス（vmmio_address として分解）<br>`buffer`: データバッファ (read時out, write時in)<br>`is-write`: 書き込みフラグ |
| 事前条件 | リニアRAMまたは vMMIO領域（制限空間内）への正常な境界内アクセスであること。 |
| 事後条件 | 許可アドレス：ハンドラ実行完了 / メモリアクセス完了。非許可アドレス：アクセス違反トラップ。 |
| 不変条件 | アドレスデコードおよびルックアップの結果は決定論的である。 |
| エラー時の挙動 | 非許可アドレス、未登録ハンドラへのアクセスはトラップを発生させる。 |
| 期待する結果 | 正常：エントリの権限チェックを通過し、登録された物理マッピングまたはハンドラが実行され、結果がゲストに反映される。 |
| 補足 | Software TLB ヒット時は FlatMap の二分探索を省略する。 |

## 6. 制約達成の方策

### 6.1 性能制約と方策
<!-- traceability: {META_ConfigurableSystem} {FastAddressCheck} {vMMIO_TLB} -->
- **目標**: MMIOアクセスのオーバーヘッドを最小化する。
- **方策1**: `{META_ConfigurableSystem}` コアデバイス（SYSCTL等）をFC=12に配置し、配列/ハッシュ参照のみで即時解決できるようにする。
- **方策2**: `{FastAddressCheck}` アドレス空間を RAM Bypass（最上位ビット=0）と vMMIO領域（最上位ビット=1）に分割し、探索とデコードのホットパス探索コストを削減する。
- **方策3**: `{vMMIO_TLB}` ダイレクトマップ型 Software TLB により、Tier 3 の繰り返しアクセスを完全 O(1) で超高速キャッシュ解決する。

### 6.2 メモリ制約と方策
<!-- traceability: {META_ConfigurableSystem} {META_FlatMapIndexed} -->
- **目標**: マップ管理用のメモリを最小化する。
- **方策**: `{META_ConfigurableSystem}` `{META_FlatMapIndexed}` `fireball::flat_map_view<uint32_t, uint32_t>`（静的ソート済み配列）によるフラットな PTE 管理に集約し、登録されたページ数に応じた最小限のメモリフットプリントを実現する。

### 6.3 安全性制約と方策
<!-- traceability: {META_RestrictedPhysicalAccess} {OwnershipTransfer} -->
- **目標**: ゲストが許可されていない物理アドレスにアクセスできないことを保証する。
- **方策**: `{META_RestrictedPhysicalAccess}` `{OwnershipTransfer}` 権限チェックを解決された PTE フラグで行い、TLBヒット時も含めてすべてのアクセスパスで必ず実行する。TLBはページテーブル探索のスキップのみを担い、権限チェックをバイパスしない。FC=14 (SHM) の所有権は IPCルータが唯一の書き込み権限を持ち、Revoke 時に該当マッピングの TLB エントリを即時無効化する。
