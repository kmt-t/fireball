# システムコンフィグマクロ一覧

## 1. 概要
Fireballハイパーバイザの動作パラメータを定義するコンパイル時設定マクロの一覧である。これらのマクロは `inc/fireball_config.hxx` で定義される。 `{ConfigurableSystem}`

## 2. マクロ一覧

### 2.1 メモリ管理 (Memory Management)
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_KERNEL_HEAP_SIZE` | COOSカーネルヒープのサイズ | `8192` | `{IndependentHeap}` |
| `FB_CONF_RUNTIME_HEAP_SIZE` | WASMランタイムヒープのサイズ | `4096` | `{IndependentHeap}` |
| `FB_CONF_SUBSYSTEM_HEAP_SIZE` | サブシステムヒープのサイズ | `4096` | `{IndependentHeap}` |
| `FB_CONF_SERVICE_HEAP_SIZE` | Tier1サービスヒープのサイズ | `4096` | `{IndependentHeap}` |
| `FB_CONF_GUEST_HEAP_SIZE` | ゲストモジュールヒープのサイズ | `24576` | `{IndependentHeap}` |

### 2.2 IPCルータ (IPC Router)
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_ROUTER_MAX_SERVICES` | 登録可能な最大サービス数 | `16` | `{StaticScalability}` |
| `FB_CONF_ROUTER_ROLE_MATRIX` | ロールベースのアクセス制御マトリックス | `constexpr`定義 | `{RoleBasedAccessControl}` |

### 2.3 HAL (Hardware Abstraction Layer)
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_HAL_MAX_DEVICES` | 管理可能な最大デバイス数 | `8` | `{ConfigurableSystem}` |
| `FB_CONF_HAL_BUFFER_SIZE` | デバイス通信用バッファの最大サイズ | `1024` | `{ConfigurableSystem}` |
| `FB_CONF_HAL_MAX_BUFFERS` | デバイス通信用バッファの最大数 | `4` | `{ConfigurableSystem}` |

### 2.4 vSoC / vMMIO
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_JIT_CACHE_SIZE` | JITキャッシュサイズ (Active/Old合計) | `4096` | `{JIT_DoubleBuffer_Cache}` |
| `FB_CONF_VMMIO_ALLOWED_ADDRS` | ゲストからのアクセスを許可する物理アドレス範囲 | `constexpr`定義 | `{RestrictedPhysicalAccess}` |

### 2.5 ロギング (Logging)
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_LOG_BUFFER_SIZE` | ログメッセージ保持用のバッファサイズ | `2048` | `{BufferedLogging}` |

### 2.6 デバッガ (Debugger)
| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_DEBUG_MAX_BREAKPOINTS` | 最大ブレークポイント数 | `8` | `{ConfigurableSystem}` |
| `FB_CONF_DEBUG_PACKET_SIZE` | RSPパケットバッファサイズ | `2048` | `{Challenge_DebuggerResource}` |
