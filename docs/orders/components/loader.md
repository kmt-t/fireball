# WASMローダ コンポーネント設計書

## 1. コンセプト
WASMローダは、ROM上のWASM32バイナリをパースし、実行環境が参照しやすい索引構造（ModuleView）を生成する。RAMへの全展開を避け、ROM上のデータを直接参照することでメモリ消費を極小化する。 `{ROMParsing}` `{AccessDictionary}` `{BumpAllocator}`

## 2. アーキテクチャ分類 (Tier 3: Implementation Domain)
本コンポーネントは **Tier 3 (実装ドメイン)** に属する。WASMバイナリの解析と索引構築に特化した単一責務のモジュールとして設計する。 `{3TierSeparation}`

## 3. 静的モデル

### 3.1 データ構造 (Natural OO)
- **`WasmLoader` (Class)**: WASMバイナリのパース、検証、およびロード済みモジュールの管理を一括して行う主要クラス。
- **`module_view` (View)**: ROM上のバイナリデータへの参照と、構築された索引群を保持する読み取り専用の構造体。
- **`module_registry` (Internal)**: ロード済みの `module_view` を名前で管理するための内部リスト。 `{MultiModule_Support}`

### 3.2 内部ブロック図
```mermaid
    subgraph Loader_Layer
        Loader[WasmLoader Engine]
        Registry[Internal Registry]
    end

    subgraph Memory
        ROM[Wasm ROM Binary]
        Alloc[bump_allocator]
    end

    Loader -- holds reference --> Alloc
    Loader -- manages --> Registry
    Registry -- holds --> View[module_view]
    View -- refers to --> ROM
```

### 3.3 主要なクラス・構造体・配列・定数

#### `WasmLoader` クラス
依存関係（アロケータ等）と内部レジストリをカプセル化する。

| 項目名 | 機能と役割 | 備考（制約、型など） |
| :--- | :--- | :--- |
| 作業用アロケータ | 索引構築時のメモリ割り当てに使用する（プライベートメンバ）。 | `bump_allocator*` |
| モジュール索引 | ロード済みモジュールを名前で引くための内部管理リスト。 | `module_registry` |

#### `module_view` (モジュールビュー)
ROM上のバイナリデータに対する「窓」として機能する。
データをRAM上に展開するのではなく、必要な時に必要な情報へアクセスするためのアクセサを提供する。
これにより、RAM消費を最小限（オフセット配列のみ）に抑えつつ、クライアントに対しては型安全なインターフェイスを提供する。 `{ROMParsing}`

#### `function_accessor` (関数アクセサ)
関数の詳細情報へアクセスするための一時的なプロキシオブジェクト。
メソッド呼び出し時にROM上のデータをデコードして値を返す。

| 項目名（プロパティ） | 機能と役割 | 備考（制約、型など） |
| :--- | :--- | :--- |
| シグネチャ索引 | 関数の型定義へのインデックス。 | デコードメソッド |
| ローカル変数定義 | 関数のローカル変数定義のイテレータ。 | デコードメソッド |
| コード開始点 | 関数の実行本体（バイトコード）の開始位置。 | デコードメソッド |
| コード長 | 命令列全体のバイトサイズ。 | 計算プロパティ |

#### `global_accessor` (グローバルアクセサ)
グローバル変数の定義情報へアクセスするための一時的なプロキシオブジェクト。

| 項目名（プロパティ） | 機能と役割 | 備考（制約、型など） |
| :--- | :--- | :--- |
| データ型 | グローバル変数の値の種類。 | デコードメソッド |
| 書き込み可否 | 実行中に値を変更可能かどうかを示す。 | デコードメソッド |
| 初期化定数 | 起動時に設定される初期値。 | デコードメソッド |

#### `verification_result` (検証結果)
バイナリ検証の結果と、不備があった場合の情報を保持する。 `{LightweightVerifier}`

| 項目名 | 機能と役割 | 備考（制約、型など） |
| :--- | :--- | :--- |
| 検証成功フラグ | すべての形式・意味検証をパスしたか。 | ブール値 |
| 異常箇所特定 | 検証失敗時のバイナリ内オフセットと範囲。 | データ範囲 |

## 4. 動的モデル

### 4.1 アルゴリズム
- **バイナリパース**: ROM上のデータを `std::span` でラップし、境界チェックを行いながら順次読み取る。
- **module_view 構築**: 関数ボディやエクスポート名を抽出し、ソート済みインデックス付き配列として構築する。検索には二分探索を用いる。 `{AccessDictionary}`
- **依存関係解決**: インポートセクションをスキャンし、必要なモジュールが未ロードの場合は `module_reader` を介して再帰的にロードを試みる。 `{MultiModule_Support}`

### 4.2 状態遷移図
```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Parsing: load_module
    Parsing --> Verifying: header_ok
    Verifying --> Ready: verify_ok
    Verifying --> Error: verify_fail
    Parsing --> Error: parse_fail
    Ready --> Idle: unload
```

### 4.3 内部シーケンス
#### モジュールロードシーケンス
```mermaid
sequenceDiagram
    participant Client as LoaderClient
    participant Loader as WasmLoader
    participant Alloc as BumpAllocator
    participant ROM as WasmBinary
    
    Client->>Loader: load_module(binary_ptr)
    Loader->>Alloc: allocate(module_view)
    Loader->>ROM: read_header
    Loader->>Loader: verify_magic_and_version
    Loader->>ROM: scan_sections
    Loader->>Alloc: allocate(section_index)
    Loader->>Loader: build_dictionaries
    Loader-->>Client: module_view_ptr
```

## 5. インターフェイス定義

### 5.1 公開API
外部から利用可能なオブジェクト指向APIを定義する。

#### `load`
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | ROM上のWASMバイナリをパースし、内部レジストリに登録してビューを取得する。 |
| 引数と役割 | `binary_ptr`: バイナリ先頭, `size`: データサイズ |
| 期待する結果 | 正常：索引構築済みの `module_view` ポインタ。異常：NULL。 |

#### `lookup`
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 登録済みのモジュールを名前で検索し、そのビューを取得する。 |
| 引数と役割 | `name`: 検索するモジュール名 |
| 期待する結果 | 正常：該当するビューのポインタ。異常：NULL。 |

#### `get_section`
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 指定されたモジュール内の特定のWASMセクションの範囲情報を取得する。 |
| 引数と役割 | `view`: module_view, `id`: セクション識別子 |
| 期待する結果 | 正常：ROM上の範囲を示す `section_span`。 |
| 事前条件 | `view` が有効、かつロード済みであること。 |
| 事後条件 | なし。 |
| 不変条件 | 範囲外アクセスが発生しないこと。 |
| エラー時の挙動 | 存在しないセクションの場合はサイズ0の範囲を返す。 |
| 補足 | インタープリタやJITが命令を読み出す際に使用する。 |

#### `lookup_export`
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | モジュールが公開している関数やグローバル変数を名前で検索し、そのインデックスを取得する。 |
| 引数と役割 | `view`: module_view, `name`: エクスポート名 |
| 期待する結果 | 正常：WASMインデックス値。異常：エラーID。 |
| 事前条件 | `view` のエクスポート辞書が構築済みであること。 |
| 事後条件 | なし。 |
| 不変条件 | 検索は `constexpr` に準じた高速な方式で行われること。 |
| エラー時の挙動 | 見つからない場合は無効値を返す。 |
| 補足 | 二分探索により O(log N) の性能を実現する。 |

### 5.2 URI/IPCインターフェイス
本コンポーネントは vSoC 内部で使用されるライブラリであり、直接のIPCインターフェイスは持たない。

## 6. 制約達成の方策

### 6.1 性能制約と方策
- **目標**: モジュールロード時間を最小化する。
- **方策**: `{ROMParsing}` `{AccessDictionary}` RAMへのコピーを排除し、主要な要素を索引化することで、実行時の探索コストを抑える。

### 6.2 メモリ制約と方策
- **目標**: ロード時のRAM消費を極小化する。
- **方策**: `{BumpAllocator}` `{NoStdVector}` バンプアロケータを使用し、断片化を防止しつつ、固定長配列による索引管理を行う。

### 6.3 安全性制約と方策
- **目標**: 不正なWASMバイナリによるクラッシュを防止する。
- **方策**: `{LightweightVerifier}` `{Wasm32Only}` ロード時にマジック値、バージョン、セクション境界の整合性を検証し、不正なバイナリを拒否する。

## 7. 設計完了チェックリスト（網羅性確認）
- [x] Tier 3 (Implementation Domain) に基づき設計となっているか
- [x] ローダの責務が明確に定義されているか
- [x] コンポーネントの責務が明確に定義されているか
- [x] 内部設計（データ構造、ブロック図、クラス、アルゴリズム）が適切に定義されているか
- [x] 内部ブロック図（静的）とシーケンス/状態遷移図（動的）がセットで定義されているか
- [x] 公開APIのメソッド名が英語で記述され、事前/事後条件が明確か
- [x] 非機能制約（性能、メモリ、安全性）に対する具体的な方策が明示されているか
- [x] 設計の交差点（トレードオフ）が解消されているか
- [x] 上位の要求 `{Keyword}` とのトレーサビリティが確保されているか
