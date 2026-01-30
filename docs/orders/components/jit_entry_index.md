# コンポーネント設計：JIT Entry Index

## 1. コンセプト
JIT Entry Index は、WASM PC とそれに対応するネイティブコードのアドレスの紐付けを管理する。
インタープリタの実行ループ内という極めてクリティカルなパスで呼び出されるため、**カードマーキング**（コンパイル状態の高速判定）と**カードグループインデックス**（二分探索の範囲絞り込み）を組み合わせた高速な検索アルゴリズムを提供する。また、Active/Old ダブルバッファ間のトレース昇格（Promotion）を制御し、限られたメモリ内での動的キャッシュ代謝を実現する。 `{SimpleJITArchitecture}` `{JIT_DoubleBuffer_Cache}`

## 2. 静的モデル

### 2.1 データ構造
- **`jit_entry` Table**: `pc` (WASMオフセット) と `code_offset` (キャッシュ内位置) のペアを PC 昇順で保持する配列。
- **Card Group Index**: 複数のカードをグループ化し、各グループの最初の `jit_entry` の開始インデックスを保持する。検索範囲の絞り込みに使用する。

### 2.2 内部ブロック図
```mermaid
graph TD
    Search[Search Request] --> Mark[Card Marking check]
    Mark --> Card[Card Group Index lookup]
    Card --> BinSearch[Binary Search in Range]
    BinSearch --> Result{Hit?}
    Result -->|Active Hit| Return[Return Address]
    Result -->|Active Miss| OldSearch[Search Old Cache]
    OldSearch -->|Old Hit| Promote[Promote to Active]
```

### 2.3 主要なクラス・構造体・配列・定数

#### `jit_entry`
PCとコードアドレスの対応。

| 構成項目 | 機能と役割 | 備考 |
| :--- | :--- | :--- |
| `pc` | WASMバイトコードオフセット | 32bit |
| `code_offset` | キャッシュ先頭からのオフセット | 16bit |

#### `card_group_index`
検索範囲を絞り込むためのインデックス。

| 構成項目 | 機能と役割 | 備考 |
| :--- | :--- | :--- |
| `first_entry_idx` | そのカードグループ範囲の最初の `jit_entry` の添字 | 16bit |

## 3. 動的モデル

### 3.1 アルゴリズム

#### 高速検索 (Lookup)
1. **カードマーキング確認**: カード単位でコンパイル状態を保持する `Hotspot Bitmap` を確認し、`COMPILED` 状態でなければ即座に終了する。
    - ※ カード単位の管理であるため、コンパイルされていないPCでも同じカード内の他PCの影響でパスする場合がある（後に二分探索で厳密にチェックされる）。
2. **カードグループ検索**: 検索対象の PC をシフト演算し、対応する `card_group_index` を取得する。これにより二分探索の範囲 `[low, high]` を限定する。
3. **二分探索**: `jit_entry` 配列の限定された範囲から `pc` を検索する。
4. **オンデマンド・キューイング**: Active/Old キャッシュでミスし、かつ `Hotspot Bitmap` が `COMPILED` 状態である場合は、対象の PC を `Compile Queue` へプッシュし、インタープリタ実行を継続する。

### 3.2 状態遷移図
本コンポーネントは管理情報の更新と検索を行うため、明確な内部状態（ステートマシン）は持たないが、エントリの `Valid/Invalid` を管理する。

### 3.3 内部シーケンス
```mermaid
sequenceDiagram
    participant I as Interpreter
    participant M as JIT Searcher
    participant A as Active Index
    participant O as Old Index

    I->>M: Lookup(PC)
    M->>A: Search(PC)
    alt Active Hit
        A-->>M: code_addr
    else Active Miss
        M->>O: Search(PC)
        alt Old Hit
            O-->>M: code_addr
            M->>M: Promote to Active
        else Old Miss
            alt Bitmap == COMPILED
                M->>Q: Push(PC)
            end
            M-->>I: NULL (Fallback)
        end
    end
    M-->>I: code_addr
```

## 4. インターフェイス定義

### 4.1 公開API

#### lookup
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 指定されたPCに対応するネイティブコードアドレスを返す。 |
| 引数と役割 | `pc`: WASM PC |
| 期待する結果 | ネイティブアドレス、または NULL。 |

#### register_entry
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 新しいPCとコードアドレスのペアを登録する。 |
| 引数と役割 | `pc`, `addr` |
| 事前条件 | `pc` 順を維持して挿入する必要がある（または挿入後にソート）。 |

## 5. 制約達成の方策

### 5.1 性能制約
- **方策**: カードグループインデックスによる範囲絞り込みと、二分探索の組み合わせにより、多数のトレースが存在しても高速な検索を維持する。

### 5.2 メモリ制約
- **方策**: `{JIT_DoubleBuffer_Cache}` による Copy-GC 方式により、断片化を防ぎつつ、実行頻度の低いコードを自然に破棄（代謝）させる。

## 6. 設計完了チェックリスト
- [x] 検索アルゴリズムが記述されているか
- [x] カードインデックスの役割が明確か
- [x] 要求キーワード `{JIT_DoubleBuffer_Cache}` と紐づいているか
