# WIT インターフェース仕様書 (WASI 準拠版) {VERIFY_WIT} {VERIFY_LLM} {VERIFY_FORMAL}
<!-- evidence:
     wit: wit/fireball.wit
     formal: formal/wit_resource_lifecycle_model.py
     test: tests/interface_wit_test_spec.md
-->

## 1. 目的

<!-- traceability: {WIT_Interface_Purpose} {WIT_First} {WIT_Common_Types} {URIAbstraction} -->
本ドキュメントは、Fireballプロジェクトにおいてゲスト（WASM）環境に公開されるシステムコールおよびハードウェア抽象化層（HAL）のインターフェース仕様を定義する。

**HAL は WASI 0.3 Preview (WASI 0.3p / Component Model) そのものである。** GPIO・タイマー・バス通信・ストリーム・コンソール出力等の個別デバイス/サービスごとに専用の WIT リソース型を定義することはしない。ゲストは階層型 URI から対象を動的に解決する **URI Resolver** と、ゼロコピー転送用の **HALバッファプール** の2つの汎用機構のみを介して、あらゆる WASI 0.3p 相当の読み書き・バス転送・非同期通知を行う。個々のデバイス/サービスの振る舞いは、IPCコマンドID（`runtime_hal.md` §5.2 を正本とする）によって決定される。レガシーな WASI 0.1p (`wasi_snapshot_preview1`) ABI は、この URI Resolver + HALバッファプール機構を背後で呼び出す薄いアダプタ/ラッパーレイヤーとして完全サポートする（[`wasi_preview1_abi.md`](docs/specs/wasi_preview1_abi.md) を正本とする）。 `{WIT_Interface_Purpose}` `{WIT_First}` `{WIT_Common_Types}` `{URIAbstraction}`

## 2. アーキテクチャ原則

<!-- traceability: {CleanArchitecture} {META_SpecificationFirst} {META_Risk_Tiering} {URIAbstraction} -->
- **URI Resolver メソッド**: `resolver.get-interface(uri: string)` により、URI 文字列からインターフェースハンドルを取得可能とする。個別デバイスの WIT リソース型は存在しない——ハンドルに対する操作はすべて IPC コマンドID経由で行う。
- **HALバッファプール（vMMIO/DYNAMIC）ゼロコピー I/O**: デバイス通信のデータ送受信は、`resolver.acquire-buffer` / `resolver.release-buffer` で貸与される HALバッファプール（vMMIO/DYNAMIC 領域の `hal-buffer-slice`）を通じてゼロコピー／極低レイテンシで実行される。 `{URIAbstraction}` `{META_RestrictedPhysicalAccess}`
- **IPC 宛先 URI と階層命名規則**: URI は、`fireball://<domain>/<type>/<instance>`（例: `fireball://device/uart/0`, `fireball://device/gpio/0`, `fireball://device/timer/0`, `fireball://device/i2c/0`, `fireball://service/stdout/0`）の**階層型 URI 命名規則**に従い、**IPC ルータ（`ipc_router`）でデバイスやサービスと通信するための宛先 URI** として機能する。
- **WASI 0.1p 互換ラッパー (Adapter Pattern)**: 既存の WASI Preview 1 (`fd_write`, `fd_read`, `clock_time_get`, `proc_exit` 等) は、上記の URI Resolver + HALバッファプール機構を呼び出すラッパーとして機能する。
- **Stateless Interface**: リソースハンドルを通じた操作を行い、ホスト側で状態を管理する。

## 3. 共通データ構造

### 3.1 基礎インターフェース & IPC URI Resolver
<!-- traceability: {CooperativeMultitasking} {Asynchronous_Notification} {URIAbstraction} {META_RestrictedPhysicalAccess} -->
以下の基礎コンポーネントのみを提供する。個別デバイス/サービス向けの WIT リソース型は定義しない。

- `resolver`: 階層型 IPC 宛先 URI（`fireball://device/<type>/<instance>`, `fireball://service/<type>/<instance>`）から通信ハンドルを取得し、HALバッファプール（`hal-buffer-slice`）を貸与・返却するリゾルバ。 `{URIAbstraction}` `{META_RestrictedPhysicalAccess}`

```wit
/// WASI 0.3p / IPC 動的インターフェース取得・HALバッファ解決 (URI Resolver)
interface resolver {
    use types.{hal-buffer-slice, operation-result, recovery-strategy-category};

    /// IPC 宛先 URI から通信チャネルハンドルを取得
    get-interface: func(uri: string) -> result<u32, recovery-strategy-category>;
    /// HALバッファプール（vMMIO/DYNAMIC）バッファの確保
    acquire-buffer: func(size-bytes: u32) -> result<hal-buffer-slice, recovery-strategy-category>;
    /// HALバッファプール（vMMIO/DYNAMIC）バッファの解放
    release-buffer: func(slice: hal-buffer-slice) -> operation-result;
}
```

非同期通知（GPIOエッジ、タイマー満了等の待機）は、専用の `pollable` リソース型を設けず、IPCコマンドID（`POLL_CHECK` / `POLL_WAIT`、`runtime_hal.md` を正本とする）による汎用ポーリングとして表現する（§6 参照）。 `{CooperativeMultitasking}` `{Asynchronous_Notification}`

```mermaid
graph TD
    Guest[Guest WASM Application] -->|1. resolver.get-interface URI: fireball://device/uart/0| Res[URI Resolver / IPC Router]
    Guest -->|2. resolver.acquire-buffer size| HBP[HAL Buffer Pool vMMIO/DYNAMIC]
    Guest -->|3. IPCコマンドID発行 例: CMD_STREAM_WRITE_BUFFER| W3Core[WASI 0.3p HAL Drivers]
    W1Wrap[WASI 0.1p Adapter Layer] -->|Delegates fd_write/read via HAL Buffer + Command ID| W3Core
    W3Core --> UART[fireball://device/uart/0]
    W3Core --> GPIO[fireball://device/gpio/0]
    W3Core --> Timer[fireball://device/timer/0]
    W3Core --> I2C[fireball://device/i2c/0]
    W3Core --> Console[fireball://service/stdout/0]
```

### 3.2 リカバリー戦略とエラーハンドリング
<!-- traceability: {META_RecoveryStrategy} {Errorcode_To_Strategy} -->

本プロジェクトでは、エラーコードではなくリカバリー戦略を返すことで、呼び出し側が具体的なアクション（リトライ/諦める）を取れるようにする。低レイヤー（Syscall）の `errno` は、Shim層でこの戦略に変換される。 `{META_RecoveryStrategy}` `{Errorcode_To_Strategy}`
※なお、ホスト内部で各デバイスドライバと通信する低レイヤーの IPC コマンドプロトコルおよび RSP デバッグ仕様は、Tier 2/3 の HAL コンポーネント設計書（[`runtime_hal.md`](docs/components/tier2_runtime/runtime_hal.md) / [`platform_driver.md`](docs/components/tier3_platform/platform_driver.md)）を正本とする。

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

#### リカバリー戦略の事前・事後条件と不変条件
<!-- traceability: {META_RecoveryStrategy} {Errorcode_To_Strategy} -->

| 戦略カテゴリ | 選択基準（事前条件） | 事後条件 / システム状態 | 不変条件 |
| :--- | :--- | :--- | :--- |
| `ignore` | 一時的なバッファ空/満杯通知など、データ喪失を伴わず無視可能な事象 | 状態変化なし。呼び出し元は継続実行 | システム整合性は完全に維持される |
| `retry` | 一時的なリソース競合やタイムアウト。再試行により回復可能な場合 | 引数状態は維持。`FB_CONF_RETRY_BACKOFF_MS`（`{META_RecoveryStrategy}`）のバックオフ後に再実行 | 再試行上限回数（3回）を超えないこと |
| `restart` | サービスコンテキストやメモリ破損の疑い。モジュール単体の自己修復が必要な場合 | 該当タスク/サービスのTCB・ヒープを初期化し再起動 | 他サービスおよびカーネルのメモリ空間は隔離され保護される |
| `panic` | MPU違反、二重解放、デッドロック検知など、安全な継続が不可能な致命的障害 | 全タスク停止、クラッシュダンプを出力しフェイルセーフ停止 | ハードウェアおよび不揮発性領域への不正書き込みを即時遮断 |

#### 設計判断
<!-- traceability: {META_RecoveryStrategy} {Errorcode_To_Strategy} -->
- **実装詳細の分離**: `hardware-error`や`timeout`は実装の内部状態であり、クリーンアーキテクチャの内側が知るべきではない。
- **アクション指向**: リカバリー戦略により、呼び出し側は具体的なアクション（リトライ/エラーログ出力して諦める）を決定できる。
- **リトライ上限到達時の段階的エスカレーション (Retry Exhaustion Escalation)**: `retry` 戦略で `RETRY_MAX_ATTEMPTS`（3回）を超過した場合、呼び出し元は自動的に `restart`（タスクコンテキスト再初期化・再起動）へエスカレーションする。再起動後もエラーが回復不能な場合は最終的に `panic` へエスカレーションし、システム安全性を担保する。
- **IPC は所有権ロールバックを必要としない**: IPC ルータ（`ipc_router.md`）はバッファなし同期 CSP チャネル（`{ADR_RendezvousChannel}`）であり、宛先ごとの有界キューを持たない。したがって `ERR_QUEUE_FULL` のような一時的な資源競合は原理的に発生せず、`Revoke` 後の所有権ロールバックという回復処理も存在しない——送信は相手タスクの到達を待つのみで、失敗して差し戻る経路がない。
- **デバッグ情報の分離**: 失敗の詳細理由はログシステムで確認する。インターフェースには含めない。

## 4. 低レベル・トラップ・インターフェース
<!-- traceability: {Syscall_Mapping} -->
WASI標準には存在しない、Fireball固有の高速システムコール。実体は `../tier1_core/system_syscall.md` で定義される `fireball::fireball_call` である。このインターフェース設計を通じて、低レベルなシステムコールがWITの世界とマッピングされる（`{Syscall_Mapping}`）。

### 4.1. `fireball:host/trap` の定義
<!-- traceability: {Syscall_Mapping} -->
WIT内では `fireball-call` という kebab-case 名で定義されるが、C++バインディングおよび公開APIとしては名前空間 `fireball` 内に `fireball_call`（snake_case）としてマッピングされ公開される。

- `fireball-call(id: u32, arg0: u32, arg1: u32, arg2: u32, arg3: u32, arg4: u32, arg5: u32) -> u32`

### 4.2. 高応答トリガーインターフェース
<!-- traceability: {Syscall_Mapping} -->
GPIO のような割り込み応答性・ビットバンギング等の要求から、URI Resolver 経由のハンドルルックアップを介さず、`fireball-call` に直接マッピングされた ID を通じて操作するものとする。

- **理由**: ハンドルルックアップのオーバーヘッド排除、レジスタ直結に近いレイテンシの確保。
- **実装例**: `FB_SYSCALL_TRIGGER_SET_PIN` ID を直接指定（`{Syscall_Mapping}`）。

```python
# ゲスト側での trigger.set_pin の実装例 (Shim)
def fireball_trigger_set_pin(pin: int, value: bool):
    __fireball_call(fb_syscall_id.FB_SYSCALL_TRIGGER_SET_PIN, pin, int(value), 0, 0, 0, 0)
```

## 5. `console-output` の位置づけ
<!-- traceability: {WASI_ConsoleRawOutput} {DictionaryBasedIPC} -->
ゲストの `print`/`eprint` が書き込む文字列は実行時に組み立てられる任意長データであり、`system_logging.md` の内部ロガー（`{DictionaryBasedIPC}`、ビルド時登録の辞書オフセット＋固定4引数のみを扱い、実行時の辞書追加は不可）では表現できない。そのため、コンソール出力は内部ロガーとは独立した経路として扱う。 `{WASI_ConsoleRawOutput}`

専用の `console-output` リソース型は設けない。ゲストは `resolver.get-interface("fireball://service/stdout/0")` で標準出力サービスを解決し、`acquire-buffer` で確保した `hal-buffer-slice` に任意長の生バイト列を書き込んだ上で、UART 等と同じ `CMD_STREAM_WRITE_BUFFER` コマンドを発行する。辞書変換もリングバッファへの構造化格納も行わず、`HAL_Transport`（UART/ITM 等）へそのまま渡される。

物理トランスポート（`HAL_Transport`）は `system_logging.md` のロガーと共有するが、辞書・リングバッファは経由しない別経路であり、両者は排他的に出力順序が保証されるわけではない（インターリーブし得る）。

`fireball_call(WASI_FD_WRITE, ...)`（`system_syscall.md` 正本）は、ゲストの `print`/`eprint` 呼び出しをこの `fireball://service/stdout/0` 経路へ自動的にルーティングする。

## 6. 非同期通知メカニズム

<!-- traceability: {Asynchronous_Notification} {WASI_Async_Bridge} -->
WASIでは割り込みを直接扱うのではなく、汎用ポーリングコマンドによるイベント待機としてモデル化する。専用の `pollable` リソース型は設けず、`resolver.get-interface`/IPCコマンド発行が返す `u32` ハンドルに対して `POLL_CHECK` / `POLL_WAIT`（`runtime_hal.md` を正本とする）コマンドIDを発行することで、ready 状態を確認する。

- **Virtual Interrupts**: 物理割り込みはホストで処理され、対応するハンドル（GPIO エッジ購読、タイマー満了購読、バス受信購読等の IPC コマンドが返す `u32` ポーリングハンドル）が `POLL_CHECK` で ready を返すことでゲストに通知される。

## 7. フィードバック：WASI 準拠における制約事項
WASI仕様と HAL の乖離および考慮点は以下の通り：

1. **GPIO/Bus の不在**: WASI (CLI/Cloud) には GPIO や I2C/SPI の標準インターフェースがない。これらは専用 WIT リソース型を設けず、URI Resolver + HALバッファプール + IPCコマンドIDの汎用機構上で「Fireball 独自プロポーザル」として実現する。
2. **リアルタイム性**: WASI 0.2 の `poll` モデルは非同期イベントの集約には優れるが、極めて高速なリアルタイム応答が必要な場合、`fireball_call` (Trap) を併用する方が効率的である可能性がある。
3. **リソース管理のオーバーヘッド**: 専用 WIT リソース型（ハンドル管理）を廃したことで、単純な `u32` ID渡し + IPCコマンドIDのみの薄い構成となり、64KB RAM 環境でのホスト側オーバーヘッドを最小化している。

## 8. 命名規則 (Naming Conventions)

WIT識別子は WASI 標準および `wasm-tools` の制約により `kebab-case` (ハイフン区切り) が必須である。プロジェクトでは以下のセマンティクス規約を適用する。

| 対象カテゴリ | 命名規則 (Semantic) | 例 |
| :--- | :--- | :--- |
| **Object** (Record, Resource) | `性質-責務名` | `hal-buffer-slice`, `ipc-message` |
| **Enum** (Type) | `性質-カテゴリ` | `sys-log-level`, `recovery-strategy-category` |
| **Method** (Function) | `動詞` または `動詞-機能` | `get-interface`, `acquire-buffer` |
| **Field / Enum Case** | `kebab-case` | `max-latency`, `retry` |

### 8.1 設計上の留意点
- **Kebab-Case Mandatory**: WIT定義内で `snake_case` (アンダースコア) は使用禁止。
- **C++ へのマッピング**: 生成される C++ コードではプロジェクト標準規約に従い、自動的に `snake_case` へ変換される。
- **名前の衝突回避**: ドメインプレフィックスを積極的に活用し、グローバルな名前空間での衝突を避ける。
