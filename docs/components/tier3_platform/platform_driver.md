# HAL ドライバ実装（UART/SEGGER RTT/GPIO/I2C/SPI/Timer 物理層） コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}
<!-- evidence:
     formal: formal/interrupt_boundary_model.py
     concept: concepts/platform_driver_concept.py
     test: tests/platform_driver_test_spec.md
-->

本コンポーネントは、Tier 2 の抽象契約 [`hal_dispatch.md`](docs/components/tier2_runtime/hal_dispatch.md)（URI Resolver、コマンドプロトコル、ゼロコピー転送インターフェース）の物理ドライバ実装である。契約と実装の記述に食い違いがあれば `hal_dispatch.md` を正とする（`{META_ContractImplSplit}` 契約/実装分割パターン）。

## 1. コンセプト
<!-- traceability: {Challenge_InterruptSafety} {TaskPollInterruptEvent} {RSPMinimalSet} {Fast_Path_GPIO} -->
[`hal_dispatch.md`](docs/components/tier2_runtime/hal_dispatch.md) が定義する `hal_task` コマンドディスパッチ契約を実現するため、UART・SEGGER RTT・GPIO・I2C・SPI・Timer の物理レジスタ操作ドライバ群を実装する。物理割り込みは原因付きイベントの通知とタスクウェイクアップによって安全に処理される。また、デバッグ用の GDB Remote Serial Protocol (RSP) のパケット物理エンコード/デコード（RSP Parser）を担い、解析済みデバッグコマンドを `debug_command_queue` へ供給する。 `{Challenge_InterruptSafety}` `{TaskPollInterruptEvent}` `{RSPMinimalSet}` `{Fast_Path_GPIO}`

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} -->
本コンポーネントは **Tier 3 (プラットフォーム / リーフコンポーネント: Leaf Component)** に属し、ハードウェアとハイパーバイザの物理境界を抽象化する物理ドライバ実装を担当する。抽象化層（URI Resolver、コマンドプロトコル）は Tier 2 の [`hal_dispatch.md`](docs/components/tier2_runtime/hal_dispatch.md) が担う。 `{META_3TierSeparation}`

## 3. 静的モデル

### 3.1 データ構造
- **デバイスレジストリ（物理実体）**: 管理対象のデバイス情報を保持する静的配列。
- **HALバッファプール（物理実体）**: デバイス通信用に使用する、ドライバ側で管理される固定長バッファプール。**vMMIOの DYNAMIC 領域（コンパイル時に事前予約された専用ページプール）に配置され、安全かつ有界に管理される。**
- **RSPパケットバッファ**: RSPパケットの送受信に使用する固定長バッファ。

### 3.2 内部ブロック図
```mermaid
flowchart TD
    IPCR["Tier1: ipc_router (URI resolved to dedicated Role/Channel)"]
    IPCR -->|CSP Rendezvous| T1["hal_task: Role.HAL_UART"] --> UART[UART Driver: fireball://device/uart/0]
    IPCR -->|CSP Rendezvous| T2["hal_task: dedicated Role"] --> RTT[RTT Driver: fireball://device/rtt/0]
    IPCR -->|CSP Rendezvous| T3["hal_task: Role.HAL_GPIO"] --> GPIO[GPIO Driver: fireball://device/gpio/0]
    IPCR -->|CSP Rendezvous| T4["hal_task: Role.HAL_I2C"] --> I2C[I2C Driver: fireball://device/i2c/0]
    IPCR -->|CSP Rendezvous| T5["hal_task: Role.HAL_SPI"] --> SPI[SPI Driver: fireball://device/spi/0]
    IPCR -->|CSP Rendezvous| T6["hal_task: Role.HAL_TIMER"] --> Timer[Timer Driver: fireball://device/timer/0]
    UART --> RSP[RSP Parser]
    RTT --> RSP
    RSP --> Queue[debug_command_queue]
    Queue --> Debugger[Debugger Task]
```

各 `hal_task` インスタンスは Tier 2 [`hal_dispatch.md`](docs/components/tier2_runtime/hal_dispatch.md) が定義する契約に従い、1 インスタンスにつき 1 物理ドライバのみを専有する（1 タスクが複数デバイスの URI を見て振り分けることはない）。RTT はデバッグトランスポートの代替経路であり、UART 同様 RSP Parser へ接続される（`{RSP_Transport_Selectable}`）。

### 3.3 主要なクラス・構造体・配列・定数

#### デバイス情報（device）
個別のデバイスの属性と状態を管理する。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| デバイス識別子 | システム全体で重複しない、デバイスごとの管理番号 | ID値 | `device_id` |
| デバイス名 | 人間が識別可能なデバイスの名称 | 固定長配列 | 16bytes |
| 階層URI | IPC ルータに登録される正規化 URI | 文字列 | `fireball://device/<type>/<instance>` |
| デバイス種別 | 入出力の特性（ブロック/ストリーム等）を識別する | 列挙型 | - |
| 転送単位 | デバイスが扱う最小のデータブロックサイズ | バイト数 | - |
| 予約ページ数 | vMMIO DYNAMIC領域に確保するページ数 (`reserved_pages`) | ページ数 | デフォルト0 |

#### HAL構成（hal_config、物理値）
<!-- traceability: {META_ConfigurableSystem} -->
`hal_dispatch.md` の契約上限を物理的な既定値として確定する。 `{META_ConfigurableSystem}`

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 最大登録デバイス数 | システムが同時に管理可能なデバイスの総数。ビルド時定数 `FB_CONF_HAL_MAX_DEVICES`（現行値 `8`）で設定する | エントリ数 | 1-255 (許容範囲) |
| 最大バッファ数 | 通信に使用する内部バッファの予約数。ビルド時定数 `FB_CONF_HAL_MAX_BUFFERS`（現行値 `4`）で設定する | エントリ数 | 1-255 (許容範囲) |
| `buffer_size` | 単一バッファに割り当てられる固定バイト数。ビルド時定数 `FB_CONF_HAL_BUFFER_SIZE`（現行値 `256`）で設定する | バイト数 | - |

---

## 4. 動的モデル

### 4.1 割り込み処理の物理実装
<!-- traceability: {RSP_Transport_Selectable} {TaskPollInterruptEvent} {GLOBAL_InterruptWakeup} -->
- **割り込み通知（push）**: 物理割り込み発生時、ISRは原因情報を固定5ワードの`interrupt-event`へ変換し、COOSの`notify_interrupt(event)`で固定長FIFOへ投函するのみとする。物理デバイスの複数原因は、上位のイベント源マッピングで同じデバイス系統へ集約する。**ISRがタスク状態を直接書き換えることはない。**実際のREADY遷移は、スケジューラが協調境界でFIFOをドレインする際に行われる（`{GLOBAL_InterruptWakeup}`を正本とする）。この非同期境界の分離は、[`interrupt_boundary_model.py`](docs/components/tier3_platform/formal/interrupt_boundary_model.py) に定義されたCTL検証項目 `isr_does_not_update_task_state_directly` および `interrupt_event_reaches_scheduler_boundary` として証明されている性質である。
- **割り込み配送**: COOSから渡された`interrupt-event`は、vSoCがSafepointで受け取り、ゲスト配送またはドロップを行う。vIRQの分類・デバイスノード・ゲスト関数登録はvSoCとvMMIOの契約に従い、物理ドライバはゲスト関数を直接呼び出さない。 `{TaskPollInterruptEvent}` `{GLOBAL_InterruptWakeup}`

#### HalBufferPool バッファ確保・境界検査手順（手順アクティビティ図）
<!-- traceability: {GOTCHA-HAL-01} {HAL_Interface} {IPC_ZeroCopy} -->
デバイス通信用バッファスロットの固定長境界検証、タスク所有権照合、および不正アクセス防御手順を示す。

```mermaid
flowchart TD
    Start(["HAL Driver: acquire_buffer(size)"]) --> CheckSize{"Requested size <= FB_CONF_HAL_BUFFER_SIZE (256B)?"}

    CheckSize -- "No (> 256B)" --> RejectSize(["Reject: HAL_ERROR_INVALID_SIZE"])
    CheckSize -- "Yes" --> AllocSlot["Find Free Slot in Fixed-Capacity HalBufferPool (FB_CONF_HAL_MAX_BUFFERS = 4 slots)"]
    AllocSlot --> SlotFound{"Available slot found?"}

    SlotFound -- "No" --> RejectFull(["Reject: Pool Exhausted (ERR_NO_RESOURCE)"])
    SlotFound -- "Yes" --> MarkSlot["Mark Slot Active & Set slot.owner_id = caller_task_id"]
    MarkSlot --> ReturnHandle(["Return hal_buf_id Handle to Caller"])

    subgraph Buffer Release / Destruction
        RelStart(["HAL Driver: release_buffer(hal_buf_id)"]) --> VerifyOwner{"caller_task_id == slot.owner_id?"}
        VerifyOwner -- "No (Unauthorized Task!)" --> TrapOwner(["GOTCHA-HAL-01 Trap: HalBufferTrap / ERR_PERMISSION_DENIED"])
        VerifyOwner -- "Yes" --> ClearSlot["Zero slot memory & Reset slot.owner_id = 0"]
        ClearSlot --> ReturnPool(["Slot returned to Free Pool"])
    end
```

### 4.2 状態遷移図（物理デバイス状態）
<!-- traceability: {RSP_Transport_Selectable} {TaskPollInterruptEvent} {GLOBAL_InterruptWakeup} -->
```mermaid
stateDiagram-v2
    [*] --> Uninitialized
    Uninitialized --> Ready: init
    Ready --> Busy: read / write / control
    Busy --> Ready: complete
    Ready --> Error: fault
    Error --> Ready: reset
```

### 4.3 内部シーケンス (RSP パーサとデバッグキュー)
<!-- traceability: {RSPMinimalSet} {RSP_Transport_Selectable} -->
本コンポーネントは、ホスト PC 上の GDB / LLDB / VSCode デバッガと通信するための GDB RSP パケット物理処理を担当する：

1. **RSP パケット受信**: UART または RTT ドライバ経由でシリアルデータ（`$<packet-data>#<checksum>`）を受信する。 `{RSP_Transport_Selectable}`
2. **パケット検証 & ACK**: 2桁の 16進チェックサムを検証し、一致すれば `+`（ACK）、不一致なら `-`（NACK）を即座に応答する。
3. **コマンド解析 (RSP Parser)**:
   - `$g` / `$G`: レジスタ一括読み出し / 書き込み
   - `$m<addr>,<len>` / `$M<addr>,<len>:<data>`: ゲストメモリ読み出し / 書き込み
   - `$s` / `$c`: シングルステップ実行 / 実行再開（Continue）
   - `$Z0,<addr>,<kind>` / `$z0,<addr>,<kind>`: ソフトウェアブレークポイント設定 / 解除
   - `$?`: 停止理由問い合わせ (Stop Reply)
4. **デバッグコマンドキュー投入**: 解析済みコマンドを `debug_command` 構造体に変換し、`debug_command_queue` へ Push。デバッガタスクがこれを Pop して vSoC / インタープリタ / JIT の実行を制御する。 `{RSPMinimalSet}`

```mermaid
sequenceDiagram
    participant Host as GDB / VSCode
    participant UART as UART/RTT Driver
    participant RSP as RSP Parser (Tier3)
    participant Q as debug_command_queue
    participant Dbg as Debugger Task (Tier 2)

    Host->>UART: Send "$g#67" (Read Registers)
    UART->>RSP: Raw Packet Bytes
    RSP->>RSP: Verify Checksum & Parse
    RSP-->>Host: Send "+" (ACK)
    RSP->>Q: Push(CMD_READ_REGISTERS)
    Note over Q,Dbg: IPC Notification / Task Wakeup
    Dbg->>Q: Pop Command & Read CPU Context
    Dbg->>UART: Send "$<reg-values>#<cksum>"
```

## 5. インターフェース定義

### 5.1 物理実装の勘所・不変条件
<!-- traceability: {HAL_Interface} {IPC_ZeroCopy} -->
[`hal_dispatch.md`](docs/components/tier2_runtime/hal_dispatch.md) の `{HAL_Interface}` で定義された契約API（`read`, `write`, `transfer`, `acquire_buffer`）を、以下の物理不変条件に従って実装する。

**静的固定長バッファプールの境界厳格検査 (`GOTCHA-HAL-01`)**:
`acquire_buffer`（`HalBufferPool`）は、固定サイズスロット（`FB_CONF_HAL_BUFFER_SIZE` = 256 バイト）の静的プールからバッファを切り出す。
**設計理由と不変条件**: 要求サイズが 256 バイトを超過した場合（`size > FB_CONF_HAL_BUFFER_SIZE`）は即座に `HAL_ERROR_INVALID_SIZE` で拒絶する。また、バッファ解放時（`release_buffer`）は呼び出し元タスク ID が割り当て時の所有タスク ID と一致することを厳格に検査し、不一致時は `HalBufferTrap` により即時停止させる。これにより、隣接する固定長スロットの汚染や不正解放を完全に防止する。

**UART トランスポートの双方向独立性 (`GOTCHA-HAL-02`)**:
UART デバイスドライバにおける送信リングバッファと受信リングバッファは、メモリ領域・ポインタ共に完全に独立したデータ構造として管理される。送受信でバッファや状態変数を不用意に共有・使い回すことを禁止し、全二重シリアル通信時における送受信ポインタ競合やデータ化けを防止する。

**単調増加タイマーの差分計算安全性 (`GOTCHA-HAL-03`)**:
32-bit ハードウェアカウンタ（SysTick / タイマー）による経過時間計測は、絶対時刻比較（`t2 > t1`）ではなく、必ず符号なし差分減算（`elapsed = t2 - t1`）により評価する。32-bit カウンタが 0xFFFFFFFF から 0x00000000 へラップアラウンドした場合であっても、2の補数演算のモジュロ代数によりアンダーフロー減算が正しく正確な経過時間を導出し、タイマーの単調増加性を保証する。

### 5.4 RSP デバッグトランスポート仕様
<!-- traceability: {RSP_Transport_Selectable} -->
UART および SEGGER RTT の双方において同一の RSP パケットエンコード/デコード処理を提供し、デバッガタスク（Tier 2）との間で透過的なリモートデバッグを実現する。

## 6. 制約達成の方策

### 6.1 メモリ制約と方策
<!-- traceability: {META_ConfigurableSystem} -->
- **目標**: 通信バッファによるメモリ圧迫を防止する。
- **方策**: `{META_ConfigurableSystem}` バッファ数とサイズをコンパイル時に固定し、**vMMIOの動的領域 (`DYNAMIC`)** に配置する。

### 6.2 安全性制約と方策
<!-- traceability: {Challenge_InterruptSafety} -->
- **目標**: 割り込みによる実行コンテキストの破壊を防止する。
- **方策**: `{Challenge_InterruptSafety}` 割り込みハンドラ内ではフラグセットのみを行い、実際のデータ処理はタスクのコンテキストで実行する。

## 7. 形式検証・テスト仕様との対応

### 7.1 検証対象の不変条件
- **非同期割り込み境界分離**: ISR からタスク状態を直接変更せずキュー経由で安全にディスパッチすること（`interrupt_boundary_model.py` の `isr_does_not_update_task_state_directly` および `interrupt_event_reaches_scheduler_boundary`）。
- **固定長スロット境界保護**: 要求サイズが 256 バイトを超える場合の即時拒絶（`GOTCHA-HAL-01`）。

### 7.2 テスト仕様書との連携
本コンポーネントの物理実装テストケース（TEST-HAL-03, TEST-HAL-05〜TEST-HAL-08, GOTCHA-HAL-01〜03）は、[`platform_driver_test_spec.md`](docs/components/tier3_platform/tests/platform_driver_test_spec.md) を正本として定義する。HAL契約レベルのテストケース（TEST-HAL-01, TEST-HAL-02, TEST-HAL-04, TEST-HAL-09〜TEST-HAL-13）は [`hal_dispatch_test_spec.md`](docs/components/tier2_runtime/tests/hal_dispatch_test_spec.md) を参照する。
