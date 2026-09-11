# サービス コンポーネント設計書 {VERIFY_LLM} {VERIFY_FORMAL}
<!-- evidence:
     formal: formal/service_fault_isolation_model.py
     concept: concepts/service_concept.py
     test: tests/system_service_test_spec.md
-->

## 1. コンセプト
<!-- traceability: {META_FaultIsolation} {MemoryIsolation} {IPCRouter} {META_ServiceIsWasmResident} -->
**サービス（Service）とは、WASM 上で実行される常駐タスクを指す。** ロギングや HAL 等、ネイティブコードとして COOS 上に常駐する基盤機能は**サブシステム（Subsystem）**と呼び、サービスとは明確に区別する（`{META_ServiceIsWasmResident}`、[`architecture_overview.md`](docs/architecture/architecture_overview.md) のレイヤー構成表を正本とする）。サービスは、IPCルータを経由してサブシステム（WASI、ロギング、HALデバイス等）へのアクセスを仲介し、WASMゲストに対してシステム機能を提供するコンポーネントである。IPCルータを経由したゼロコピー通信によってタスク分離を行い、障害隔離とメモリ安全性を確保する。 `{META_FaultIsolation}` `{MemoryIsolation}` `{IPCRouter}`

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} {IPCRouter} {URIAbstraction} -->
本コンポーネントは **Tier 1 (主要システムコンポーネント: Primary Component)** に属する。ゲストWASMに対する抽象化されたサービスレイヤを提供し、IoC (Inversion of Control) と URIベースのDIを用いて、機能拡張性と隔離性を統括する。 `{META_3TierSeparation}` `{IPCRouter}` `{URIAbstraction}`

## 3. 静的モデル

### 3.1 データ構造
- **サービスレジストリ**: システム起動時に構成ファイルから静的に構築され、ロードされているサービスの情報（URI、Tier、エントリポイント）のインデックスを管理する不変の構造体。

### 3.2 内部ブロック図
```mermaid
flowchart TD
    subgraph WasmLayer["WASM 実行層（サービス）"]
        Guest[WASM Guest] --> IPCService[Isolated IPC Service]
        Guest --> Lib[libfireball guest adapter]
    end
    subgraph NativeLayer["ネイティブ常駐層（サブシステム）"]
        Logging[Logging Subsystem]
        HAL[HAL Subsystem]
    end
    IPCService --> Logging
    Lib --> HAL
```

### 3.3 主要なクラス・構造体・配列・定数

#### サービス定義（service）
システムが管理する個別のサービスの属性。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| サービス名称 | サービスを識別するための共通のシステム名 | 文字列ビュー | - |
| 識別URI | ルータを介して公開される、サービスを指し示す唯一の正規名称 | 文字列ビュー | - |

#### サービス構成（service_config）
<!-- traceability: {META_ConfigurableSystem} -->
ヘッダファイルのマクロ定義によりシステム全体のパラメータおよび初期ロード構成をコンパイル時に固定する設定。 `{META_ConfigurableSystem}`

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| ゲスト識別子 | 構成設定が適用されるWASMゲストの管理ID | ID値 | 32bit |
| ロード対象リスト | ゲスト起動時に自動的に接続・初期化されるサービスのURI一覧 | `std::span<const std::string_view>` | 固定長配列へのスパン |

## 4. 動的モデル

### 4.1 アルゴリズム
<!-- traceability: {META_FaultIsolation} {IPCRouter} {SelfReboot_via_Event} {ServiceSelfReboot} {FaultTolerant} -->
- **サービス分離**: 各サービスは WASM 上で実行される独立した常駐タスクとして動作し、IPCルータを介してゼロコピーで通信する。タスク単位の障害局所化（`{META_FaultIsolation}`）と自己再起動（`{SelfReboot_via_Event}`）を組み合わせることで、単一サービスの異常終了が他サービスへ波及せず、かつ自律的に復旧するフォールトトレラント設計を実現する。 `{META_FaultIsolation}` `{FaultTolerant}`
- **WASI呼び出し**: ゲストからのWASIシステムコールを、HALサブシステムのIPCコマンドへ変換して転送する。 `{IPCRouter}`
- **自己再起動**: 異常終了したサービスは、IPCルータまたは上位マネージャからの障害イベント通知を契機として自律的に初期化・再起動される。TCBスロットの状態をリセットし、当該サービスのみを再初期化する（他サービスやシステム全体への波及はない）。 `{SelfReboot_via_Event}` `{ServiceSelfReboot}`

### 4.2 状態遷移図
<!-- traceability: {META_FaultIsolation} {IPCRouter} -->
```mermaid
stateDiagram-v2
    [*] --> Loaded: load_service (static)
    Loaded --> Running: start_guest
    Running --> Stopped: stop_guest
    Running --> Failed: fault detected
    Failed --> Loaded: self_reboot (SelfReboot_via_Event)
```

図中の各ブロックは以下を指す：
- **Isolated IPC Service**（サービス）: IPCルータ経由で隔離実行される、WASI以外の汎用サービス（本節冒頭で定義した「サービス」の実体。WASM上で実行される常駐タスクである）。
- **libfireball**（ゲスト側ライブラリ）: ゲストの WASI 呼び出しを公開WIT／HAL IFへ変換する静的ライブラリ。サービスやサブシステムではない。詳細はTier 3のゲストアダプタ仕様を参照する。
- **Logging Subsystem**（サブシステム）: `runtime_logging.md` の内部ロガーおよび `interface_wit.md` のコンソール生バイト出力経路（`fireball://service/stdout/0`）を介した出力を担う、ネイティブコードとして常駐する基盤機能。サービスではない。
- **HAL Subsystem**（サブシステム）: HAL（ハードウェア抽象化層）ドライバ群全体。ネイティブコードとして常駐する基盤機能であり、サービスではない。

ゲストWASMサービスは、起動時に独立した物理メモリパーティションを割り当てられ、メモリのハードウェア境界が確立される（障害伝播防止）。すべてのサービスへのアクセスおよびシステムコール呼び出しは、必ずIPCルータ（`IPCRouter`）のルックアップおよびアクセス制御チェックを経由してのみ開始される。 `{META_FaultIsolation}` `{IPCRouter}`

※ `load_service (static)` における `static` とは、システムビルド時にコンフィグによって登録されたサービス一覧に基づき、実行時の動的なURI追加を行わずに、起動時に固定配列からサービスをロードする静的ロード処理を意味する。

### 4.3 内部シーケンス
<!-- traceability: {META_FaultIsolation} {IPCRouter} -->
#### WASI呼び出しシーケンス
```mermaid
sequenceDiagram
    participant G as WASM Guest
    participant L as libfireball
    participant R as IPC Router
    participant H as HAL

    G->>L: WASI Call (e.g., fd_write)
    L->>R: get-interface("fireball://device/uart/0")
    R-->>L: interface handle
    L->>H: stream-write(handle, buffer)
    H-->>L: operation result
    L-->>G: WASI result
```

ゲストWASMタスクと `libfireball`、およびHAL間は、メモリ空間がメモリパーティションによって相互に保護されている。`libfireball` がゲストメモリ上のデータ（例: `fd_write` で書き込むバッファ）を公開IFへ渡す際は、ホストが提供するメモリ境界検証と、IPCルータ（`IPCRouter`）が仲介する所有権移譲ベースのゼロコピー通信によって安全にHALへ引き渡される。 `{META_FaultIsolation}` `{IPCRouter}`

### 4.4 WASI API と HAL IF の境界
<!-- traceability: {META_FaultIsolation} {IPCRouter} -->

ゲスト側の WASI API を Fireball の公開 IF へ変換する責務は、Tier 3のゲストアダプタに属する。本コンポーネントはサービスのロード、障害隔離、再起動、およびサービスが利用する境界の説明に限定し、Preview1 の関数シグネチャ、iovec 走査、HAL コマンド生成を記述しない。

`libfireball` は `get-interface`、`acquire-buffer`、`stream-write` 等の Tier 2 HAL IF を呼び出す。サービスの実行状態、COOS のスケジューリング、HAL タスクの待機状態は、それぞれの正本仕様に従う。

## 5. インターフェース定義

### 5.1 エラーハンドリング戦略
<!-- traceability: {META_RecoveryStrategy} -->

本コンポーネントでは、エラーコードではなくリカバリー戦略を返すことで、呼び出し側が具体的なアクションを取れるようにする。 `{META_RecoveryStrategy}`

#### リカバリー戦略の種類と具体的ポリシー
<!-- traceability: {META_RecoveryStrategy} -->
- **ignore**: エラーを無視し、処理を継続する。一時的な軽微なエラーに適用され、呼び出し側は特に対処を行わずそのまま継続する。
- **retry**: 一時的な失敗。再試行により成功する可能性がある。I/Oビジーなどの一時的エラーに適用され、呼び出し側は `FB_CONF_RETRY_BACKOFF_MS`（`{META_RecoveryStrategy}`）のウェイトを挟んで最大 3 回まで再試行を行う。
- **restart**: モジュールまたはシステムの再初期化が必要な失敗。内部状態矛盾などの復旧可能エラーに適用され、サービスマネージャに対してサービスの再初期化（Restart）を要求し、TCBスロットの状態をリセットして再起動する。
- **panic**: システムを即座に停止し、ダンプを出力する。カーネルパニックに適用され、システムを即座に停止（Halt）し、デバッグポートへ状態ダンプを出力する。

#### 設計判断
<!-- traceability: {META_RecoveryStrategy} -->
失敗の詳細理由は実装詳細であり、クリーンアーキテクチャの内側が知るべきではない。デバッグ情報はログシステムで確認する。 `{META_RecoveryStrategy}`

### 5.2 公開API
外部から利用可能なオブジェクト指向APIを定義する。

#### サービスロード（load_service）

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 指定されたURIに対応するサービスを初期化し、システムから利用可能な状態にする。 |
| シグネチャ | `load_service(std::string_view uri) -> service_load_result_t` |
| 引数 | `uri`: サービスの識別子を示す `std::string_view` |
| 戻り値 | `service_load_result_t` (成功時は `SUCCESS`、失敗時は `RETRY`/`RESTART`/`PANIC` のいずれかを示す列挙型) |
| 期待する結果 | 正常：サービスが初期化（またはリンク）され、Ready状態になる。 |
| 補足 | 戻り値となる `service_load_result_t` は、呼び出し元のリカバリー戦略（META_RecoveryStrategy）の決定に使用される。 |

##### service_load_result_t の定義
```text
enum class service_load_result_t : uint32_t {
    SUCCESS = 0,
    RETRY = 1,     // 一時的障害に対する再ロード試行
    RESTART = 2,   // モジュール/サービスの再初期化・TCBスロットリセット
    PANIC = 3      // 起動不可、システム停止
};
```
サービスロード処理において `IGNORE` は非適用（ロード失敗を無視して未初期化のまま続行することは許容されない）であり、`SUCCESS` または 3 つのエラーリカバリー戦略（`RETRY`, `RESTART`, `PANIC`）のいずれかを返却する。各ステータスに応じて、呼び出し側（システムマネージャなど）は本書のリカバリー戦略ポリシーに従うアクションを決定し、実行する。 `{META_RecoveryStrategy}`

### 5.3 URI/IPCインターフェース
<!-- traceability: {META_RecoveryStrategy} -->
- **URI規則**: `fireball://<subsystem_id>/<service_name>/<instance_id>` に準拠する（例: `fireball://services/wasi/0`）。
- **メッセージ形式**: 64ビットのKey-Value値を `arg0`〜`arg7` の計8スロット（最大8個）含むパケット。
  * **ヘッダ部**: `arg0` にコマンドID、`arg1` にリカバリー戦略カテゴリ `{META_RecoveryStrategy}`（`recovery-strategy-category` 値）を格納。
  * **ペイロード部**: `arg2`〜`arg5` にコマンド固有引数（または共有メモリハンドル等）を格納。
  * **拡張部**: `arg6`〜`arg7` は将来のコマンド固有引数用に予約し、未使用時はゼロを格納。

## 6. 制約達成の方策

### 6.1 性能制約と方策
<!-- traceability: {IPCRouter} -->
- **目標**: システムコールのオーバーヘッドを最小化する。
- **方策**: `{IPCRouter}` メッセージ通信自体はIPCルータを経由するが、高頻度な呼び出し（libc等）におけるオーバーヘッドを低減するため、コンテキストスイッチのオーバーヘッドを回避するダイレクトな実行権移譲（スケジューラを介さないHandoff）を使用する。ダイレクトな実行権移譲を用いる場合であっても、呼び出しの起点となる制御フローは必ずIPCルータを通過し、アクセス制御ルーティングが行われる。

### 6.2 メモリ制約と方策
<!-- traceability: {ConsolidatedHeap} {MemoryIsolation} -->
- **目標**: サービスによるメモリ消費を隔離する。
- **方策**: `{ConsolidatedHeap}` `{MemoryIsolation}` システム全体の物理メモリ総領域（ConsolidatedHeap）を静的に一括確保し、そこから各サービスに対して固定サイズの独立したメモリプール（GLOBAL_IndependentHeap）をメモリパーティションとして切り出すことで、動的確保を排除しつつメモリの論理的・物理的な隔離（MemoryIsolation）を実現する。 `{ConsolidatedHeap}` `{MemoryIsolation}`

### 6.3 安全性制約と方策
<!-- traceability: {META_FaultIsolation} -->
- **目標**: サービスの障害が他へ波及するのを防止する。
- **方策**: `{META_FaultIsolation}` サービスを独立した実行コンテキスト（タスク）で実行し、メモリパーティションを用いて不正アクセスやクラッシュを領域的に隔離する。
