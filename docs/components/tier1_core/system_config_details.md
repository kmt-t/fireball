/# システムコンフィグマクロ一覧

## 1. 概要
<!-- traceability: {META_ConfigurableSystem} -->
Fireballハイパーバイザの動作パラメータを定義するコンパイル時設定マクロの一覧である。これらのマクロは `inc/fireball_config.hxx` で定義される。 `{META_ConfigurableSystem}`

## 2. マクロ一覧


### 2.1 メモリ管理
<!-- traceability: {GLOBAL_IndependentHeap} {GLOBAL_StrictMemoryLimit} -->
| マクロ名 | 説明 | デフォルト値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_TASK_HEAP_SIZE` | 各VM/タスクに対してコンパイル時に固定された、個別に静的割り当てされる独立メモリプールのサイズ（動的ヒープアロケーションは一切行わない） | `8192` | `{GLOBAL_IndependentHeap}` |
| `FB_CONF_RUNTIME_HEAP_SIZE` | ホスト(WASMランタイム)実行専用 of 独立メモリプールのサイズ（動的ヒープアロケーションは一切行わない） | `4096` | `{GLOBAL_IndependentHeap}` |

##### メモリプールの分離設計
VM（ゲストタスク）ごとのプール領域（`FB_CONF_TASK_HEAP_SIZE`）およびホスト用のプール領域（`FB_CONF_RUNTIME_HEAP_SIZE`）は、それぞれが物理的・領域的に完全に別個の静的メモリプールとして分離されて管理される。これらはコンパイル時に固定サイズ領域として確保され、実行時の動的なヒープアロケーション（`malloc` / `new`）は一切行われない。これにより、あるVMのメモリ不足や暴走がホストランタイムや他のVMを道連れにしてクラッシュすることを防ぐ。システム全体の物理メモリ総領域は物理的に一括配置（Consolidated Memory Allocation）されるが、その内部はコンパイル時に各VMおよびホスト用の固定サイズプールに厳密にパーティショニング（分離）される。これにより、物理メモリ資源の静的一括管理と各実行コンテキスト間の強固なメモリ分離（`GLOBAL_IndependentHeap`）を両立させる。 `{GLOBAL_IndependentHeap}`

### 2.2 IPCルータ
<!-- traceability: {GLOBAL_StaticScalability} {RoleBasedAccessControl} -->
| マクロ名 | 説明 | デフォルト値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_ROUTER_MAX_SERVICES` | 登録可能な最大サービス数 | `16` | `{GLOBAL_StaticScalability}` |
| `FB_CONF_ROUTER_ROLE_MATRIX` | ロールベースのアクセス制御マトリックス | `constexpr`構造体配列 | `{RoleBasedAccessControl}` |

##### ロールベースアクセス制御の定義
サービス要求元のタスクロールとURIの対応関係を以下のように静的なアクセス制御エントリ（またはロールマトリックス）として定義し、C++コンパイル時に固定する。 `{RoleBasedAccessControl}`

```text
// inc/fireball_config.hxx での定義形式 (C++23)
namespace fireball {
    inline constexpr size_t FB_CONF_MAX_ROLES_PER_SERVICE = 4;

    struct role_access_entry {
        std::string_view service_uri;
        std::array<std::string_view, FB_CONF_MAX_ROLES_PER_SERVICE> allowed_roles;
    };

    inline constexpr std::array<role_access_entry, 3> FB_CONF_ROUTER_ROLE_MATRIX {{
        {"fireball://system/log",   {"Kernel", "Driver", "App", ""}},
        {"fireball://system/power", {"Kernel", "", "", ""}},
        {"fireball://driver/gpio",  {"Kernel", "Driver", "", ""}}
    }};
}
```

※ **空文字列 `""` のセマンティクス**: `allowed_roles` の配列内に記述された空文字列 `""` は、割り当てられた権限ロールが存在しないこと（アクセス権限なし / None）を意味する。このスロットに対するマッチングは常に不合格となり、明示的にアクセスが拒否される。アクセス拒否時には、IPCルータは `ERR_ACCESS_DENIED` を返し、メッセージ送信要求を破棄して所有権の移譲を Rollback する。


### 2.3 HAL
<!-- traceability: {META_ConfigurableSystem} -->
| マクロ名 | 説明 | デフォルト値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_HAL_MAX_DEVICES` | 管理可能な最大デバイス数 | `8` | `{META_ConfigurableSystem}` |
| `FB_CONF_HAL_BUFFER_SIZE` | デバイス通信用バッファの最大サイズ (Bytes) | `256` | `{META_ConfigurableSystem}` |
| `FB_CONF_HAL_MAX_BUFFERS` | デバイス通信用バッファの最大数 | `4` | `{META_ConfigurableSystem}` |

### 2.4 vSoC / vMMIO {VERIFY_FORMAL}
<!-- traceability: {JIT_MultiBuffer_Cache} {FastAddressCheck} {GLOBAL_StrictMemoryLimit} {vMMIO_Isolation} {META_ConfigurableSystem} {META_RestrictedPhysicalAccess} -->
| マクロ名 | 説明 | デフォルト値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_WASM_PAGE_SIZE` | WASM標準論理ページサイズ (64KB, 65,536 Bytes) | `65536` | `{FastAddressCheck}` |
| `FB_CONF_JIT_CACHE_SIZE` | JITキャッシュサイズ (合計バイト数: 2KB x 3面) | `6144` | `{JIT_MultiBuffer_Cache}` |
| `FB_CONF_JIT_NUM_BUFFERS` | JITキャッシュバッファ面数 (3: トリプルバッファ推奨) | `3` | `{JIT_MultiBuffer_Cache}` `{JIT_OldestOnly_Promote}` |
| `FB_CONF_GUEST_RAM_BASE` | ゲストRAMの開始アドレス（アライメント検証と高速境界チェックのため、必ず64KB境界に配置） | `0x00000000` | `{FastAddressCheck}` |
| `FB_CONF_GUEST_RAM_SIZE` | ゲストRAMの物理割り当てサイズ（RAM<64KB環境向け部分ページ。デフォルト: 8KB = 8192 Bytes） | `8192` | `{GLOBAL_StrictMemoryLimit}` `{FastAddressCheck}` |
| `FB_CONF_VMMIO_BASE` | vMMIO領域の開始アドレス (Bit 31 == 1, 2段階ダイレクトデコード) | `0x80000000` | `{vMMIO_Isolation}` |
| `FB_CONF_VMMIO_MAX_REGIONS` | 登録可能な最大vMMIO領域数 | `8` | `{META_ConfigurableSystem}` |
| `FB_CONF_VMMIO_ALLOWED_ADDRS` | ゲストからのアクセスを許可する物理アドレス範囲 | `constexpr`構造体配列 | `{META_RestrictedPhysicalAccess}` |

##### 物理アクセス許可範囲の定義
ゲストからのアクセスが許可される物理アドレス範囲は以下のように構造化してコンパイル時定数として定義される。 `{META_RestrictedPhysicalAccess}`

```text
// inc/fireball_config.hxx での定義形式 (C++23)
namespace fireball {
    struct vmmio_range_entry {
        uintptr_t start_addr;
        uintptr_t end_addr;
    };

    inline constexpr std::array<vmmio_range_entry, 3> FB_CONF_VMMIO_ALLOWED_ADDRS {{
        {0x40000000, 0x4000FFFF},  // GPIO領域
        {0x40010000, 0x4001FFFF},  // UART領域
        {0x80000000, 0x807FFFFF}   // ゲスト物理RAM
    }};
}
```

### 2.5 ロギング
<!-- traceability: {BufferedLogging} -->
| マクロ名 | 説明 | デフォルト値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_LOG_BUFFER_SIZE` | ログメッセージ保持用のバッファサイズ (Bytes) | `512` | `{BufferedLogging}` |

### 2.6 デバッガ
<!-- traceability: {META_ConfigurableSystem} {Challenge_DebuggerResource} -->
| マクロ名 | 説明 | デフォルト値 | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_DEBUG_MAX_BREAKPOINTS` | 最大ブレークポイント数 | `8` | `{META_ConfigurableSystem}` |
| `FB_CONF_DEBUG_PACKET_SIZE` | RSPパケットバッファサイズ | `1024` | `{Challenge_DebuggerResource}` |

### 2.7 型定義・予約値
<!-- traceability: {GLOBAL_IndependentHeap} {GLOBAL_StrictMemoryLimit} {GLOBAL_StaticScalability} {RoleBasedAccessControl} {META_ConfigurableSystem} {JIT_MultiBuffer_Cache} {FastAddressCheck} {vMMIO_Isolation} {META_RestrictedPhysicalAccess} {BufferedLogging} {Challenge_DebuggerResource} -->

タスク識別子 `task_id` はシステム全体（COOS・IPCルータ・vMMIO）で共通して使用される識別子型である。値域を明示することで、予約値との重複や型キャストミスをコンパイル時に検出できる。

#### task_id 型

| マクロ名 | 説明 | 値 | 備考 |
| :--- | :--- | :--- | :--- |
| `FB_TASK_ID_T` | タスクIDの基底型 | `uint8_t` | 有効値域 `1`〜`FB_CONF_MAX_TASKS`。0 と 0xFF は予約済み |
| `FB_TASK_ID_INVALID` | 未割り当て・無効を示す予約値 | `0` | `vmmio_perm_table` の `owner_id` 初期値。「誰も所有していない」を表す |
| `FB_TASK_ID_FLIGHT` | 所有権移譲中（飛行中）を示す予約値 (FLIGHT_SENTINEL) | `0xFF` | IPCルータが Revoke→Grant シーケンス中にセットする。この値の間は vMMIO がアクセスを拒否する。`FB_CONF_MAX_TASKS` は必ず `0xFE` 以下でなければならない |

#### 最大タスク数制約
<!-- traceability: {GLOBAL_StaticScalability} -->

| マクロ名 | 説明 | デフォルト値 (例) | 導出元 |
| :--- | :--- | :--- | :--- |
| `FB_CONF_MAX_TASKS` | 同時実行可能な最大タスク数 | `16` | `{GLOBAL_StaticScalability}`。`FB_TASK_ID_FLIGHT (0xFF)` との衝突を防ぐため `≤ 254` を静的アサートで保証すること |

##### 最大タスク数のコンパイル時検証
同時実行タスクの上限を定義し、予約値との競合を防ぐためのコンパイル時制約条件。 `{GLOBAL_StaticScalability}`

```python
# コンパイル時の検証ルール (Python表現)
assert FB_CONF_MAX_TASKS <= 254, "FB_CONF_MAX_TASKS must be <= 254 to prevent collision with FB_TASK_ID_FLIGHT (0xFF)"
```

