# IPCルータ コンポーネント設計書

## 1. コンセプト
IPCルータは、URIベースのサービスディスカバリとロールベースのアクセス制御を備えたメッセージルーティング層である。コンポーネント間の依存性をURIで抽象化し、所有権移譲を伴う安全なデータ移動を実現する。 `{IPCRouter}` `{URIAbstraction}` `{RoleBasedAccessControl}` `{OwnershipTransfer}` `{IPCDI}`

## 2. アーキテクチャ分類 (Tier 1: Architecture Domain)
本コンポーネントは **Tier 1 (アーキテクチャドメイン)** に属する。システム全体の通信基盤として機能し、IoC (Inversion of Control) と URIベースのDIを用いて、コンポーネント間の疎結合性を担保する。 `{3TierSeparation}` `{IPCRouter}` `{URIAbstraction}`

## 3. 静的モデル

### 3.1 データ構造
- **registry_entry Array**: 登録されたサービスのURI、ロール、チャンネルIDを保持するソート済み配列。 `{IPCRegistry}`
- **Role Matrix**: コンパイル時に定義された、ロール間の通信許可を判定するマトリックス。 `{StaticRoleDefinition}`

### 3.2 内部ブロック図
```mermaid
graph TB
    subgraph "IPC Router"
        Reg[Registry<br/>Service Registration]
        AC[AccessControl<br/>Permission Check]
        R[Router<br/>Request Handling]
        MH[MessageHandler<br/>Message Processing]
        OM[OwnershipManager<br/>Ownership Transfer]
    end
    
    R --> Reg
    R --> AC
    R --> MH
    MH --> OM
```

### 3.3 主要なクラス・構造体・配列・定数

#### `kv_pair` (Key-Valueペア)
IPC通信の最小単位。1つのメッセージで8個のペアを送信できる。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 型スコープ | 上位3ビットで種別、下位5ビットでデータの型（定数、ID等）を定義する | ビットフラグ | 8bit |
| 識別キー | スコープ（Functional/Dictionary）内でデータの意味を一意に識別する | ID値 | 24bit |
| 属性値 | 実際のデータ本体、あるいはリソースを指すハンドルや即値 | 値 | 32bit |

##### スコープ定義
- **機能的IPC (Functional IPC)**: キーを、受信側が定義する関数やリクエスト種類を特定する識別子として使用する。
- **辞書参照IPC (Dictionary-based IPC)**: キーを、受信側が保持する静的な辞書内の文字列オフセットとして解釈する。 `{DictionaryBasedIPC}`

#### `message` (IPCメッセージ)
Key-Valueペアを複数集約した通信の基本単位。 `{TypeSafeMessaging}`

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| KVペア配列 | 通信内容を構成するKey-Valueペアの集合 | シーケンス | 8個固定 |

#### `indexed_array_adapter` (インデックス付き配列アダプタ)
元のデータの順序を変えることなく、インデックス配列を用いて仮想的にソート状態を提供するアダプタ。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 元データ参照 | 参照元となる `kv_pair` 配列への不変ポインタ | 構造体への参照 | 不変ポインタ |
| 索引順序 | キーに基づいてソートされた要素のインデックス。二分探索に使用 | ソート済み配列 | 8bit×8エントリ |

#### `registry_entry` (レジストリエントリ)
システム内で公開されているサービスの情報を管理する。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| サービスURI | サービスを一意に特定するための正規化された文字列 | 文字列ビュー | - |
| セキュリティロール | サービスに割り当てられた権限レベル。アクセス制御に利用 | ビットフラグ | - |
| 待ち受けチャネル | サービスがメッセージを待機している通信路の識別子 | ID値 | `channel_id` |

## 4. 動的モデル

### 4.1 アルゴリズム
- **サービス検索**: `constexpr` でソートされたURI文字列配列に対し、二分探索を用いることで O(log N) でチャンネルIDを取得する。 `{LowLatencyLookup}`
- **インデックス付き検索**: `indexed_array_adapter` は、元のデータの順序を変えずに、インデックス配列をソートすることで高速な二分探索を実現する。 `{AccessDictionary}`
- **所有権移譲**: メッセージ内の `kv_pair` に共有メモリIDが含まれる場合、送信側タスクから受信側タスクへ `co_value` の所有権を自動的に移譲する。 `{OwnershipTransfer}`

### 4.2 状態遷移図
```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Routing: lookup / send
    Routing --> Idle: complete
    Routing --> Error: permission_denied / not_found
    Error --> Idle: reset
```

### 4.3 内部シーケンス
#### サービス検索と接続フロー
```mermaid
sequenceDiagram
    participant C as Client
    participant R as IPCRouter
    participant Reg as Registry
    participant S as Server
    
    C->>R: lookup("fireball://hal/uart/0")
    R->>Reg: search(uri)
    Reg-->>R: entry(role, channel_id)
    Note over R: Check Permission
    alt Allowed
        R-->>C: channel_id
        C->>S: co_csp::send(channel_id, msg)
    else Denied
        R-->>C: ERROR_PERMISSION_DENIED
    end
```

## 5. インターフェイス定義

### 5.1 公開API
外部から利用可能なオブジェクト指向APIを定義する。

#### `register_service`

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | サービス固有のURIと、それを処理するチャネルおよびロールを関連付けて登録する。 |
| シグネチャ | `register_service(uri: 文字列ビュー, role: ビットフラグ, channel: ID値) -> 結果型` |
| 引数 | `uri`: サービスURI<br>`role`: アクセス権限<br>`channel`: 通信チャネル |
| 戻り値 | 結果型 (成功時は空、失敗時はエラー) |
| 事前条件 | レジストリに空きがあること。URIが重複していないこと。 |
| 事後条件 | レジストリがURI順に維持され、高速検索が保証される。 |

#### `lookup_service`

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 指定されたURIに対応する通信用チャネルIDを取得する。同時に送信側の権限チェックを行う。 |
| シグネチャ | `lookup_service(uri: 文字列ビュー) -> オプショナル値` |
| 引数 | `uri`: 検索対象のサービスURI |
| 戻り値 | オプショナル値 (成功時は `channel_id`, 失敗時は空) |
| エラー時の挙動 | 見つからない場合はエラーを、権限がない場合は拒否を通知する。 |
| 補足 | `{IPC_HandleBased}` のため、クライアントはこのIDをキャッシュして利用することが推奨される。 |

#### `route_message`

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 送信先チャネルに対してメッセージを転送し、必要に応じてリソースの所有権を移譲する。 |
| シグネチャ | `route_message(channel: ID値, msg: const参照) -> 結果型` |
| 引数 | `channel`: 送信先ID<br>`msg`: 送信メッセージ (`message`) |
| 戻り値 | 結果型 (成功時は空、失敗時はエラー) |
| エラー時の挙動 | 送信失敗時はエラーを返し、所有権の移譲を中止する。 |

### 5.2 URI/IPCインターフェイス
- **URI形式**: `fireball://<subsystem_id>/<stream>/<instance_id>`
- **メッセージ形式**: 64ビットのKey-Value値を最大12個含むパケット。 `{TypeSafeMessaging}`

### 5.3 サービスファサード
IPCのプリミティブ性を隠蔽し、依存性の逆転 (IoC) を実現するため、サービスの利用側（内側の層）がファサードクラスを定義する。 `{ServiceFacade}` `{IoC}`

## 6. 制約達成の方策

### 6.1 性能制約と方策
- **目標**: サービス検索のレイテンシを最小化する。
- **方策**: `{LowLatencyLookup}` ソート済み配列の二分探索を採用する。

### 6.2 メモリ制約と方策
- **目標**: レジストリ管理によるメモリ断片化を防止する。
- **方策**: `{BumpAllocator}` `{StaticScalability}` バンプアロケータを使用し、最大サービス数をコンパイル時に固定する。

### 6.3 安全性制約と方策
- **目標**: 不正なタスク間通信を防止する。
- **方策**: `{RoleBasedAccessControl}` `{OwnershipTransfer}` ロールベースの認可と、厳密な所有権管理により、データ競合と不正アクセスを排除する。

## 7. 設計完了チェックリスト（網羅性確認）
- [x] Tier 1 (Architecture Domain) に基づき設計となっているか
- [x] コンポーネントの責務が明確に定義されているか
- [x] 内部設計（データ構造、ブロック図、クラス、アルゴリズム）が適切に定義されているか
- [x] 内部ブロック図（静的）とシーケンス/状態遷移図（動的）がセットで定義されているか
- [x] 公開APIのメソッド名が英語で記述され、事前/事後条件が明確か
- [x] 非機能制約（性能、メモリ、安全性）に対する具体的な方策が明示されているか
- [x] 設計の交差点（トレードオフ）が解消されているか
- [x] 上位の要求 `{Keyword}` とのトレーサビリティが確保されているか
