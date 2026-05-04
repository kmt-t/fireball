# システムコンフィグマクロ一覧

## 1. 概要
Fireballハイパーバイザの動作パラメータを定義するコンパイル時設定マクロの一覧である。これらのマクロは `inc/fireball_config.hxx` で定義される。 `{ConfigurableSystem}`

## 2. マクロ一覧

TODO(Phase 1): ATC抽出 - 各コンフィグ値の有効範囲、メモリサイズ上限との整合に関する静的アサーション条件を明確に定義すること。

### 2.1 メモリ管理
| マクロ名 | 説明 | 標準 (20KB) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_TASK_HEAP_SIZE` | COOS/タスク統合ヒープのサイズ | `8192` | `{ConsolidatedHeap}` |
| `FB_CONF_RUNTIME_HEAP_SIZE` | ホスト(WASMランタイム)ヒープのサイズ | `4096` | `{IndependentHeap}` |
| `FB_CONF_GUEST_RAM_SIZE` | ゲストRAMのサイズ | `8192` | `{StrictMemoryLimit}` |

### 2.2 IPCルータ
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_ROUTER_MAX_SERVICES` | 登録可能な最大サービス数 | `16` | `{StaticScalability}` |
| `FB_CONF_ROUTER_ROLE_MATRIX` | ロールベースのアクセス制御マトリックス | `constexpr`定義 | `{RoleBasedAccessControl}` |

### 2.3 HAL
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_HAL_MAX_DEVICES` | 管理可能な最大デバイス数 | `8` | `{ConfigurableSystem}` |
| `FB_CONF_HAL_BUFFER_SIZE` | デバイス通信用バッファの最大サイズ | `256` | `{ConfigurableSystem}` |
| `FB_CONF_HAL_MAX_BUFFERS` | デバイス通信用バッファの最大数 | `4` | `{ConfigurableSystem}` |

### 2.4 vSoC / vMMIO
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_JIT_CACHE_SIZE` | JITキャッシュサイズ (Active/Old合計) | `4096` | `{JIT_DoubleBuffer_Cache}` |
| `FB_CONF_GUEST_RAM_BASE` | ゲストRAMの開始アドレス | `0x00000000` | `{FastAddressCheck}` |
| `FB_CONF_GUEST_RAM_SIZE` | ゲストRAMのサイズ | `8192` | `{StrictMemoryLimit}` |
| `FB_CONF_VMMIO_BASE` | vMMIO領域の開始アドレス | `0x40000000` | `{vMMIO_Isolation}` |
| `FB_CONF_VMMIO_MAX_REGIONS` | 登録可能な最大vMMIO領域数 | `8` | `{ConfigurableSystem}` |
| `FB_CONF_VMMIO_ALLOWED_ADDRS` | ゲストからのアクセスを許可する物理アドレス範囲 | `constexpr`定義 | `{RestrictedPhysicalAccess}` |

### 2.5 ロギング
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_LOG_BUFFER_SIZE` | ログメッセージ保持用のバッファサイズ | `512` | `{BufferedLogging}` |

### 2.6 デバッガ
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_DEBUG_MAX_BREAKPOINTS` | 最大ブレークポイント数 | `8` | `{ConfigurableSystem}` |
| `FB_CONF_DEBUG_PACKET_SIZE` | RSPパケットバッファサイズ | `1024` | `{Challenge_DebuggerResource}` |

### 2.7 型定義・予約値

タスク識別子 `task_id` はシステム全体（COOS・IPCルータ・vMMIO）で共通して使用される識別子型である。値域を明示することで、予約値との重複や型キャストミスをコンパイル時に検出できる。

#### task_id 型

| マクロ名 | 説明 | 値 | 備考 |
| :--- | :--- | :--- | :--- |
| `FB_TASK_ID_T` | タスクIDの基底型 | `uint8_t` | 有効値域 `1`〜`FB_CONF_MAX_TASKS`。0 と 0xFF は予約済み |
| `FB_TASK_ID_INVALID` | 未割り当て・無効を示す予約値 | `0` | `vmmio_perm_table` の `owner_id` 初期値。「誰も所有していない」を表す |
| `FB_TASK_ID_FLIGHT` | 所有権移譲中（飛行中）を示す予約値 (FLIGHT_SENTINEL) | `0xFF` | IPCルータが Revoke→Grant シーケンス中にセットする。この値の間は vMMIO がアクセスを拒否する。`FB_CONF_MAX_TASKS` は必ず `0xFE` 以下でなければならない |

#### 最大タスク数制約

| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_MAX_TASKS` | 同時実行可能な最大タスク数 | `16` | `{StaticScalability}`。`FB_TASK_ID_FLIGHT (0xFF)` との衝突を防ぐため `≤ 254` を静的アサートで保証すること |
