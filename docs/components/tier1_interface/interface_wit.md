# WIT インターフェイス仕様書 (WASI 準拠版)

## 1. 目的

<!-- traceability: {WIT_Interface_Purpose} {WIT_First} {WIT_Common_Types} -->
本ドキュメントは、Fireballプロジェクトにおいてゲスト（WASM）環境に公開されるシステムコールおよびHAL（Hardware Abstraction Layer）のインターフェイス仕様を、WASI (WebAssembly System Interface) 0.2 以降の設計パターン（Component Model, Resources, Streams）に準拠して定義する。

## 2. アーキテクチャ原則

<!-- traceability: {CleanArchitecture} {META_SpecificationFirst} {META_Risk_Tiering} -->
- **WASI 0.2 パターン採用**: ハンドル管理に `resource`、非同期処理に `pollable`、I/Oに `stream` を使用する。
- **Tier 1 分離**: システム境界は WASI 標準および Fireball 固有のインターフェイスとして定義される。
- **Stateless Interface**: リソースハンドルを通じた操作を行い、ホスト側で状態を管理する。

## 3. 共通データ構造

### 3.1 基礎インターフェイス
<!-- traceability: {CooperativeMultitasking} {Asynchronous_Notification} -->
WASI 0.2 の標準パターンに従い、以下の基礎コンポーネントを想定する。

- `pollable`: 非同期イベントの待機用リソース。 `{CooperativeMultitasking}` `{Asynchronous_Notification}`
- `input-stream` / `output-stream`: ストリーミングデータ転送用リソース。

### 3.2 リカバリー戦略とエラーハンドリング
<!-- traceability: {META_RecoveryStrategy} {Errorcode_To_Strategy} -->

本プロジェクトでは、エラーコードではなくリカバリー戦略を返すことで、呼び出し側が具体的なアクション（リトライ/諦める）を取れるようにする。低レイヤー（Syscall）の `errno` は、Shim層でこの戦略に変換される。 `{META_RecoveryStrategy}` `{Errorcode_To_Strategy}`

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

#### 設計判断
<!-- traceability: {META_RecoveryStrategy} {Errorcode_To_Strategy} -->
- **実装詳細の分離**: `hardware-error`や`timeout`は実装の内部状態であり、クリーンアーキテクチャの内側が知るべきではない。
- **アクション指向**: リカバリー戦略により、呼び出し側は具体的なアクション（リトライ/エラーログ出力して諦める）を決定できる。
- **デバッグ情報の分離**: 失敗の詳細理由はログシステムで確認する。インターフェースには含めない。

## 4. 低レベル・トラップ・インターフェイス
<!-- traceability: {Syscall_Mapping} -->
WASI標準には存在しない、Fireball固有の高速システムコール。実体は `docs/components/core/system_syscall.md` で定義される `fireball::fireball_call` である。このインターフェース設計を通じて、低レベルなシステムコールがWITの世界とマッピングされる（`{Syscall_Mapping}`）。

### 4.1. `fireball:host/trap` の定義
<!-- traceability: {Syscall_Mapping} -->
WIT内では `fireball-call` という kebab-case 名で定義されるが、C++バインディングおよび公開APIとしては名前空間 `fireball` 内に `fireball_call`（snake_case）としてマッピングされ公開される。

- `fireball-call(id: u32, arg0: u32, arg1: u32, arg2: u32, arg3: u32, arg4: u32, arg5: u32) -> u32`

### 4.2. 高応答トラインターフェイス
<!-- traceability: {Syscall_Mapping} -->
Trigger (GPIO) は、割り込み応答性およびビットバンギング等の要求から、一般のリソースハンドルを介さず、`fireball-call` に直接マッピングされた ID を通じて操作するものとする。

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

### 5.3 `fireball:host/bus` (Master/Slave Bus)
<!-- traceability: {WASI_Implementation} -->
バス通信も標準WASIにはないため、リソースパターンを適用。

```wit
record buffer-slice {
    offset: u32,
    len: u32,
}

resource bus-master {
    // tx-bufのオフセット/サイズを渡し、受信データはrx-buf（事前に確保したバッファ）に直接書き込ませ、実際に転送したバイト数を返す
    transfer-data: func(tx-buf: buffer-slice, rx-buf: buffer-slice) -> result<u32, recovery-strategy>;
}

resource bus-slave {
    // 送信応答データを設定
    set-response: func(data: buffer-slice) -> operation-result;
    // 受信データを指定した静的バッファに読み出し、実際に取得したバイト数を返す
    get-received: func(dest-buf: buffer-slice) -> result<u32, recovery-strategy>;
    subscribe: func() -> pollable; // マスタからのアクセス通知
}
```

### 5.4 `fireball:host/streaming` (wasi:io 準拠)
<!-- traceability: {WASI_Implementation} -->
一方的なデータ転送は標準のストリームとして扱う。

```wit
resource streaming-master {
    get-output-stream: func() -> output-stream;
}

resource streaming-slave {
    get-input-stream: func() -> input-stream;
}
```

### 5.5. WASI標準APIの実装仕様 (WASI Standard API Implementation Specification)
<!-- traceability: {WASI_Implementation} -->
FireballにおけるWASI 0.2標準APIの具体的なマッピングと実装方針（`{WASI_Implementation}`）は以下の通りである。

1. **`wasi:clocks/monotonic-clock`**:
   - モノトニックタイマー要求は、HALの物理タイマー割り込みおよびカウンタレジスタに直結して処理される。
   - タイマーの周期呼び出しやタイムアウト機能は、`periodic-timer` リソースおよび `pollable` オブジェクトを通じて実装される。
2. **`wasi:io/streams` (input-stream / output-stream)**:
   - ストリームI/O操作（データの順次読み書き）は、COOSのIPCチャネルを用いたメッセージ通信として実装される。
   - ホスト側のメモリバッファとゲスト（WASM）側のリニアメモリ間で、ゼロコピーまたは最小限のオーバーヘッドでデータ転送を行う。
3. **`wasi:cli/stdout` / `wasi:cli/stderr`**:
   - コンソール出力（標準出力・標準エラー出力）は、ホスト環境のシステムログサービス（`system_logging.md`）へ転送される。
   - ゲスト内での `print` や `eprint` は、自動的にシステムコール経由でロガーにルーティングされる。
4. **`wasi:filesystem/types` / `wasi:filesystem/preopens`**:
   - Fireballは組み込み向け極小ハイパーバイザであるため、一般的な物理ディスク上のファイルシステムはサポートしない。
   - ただし、特定のメモリマップドI/O（VMMIO）領域や共有メモリ領域を「事前オープンされた仮想ファイル記述子」としてエミュレートする仕組みを提供する。

## 6. 非同期通知メカニズム

<!-- traceability: {Asynchronous_Notification} {WASI_Async_Bridge} -->
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
