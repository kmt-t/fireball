# システムコンフィグ コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}
<!-- evidence:
     formal: formal/system_config_model.py
     test: docs/qa/tier1_core/system_config_test_spec.md
-->

## 1. コンセプト
<!-- traceability: {META_ConfigurableSystem} {META_Static_Resolution} {GLOBAL_IndependentHeap} {GLOBAL_StrictMemoryLimit} {ConsolidatedHeap} {GLOBAL_StaticScalability} {RoleBasedAccessControl} {FastAddressCheck} {vMMIO_Isolation} {META_RestrictedPhysicalAccess} {BufferedLogging} {Challenge_DebuggerResource} {ZeroRuntimeOverhead} -->
Fireballハイパーバイザは、リソース制約の厳しい組み込み環境で動作するため、メモリサイズや最大リソース数をコンパイル時に固定する設計を採用する。設定はヘッダファイル形式のコンフィグファイル（`inc/fireball_config.hxx`）内のマクロ定義および `constexpr` 定数によって行われ、実行時オーバーヘッドを完全に排除する（ゼロコスト抽象化）。

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} {META_Static_Resolution} -->
本コンポーネントは **Tier 1 (主要システムコンポーネント: Primary Component)** に属し、システム全体の静的構成方針、メモリパーティション配分、および各サブシステムの設定定数を統括・提供する。

## 3. 静的モデル

### 3.1 データ構造
<!-- traceability: {Resource_Estimation_Model} -->
コンフィグ項目は、実行時のオーバーヘッドを排除するため、主にプリプロセッサマクロおよび C++ `constexpr` 定数として定義される。設計段階でリソース使用量を概算し、制約適合性を検証するためのモデルを提供する。

### 3.2 内部ブロック図
<!-- traceability: {Resource_Estimation_Model} -->
```mermaid
graph TD
    Config[fireball_config.hxx] --> Memory[Memory Management]
    Config --> IPCR[IPC Router]
    Config --> HAL[HAL]
    Config --> vSoC[vSoC / vMMIO]
    Config --> Log[Logging / Debugger]
    Config --> Task[Task ID / Reserved Values]
    Config --> Recovery[Recovery Strategy]
```

### 3.3 コンフィグマクロ一覧・定義

#### 3.3.1 メモリ管理
<!-- traceability: {GLOBAL_IndependentHeap} {GLOBAL_StrictMemoryLimit} {ConsolidatedHeap} {ContextPointerRegister} {GLOBAL_StaticScalability} {IPC_ZeroCopy} -->
デフォルト値は **評価ターゲットである最小構成（RAM 32KB）** の予算配分に対応する。

| マクロ名 | 説明 | デフォルト値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_TASK_HEAP_SIZES` | ゲストVMスロットごとに個別設定するコンパイル時固定パーティションサイズのROM配列（要素数 `FB_CONF_MAX_GUEST_VMS`）。搭載予定WASMモジュールの規模に応じてスロットごとに異なる値を設定できる | `[4096]` | |
| `FB_CONF_RUNTIME_HEAP_SIZE` | ホスト（WASMランタイム）実行専用の独立静的プールサイズ | `2048` | |
| `FB_CONF_KERNEL_HEAP_SIZE` | COOSカーネル（スケジューラ、CSP、TCB、共有メモリ）用静的プールサイズ | `4096` | |
| `FB_CONF_SUBSYS_HEAP_SIZE` | IPCルータ・HAL・ログバッファ用静的プールサイズ | `3072` | |
| `FB_CONF_INTERP_STACK_SIZE` | インタープリタ統合スタック（`execution_context` + フレーム/オペランド）総容量 | `2048` | |
| `FB_CONF_JIT_CACHE_SIZE` | 共通コード領域と3バンクを含む連続JITコードキャッシュ領域 | `8192` | `{JIT_MultiBuffer_Cache}` |
| `FB_CONF_MAX_GUEST_VMS` | 同時にロード可能なゲストVMの最大数 | `1` | |
| `FB_CONF_SHM_SIZE` | ゼロコピーIPCで使用する静的共有メモリの総バイト数（カーネル用プールの内数） | `1024` | |
| `FB_CONF_MAX_SHM_PAGES` | FC=14で予約する4KB仮想アドレススロット数。物理SHMバイト予算とは独立 | `32` | |
| `FB_CONF_MEMORY_POOL_SIZE` | 全パーティションを切り出す統合物理プールの総サイズ | `23552` | |
| `FB_CONF_PHYSICAL_RAM_SIZE` | ターゲットの物理SRAM容量（最小構成 32KB / 想定構成 64KB） | `32768` | |

##### メモリ総量と個別プールの依存関係
統合物理プール（`FB_CONF_MEMORY_POOL_SIZE` = 23,552 Bytes）は、以下の静的パーティションの総和として完全に一致する：
- カーネルプール (`FB_CONF_KERNEL_HEAP_SIZE`): 4,096 Bytes（共有メモリの物理バック領域 `FB_CONF_SHM_SIZE` 1,024 Bytes を内包。FC=14の仮想予約スロットは物理消費量に加算しない）
- ランタイムプール (`FB_CONF_RUNTIME_HEAP_SIZE`): 2,048 Bytes
- サブシステムプール (`FB_CONF_SUBSYS_HEAP_SIZE`): 3,072 Bytes
- JITコードキャッシュ (`FB_CONF_JIT_CACHE_SIZE`): 8,192 Bytes (共通コード2KB + 可変バンク2KB × 3面)
- インタープリタ統合スタック (`FB_CONF_INTERP_STACK_SIZE`): 2,048 Bytes
- ゲストタスクRAM (`sum(FB_CONF_TASK_HEAP_SIZES)`、スロット別ROM配列の総和): `[4096]` の総和 = 4,096 Bytes
- **合計**: 4,096 + 2,048 + 3,072 + 8,192 + 2,048 + 4,096 = **23,552 Bytes**

```text
static_assert(FB_CONF_KERNEL_HEAP_SIZE
            + FB_CONF_RUNTIME_HEAP_SIZE
            + FB_CONF_SUBSYS_HEAP_SIZE
            + FB_CONF_JIT_CACHE_SIZE
            + FB_CONF_INTERP_STACK_SIZE
            + sum(FB_CONF_TASK_HEAP_SIZES)
            == FB_CONF_MEMORY_POOL_SIZE);
static_assert(FB_CONF_TASK_HEAP_SIZES.size() == FB_CONF_MAX_GUEST_VMS);
static_assert(FB_CONF_MEMORY_POOL_SIZE <= FB_CONF_PHYSICAL_RAM_SIZE);
static_assert(FB_CONF_GUEST_RAM_SIZE == FB_CONF_TASK_HEAP_SIZES[0]);
```
各ゲストVMスロット `i` の実際の物理配置（領域）は、`FB_CONF_GUEST_RAM_BASE` を起点に先行スロットのサイズを累積したオフセット（`sum(FB_CONF_TASK_HEAP_SIZES[0..i))`）に配置される。スロットの基点アドレスを個別に設定するコンフィグ項目は持たず、サイズ配列のみから一意に導出することで、パーティション間の重複を静的に排除する。

##### PMSAv8 MPU 物理アドレスマップ
<!-- traceability: {FastAddressCheck} {META_FaultIsolation} {Shm_Allocator} {System_Allocator} {WasmPageAlignment} -->
以下は [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md) §7.1 の PMSAv8 MPU 8リージョン配分（`PMSAv8MPU`）が使う静的ベースアドレスの正本である。**想定実機ターゲットのアドレスマップ**は、Cortex-M33 の一般的な SRAM 配置（起点 `0x2000_0000`）とペリフェラル配置（起点 `0x4000_0000`）に従う。

このアドレスマップは、「メモリ総量」節が定義する評価用最小構成（`FB_CONF_MEMORY_POOL_SIZE` = 23,552 Bytes）とは異なるスケールを表す。実機は、本表のリージョン間隔を確保できる物理 SRAM 容量を想定する。評価用最小構成は、その一部だけを静的に占有する。

| マクロ名 | 対象リージョン | 値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_MPU_R1_KERNEL_DATA_BASE` | Region 1: Kernel Data & BSS | `0x2000_0000` | Cortex-M33 SRAM 起点慣行 |
| `FB_CONF_MPU_R2_KERNEL_POOL_BASE` | Region 2: Kernel Pool / Heap (`system_allocator`) | `0x2000_8000` | `{System_Allocator}` |
| `FB_CONF_MPU_R4_JIT_CACHE_BASE` | Region 4: JIT Code Cache (連続8KB) | `0x2004_0000` | `{JIT_MultiBuffer_Cache}` |
| `FB_CONF_MPU_R5_PERIPHERAL_MMIO_BASE` | Region 5: Peripheral MMIO | `0x4000_0000` | Cortex-M33 ペリフェラル起点慣行（`FB_CONF_VSOC_PASSTHROUGH_BASE`と同一値） |
| `FB_CONF_MPU_R6_SHARED_MEMORY_BASE` | Region 6: Shared Memory Buffers (`shm_allocator`) | `0x2008_0000` | `{Shm_Allocator}` |
| `FB_CONF_MPU_R7_STACK_GUARD_BASE` | Region 7: Stack Guard Band | `0x200C_0000` | スタックオーバーフロー検出用ガードバンド |

Region 0（Flash/Kernel Code, `0x0000_0000`）・Region 3（Guest WASM RAM, `pool_base` 実行時決定）は上記表に含まれない（それぞれ ROM 起点・`init_manager`の実行時引数のため）。

Region 4 は4KBページ2枚分の連続8,192バイト領域とし、基底アドレスは8KB境界に置く。領域内は次の4区画に固定し、共通コード領域をバンクローテーションやflushの対象から除外する。

| オフセット | アドレス範囲 | サイズ | 用途 |
| :--- | :--- | ---: | :--- |
| `0x0000–0x07FF` | `0x2004_0000–0x2004_07FF` | 2,048 B | 相対ジャンプ用の共通コード、AAPCS境界コード、復帰トランポリン等。非エビクション |
| `0x0800–0x0FFF` | `0x2004_0800–0x2004_0FFF` | 2,048 B | Active バンク |
| `0x1000–0x17FF` | `0x2004_1000–0x2004_17FF` | 2,048 B | Warm バンク |
| `0x1800–0x1FFF` | `0x2004_1800–0x2004_1FFF` | 2,048 B | Oldest バンク |

#### 3.3.2 IPCルータ
<!-- traceability: {META_ConfigurableSystem} {IPC_ZeroCopy} -->
| マクロ名 | 説明 | デフォルト値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_IPC_MAX_SERVICES` | 登録可能な最大サービス数 | `16` | |
| `FB_CONF_ROUTER_MAX_KV_PAIRS` | 1メッセージが保持できるkv_pairの最大数（[`ipc_router.md`](docs/components/tier1_interface/ipc_router.md) の ） | `8` | |
| `FB_CONF_MAX_CONSECUTIVE_HANDOFFS` | スケジューラ復帰なしでの最大連続CSPハンドオフ回数 | `4` | `{Challenge_CspHandoffStarvation}` |

```cpp
namespace fireball::config {
    // ロール定義: HAL_* の各ロールはデバイス/サービスインスタンス 1 つに専用のロール
    // （＝専用チャネル）を割り当てる。「1 チャネル 1 待機者」制約下で複数の同種デバイス
    // インスタンス（例: 物理UARTと、それとは別に登録されるコンソール出力ストリーム）を
    // 区別するために必要である（{ADR_RendezvousChannel}）。
    enum class router_role : uint8_t {
        RUNTIME = 0,
        CORE_SERVICE = 1,
        HAL_UART = 2,
        HAL_STDOUT = 3,
        HAL_GPIO = 4,
        HAL_TIMER = 5,
        HAL_I2C = 6,
        HAL_SPI = 7,
        DEBUGGER = 8,
        HAL_LOGGER = 9,
        COUNT = 10
    };

    // ロール間通信許可マトリクス (10x10 static bool table)
    inline constexpr std::array<std::array<bool, 10>, 10> FB_CONF_ROUTER_ROLE_MATRIX {{
        // Target:            RUNTIME, CORE_SERVICE, HAL_UART, HAL_STDOUT, HAL_GPIO, HAL_TIMER, HAL_I2C, HAL_SPI, DEBUGGER, HAL_LOGGER
        /* RUNTIME         */ {false,  true,         true,     true,       true,     true,      true,    true,    false,    true},
        /* CORE_SERVICE    */ {false,  false,        true,     true,       true,     true,      true,    true,    false,    true},
        /* HAL_UART        */ {false,  false,        false,    false,      false,    false,     false,   false,   false,    false},
        /* HAL_STDOUT      */ {false,  false,        false,    false,      false,    false,     false,   false,   false,    false},
        /* HAL_GPIO        */ {false,  false,        false,    false,      false,    false,     false,   false,   false,    false},
        /* HAL_TIMER       */ {false,  false,        false,    false,      false,    false,     false,   false,   false,    false},
        /* HAL_I2C         */ {false,  false,        false,    false,      false,    false,     false,   false,   false,    false},
        /* HAL_SPI         */ {false,  false,        false,    false,      false,    false,     false,   false,   false,    false},
        /* DEBUGGER        */ {false,  true,         true,     true,       true,     true,      true,    true,    false,    true},
        /* HAL_LOGGER      */ {false,  false,        false,    false,      false,    false,     false,   false,   false,    false},
    }};
}
```

#### 3.3.3 HAL
<!-- traceability: {META_ConfigurableSystem} -->
| マクロ名 | 説明 | デフォルト値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_HAL_MAX_DEVICES` | 管理可能な最大デバイス数 | `8` | |
| `FB_CONF_HAL_BUFFER_SIZE` | デバイス通信用バッファの最大サイズ (Bytes) | `256` | |
| `FB_CONF_HAL_MAX_BUFFERS` | デバイス通信用バッファの最大数 | `4` | |

#### 3.3.4 vSoC / vMMIO
<!-- traceability: {JIT_MultiBuffer_Cache} {GLOBAL_StrictMemoryLimit} {vMMIO_Isolation} {META_ConfigurableSystem} {META_RestrictedPhysicalAccess} {META_FlatMapIndexed} {GLOBAL_StaticScalability} {JIT_CardAgingSweep} -->
| マクロ名 | 説明 | デフォルト値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_JIT_ENABLED` | JITコンパイラ機能の有効化フラグ | `true` | |
| `FB_CONF_WASM_PAGE_SIZE` | WASM標準論理ページサイズ (64KB, 65,536 Bytes) | `65536` | |
| `FB_CONF_MAX_WASM_PAGES` | システム物理予算上限としての最大WASMページ数（最小構成は1ページ/部分ページ） | `1` | |
| `FB_CONF_JIT_CACHE_PAGE_SIZE` | Region 4 を構成する物理ページのサイズ | `4096` | |
| `FB_CONF_JIT_COMMON_CODE_SIZE` | 相対ジャンプ等の非エビクション共通コード領域 | `2048` | |
| `FB_CONF_JIT_BANK_SIZE` | 各エビクション対象バンクのサイズ | `2048` | |
| `FB_CONF_JIT_CACHE_SIZE` | 連続JIT領域（共通コード2KB + 3バンク×2KB） | `8192` | |
| `FB_CONF_JIT_NUM_BUFFERS` | JITキャッシュバッファ面数 (3面) | `3` | `{JIT_OldestOnly_Promote}` |
| `FB_CONF_JIT_MAX_INBOUND_CHAINS_PER_BANK` | 単一キャッシュバンクの最大被チェインエントリ数 | `32` | `{JIT_LazyChaining}` |
| `FB_CONF_JIT_CARD_SHIFT` | JITカードテーブルのビットシフト数（関数ごと、4バイト単位 = 2） | `2` | |
| `FB_CONF_JIT_AGING_STEP_UNITS` | 3面キャッシュのローテーション1回ごとに処理する関数更新表の非ゼロバイト数（1バイト = 8関数） | `2` | |
| `FB_CONF_JIT_AGING_STEP_SCAN_BYTES` | 3面キャッシュのローテーション1回ごとに走査する関数更新表のバイト数の上限（値が0のバイトも数える） | `8` | |
| `FB_CONF_GUEST_RAM_BASE` | ゲストRAMの開始アドレス（64KB境界配置） | `0x00000000` | |
| `FB_CONF_GUEST_RAM_SIZE` | 単一ゲストVMインスタンスに割り当てられるRAMの物理サイズ（対応スロットの `FB_CONF_TASK_HEAP_SIZES[vm_index]` と同値、4KB部分ページ） | `4096` | |
| `FB_CONF_VMMIO_BASE` | vMMIO領域の開始アドレス (Bit 31 == 1) | `0x80000000` | |
| `FB_CONF_VSOC_PASSTHROUGH_BASE` | ゲスト仮想PASSTHROUGH領域（FC=15）のホスト実ペリフェラル基底アドレス | `0x40000000` | |
| `FB_CONF_VMMIO_MAX_REGIONS` | 登録可能な最大vMMIO領域数 | `8` | |
| `FB_CONF_VMMIO_MAX_PTES` | FlatMap ページテーブルに保持可能な PTE の最大件数 | `64` | |
| `FB_CONF_VMMIO_ALLOWED_ADDRS` | ゲストからのアクセスを許可する物理アドレス範囲 | `constexpr`構造体配列 | |
| `FB_CONF_VMMIO_VIRQ_BASE` | 原因付き仮想割り込みディスパッチャ（vIRQ）専用ページの基底アドレス | `0xC0003000` | |
| `FB_CONF_VMMIO_VIRQ_PAGE_SIZE` | vIRQ専用ページの固定サイズ | `4096` | |
| `FB_CONF_VIRQ_CATEGORY_COUNT` | vIRQの固定分類ノード数（DEVICE/SYSTEM/RUNTIME/FAULT） | `4` | |
| `FB_CONF_VIRQ_MAX_NODES` | vIRQ静的ノード数（root + 4分類 + デバイスノード） | `1 + FB_CONF_VIRQ_CATEGORY_COUNT + FB_CONF_HAL_MAX_DEVICES` | |
| `FB_CONF_VIRQ_MAX_SOURCES` | vIRQ静的原因源数（SYSTEM/RUNTIME/FAULT + デバイス源） | `3 + FB_CONF_HAL_MAX_DEVICES` | |

ゲストRAMへのアクセスは、アドレスを有効サイズと比較するFastAddressCheckで保護する（{FastAddressCheck}）。境界外アドレスはトラップし、マスクで折り返して実行を継続しない。この判定はRAMサイズが2の冪であることを要求しない。

#### 3.3.5 ロギング・デバッガ
<!-- traceability: {BufferedLogging} {Challenge_DebuggerResource} {Debug_Integrated} -->
| マクロ名 | 説明 | デフォルト値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_LOG_BUFFER_SIZE` | ログメッセージ保持用のバッファサイズ (Bytes) | `512` | |
| `FB_CONF_DEBUG_MAX_BREAKPOINTS` | 最大ブレークポイント数 | `8` | `{META_ConfigurableSystem}` |
| `FB_CONF_DEBUG_PACKET_SIZE` | RSPパケットバッファサイズ | `1024` | |
| `FB_CONF_DEBUG_MAX_PC_SAMPLES` | プロファイラバッファに保持可能なPCサンプリングエントリの最大件数 | `64` | `Debug_Integrated` `{META_NoStdVector}` |

#### 3.3.6 タスクID型・予約値
<!-- traceability: {GLOBAL_StaticScalability} -->
| マクロ名 | 説明 | 値 | 備考 |
| :--- | :--- | :--- | :--- |
| `FB_TASK_ID_T` | タスクIDの基底型 | `uint8_t` | 有効値域 `1`〜`FB_CONF_MAX_TASKS` |
| `FB_TASK_ID_INVALID` | 未割り当て・無効を示す予約値 | `0` | 初期値。「誰も所有していない」を表す |
| `FB_TASK_ID_FLIGHT` | 所有権移譲中を示す予約値 (FLIGHT_SENTINEL) | `0xFF` | IPCルータ移譲中にセット |
| `FB_CONF_MAX_TASKS` | 同時実行可能な最大タスク数 | `16` | `≤ 254`（`FB_TASK_ID_FLIGHT` との衝突防止） |

#### 3.3.7 割り込みイベントFIFO
<!-- traceability: {GLOBAL_InterruptWakeup} -->
| マクロ名 | 説明 | デフォルト値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_INTERRUPT_QUEUE_SIZE` | COOSが所有する原因付き割り込みイベントFIFOの固定エントリ数。ISR producerとCOOS consumerのSPSCロックフリーリングで実装する | `16` | [`requirement_list.md`](../../requires/requirement_list.md) |

```python
# コンパイル時検証
assert FB_CONF_MAX_TASKS <= 254, "FB_CONF_MAX_TASKS must be <= 254"
```

#### 3.3.8 リカバリー戦略
<!-- traceability: {META_RecoveryStrategy} {Errorcode_To_Strategy} -->
| マクロ名 | 説明 | デフォルト値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_RETRY_BACKOFF_MS` | `retry` 戦略の再試行間ウェイト（ミリ秒） | `10` | |

`retry` の上限回数（3回、の不変条件）とあわせ、を実装するすべてのコンポーネントはこの2値を共有する。個別のコンポーネント文書で異なる待機時間・回数を独自に定義しないこと。

## 4. 動的モデル

### 4.1 アルゴリズム
<!-- traceability: {META_Static_Resolution} -->
本コンポーネントは静的な定義のみを提供し、すべての値はコンパイル時に確定する。

## 5. 制約達成の方策

### 5.1 性能・メモリ制約と方策
<!-- traceability: {META_Static_Resolution} {META_ConfigurableSystem} {GLOBAL_StaticScalability} -->
- **方策**: すべてのパラメータをコンパイル時定数（`constexpr` / マクロ）とし、実行時の探索・計算コストおよび動的ヒープ（malloc/new）消費を完全排除する。

### 5.2 安全性制約と方策
<!-- traceability: {META_ConfigurableSystem} -->
- **方策**: システム構成定数はすべて `constexpr` / `const` として ROM / Flash（`.rodata`）に静的配置され、実行時の不正な書き換えから保護される。

## 6. 形式検証との対応

[`system_config_model.py`](docs/components/tier1_core/formal/system_config_model.py) は、構成値の実行時変更禁止と、定義済みリソース予算内への収束をCTLで検証する。`guards=False` では実行時上書きおよび予算超過の遷移を追加し、両特性が反証されることを確認する。
