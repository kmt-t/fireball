# システムコンフィグマクロ一覧

## 1. 概要
<!-- traceability: {ConfigurableSystem} -->
Fireballハイパーバイザの動作パラメータを定義するコンパイル時設定マクロの一覧である。これらのマクロは `inc/fireball_config.hxx` で定義される。 `{ConfigurableSystem}`

## 2. マクロ一覧

TODO(Phase 1): ATC抽出 - 各コンフィグ値の有効範囲、メモリサイズ上限との整合に関する静的アサーション条件を明確に定義すること。

### 2.1 メモリ管理
<!-- traceability: {ConsolidatedHeap} {IndependentHeap} {StrictMemoryLimit} -->
| マクロ名 | 説明 | 標準 (20KB) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_TASK_HEAP_SIZE` | COOS/タスク統合ヒープのサイズ | `8192` | `{ConsolidatedHeap}` |
| `FB_CONF_RUNTIME_HEAP_SIZE` | ホスト(WASMランタイム)ヒープのサイズ | `4096` | `{IndependentHeap}` |
| `FB_CONF_GUEST_RAM_SIZE` | ゲストRAMのサイズ | `8192` | `{StrictMemoryLimit}` |

### 2.2 IPCルータ
<!-- traceability: {StaticScalability} {RoleBasedAccessControl} -->
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_ROUTER_MAX_SERVICES` | 登録可能な最大サービス数 | `16` | `{StaticScalability}` |
| `FB_CONF_ROUTER_ROLE_MATRIX` | ロールベースのアクセス制御マトリックス | `constexpr`定義 | `{RoleBasedAccessControl}` |

##### ロールベースアクセス制御の定義
サービス要求元のタスクロールとURIの対応関係を以下のように静的なロールマトリックスとして定義する。 `{RoleBasedAccessControl}`

```python
# ロールマトリックスの定義例 (Python表現)
FB_CONF_ROUTER_ROLE_MATRIX = {
    # "サービスURI": [アクセスを許可するロール名のリスト]
    "fireball://system/log": ["Kernel", "Driver", "App"],
    "fireball://system/power": ["Kernel"],
    "fireball://driver/gpio": ["Kernel", "Driver"],
}
```

### 2.3 HAL
<!-- traceability: {ConfigurableSystem} -->
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_HAL_MAX_DEVICES` | 管理可能な最大デバイス数 | `8` | `{ConfigurableSystem}` |
| `FB_CONF_HAL_BUFFER_SIZE` | デバイス通信用バッファの最大サイズ | `256` | `{ConfigurableSystem}` |
| `FB_CONF_HAL_MAX_BUFFERS` | デバイス通信用バッファの最大数 | `4` | `{ConfigurableSystem}` |

### 2.4 vSoC / vMMIO
<!-- traceability: {JIT_DoubleBuffer_Cache} {FastAddressCheck} {StrictMemoryLimit} {vMMIO_Isolation} {ConfigurableSystem} {RestrictedPhysicalAccess} -->
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_JIT_CACHE_SIZE` | JITキャッシュサイズ (Active/Old合計) | `4096` | `{JIT_DoubleBuffer_Cache}` |
| `FB_CONF_GUEST_RAM_BASE` | ゲストRAMの開始アドレス | `0x00000000` | `{FastAddressCheck}` |
| `FB_CONF_GUEST_RAM_SIZE` | ゲストRAMのサイズ | `8192` | `{StrictMemoryLimit}` |
| `FB_CONF_VMMIO_BASE` | vMMIO領域の開始アドレス | `0x40000000` | `{vMMIO_Isolation}` |
| `FB_CONF_VMMIO_MAX_REGIONS` | 登録可能な最大vMMIO領域数 | `8` | `{ConfigurableSystem}` |
| `FB_CONF_VMMIO_ALLOWED_ADDRS` | ゲストからのアクセスを許可する物理アドレス範囲 | `constexpr`定義 | `{RestrictedPhysicalAccess}` |

##### 物理アクセス許可範囲の定義
ゲストからのアクセスが許可される物理アドレス範囲は以下のように構造化して静的に定義される。 `{RestrictedPhysicalAccess}`

```python
# 物理アクセス制限用の定義例 (Python表現)
FB_CONF_VMMIO_ALLOWED_ADDRS = [
    # (開始物理アドレス, 終了物理アドレス) のペアで定義する
    (0x40000000, 0x4000FFFF),  # GPIO領域
    (0x40010000, 0x4001FFFF),  # UART領域
    (0x80000000, 0x807FFFFF),  # ゲスト物理RAM
]
```

### 2.5 ロギング
<!-- traceability: {BufferedLogging} -->
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_LOG_BUFFER_SIZE` | ログメッセージ保持用のバッファサイズ | `512` | `{BufferedLogging}` |

### 2.6 デバッガ
<!-- traceability: {ConfigurableSystem} {Challenge_DebuggerResource} -->
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_DEBUG_MAX_BREAKPOINTS` | 最大ブレークポイント数 | `8` | `{ConfigurableSystem}` |
| `FB_CONF_DEBUG_PACKET_SIZE` | RSPパケットバッファサイズ | `1024` | `{Challenge_DebuggerResource}` |

### 2.7 型定義・予約値
<!-- traceability: {ConsolidatedHeap} {IndependentHeap} {StrictMemoryLimit} {StaticScalability} {RoleBasedAccessControl} {ConfigurableSystem} {JIT_DoubleBuffer_Cache} {FastAddressCheck} {vMMIO_Isolation} {RestrictedPhysicalAccess} {BufferedLogging} {Challenge_DebuggerResource} -->

タスク識別子 `task_id` はシステム全体（COOS・IPCルータ・vMMIO）で共通して使用される識別子型である。値域を明示することで、予約値との重複や型キャストミスをコンパイル時に検出できる。

#### task_id 型

| マクロ名 | 説明 | 値 | 備考 |
| :--- | :--- | :--- | :--- |
| `FB_TASK_ID_T` | タスクIDの基底型 | `uint8_t` | 有効値域 `1`〜`FB_CONF_MAX_TASKS`。0 と 0xFF は予約済み |
| `FB_TASK_ID_INVALID` | 未割り当て・無効を示す予約値 | `0` | `vmmio_perm_table` の `owner_id` 初期値。「誰も所有していない」を表す |
| `FB_TASK_ID_FLIGHT` | 所有権移譲中（飛行中）を示す予約値 (FLIGHT_SENTINEL) | `0xFF` | IPCルータが Revoke→Grant シーケンス中にセットする。この値の間は vMMIO がアクセスを拒否する。`FB_CONF_MAX_TASKS` は必ず `0xFE` 以下でなければならない |

#### 最大タスク数制約
<!-- traceability: {StaticScalability} -->

| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_MAX_TASKS` | 同時実行可能な最大タスク数 | `16` | `{StaticScalability}`。`FB_TASK_ID_FLIGHT (0xFF)` との衝突を防ぐため `≤ 254` を静的アサートで保証すること |

##### 最大タスク数のコンパイル時検証
同時実行タスクの上限を定義し、予約値との競合を防ぐためのコンパイル時制約条件。 `{StaticScalability}`

```python
# コンパイル時の検証ルール (Python表現)
assert FB_CONF_MAX_TASKS <= 254, "FB_CONF_MAX_TASKS must be <= 254 to prevent collision with FB_TASK_ID_FLIGHT (0xFF)"
```

