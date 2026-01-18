# システムコンフィグ仕様

## コンセプト

Fireballハイパーバイザは、リソース制約の厳しい組み込み環境で動作するため、メモリサイズや最大リソース数をコンパイル時に固定する設計を採用する。設定はヘッダファイル形式のコンフィグファイル（`inc/fireball_config.hxx`）内のマクロ定義によって行われる。 `{ConfigurableSystem}`

## 構成要素

コンフィグ項目は以下のカテゴリに分類される。

1. **メモリ管理**: ヒープパーティションサイズ、コルーチンスタック設定
2. **IPCルータ**: 最大サービス数、アクセス制御マトリックス
3. **HAL**: 最大デバイス数、通信バッファ設定
4. **ロギング**: ログバッファサイズ
5. **vSoC / vMMIO**: 物理アドレスアクセス許可範囲
6. **サービス**: ゲストごとのロードサービス定義

## コンフィグ項目一覧

### 1. メモリ管理 (Memory Management)

| 項目名 | 説明 | 制約・デフォルト値 | 導出元 |
|---|---|---|---|
| `COOS_KERNEL_HEAP_SIZE` | COOSカーネルヒープのサイズ | 2.0KB - 4.0KB | `{IndependentHeap}` |
| `WASM_RUNTIME_HEAP_SIZE` | WASMランタイムヒープのサイズ | 2.0KB - 8.0KB | `{IndependentHeap}` |
| `SUBSYSTEM_HEAP_SIZE` | サブシステムヒープのサイズ | 2.0KB - 8.0KB | `{IndependentHeap}` |
| `TIER1_SERVICE_HEAP_SIZE` | Tier1サービスヒープのサイズ | 2.0KB - 8.0KB | `{IndependentHeap}` |
| `GUEST_MODULE_HEAP_SIZE` | ゲストモジュールヒープのサイズ | 24KB - 残余 | `{IndependentHeap}` |
| `CORO_STACK_SIZE` | 1コルーチンあたりのスタックサイズ | 1KB | `{IndependentHeap}` |
| `MAX_CORO_COUNT` | 最大コルーチン数 | 8 - 16 | `{IndependentHeap}` |

### 2. IPCルータ (IPC Router)

| 項目名 | 説明 | 制約・デフォルト値 | 導出元 |
|---|---|---|---|
| `ROUTER_MAX_SERVICES` | 登録可能な最大サービス数 | 静的配列サイズを決定 | `{StaticScalability}` |
| `ROUTER_ROLE_MATRIX` | ロールベースのアクセス制御マトリックス | constexpr定義 | `{RoleBasedAccessControl}` |

### 3. HAL (Hardware Abstraction Layer)

| 項目名 | 説明 | 制約・デフォルト値 | 導出元 |
|---|---|---|---|
| `HAL_MAX_DEVICES` | 管理可能な最大デバイス数 | 静的配列サイズを決定 | `{ConfigurableSystem}` |
| `HAL_BUFFER_SIZE` | デバイス通信用バッファの最大サイズ | ブロックサイズに依存 | `{ConfigurableSystem}` |
| `HAL_MAX_BUFFERS` | デバイス通信用バッファの最大数 | 静的確保 | `{ConfigurableSystem}` |

### 4. ロギング (Logging)

| 項目名 | 説明 | 制約・デフォルト値 | 導出元 |
|---|---|---|---|
| `LOG_BUFFER_SIZE` | ログメッセージ保持用のバッファサイズ | 静的確保 | `{ConfigurableSystem}` |

### 5. vSoC / vMMIO

| 項目名 | 説明 | 制約・デフォルト値 | 導出元 |
|---|---|---|---|
| `VMMIO_ALLOWED_ADDRS` | ゲストからのアクセスを許可する物理アドレス範囲 | アドレス範囲のリスト | `{RestrictedPhysicalAccess}` |

### 6. サービス (Services)

| 項目名 | 説明 | 制約・デフォルト値 | 導出元 |
|---|---|---|---|
| `GUEST_LOAD_SERVICES` | ゲストアプリケーションにロードするサービスのリスト | URIのリスト | `{ConfigurableSystem}` |

## 非機能制約達成のための方策

- **メモリ効率**: すべてのコンフィグ項目はマクロまたは `constexpr` として定義され、実行時のメモリ消費を最小限に抑える。
- **安全性**: メモリサイズやアクセス許可範囲をコンパイル時に固定することで、実行時の動的な設定変更による脆弱性を排除する。
