# WIT インターフェイス仕様書 (WASI 準拠版)

## 1. 目的 `{WIT_Interface_Purpose}`
本ドキュメントは、Fireballプロジェクトにおいてゲスト（WASM）環境に公開されるシステムコールおよびHAL（Hardware Abstraction Layer）のインターフェイス仕様を、WASI (WebAssembly System Interface) 0.2 以降の設計パターン（Component Model, Resources, Streams）に準拠して定義する。

## 2. アーキテクチャ原則 `{3TierSeparation}` `{IoC}`
- **WASI 0.2 パターン採用**: ハンドル管理に `resource`、非同期処理に `pollable`、I/Oに `stream` を使用する。
- **Tier 1 分離**: システム境界は WASI 標準および Fireball 固有のインターフェイスとして定義される。
- **Stateless Interface**: リソースハンドルを通じた操作を行い、ホスト側で状態を管理する。

## 3. 共通データ構造 `{WIT_Common_Types}`

### 3.1 基礎インターフェイス (wasi:io/poll 等の流用)
WASI 0.2 の標準パターンに従い、以下の基礎コンポーネントを想定する。

- `pollable`: 非同期イベントの待機用リソース。
- `input-stream` / `output-stream`: ストリーミングデータ転送用リソース。

### 3.2 リカバリー戦略とエラーハンドリング

本プロジェクトでは、エラーコードではなくリカバリー戦略を返すことで、呼び出し側が具体的なアクション（リトライ/諦める）を取れるようにする。

```wit
/// Recovery strategy for operation failures.
enum recovery-strategy {
    /// Retry with same parameters may succeed (transient failure).
    /// Examples: resource temporarily unavailable, timeout
    retryable,
    /// Operation cannot succeed with current parameters, do not retry (permanent failure).
    /// Examples: invalid URI, service not found, already registered
    fatal
}

// Domain-specific result types
type operation-result = result<_, recovery-strategy>;
type service-load-result = result<_, recovery-strategy>;
type service-registration-result = result<_, recovery-strategy>;
type message-routing-result = result<_, recovery-strategy>;
```

#### 設計判断
- **実装詳細の分離**: `hardware-error`や`timeout`は実装の内部状態であり、クリーンアーキテクチャの内側が知るべきではない。
- **アクション指向**: リカバリー戦略により、呼び出し側は具体的なアクション（リトライ/エラーログ出力して諦める）を決定できる。
- **デバッグ情報の分離**: 失敗の詳細理由はログシステムで確認する。インターフェースには含めない。

## 4. 低レベル・トラップ・インターフェイス `{Trap_Interface}`
WASI標準には存在しない、Fireball固有の高速システムコール。

### `fireball:host/trap`
- `fireball-call(id: u32, arg0: u32, arg1: u32, arg2: u32, arg3: u32, arg4: u32, arg5: u32) -> u32`

### 4.2 高応答トラインターフェイス (Fast-Path Trigger) `{Fast_Path_GPIO}`
Trigger (GPIO) は、割り込み応答性およびビットバンギング等の要求から、一般のリソースハンドルを介さず、`fireball-call` に直接マッピングされた ID を通じて操作することを検討する。

- **理由**: ハンドルルックアップのオーバーヘッド排除、レジスタ直結に近いレイテンシの確保。
- **実装例**: `FB_SYSCALL_TRIGGER_WRITE` ID を直接指定。

```wit
// インターフェイスとしては定義するが、Shim層では直接トラップを叩く
interface trigger {
    set-pin: func(pin: u32, value: bool) -> operation-result;
    get-pin: func(pin: u32) -> result<bool, recovery-strategy>;
}
```

## 5. HAL インターフェイス (WASI 準拠設計) `{HAL_Interface}`

### 5.1 `fireball:host/timer` (wasi:clocks 準拠)
`wasi:clocks/monotonic-clock` のサブセットとして定義。

```wit
resource timer {
    now: func() -> u64; // ナノ秒単位
    subscribe-duration: func(nanos: u64) -> pollable; // タイマー割り込み相当
}
```

### 5.3 `fireball:host/bus` (Master/Slave Bus)
バス通信も標準WASIにはないため、リソースパターンを適用。

```wit
resource bus-master {
    transfer: func(tx-data: list<u8>, rx-len: u32) -> result<list<u8>, recovery-strategy>;
}

resource bus-slave {
    set-response: func(data: list<u8>) -> operation-result;
    get-received: func() -> result<list<u8>, recovery-strategy>;
    subscribe: func() -> pollable; // マスタからのアクセス通知
}
```

### 5.4 `fireball:host/streaming` (wasi:io 準拠)
一方的なデータ転送は標準のストリームとして扱う。

```wit
resource streaming-master {
    get-output-stream: func() -> output-stream;
}

resource streaming-slave {
    get-input-stream: func() -> input-stream;
}
```

## 6. 非同期通知メカニズム `{Asynchronous_Notification}`
WASIでは割り込みを直接扱うのではなく、`pollable` を通じたイベント待機（`poll`）としてモデル化する。

- **Virtual Interrupts**: 物理割り込みはホストで処理され、対応するリソース（`trigger`, `timer`, `bus-slave` 等）の `pollable` が ready になることでゲストに通知される。

## 7. フィードバック：WASI 準拠における制約事項
WASI仕様と HAL の乖離および考慮点は以下の通り：

1. **GPIO/Bus の不在**: WASI (CLI/Cloud) には GPIO や I2C/SPI の標準インターフェイスがない。これらは WASI リソースモデルに従った「Fireball 独自プロポーザル」として実装する必要がある。
2. **リアルタイム性**: WASI 0.2 の `poll` モデルは非同期イベントの集約には優れるが、極めて高速なリアルタイム応答が必要な場合、`fireball_call` (Trap) を併用する方が効率的である可能性がある。
3. **リソース管理のオーバーヘッド**: `resource` の生成・破棄（ハンドル管理）は、単純な `u32` ID渡しよりもホスト側のオーバーヘッドが増えるため、64KB RAM 環境ではハンドル数を制限するなどの対策が必要。
