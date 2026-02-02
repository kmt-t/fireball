# コンポーネント設計：Copy-and-Patch Engine

## 1. コンセプト
Copy-and-Patch Engine は、WASM 命令に対応する事前生成されたネイティブコードテンプレートを結合・修正することで、ネイティブ実行バイナリを高速に生成する JIT コンパイラの核心部である。レジスタ割り当てや命令選択などの計算コストの高い最適化をビルド時にオフロードし、実行時は単純なメモリコピーと特定箇所への定数書き込み（パッチ）のみを行うことで、「Zero Compile Cost」を目指す。 `{JIT_CopyAndPatch}` `{JIT_ZeroCompileCostTheorem}` `{LowLatencyJIT}`

## 2. アーキテクチャ分類 (Tier 3: Implementation Domain)
本コンポーネントは **Tier 3 (実装ドメイン)** に属する。JITコンパイラの内部アルゴリズムとして機能し、特定のアーキテクチャに依存したコード生成に特化したモジュールである。 `{3TierSeparation}` `{JIT_CopyAndPatch}`

## 3. 静的モデル

### 3.1 データ構造 (Natural OO)
- **`CopyAndPatchEngine` (Class)**: テンプレートの選択、コピー、およびパッチ適用を一括して行う主要クラス。
- **命令テンプレート (Template)**: パッチ用の「穴（Hole）」を含むネイティブ命令列の雛形。
- **パッチ定義 (Patch Info)**: テンプレート内の修正箇所のメタデータ。

### 3.2 内部ブロック図
```mermaid
graph TD
    Queue[Compile Queue] -->|Pop PC| Engine[CopyAndPatchEngine]
    Engine -->|Write| Cache[Active Code Cache]
    Const[constexpr Assembler] -.->|Generate| Engine
```

### 3.3 主要なクラス・構造体・配列・定数

#### `CopyAndPatchEngine` クラス
テンプレートの解決とバイナリ操作をカプセル化する。

| 項目名 | 機能と役割 | 備考（制約、型など） |
| :--- | :--- | :--- |
| テンプレート辞書 | WASM命令に対応するJITテンプレートの検索索引。 | `jit_template_map` |
| アセンブラ参照 | 実行時に補助的な命令生成を行う場合のインターフェイス。 | `constexpr_assembler*` |

#### `jit_template` (命令テンプレート)
WASM命令に対応するネイティブバイナリの雛形。

| 項目名 | 機能と役割 | 備考（制約、型など） |
| :--- | :--- | :--- |
| 命令バイナリ | ネイティブ命令列の実体。 | `uint32_t` 配列等 |
| パッチ箇所数 | テンプレート内で修正（パッチ）が必要なスロットの数。 | |
| パッチ情報 | 各パッチ位置のオフセットと修正方法を定義する情報の配列。 | |

## 4. 動的モデル

### 4.1 アルゴリズム

#### トレースコンパイル手順
1. **フェッチ**: WASM命令オフセットから命令を取得（フェッチ）する。
2. **テンプレート選択**: 命令に対応する `jit_template` を取得する。
3. **コピー**: キャッシュの空き領域にテンプレートの命令列をコピーする。
4. **パッチ適用 (プレースホルダ埋め)**:
    - 命令内に含まれる即値（定数）をテンプレートの指定位置に書き込む。
    - 実行コンテキストポインタやランタイムAPIのアドレスをパッチする。
    - 分岐命令の相対オフセットを計算してパッチする。
5. **ポインタ更新**: キャッシュの使用済みサイズを更新する。

### 4.2 状態遷移図
本コンポーネントはステートレスなプロセッサとして動作するため、状態遷移は省略する。

### 4.3 内部シーケンス
```mermaid
sequenceDiagram
    participant J as JIT Compiler
    participant E as Engine
    participant R as Resolver
    participant A as Applicator
    participant C as Cache

    J->>E: Compile(WASM_PC)
    E->>R: Resolve(Instruction)
    R-->>E: Template
    E->>A: Apply(Template, Context)
    A->>C: Copy Binary
    A->>C: Patch Holes (Immediates, etc.)
    A-->>E: Done
    E-->>J: Native Entry Address
```

## 5. インターフェイス定義

### 5.1 公開API
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | WASM命令列をネイティブコードへ変換し、キャッシュへ書き込む。 |
| 引数と役割 | `pc`: 開始位置, `dest`: 書き込み先, `runtime`: 実行環境ポインタ |
| 期待する結果 | 正常：生成されたバイナリサイズ。異常：エラーID。 |

## 6. 制約達成の方策

### 6.1 性能制約
- **方策**: `{JIT_CopyAndPatch}` により、コンパイル時間を最適化理論の限界まで短縮する。
- **方策**: ランタイムAPIフォールバック `{JIT_RuntimeAPI_Fallback}` により、複雑なエッジケースを簡素化する。

## 7. 設計完了チェックリスト
- [x] Tier 3 (Implementation Domain) に基づき設計となっているか
- [x] Copy-and-Patch の原理が記述されているか
- [x] パッチ適用のプロセスが明確か
- [x] 要求キーワード `{JIT_CopyAndPatch}` に紐づいているか
