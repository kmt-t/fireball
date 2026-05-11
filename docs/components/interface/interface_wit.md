# WIT インターフェイス仕様書 (WASI 準拠版)

## 1. 目的
本ドキュメントは、Fireballプロジェクトにおいてゲスト（WASM）環境に公開されるシステムコールおよびHAL（Hardware Abstraction Layer）のインターフェイス仕様を、WASI (WebAssembly System Interface) 0.2 以降の設計パターン（Component Model, Resources, Streams）に準拠して定義する。

## 2. アーキテクチャ原則
- **WASI 0.2 パターン採用**: ハンドル管理に `resource`、非同期処理に `pollable`、I/Oに `stream` を使用する。
- **Tier 1 分離**: システム境界は WASI 標準および Fireball 固有のインターフェイスとして定義される。
- **Stateless Interface**: リソースハンドルを通じた操作を行い、ホスト側で状態を管理する。

## 3. 共通データ構造

### 3.1 基礎インターフェイス `{CooperativeMultitasking}` `{Asynchronous_Notification}`
WASI 0.2 の標準パターンに従い、以下の基礎コンポーネントを想定する。

- `pollable`: 非同期イベントの待機用リソース。 `{CooperativeMultitasking}` `{Asynchronous_Notification}`
- `input-stream` / `output-stream`: ストリーミングデータ転送用リソース。

### 3.2 リカバリー戦略とエラーハンドリング `{RecoveryStrategy}` `{Errorcode_To_Strategy}`

本プロジェクトでは、エラーコードではなくリカバリー戦略を返すことで、呼び出し側が具体的なアクション（リトライ/諦める）を取れるようにする。低レイヤー（Syscall）の `errno` は、Shim層でこの戦略に変換される。 `{RecoveryStrategy}` `{Errorcode_To_Strategy}`

```wit
/// Recovery strategy for operation failures.
enum recovery-strategy-category {
    /// Error can be ignored, continue operation.
    ignore,
    /// Retry with same parameters may succeed.
    retry,
    /// Module or system needs to be re-initialized.
    restart,
    /// Fatal error, halt the system and dump state.
    panic
}

// Domain-specific result types
type operation-result = result<_, recovery-strategy-category>;
type load-result = result<_, recovery-strategy-category>;
type registration-result = result<_, recovery-strategy-category>;
type routing-result = result<_, recovery-strategy-category>;
```

TODO(Phase 1): ATC抽出 - 各リカバリー戦略（retry, restart等）を選択するための不変条件、およびシステム状態（panic時の状態保存など）の事後条件を明確にすること。

#### 設計判断 `{RecoveryStrategy}` `{Errorcode_To_Strategy}`
- **実装詳細の分離**: `hardware-error`や`timeout`は実装の内部状態であり、クリーンアーキテクチャの内側が知るべきではない。
- **アクション指向**: リカバリー戦略により、呼び出し側は具体的なアクション（リトライ/エラーログ出力して諦める）を決定できる。
- **デバッグ情報の分離**: 失敗の詳細理由はログシステムで確認する。インターフェースには含めない。

## 4. 低レベル・トラップ・インターフェイス `{Syscall_Mapping}`
WASI標準には存在しない、Fireball固有の高速システムコール。実体は `docs/components/core/system_syscall.md` で定義される `fireball_call` である。 `{Syscall_Mapping}`

### `fireball:host/trap` `{Syscall_Mapping}`
- `fireball-call(id: u32, arg0: u32, arg1: u32, arg2: u32, arg3: u32, arg4: u32, arg5: u32) -> u32`

### 4.2 高応答トラインターフェイス `{Syscall_Mapping}`
Trigger (GPIO) は、割り込み応答性およびビットバンギング等の要求から、一般のリソースハンドルを介さず、`fireball-call` に直接マッピングされた ID を通じて操作することを検討する。

- **理由**: ハンドルルックアップのオーバーヘッド排除、レジスタ直結に近いレイテンシの確保。
- **実装例**: `FB_SYSCALL_TRIGGER_WRITE` ID を直接指定。

```wit
// インターフェイスとしては定義するが、Shim層では直接トラップを叩く
interface trigger-controller {
    set-pin: func(pin: u32, value: bool) -> operation-result;
    get-pin: func(pin: u32) -> result<bool, recovery-strategy-category>;
}
```

## 5. HAL インターフェイス

### 5.1 `fireball:host/timer` (wasi:clocks 準拠)
`wasi:clocks/monotonic-clock` のサブセットとして定義。

```wit
resource periodic-timer {
    get-now: func() -> u64; // ナノ秒単位
    subscribe-timer: func(nanos: u64) -> pollable; // タイマー割り込み相当
}
```

### 5.3 `fireball:host/bus` (Master/Slave Bus) `{WASI_Implementation}`
バス通信も標準WASIにはないため、リソースパターンを適用。

```wit
resource bus-master {
    transfer-data: func(tx-buffer: list<u8>, rx-len: u32) -> result<list<u8>, recovery-strategy>;
}

resource bus-slave {
    set-response: func(data: list<u8>) -> operation-result;
    get-received: func() -> result<list<u8>, recovery-strategy>;
    subscribe: func() -> pollable; // マスタからのアクセス通知
}
```

### 5.4 `fireball:host/streaming` (wasi:io 準拠) `{WASI_Implementation}`
一方的なデータ転送は標準のストリームとして扱う。

```wit
resource streaming-master {
    get-output-stream: func() -> output-stream;
}

resource streaming-slave {
    get-input-stream: func() -> input-stream;
}
```

## 6. 非同期通知メカニズム
WASIでは割り込みを直接扱うのではなく、`pollable` を通じたイベント待機（`poll`）としてモデル化する。

- **Virtual Interrupts**: 物理割り込みはホストで処理され、対応するリソース（`trigger`, `timer`, `bus-slave` 等）の `pollable` が ready になることでゲストに通知される。

## 7. フィードバック：WASI 準拠における制約事項
WASI仕様と HAL の乖離および考慮点は以下の通り：

1. **GPIO/Bus の不在**: WASI (CLI/Cloud) には GPIO や I2C/SPI の標準インターフェイスがない。これらは WASI リソースモデルに従った「Fireball 独自プロポーザル」として実装する必要がある。
2. **リアルタイム性**: WASI 0.2 の `poll` モデルは非同期イベントの集約には優れるが、極めて高速なリアルタイム応答が必要な場合、`fireball_call` (Trap) を併用する方が効率的である可能性がある。
3. **リソース管理のオーバーヘッド**: `resource` の生成・破棄（ハンドル管理）は、単純な `u32` ID渡しよりもホスト側のオーバーヘッドが増えるため、64KB RAM 環境ではハンドル数を制限するなどの対策が必要。

## 8. 命名規則 (Naming Conventions)

WIT識別子は WASI 標準および `wasm-tools` の制約により `kebab-case` (ハイフン区切り) が必須である。プロジェクトでは以下のセマンティクス規約を適用する。

| 対象カテゴリ | 命名規則 (Semantic) | 例 |
| :--- | :--- | :--- |
| **Object** (Record, Resource) | `性質-責務名` | `periodic-timer`, `ipc-message` |
| **Enum** (Type) | `性質-カテゴリ` | `sys-log-level`, `recovery-strategy-category` |
| **Method** (Function) | `動詞` または `動詞-機能` | `set-pin`, `get-now` |
| **Field / Enum Case** | `kebab-case` | `max-latency`, `retry` |

### 8.1 設計上の留意点
- **Kebab-Case Mandatory**: WIT定義内で `snake_case` (アンダースコア) は使用禁止。
- **C++ へのマッピング**: 生成される C++ コードでは `embedded_cpp_rule.md` に従い、自動的に `snake_case` へ変換される。
- **名前の衝突回避**: ドメインプレフィックスを積極的に活用し、グローバルな名前空間での衝突を避ける。
