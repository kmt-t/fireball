# Subsystems & Services Design

**Version:** 0.1.0
**Date:** 2025-11-28
**Author:** Takuya Matsunaga

---

## Overview

**概要：** Fireball システムは、2 つのレイヤーで COOS コアの外部に機能を提供します：

1. **Subsystems**（ネイティブ実装、C++）：ROM/RAM 制約が厳しい基本機能
2. **Services**（WASM プラグイン、ユーザー提供）：アプリケーション固有の拡張機能

両者とも COOS カーネルから完全に独立し、IPC（型付きKey-Value形式 over CSP チャネル）で通信します。

**マイクロカーネル設計の要点：**

Fireball は、カーネルを最小限（4 つのコンポーネント）に保ち、ロギング、HAL、デバッグなどの固定機能は Subsystems として、カスタム機能は Services として独立させています。これにより：

- **カーネルの単純性**: スケジューラー、チャネル、メモリ管理、値追跡の 4 つだけに集中
- **拡張性**: ユーザーが WASM プラグイン（Services）を追加・カスタマイズ可能
- **テスト容易性**: Subsystems と Services は COOS に依存しないため、独立してテスト可能
- **プラットフォーム移植**: 各プラットフォームで Subsystems 実装を変更するだけで対応可能

**Subsystems の特性（本ドキュメントで扱う）：**
- **COOS 非依存**: COOS カーネルコンポーネント（co_sched、co_csp など）に直接依存しない
- **IPC 通信**: すべての通信は IPC（型付きKey-Value形式 over CSP チャネル）経由
- **命名規約**: `logger`、`hal`、`debugger`、`jit` など、`co_` プリフィックスなし
- **ネイティブ実装**: C++ で実装、ROM/RAM 効率を最優先
- **段階的実装**: Phase ごとに異なるサブシステムを実装（Phase 1: logger、Phase 2: hal、Phase 3～4: debugger、jit）

**Services の特性（plugin-system.md で詳細）：**
- **ユーザー実装**: C、Rust、AssemblyScript などで記述可能
- **WASM プラグイン**: Fireball ランタイム上で実行される拡張機能
- **ホットロード対応**: 実行中にプラグインの読み込み・アンロード可能
- **IPC 通信**: Subsystems や COOS との通信は IPC 経由
- **リッチな機能**: 計装、セキュリティサンドボックス、カスタム命令など

---

## 0. IPC Router (Dependency Injection Container & DI Hub)

### 目的（Purpose）

**概要：** IPC Router は、Subsystems & Services Layer の**依存性注入（DI）コンテナ兼通信ハブ**です。

**2つの責務：**

1. **DI コンテナ（上層による制御）**
   - Subsystem・Service の登録・検索
   - コンポーネント間の依存関係を上層（WASM Runtime、COOS）が注入
   - URI ベースのコンポーネント検索と動的バインディング
   - アクセス権管理と検証

2. **IPC 通信ハブ（スター型トポロジ）**
   - すべてのコンポーネント間通信を Router 経由で管理
   - 型付きKey-Value形式 エンコード/デコード
   - 複数通信モード対応（値ベース/参照ベース/型付きKey-Value形式）

**主な役割：**
- **コンポーネント登録**：Subsystem/Service を URI で管理
- **動的ルーティング**：URI → ルートID 変換、アクセス権検証
- **スター型トポロジ**：全コンポーネントから Router のみへの接続
- **通信モード分岐**：IPC メッセージ種別に応じた処理
- **隔離性保証**：Point-to-Point 直接通信を禁止

### アーキテクチャ（Architecture）

**DI コンテナとしてのRouter：**

```
┌─────────────────────────────────────┐
│  COOS Kernel / WASM Runtime（上層） │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ Component Registration       │   │
│  │ - register("wasi://stdio")   │   │
│  │ - register("io://uart/0")    │   │
│  │ - register("accel://blas")   │   │
│  └──────────────────────────────┘   │
└──────────────────┬──────────────────┘
                   │ (DI)
                   ▼
┌─────────────────────────────────────┐
│  IPC Router（DIコンテナ兼Hub）      │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ Component Registry           │   │
│  │ - uri → route_id mapping     │   │
│  │ - access control table       │   │
│  │ - binary search（URI lookup）│   │
│  └──────────────────────────────┘   │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ IPC Communication Hub        │   │
│  │ - StarTopology routing       │   │
│  │ - Multi-mode IPC             │   │
│  │ - 型付きKey-Value形式 encode/decode  │   │
│  └──────────────────────────────┘   │
└──────────────────┬──────────────────┘
        ┌─────────┼──────────┬──────────┐
        ▼         ▼          ▼          ▼
    [logger] [hal] [debugger] [Services]
```

**通信フロー（例：WASM が HAL を呼び出す）：**

```
WASM Runtime (Coroutine A)
  │
  ├─ Query: resolve_route("io://uart/0")
  │         ↓ Router verifies access
  │         ↓ Returns ルートID (access granted)
  │
  └─ Send IPC message to ルートID
           ↓ Router routes to HAL
           ↓ Decode 型付きKey-Value形式
           ↓
          [HAL] executes operation
           ↓
           └─ Send response back to ルートID
                  ↓ Router re-routes to Coroutine A
                  ↓ Encode result
                  ↓
            Coroutine A receives
```

### インターフェース（Interface）

**ファイル:** `inc/subsystems/router.hxx`

```cpp
namespace fireball { namespace subsystems {

// コンポーネント情報
typedef struct {
  std::string_view uri;              // e.g. "io://uart/0", "wasi://stdio"
  uint32_t route_id;                 // ルートID（内部識別子 = コルーチンID）
  uint16_t access_mask;              // アクセス制御ビット
} component_entry_t;

// IPC メッセージ（複数モード対応）
typedef struct {
  uint32_t source_route_id;          // 送信元のルートID
  uint32_t dest_route_id;            // 送信先のルートID
  uint16_t message_type;             // IPC メッセージ種別
  uint8_t  mode;                     // 0=POD, 1=co_value (ownership), 2=型付きKey-Value形式
  std::span<uint8_t> payload;        // モード別ペイロード
} ipc_message_t;

class ipc_router {
 public:
  // ─────────────────────────────────────
  // DI Container Interface（上層使用）
  // ─────────────────────────────────────

  // コンポーネント登録：上層（COOS or WASM Runtime）が下層の依存を注入
  void register_component(
    std::string_view uri,              // "io://uart/0", "wasi://stdio", etc
    uint32_t route_id,                 // ルートID（コルーチンID または サブシステムID）
    uint16_t access_mask = 0xFFFF);    // デフォルト：フルアクセス

  // URI からルートIDを動的解決（アクセス権検証付き）
  // 失敗時は std::nullopt
  std::optional<uint32_t> resolve_route(
    std::string_view uri,
    uint32_t requester_route_id);      // リクエスト元のルートID

  // アクセス権検証
  bool verify_access(
    uint32_t requester_route_id,
    std::string_view uri);

  // ─────────────────────────────────────
  // IPC Communication Interface
  // ─────────────────────────────────────

  // メッセージ送信（ルーティング・エンコード）
  bool send(const ipc_message_t& msg);

  // メッセージ受信（ルーティング・デコード）
  bool recv(uint32_t dest_route_id, ipc_message_t& msg);

  // ─────────────────────────────────────
  // Multi-Mode IPC Helpers
  // ─────────────────────────────────────

  // Mode 0: POD値ベース（小規模、所有権不要）
  // 制約：sizeof(data) < threshold（通常 256B以下）
  template<typename T>
  bool send_pod(uint32_t dest_route_id, const T& data);

  // Mode 1: 共有ヒープメモリ（大規模、所有権移譲）
  // co_value<T>を使用して、所有権を明示的に移譲
  template<typename T>
  bool send_shmem(
    uint32_t dest_route_id,
    co_value<T> shared_mem);           // co_value<T> により所有権移譲

  // Mode 2: 型付きKey-Value形式 ベース（柔軟、エンコード・デコード時間あり）
  std::vector<uint8_t> encode_to_kv(const void* data, size_t size);
  bool decode_from_kv(std::span<uint8_t> data, void* dest, size_t size);

  // ─────────────────────────────────────
  // Utility
  // ─────────────────────────────────────

  // 登録済みコンポーネント一覧
  std::vector<component_entry_t> list_components() const;

  // URI検索（二分検索）
  std::optional<component_entry_t> lookup_by_uri(std::string_view uri) const;
};

} } // namespace fireball { namespace subsystems
```

### メモリ構成（Memory Layout）

Router が P3（Subsystems Heap）に割り当てるメモリ：

```
Router Heap Usage (P3 内):
├── Component Registry (~512B)
│   └── 最大 32 エントリ（URI→ルートID マッピング）
│       各エントリ 16B（uri string_view + ルートID + access_mask）
│       → 二分検索対応（事前ソート）
│
├── Access Control Table (~256B)
│   └── 32 × (ルートID, access_mask) ペア
│
├── IPC Send/Receive Buffers (~256B)
│   ├── Mode 0 (POD): ~100B - 小規模PODデータ
│   ├── Mode 1 (co_value): ~64B - co_value<T>所有権メタデータ
│   └── Mode 2 (型付きKey-Value形式): ~92B - 型付きKey-Value形式バッファ
│
└── Router Metadata (~128B)
    └── Statistics, state flags, etc

合計：~1.4KB / P3 (4KB)
```

### 設計原則

1. **依存関係の反転**：下層（Router, HAL, logger）の仕様を上層（COOS, WASM Runtime）が決める
   - 上層がコンポーネント登録（DI注入）
   - 下層は受動的

2. **URI ベースのルーティング**：コンパイル時固定から動的へ
   - 例：`"io://uart/0"`、`"wasi://stdio"`、`"accel://blas"`
   - アクセス権も同時に検証

3. **複数 IPC モード**：用途に応じた効率化
   - POD（値）：高速、小データ向け
   - co_value（所有権移譲）：大容量データ向け
   - 型付きKey-Value形式：柔軟性重視

4. **二分検索による URI 検索**：事前にソート
   - O(log N) 検索時間
   - 静的なレジストリ

---

## IPC複数モード戦略（Multi-Mode IPC Strategy）

### 概要

IPC には 3 つのモードがあり、データサイズ・帯域幅・遅延のトレードオフに応じて使い分けます。

| モード | 対象 | 所有権 | 用途 | オーバーヘッド |
|--------|------|--------|------|------------|
| **Mode 0: POD（値）** | 構造体等PODデータ | 不要 | 小規模制御信号、ステータス | 低（コピー） |
| **Mode 1: co_value（共有ヒープ）** | 大規模バッファ（画像、バイナリ） | 必須 | read/write等の大容量 I/O | 中（所有権管理） |
| **Mode 2: 型付きKey-Value形式** | 柔軟なデータ型 | 不要 | ioctl、ログ出力 | 高（エンコード/デコード） |

### Mode 0: POD値ベース（小規模、高速）

**特徴：**
- 小規模 POD 構造体をそのままコピー転送
- 所有権管理不要（値セマンティクス）
- 実装が単純、オーバーヘッド最小

**制約：**
- `sizeof(data) ≤ 256B`（Router バッファサイズ）
- POD型のみ（トリビアルコピー可能）

**使用例：**

```cpp
// HAL ioctl のパラメータ (Mode 0)
struct {
  uint32_t baud_rate;
  uint8_t  data_bits;
  uint8_t  parity;
};

router.send_pod(uart_route_id, uart_config{115200, 8, 0});
```

### Mode 1: co_value（共有ヒープ、大容量）

**特徴：**
- 大規模バッファを共有ヒープ経由で転送
- `co_value<T>` による明示的な所有権移譲
- バッファ本体はコピーしない（ポインタのみ転送）

**前提条件：**

1. **共有ヒープ（Shared Heap）の存在**
   - COOS が管理する特別なメモリ領域（P3の一部または独立）
   - Subsystem と Service 間で共有可能
   - アクセス権管理あり

2. **所有権のライフサイクル**
   ```
   COOS malloc → co_value<T>取得
     ↓ 所有権移譲（co_value経由）
   → Router（一時保持）
     ↓ 所有権移譲
   → Subsystem（操作実施）
     ↓ co_value release時に自動dealloc
   ```

3. **メモリ管理ルール**
   - 送信側：`co_value<T> buf = shared_heap.allocate<T>(size)` で確保
   - Router：`send_shmem(dest_route_id, std::move(buf))` で所有権移譲
   - 受信側：受信時に所有権を得る
   - 所有権を持たなくなったら自動解放

**使用例：**

```cpp
// WASM Runtime が HAL に大容量バッファを送信 (Mode 1)
co_value<uint8_t[]> write_buffer =
  shared_heap.allocate<uint8_t[]>(4096);  // 4KB確保

// バッファにデータコピー
memcpy(write_buffer.get(), data, 4096);

// 所有権を移譲して送信
router.send_shmem(
  uart_route_id,
  std::move(write_buffer)  // 所有権移譲
);

// この後、write_buffer は無効（アクセス禁止）
```

**HAL受信側：**

```cpp
// HAL が受信
co_value<uint8_t[]> buffer = recv_shmem<uint8_t[]>(uart_route_id);

// 実際のI/O実行
uart_write(buffer.get(), buffer.size());

// 自動的に解放（スコープ脱出時）
```

### Mode 2: 型付きKey-Value形式（柔軟、エンコード必須）

**特徴：**
- JSON互換の構造化データ
- エンコード・デコードのオーバーヘッドあり
- 型の柔軟性あり（可変長、ネストされた構造など）

**所有権：**
- 不要（型付きKey-Value形式 バッファ自体は ephemeral）

**使用例：**

```cpp
// HAL ioctl のレスポンス (Mode 2)
struct ioctl_response {
  uint32_t status;
  uint32_t flags;
  std::string_view error_msg;
};

auto kv_data = router.encode_to_kv(&response, sizeof(response));
router.send_kv(requester_route_id, kv_data);
```

**ログ出力 (Mode 2)：**

```cpp
// logger への送信（Mode 2 型付きKey-Value形式固定）
struct log_event {
  log_level level;
  uint32_t timestamp;
  std::string_view message;
};

auto encoded = router.encode_to_kv(&event, sizeof(event));
router.send_kv(logger_route_id, encoded);
```

### 操作別モード選択ガイド

| 操作 | モード | 理由 |
|------|--------|------|
| **open** | Mode 2 (型付きKey-Value形式) | デバイスパス・オプションが可変長 |
| **close** | Mode 0 (POD) | デバイスハンドルのみ（数bytes） |
| **read** | Mode 1 (co_value) | 大容量バッファ読み込み（効率重視） |
| **write** | Mode 1 (co_value) | 大容量バッファ書き込み（効率重視） |
| **seek** | Mode 0 (POD) | offset, whence のみ（16bytes） |
| **ioctl** | Mode 2 (型付きKey-Value形式) | 可変パラメータ、柔軟性重視 |
| **ログ出力** | Mode 2 (型付きKey-Value形式) | 構造化ログ、可変長メッセージ |
| **デバッグ情報** | Mode 2 (型付きKey-Value形式) | 複雑な構造、ツール連携 |

### 共有ヒープ（Shared Heap）の設計

**目的：**
- Subsystem と Service 間で大容量データを効率的に転送
- ポインタベースの転送により、メモリコピーを最小化
- 所有権管理により、メモリリークを防止

**配置：**

```
P3 (Subsystems Heap) の一部、または独立
├── Subsystem Metadata (~1.5KB)
│   ├── Router (1.4KB)
│   ├── logger (2.0KB)
│   ├── hal (1.8KB)
│   └── debugger [Phase 3]
│
└── Shared Heap (~6KB, 共有領域)
    ├── 大容量バッファプール
    └── co_value<T> メタデータ
```

**ルール：**

1. **割り当て**
   - COOS が共有ヒープを管理
   - `co_value<T> buf = shared_heap.allocate<T>(size)` で確保
   - 失敗時は `std::nullopt`

2. **移譲**
   - `co_value<T>` で所有権を明示的に move
   - 送信側は move 後アクセス禁止（コンパイル時チェック）

3. **受信**
   - 受信側が所有権を取得
   - スコープ脱出で自動解放

4. **リサイクル**
   - deallocate されたメモリは、shared_heap に戻される
   - 次の allocate で再利用可能

**メモリプール戦略：**

```cpp
class shared_heap {
  // 事前割り当て：最大 64 エントリ（各 4KB）
  struct pool_entry {
    co_value<void> data;
    uint32_t size;
    bool in_use;
  };

  // ルーティングテーブル：owner_route_id → in_use
};
```

---

## 1. Logger Subsystem

### 目的（Purpose）

**概要：** logger サブシステムは、Fireball システム全体のイベント記録を一元管理します。COOS カーネル内の各コンポーネント（スケジューラー、チャネル、メモリ管理）や他のサブシステムがイベントを記録し、ロギングバックエンドを通じて外部へ出力します。

**主な役割：**
- **イベント記録**: コルーチンのライフサイクル（spawn、resume、suspend、complete）、チャネル操作（send、recv）、メモリ割り当て失敗などのイベント
- **バッファリング**: リングバッファにイベントを蓄積し、メモリ割り当てなしで動作
- **フォーマット変換**: イベントを 型付きKey-Value形式 バイナリ形式にエンコード
- **複数バックエンド対応**: UART、ファイル、ネットワークなど、異なる出力先をサポート

### インターフェース（Interface）

**ファイル:** `inc/subsystems/logger.hxx`

```cpp
namespace fireball { namespace subsystems {

enum class log_level {
  DEBUG = 0,
  INFO = 1,
  WARN = 2,
  ERROR = 3,
  FATAL = 4
};

typedef struct {
  log_level level;
  uint32_t timestamp;
  uint32_t source_module_id;
  std::string_view message;
  // Additional context fields
} logger_event_t;

class logger_service {
 public:
  virtual ~logger_service() = default;

  // Log event with context
  virtual void log(const logger_event_t& event) = 0;

  // Flush pending logs
  virtual void flush() = 0;

  // Register output backend
  virtual void register_backend(
    std::function<void(const logger_event_t&)> backend) = 0;
};

} } // namespace fireball { namespace subsystems
```

### 実装（Implementation）

**ロケーション:** `src/subsystems/logger/`

**実装の特徴：**
- **リングバッファ**: 固定サイズのリングバッファでイベントを蓄積、メモリ割り当てなし
- **UART バックエンド**: デフォルトは UART（ボーレート 115200、8N1）へのテキスト出力
- **タイムスタンプ生成**: co_sched の tick カウンターから相対時間を取得
- **型付きKey-Value形式 フォーマット**: バイナリ形式でのコンパクト化、JSON への変換可能
- **ノンブロッキング**: イベント記録時にブロック不可（スケジューラー安全性）

### COOS との統合（Integration with COOS）

**通信モデル：** COOS カーネルコンポーネントは、logger サブシステムと直接リンクせず、CSP チャネル経由でイベントを送信します。これにより、logger の実装変更がカーネルに波及しません。

**イベントフロー：**
- `co_sched` → logger: コルーチンライフサイクルイベント
- `co_csp` → logger: チャネル送受信イベント
- `co_mem` → logger: メモリ割り当て失敗イベント
- WASM インタプリタ → logger: 実行エラーイベント

**コード例：** COOS コンポーネント内でのロギング呼び出し

```cpp
// In co_mem.cpp
void allocate_in_partition(...) {
  if (part->current_usage + size > part->max_size) {
    logger_event error = {
      .level = LOG_ERROR,
      .type = LOG_ERROR_HEAP_OVERFLOW,
      .module_id = part->module_id,
      .requested = size
    };
    get_logger()->log(error);
    kernel->terminate_module(part->module_id);
  }
}
```

### Phase 1 実装タスク

- [ ] logger インターフェース定義
- [ ] リングバッファ実装（イベント蓄積）
- [ ] UART バックエンド実装
- [ ] タイムスタンプ生成機構
- [ ] COOS コンポーネントとの統合テスト

---

## 2. HAL Subsystem

### 目的（Purpose）

**概要：** HAL（Hardware Abstraction Layer）サブシステムは、POSIX風の統一インターフェース（open/close/read/write/seek/ioctl）を通じて、物理デバイスへのアクセスを抽象化します。複雑な操作は ioctl で柔軟に対応します。

**主な役割：**
- **統一インターフェース**: open、close、read、write、seek、ioctl の 6 操作のみ
- **Virtual SoC**: URI ベースのデバイス識別（`io://uart/0`、`accel://blas` など）
- **プラットフォーム実装**: Zephyr、ベアメタル、RISC-V など各プラットフォーム向けドライバ
- **柔軟な制御**: ioctl で設定・状態取得・カスタム操作に対応

### インターフェース（Interface）

**ファイル:** `inc/subsystems/hal.hxx`

```cpp
namespace fireball { namespace subsystems {

// ファイルディスクリプタ型（デバイスハンドル）
typedef uint32_t fd_t;

// ioctl コマンド定義
enum class ioctl_cmd : uint16_t {
  // 共通コマンド
  GET_INFO = 0x0001,        // デバイス情報取得
  SET_CONFIG = 0x0002,      // 設定値変更（ボーレート、周波数など）
  GET_STATUS = 0x0003,      // ステータス取得

  // デバイス固有コマンド（0x1000 以上をメーカー領域に）
  CUSTOM_START = 0x1000,
};

class hal_subsystem {
 public:
  virtual ~hal_subsystem() = default;

  // ─────────────────────────────────────
  // Mode 0: POD値ベース操作
  // ─────────────────────────────────────

  // open: URI からデバイスを開く（複数ポート対応）
  //
  // デバイスが複数の入出力ポートを持つ場合がある：
  // - UART デバイス: 複数のシリアルポートを持つ場合、ポート番号で区別
  // - GPIO：複数のピンを持つ場合、ピン番号で区別
  // - その他センサー、インターフェース: デバイス固有のストリーム構成
  //
  // URI はデバイスを指定し、flags やデバイス固有パラメータでポート/ストリームを選択
  // 戻り値: fd（成功）or 負の値（エラー）
  virtual fd_t open(
    std::string_view uri,              // e.g. "io://uart/0", "accel://blas"
    uint16_t flags = 0) = 0;          // ポート選択など、デバイス固有の flags

  // close: ファイルディスクリプタを閉じる
  virtual int close(fd_t fd) = 0;

  // seek: ファイルポジション移動（デバイスに依存）
  virtual int64_t seek(fd_t fd, int64_t offset, int whence) = 0;

  // ioctl: デバイス制御（可変パラメータ、型付きKey-Value形式使用）
  // 戻り値: 操作固有の値
  virtual int ioctl(
    fd_t fd,
    ioctl_cmd cmd,
    const std::vector<uint8_t>& input,   // 型付きKey-Value形式 encoded
    std::vector<uint8_t>& output) = 0;  // 型付きKey-Value形式 encoded result

  // ─────────────────────────────────────
  // Mode 1: co_value ベース操作（大容量I/O）
  // ─────────────────────────────────────

  // read: 大容量バッファ読み込み（共有ヒープ経由）
  virtual int64_t read(
    fd_t fd,
    co_value<uint8_t[]>& buffer) = 0;  // 所有権取得、読み込み結果サイズ返却

  // write: 大容量バッファ書き込み（共有ヒープ経由）
  virtual int64_t write(
    fd_t fd,
    co_value<uint8_t[]> buffer) = 0;   // 所有権移譲、書き込みサイズ返却
};

} } // namespace fireball { namespace subsystems
```

### 操作詳細

| 操作 | 通信モード | 用途 | 例 |
|------|----------|------|-----|
| **open** | Mode 2 (型付きKey-Value形式) | デバイス オープン、オプション指定 | `open("io://uart/0", O_RDWR)` |
| **close** | Mode 0 (POD) | デバイス クローズ | `close(fd)` |
| **read** | Mode 1 (co_value) | 大容量データ読み込み | `read(fd, buf)` → 共有ヒープから読み込み |
| **write** | Mode 1 (co_value) | 大容量データ書き込み | `write(fd, buf)` → 共有ヒープへ書き込み |
| **seek** | Mode 0 (POD) | ファイルポジション移動 | `seek(fd, 0, SEEK_SET)` |
| **ioctl** | Mode 2 (型付きKey-Value形式) | 設定・状態取得・カスタム操作 | ボーレート設定、GPIO制御など |

### ioctl による柔軟な制御

ioctl を通じて、デバイス固有のすべての操作を実装します。

**共通コマンド例：**

```cpp
// UART ボーレート設定
struct uart_config {
  uint32_t baud_rate;
  uint8_t data_bits;
  uint8_t parity;
} cfg{115200, 8, 0};

std::vector<uint8_t> input = kv_encode(&cfg);
std::vector<uint8_t> output;
hal.ioctl(uart_fd, ioctl_cmd::SET_CONFIG, input, output);
```

```cpp
// GPIO ピン出力設定
struct gpio_cmd {
  uint32_t pin;
  uint8_t value;     // 0=LOW, 1=HIGH
} cmd{5, 1};

std::vector<uint8_t> input = kv_encode(&cmd);
hal.ioctl(gpio_fd, ioctl_cmd::SET_CONFIG, input, {});
```

**デバイス固有コマンド（0x1000 以上）：**

- I2C: START、STOP、FREQUENCY 設定
- SPI: MODE、速度設定
- Timer: 周期設定、割り込み設定
- その他: 各メーカー実装領域

### 実装（Implementation）

**ファイル構成：**

```
src/subsystems/hal/
├── hal_subsystem.cpp       # インターフェース実装
└── platform/{zephyr,riscv}/
    ├── hal_device_map.cpp  # URI → ドライバマッピング
    ├── hal_uart.cpp        # UART ドライバ
    ├── hal_gpio.cpp        # GPIO ドライバ
    ├── hal_i2c.cpp         # I2C ドライバ
    ├── hal_spi.cpp         # SPI ドライバ
    └── hal_timer.cpp       # Timer ドライバ
```

**特徴：**
- 単純な 6 操作のみ
- ioctl で全てのカスタム機能に対応
- プラットフォーム固有の実装は `platform/` に隔離
- 型付きKey-Value形式 で複雑なパラメータを処理

### Phase 2 実装タスク

- [ ] HAL インターフェース定義（open/close/read/write/seek/ioctl）
- [ ] URI → ファイルディスクリプタマッピング
- [ ] 型付きKey-Value形式 エンコード・デコード（ioctl パラメータ用）
- [ ] Zephyr HAL バックエンド実装
- [ ] 標準ドライバ実装（UART、GPIO）
- [ ] カスタム ioctl コマンド定義
- [ ] 統合テスト

---

## 3. Debugger Subsystem [Future]

### 目的（Purpose）

**概要：** debugger サブシステム（Phase 3 で実装予定）は、GDB Remote Protocol をサポートし、VSCode、GDB、lldb などの標準的なデバッガと統合します。これにより、Fireball 実行環境のコルーチンをステップ実行したり、ブレークポイントを設定したりできるようになります。

### 主な機能（Key Features）

- **ブレークポイント管理**: 最大 5 個のブレークポイント設定・削除
- **ステップ実行**: 1 命令単位での WASM 実行制御
- **レジスタ読み取り**: 現在のコルーチン状態（ローカル変数、スタック）のスナップショット
- **メモリアクセス**: WASM 線形メモリの読み取り（セキュリティ制御あり）
- **IDE 統合**: VSCode、GDB、lldb などとの通信

### 実装（Implementation）

**ロケーション:** `src/subsystems/debugger/`

詳細設計は [debugging.md](debugging.md) を参照。

### Phase 3 実装タスク

- [ ] GDB Remote Protocol パーサー
- [ ] ブレークポイント管理（追加・削除・有効化）
- [ ] レジスタスナップショット取得
- [ ] メモリアクセス制御（バウンダリチェック）
- [ ] UART 通信層（GDB プロトコル送受信）

---

## 4. JIT Subsystem [Future]

### 目的（Purpose）

**概要：** JIT（Just-In-Time）コンパイラサブシステム（Phase 4 で実装予定）は、WASM インタプリタのボトルネックを検出し、頻繁に実行される「ホットパス」をネイティブコードに動的コンパイルします。インタプリタから JIT へ段階的にマイグレーションすることで、パフォーマンスを保ちながら初期起動時間を短縮します。

### 主な機能（Key Features）

- **ホットパス検出**: 実行頻度カウント、閾値超過時にコンパイル対象として選別
- **基本ブロック単位のコンパイル**: 分岐点でコンパイル境界を決定、最小単位化
- **段階的マイグレーション**: インタプリタから JIT へ動的に切り替え、フォールバックも可能
- **キャッシュ管理**: コンパイル済みコードの保存、LRU キャッシュで容量制限

### 実装（Implementation）

**ロケーション:** `src/subsystems/jit/`

詳細は Phase 4 計画で指定予定。

### Phase 4 実装タスク

- [ ] ホットパス プロファイリング機構
- [ ] 基本ブロック抽出アルゴリズム
- [ ] ネイティブコード生成（ARM Thumb-2/RISC-V 対応）
- [ ] コンパイルキャッシュ管理
- [ ] インタプリタへのフォールバック

---

---

## 通信モデル（Communication Model）

### CSP チャネルの使用

**概要：** Subsystems と Services は、入力チャネルでリクエストを受け取り、出力チャネルでレスポンスを送信します。この単方向通信により、各コンポーネント間の疎結合を維持し、スケーラビリティを確保します。

**Subsystem/Service のイベントループパターン：**

```cpp
// Input channel: リクエスト受け取り
co_channel<message_type>* input_ch = ...;

// Output channel: レスポンス送信
co_channel<message_type>* output_ch = ...;

// Event loop
while (true) {
  // ブロッキング受信（rendezvous）
  message_type req = input_ch->recv();

  // リクエスト処理
  message_type resp = process_request(req);

  // ブロッキング送信
  output_ch->send(resp);
}
```

### 統合ポイント（Integration Points）

**COOS カーネルとサービスの接続：**
- `co_sched` → logger: コルーチンライフサイクルイベント（spawn、resume、suspend、complete）
- `co_csp` → logger: チャネル操作イベント（send、recv）
- `co_mem` → logger: メモリ割り当て失敗イベント
- WASM インタプリタ → hal: I/O syscall（write、read、ioctl）

**サービス間の接続：**
- logger → hal: ログを外部デバイス（UART など）に出力
- debugger → hal: デバッガ情報をシリアルポート経由で GDB に送信
- (将来) jit → logger: コンパイル情報の記録

---

## 5. WASM Runtime Architecture: Interpreter, vSoC, JIT, vOffloader

### 目的（Purpose）

WASM Runtime は、WASM バイナリを実行するコア実行エンジンです。本セクションでは、WASM インタープリタ、Virtual SoC (vSoC)、JIT コンパイラ、vOffloader（ハードウェアアクセラレータインターフェース）の関係を明確に定義し、パフォーマンスと柔軟性のバランスを取ります。

**核となる設計戦略：**

1. **vSoC（Virtual System-on-Chip）が主体**：HAL サブシステムとの通信はすべて vSoC 経由で行う（インタープリタは vSoC の「クライアント」）
2. **インタープリタと JIT は命令セット実装**：命令セットの詳細（i32 命令、演算、制御フロー）を担当
3. **vOffloader は加速器ブリッジ**：BLAS、暗号化など CPU では遅い操作を MMIO 経由でアクセラレータに委譲
4. **密結合な内部 vs 疎結合な外部**：インタープリタ・JIT・vOffloader は最適化のため密結合だが、HAL との通信は IPC Router 経由（疎結合）

### 全体アーキテクチャ

```
┌─────────────────────────────────────────────────────────┐
│  WebAssembly Guest Code (user)                          │
│  - WASM bytecode (i32 instructions)                     │
│  - System calls (WASI: read, write, etc)               │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  WASM Runtime (Command: wasm_interpreter.exe)           │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │ WASM Interpreter (または JIT)                   │   │
│  │ - i32 instruction execution                     │   │
│  │ - Call stack, local variables management        │   │
│  │ - Memory access (linear memory)                 │   │
│  │                                                 │   │
│  │ ┌─ Instruction Type ──────────────────────┐   │   │
│  │ │ a) Arithmetic/Logic (local only)        │   │   │
│  │ │    → execute inline, no vSoC call      │   │   │
│  │ │ b) Memory access (local memory)         │   │   │
│  │ │    → Linear Memory, no vSoC call       │   │   │
│  │ │ c) I/O syscalls (WASI: read/write)     │   │   │
│  │ │    → delegate to vSoC.hal_syscall()    │   │   │
│  │ │ d) Accelerator operations (BLAS, etc)  │   │   │
│  │ │    → delegate to vSoC.voffloader       │   │   │
│  │ └─────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────┘   │
│                     │                                    │
│  ┌──────────────────┼──────────────────┐               │
│  ▼                  ▼                   ▼               │
│ [Inline]        [vSoC]              [JIT Cache]        │
│ Local ops      HAL syscalls         Compiled code      │
│                                                         │
│ ┌───────────────────────────────────────────┐         │
│ │ vSoC (Virtual SoC)                        │         │
│ │ - URI routing (device://uart/0, etc)      │         │
│ │ - HAL subsystem delegation                │         │
│ │ - IPC Router message formatting           │         │
│ │ - fd table management (open/close)        │         │
│ └───────────────────────────────────────────┘         │
│                     │                                  │
│ ┌───────────────────┼──────────────────┐              │
│ ▼                   ▼                   ▼              │
│ [I/O]            [Accel]            [System]          │
│ read/write       vOffloader          ioctl/seek       │
│ HAL I/O ops      (MMIO Mapped)       Management       │
│                                                        │
└─────────────────────────────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
    [IPC Router via HAL subsystem]
         │
    [HAL Backend: Zephyr/BareMetal/FreeRTOS]
         │
    [Hardware: UART, GPIO, I2C, SPI, Timer, etc]
```

### 5.1 インタープリタ（Interpreter）

**責務：**
- WASM i32 命令セットの解析・実行
- ローカル変数・スタック管理
- 制御フロー（分岐、ループ、関数呼び出し）
- Linear Memory へのアクセス

**特徴：**
- **命令セット実装**: i32 命令のみ（f32/f64 は非対応）
- **インラインで実行される操作**: 算術・論理演算、メモリアクセス、ローカル変数操作はすべてインプロセス
- **vSoC へのデリゲーション**: I/O syscall（WASI）や加速器操作は vSoC へ委譲

**例：**

```cpp
// Interpreter 内でのインラインコード実行
case i32_add: {
  uint32_t a = pop_from_stack();
  uint32_t b = pop_from_stack();
  push_to_stack(a + b);  // 即座に実行、vSoC呼び出しなし
  break;
}

// WASI syscall の場合は vSoC へデリゲーション
case i32_call: {
  uint32_t func_idx = operand;
  if (is_wasi_syscall(func_idx)) {
    // vSoC.hal_syscall() を呼び出す（後述）
    result = vsoc->handle_syscall(func_idx, args...);
  } else {
    // ローカルユーザー関数の呼び出し
    enter_function(func_idx);
  }
  break;
}
```

### 5.2 vSoC（Virtual System-on-Chip）

**責務：**
- **HAL インターフェース**: WASI syscall（read、write、ioctl など）を HAL サブシステムへ変換
- **デバイスルーティング**: URI ベースのデバイス識別（`io://uart/0` など）
- **FD テーブル管理**: open/close によるファイルディスクリプタの管理
- **vOffloader ブリッジ**: 加速器操作を MMIO 経由で実装

**命名規則：**
- vSoC は「Virtual」なので、実際のハードウェアを抽象化
- アーキテクチャ非依存（ARM Cortex-M、RISC-V など）

**実装：**

```cpp
namespace fireball { namespace runtime {

class vsoc {
 private:
  ipc_router* router_;              // HAL subsystem へのルーター
  std::vector<fd_entry_t> fd_table_;  // open ファイルディスクリプタ
  voffloader* voffloader_;          // 加速器インターフェース

 public:
  // WASI syscall ハンドラ
  int64_t handle_syscall(uint32_t syscall_id, const uint32_t* args);

  // POSIX-like HAL operations
  fd_t open(const char* uri, int flags);
  int close(fd_t fd);
  int64_t read(fd_t fd, co_value<uint8_t[]> buffer);
  int64_t write(fd_t fd, co_value<uint8_t[]> buffer);
  int64_t seek(fd_t fd, int64_t offset, int whence);
  int ioctl(fd_t fd, uint16_t cmd, const uint8_t* input, size_t input_len,
           uint8_t* output, size_t output_len);

  // Accelerator delegation
  int64_t accel_call(uint32_t accel_id, const void* input, size_t input_len,
                    void* output, size_t output_len);
};

} } // namespace fireball { namespace runtime
```

**設計の利点：**
- **インタープリタの責務明確化**: 命令実行のみに集中、I/O は vSoC へ
- **テスト容易性**: vSoC を mock して、インタープリタ単体テスト可能
- **プラットフォーム移植**: HAL 実装変更時も vSoC インターフェースは安定

### 5.3 vOffloader（ハードウェアアクセラレータ インターフェース）

**責務：**
- BLAS（行列計算）、暗号化、圧縮など CPU では遅い操作を外部アクセラレータへ委譲
- MMIO（メモリマップド I/O）ベースのアクセラレータドライバ実装
- 将来：GPU、ML 加速器などの統一インターフェース

**配置：**
- vSoC 内部に統合（密結合）
- HAL subsystem と別の経路で実装（MMIO）
- IPC Router 経由ではなく、直接 MMIO アクセス

**例：**

```cpp
class voffloader {
 private:
  // アクセラレータ別のドライバハンドル
  struct accelerator_device {
    std::string_view id;           // "blas", "crypto", "compress"
    void* mmio_base;               // メモリマップドベースアドレス
    accel_descriptor_t* descriptor; // 操作ディスクリプタ
  };

  std::vector<accelerator_device> devices_;

 public:
  int64_t call(std::string_view accel_id,
              const void* input, size_t input_len,
              void* output, size_t output_len);
};
```

**設計のポイント：**
- **CPU オフロード**: 計算量が多い操作を MMIO ベースで実装、CPU リソース節約
- **プラットフォーム依存**: MMIO アドレスは Zephyr デバイスツリー（DT）で定義

### 5.4 JIT（Just-In-Time）コンパイラ

**責務（Phase 4）：**
- ホットパスの検出：実行頻度カウント、閾値超過時に JIT 対象として選別
- ネイティブコード生成：WASM i32 命令列を ARM Thumb-2 または RISC-V コードへコンパイル
- 段階的マイグレーション：インタープリタから JIT へ動的に切り替え

**インタープリタとの関係：**

インタープリタと JIT の関係は、実行頻度に基づいた動的な切り替えです。JIT は「命令セット独立」の部分（スタック管理、メモリアクセス）を大きくし、バックグラウンドで低レイテンシコンパイルを行います。

```cpp
// インタープリタ実行中にホットパスを検出
case i32_call: {
  func_idx_t idx = operand;

  // ホットパス計数
  execution_count_[idx]++;

  // JIT 候補か判定
  if (execution_count_[idx] > JIT_THRESHOLD) {
    // JIT コンパイル（バックグラウンド）
    if (!jit_compiled_[idx]) {
      schedule_jit_compile(idx);
    }

    // JIT済みなら、ネイティブコード実行
    if (jit_compiled_[idx]) {
      void (*native_fn)() = jit_cache_[idx];
      return native_fn();
    }
  }

  // まだインタープリタで実行
  enter_function(idx);
  break;
}
```

**命令セット独立性：**
- **JIT が共通化できる部分**: スタック管理、レジスタ割り当て、メモリアクセス
- **JIT が実装定義な部分**: i32 命令の機械語への変換（ARM vs RISC-V）
- **目標**: 「i32 フロントエンド + 複数バックエンド（ARM、RISC-V）」の構成

### 5.5 統合パターン: インタープリタ → JIT → vOffloader

**典型的な実行フロー：**

```
WASM アプリケーション実行
  │
  ├─ Phase 1: 初期実行（インタープリタ）
  │  - ホットパス検出開始
  │  - I/O syscall → vSoC → HAL → ハードウェア
  │  - 計算量少ない処理 → インライン実行
  │
  ├─ Phase 2: ホットパス JIT コンパイル（バックグラウンド）
  │  - 実行頻度高い関数を検出
  │  - ネイティブコードへコンパイル
  │  - キャッシュに保存
  │
  └─ Phase 3: JIT + vOffloader 実行
     - 頻出関数 → ネイティブコード（高速）
     - 計算集約操作 → vOffloader（GPU/加速器）
     - I/O syscall → vSoC（変わらず）
```

### 5.6 WASM/vSoC/JIT 分離のルール

**密結合エリア（最適化優先）：**
- インタープリタ内部：スタック、レジスタ、制御フロー
- vSoC 内部：FD テーブル、MMIO ドライバ、vOffloader
- これらは同一プロセス・コンテキストで実行

**疎結合エリア（モジュール性優先）：**
- HAL subsystem との通信：IPC Router 経由
- Logger との統合：IPC チャネル経由
- Debugger との統合：IPC チャネル経由

**Credo: 「内は高速化、外は保証」**
- インタープリタ・vSoC・vOffloader 内部は密結合で最適化
- 外部（HAL、logger、debugger）との通信は IPC で保証

---

## 実装タイムライン（Implementation Roadmap）

### SLOC ベース見積もり（Estimates in Source Lines of Code）

本セクションでは、各サブシステム・コンポーネントの実装規模を **SLOC（Source Lines of Code）** で定量化します。SLOC はコメント・空行を除いた実行可能コード行数です。

**見積もり方針：**
- **実装コード（ヘッダ + ソース）のみカウント**
- **テストコードは別途集計**
- **記号コード（{ } など）は行にカウント**

### Phase 1: Logger Subsystem

| コンポーネント | 機能 | SLOC | 備考 |
|-------------|------|------|------|
| **ipc_router** | Router インターフェース定義 | 150 | DI コンテナ、ルート管理、アクセス制御 |
| **logger_service** | Logger インターフェース | 100 | イベント定義、ログレベル |
| **logger_impl** | Ring buffer 実装 | 250 | バッファ管理、イベント蓄積 |
| **logger_backend_uart** | UART バックエンド | 120 | UART I/O、フォーマット変換 |
| **co_value template** | 所有権管理 (header) | 80 | Move semantics、shared_heap 統合 |
| **shared_heap** | 共有メモリプール | 180 | メモリ割り当て、リサイクル管理 |
| **Phase 1 合計** | | **880 SLOC** | インタープリタ開発と並行 |

### Phase 2: HAL Subsystem

| コンポーネント | 機能 | SLOC | 備考 |
|-------------|------|------|------|
| **hal_subsystem** | HAL インターフェース定義 | 120 | open/close/read/write/seek/ioctl |
| **hal_router** | Device URI ルーター | 200 | 二分検索、URI マッピング |
| **hal_uart_driver** | UART ドライバ | 180 | open/close/read/write/ioctl 実装 |
| **hal_gpio_driver** | GPIO ドライバ | 140 | pin I/O、割り込み対応 |
| **hal_i2c_driver** | I2C ドライバ | 160 | マスター mode、周波数設定 |
| **hal_spi_driver** | SPI ドライバ | 150 | モード設定、speed 制御 |
| **hal_timer_driver** | Timer ドライバ | 130 | 周期設定、割り込みハンドラ |
| **kv_codec** | 型付きKey-Value形式 en/decode | 200 | ioctl パラメータエンコード |
| **Phase 2 合計** | | **1,280 SLOC** | Platform: Zephyr/BareMetal |

### Phase 3: Debugger Subsystem

| コンポーネント | 機能 | SLOC | 備考 |
|-------------|------|------|------|
| **debugger_service** | Debugger インターフェース | 100 | ブレークポイント、ステップ実行 |
| **gdb_protocol** | GDB Remote Protocol パーサ | 280 | コマンド解析、レスポンス生成 |
| **breakpoint_manager** | ブレークポイント管理 | 120 | 追加・削除・有効化、最大 5 個 |
| **register_snapshot** | レジスタ取得 | 150 | コルーチン状態キャプチャ |
| **memory_access** | メモリアクセス制御 | 100 | バウンダリチェック、セキュリティ |
| **debugger_uart_backend** | UART 通信層 | 110 | GDB プロトコル送受信 |
| **Phase 3 合計** | | **860 SLOC** | GDB/VSCode 統合 |

### Phase 4: JIT Subsystem

| コンポーネント | 機能 | SLOC | 備考 |
|-------------|------|------|------|
| **jit_compiler** | JIT コンパイラ基盤 | 200 | ホットパス検出、スケジューリング |
| **hotpath_profiler** | 実行頻度プロファイラ | 120 | 関数カウント、閾値判定 |
| **basic_block_extractor** | 基本ブロック抽出 | 180 | CFG 構築、コンパイル単位決定 |
| **arm_codegen** | ARM Thumb-2 コード生成 | 350 | i32 命令 → ARM 機械語 |
| **riscv_codegen** | RISC-V コード生成 | 380 | i32 命令 → RISC-V 機械語 |
| **jit_cache_manager** | コンパイルキャッシュ | 150 | LRU eviction、容量管理 |
| **jit_fallback** | インタープリタ フォールバック | 80 | JIT 失敗時の復帰 |
| **Phase 4 合計** | | **1,460 SLOC** | Arch-specific codegen |

### WASM Runtime Architecture Components

| コンポーネント | 機能 | SLOC | 備考 |
|-------------|------|------|------|
| **wasm_interpreter** | i32 命令セット | 450 | 算術、分岐、メモリアクセス |
| **vsoc** | Virtual SoC | 200 | HAL delegation、FD table |
| **voffloader** | 加速器インターフェース | 120 | MMIO 基底、BLAS 統合 |
| **wasi_syscall_handler** | WASI syscall 処理 | 140 | System call routing |
| **合計** | | **910 SLOC** | インタープリタ本体 |

### 総合 SLOC 集計

| フェーズ | 説明 | SLOC |
|--------|------|------|
| Phase 1 | Logger + shared_heap | 880 |
| Phase 2 | HAL + drivers | 1,280 |
| Phase 3 | Debugger | 860 |
| Phase 4 | JIT | 1,460 |
| WASM Runtime | Interpreter + vSoC | 910 |
| **合計（Subsystems + Runtime）** | | **5,390 SLOC** |

（注：テストコード、ドキュメント生成、ビルドスクリプトは別途）

### 開発優先度と依存関係

```
Phase 1: Logger
  ↓ (Phase 1 完了後)
Phase 2: HAL Subsystem
  ↓ (Phase 1, 2 完了後)
WASM Runtime (Interpreter + vSoC + vOffloader)
  ↓ (WASM Runtime 完了後)
Phase 3: Debugger
  ↓ (Phase 3 完了後)
Phase 4: JIT
```

**並行可能な項目：**
- Phase 1 の logger と WASM Runtime インタープリタは並行開発可能
- Phase 2 の HAL ドライバ実装はプラットフォーム別に並行化可

### チームスケーリング

例：平均 **10 SLOC/人・日** のペースで開発する場合：

| フェーズ | SLOC | 1 人 | 2 人 | 3 人 |
|--------|------|------|------|------|
| Phase 1 | 880 | 88 日 | 44 日 | 30 日 |
| Phase 2 | 1,280 | 128 日 | 64 日 | 43 日 |
| WASM Runtime | 910 | 91 日 | 46 日 | 31 日 |
| Phase 3 | 860 | 86 日 | 43 日 | 29 日 |
| Phase 4 | 1,460 | 146 日 | 73 日 | 49 日 |
| **合計** | 5,390 | 539 日 | 270 日 | 182 日 |

（注：統合テスト、デバッグ、ドキュメントは別途）

---

## 6. Platform Target Strategy

### 目的（Purpose）

Fireball は複数のハードウェアプラットフォームをサポートします。本セクションでは、各プラットフォームへの対応戦略、優先順位、および移植チェックリストを定義します。

**戦略方針：**
1. **リソース制約の優先順位**：限られた RAM/ROM で動作することを優先（低消費電力組み込みシステム向け）
2. **アーキテクチャ中立性**：ISA（Instruction Set Architecture）に依存しない設計
3. **段階的移植**：コア機能から優先的にサポート拡大

### 優先順位付きプラットフォーム

#### Tier 1: 第一優先（完全サポート対象）

**RISC-V ベースプロセッサ（推奨）**

| プロセッサ | 製造元 | 周波数 | RAM | ROM | 特徴 | 優先度 |
|----------|------|-------|-----|-----|------|--------|
| **WCH CH32V307** | WCH (中国) | 144 MHz | 32 KB | 256 KB | RISC-V RV32IM、超低消費 | ⭐⭐⭐ |
| **T-Head C906** | Alibaba | 1-2 GHz | 可変 | 可変 | RISC-V RV64、高性能 | ⭐⭐⭐ |
| **SiFive HiFive1 Rev B** | SiFive | 320 MHz | 16 KB | 16 MB Flash | RISC-V RV32IM、Reference Design | ⭐⭐ |

**理由：**
- RISC-V はオープン ISA（特許フリー）
- シンプルな命令セット → インタープリタ実装が容易
- エコシステム拡大中（Nordic Wireless も RISC-V への転換検討）
- 中国国内での供給チェーン確保

**Zephyr RTOS による統合：**
- WCH CH32V307、T-Head C906 は Zephyr で完全サポート
- Device Tree (DT) で HAL ドライバ自動マッピング
- GPIO、UART、I2C、SPI、Timer など標準デバイスサポート

#### Tier 2: 第二優先（部分サポート）

**Nordic nRF シリーズ（無線 MCU）**

| プロセッサ | 周波数 | RAM | ROM | 特徴 | 優先度 |
|----------|-------|-----|-----|------|--------|
| **nRF52840** | ARM Cortex-M4 | 64 MHz | 256 KB | 1 MB | BLE/Thread、Zephyr 完全対応 | ⭐⭐ |
| **nRF5340** | ARM Cortex-M33 | 128 MHz | 512 KB | 1.5 MB | Dual-core、高機能 | ⭐⭐ |

**理由：**
- IoT・ウェアラブル向けの事実上の標準（BLE対応）
- Zephyr での Bluetooth Stack 統合
- ARM Cortex-M は既存 STM32 との互換性

**留意点：**
- Proprietary SDK (nRF SDK) への依存を最小化
- Zephyr layer を厳密に守る

#### Tier 3: レガシ（互換性維持のみ）

**STM32 シリーズ（ARM Cortex-M4/M7）**

| プロセッサ | 周波数 | RAM | 特徴 | 対応方針 |
|----------|-------|-----|------|--------|
| **STM32F4 Discovery** | 168 MHz | 192 KB | HAL/LL library | Zephyr layer で統合 |
| **STM32H7** | 400+ MHz | 512 KB | High-speed | 段階的サポート |

**理由：**
- すでに Fireball がサポート中
- 既存プロジェクトとの互換性維持
- ただし、新規開発の推奨対象から外す

**東を削除した理由：**
- ❌ **ESP32**: 廃止対象（human review で指摘）
  - Xtensa ISA は Zephyr サポートに遅れあり
  - 無線スタック（Wi-Fi/BLE）は標準 Zephyr では不十分
  - Nordic nRF で置き換え（BLE 用途）
  - RISC-V で置き換え（汎用 MCU 用途）

### プラットフォーム別 HAL 実装

```
src/subsystems/hal/
├── hal_subsystem.cpp       # 汎用インターフェース
├── platform/
│   ├── zephyr/             # ⭐ Zephyr RTOS 統合層
│   │   ├── hal_device_map.cpp     # Device Tree → URI マッピング
│   │   ├── hal_uart.cpp
│   │   ├── hal_gpio.cpp
│   │   ├── hal_i2c.cpp
│   │   ├── hal_spi.cpp
│   │   └── hal_timer.cpp
│   │
│   ├── riscv_native/       # ⭐ RISC-V ベアメタル（DT不要）
│   │   ├── hal_uart_riscv.cpp     # WCH UART コントローラ
│   │   └── hal_gpio_riscv.cpp     # Direct MMIO
│   │
│   └── arm_legacy/         # STM32 互換（段階廃止）
│       ├── hal_uart_arm.cpp
│       └── hal_gpio_arm.cpp
```

**戦略：**
- **デバイスツリー（DT）**: Zephyr が管理、HAL は DT から URI マップを生成
- **MMIO 直接アクセス**: DT 不可用な環境では、プラットフォーム固有の実装
- **テスト用 Mock**: PC 上での開発・テスト用に POSIX ベースの HAL も提供

### ビルド構成

**CMake でのプラットフォーム指定：**

```cmake
# RISC-V WCH CH32V307（推奨）
cmake -DPLATFORM=wch_ch32v307 \
      -DZEPHYR_BOARD=wch_ch32v307 \
      ..

# Nordic nRF52840（BLE必要な場合）
cmake -DPLATFORM=nrf52840 \
      -DZEPHYR_BOARD=nrf52840dk_nrf52840 \
      ..

# STM32F4（レガシ）
cmake -DPLATFORM=stm32f4 \
      -DZEPHYR_BOARD=stm32f4_discovery \
      ..

# x86 + POSIX（開発・テスト用）
cmake -DPLATFORM=posix \
      -DZEPHYR_BOARD=native_sim \
      ..
```

**コンパイル時条件分岐：**

```cpp
// platform/wch_ch32v307/hal_uart.cpp
#if defined(PLATFORM_WCH_CH32V307)
  // WCH UART コントローラ固有の実装
  #define UART_BASE 0x40000000
  #define UART_CR1_OFFSET 0x00
  // ...
#elif defined(PLATFORM_NRF52840)
  // Nordic HAL (Zephyr device_get_binding)
  struct device* uart_dev = device_get_binding("UART_0");
  // ...
#endif
```

### リソース制約ガイドライン

**各プラットフォームでの推奨メモリ割り当て：**

| プラットフォーム | 総 RAM | Kernel | Subsys | WASM App | Notes |
|-------------|--------|--------|---------|----------|-------|
| **WCH CH32V307** (32 KB) | 32 KB | 8 KB | 6 KB | 18 KB | 最小構成 |
| **nRF52840** (256 KB) | 256 KB | 16 KB | 32 KB | 200 KB | 中規模 |
| **T-Head C906** (可変) | 512 KB+ | 32 KB | 64 KB | 400 KB+ | 高性能 |

**最小メモリ要件：**
- **COOS Kernel**: 8-12 KB（固定）
- **Subsystems**: Logger (2 KB) + HAL (2 KB) = 4 KB（固定）
- **Shared Heap**: 2-4 KB（構成可能）
- **WASM Runtime**: インタープリタ 8 KB + アプリ用 4 KB 以上

### 移植チェックリスト

各プラットフォームへの移植は以下の順序で実施：

```markdown
## [ ] デバイスツリー/HAL 統合
- [ ] Device Tree Blob (DTB) 作成（Zephyr 向け）
  または
  - [ ] HAL_DEVICE_MAP テーブル作成（ベアメタル）

## [ ] UART I/O
- [ ] ボーレート設定（115200 デフォルト）
- [ ] Interrupt/Polling モード選択
- [ ] Loopback テスト

## [ ] GPIO
- [ ] Input/Output 設定
- [ ] Edge/Level Interrupt（オプション）

## [ ] Timer
- [ ] 周期タイマー生成
- [ ] 割り込みハンドラ登録

## [ ] I2C/SPI（オプション）
- [ ] クロック周波数設定
- [ ] マスター mode 実装

## [ ] ビルド・テスト
- [ ] Zephyr ビルドスイート統合
- [ ] メモリ使用量測定
- [ ] ブート〜WASM 実行確認

## [ ] ドキュメント
- [ ] プラットフォーム固有 README
- [ ] Device Tree 説明書
```

### 今後の展開

**短期（3-6 ヶ月）：**
- ✅ RISC-V（WCH CH32V307）完全サポート
- ✅ Nordic nRF52840 BLE サポート
- ⚠️ ESP32 サポート廃止予定通知

**中期（6-12 ヶ月）：**
- ✅ T-Head C906（RISC-V 64bit）サポート
- ✅ RISC-V SIG（Zephyr）での標準化推進
- ✅ STM32 サポート段階廃止

**長期（1 年以上）：**
- ✅ ARM Cortex-M0+ サポート（最小メモリ環境）
- ⏳ GPU/NPU 加速器統合（T-Head Mali-G など）

---

## 設計制約（Design Constraints）

1. **IPC Router ハブ**: すべての Subsystem・Service 間通信は Router を経由（スター型トポロジ、Point-to-Point 直接通信なし）
2. **静的コンパイル時構成**: すべてのサービスタイプはコンパイル時に定義（動的ロード不可）
3. **メッセージベースのみ**: Subsystem・Service 間でメモリを共有しない（すべてのデータはメッセージ経由）
4. **独立したヒープ**: 各 Subsystem・Service は独自のメモリ割り当て領域を持つ（メモリ隔離）
5. **CSP のみの同期**: ロック、ミューテックス、セマフォはない（データ競合なし）

---

**For detailed component designs, see:**
- [ipc-protocol.md](ipc-protocol.md) - 型付きKey-Value形式 format
- [debugging.md](debugging.md) - GDB protocol
- [hal-virtual-soc.md](hal-virtual-soc.md) - Virtual SoC details
