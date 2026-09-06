# pysim ソースコード評価ルーブリック (pysim Review Rubric)

本ドキュメントは、Fireball Hypervisor の参照シミュレータ（`experiments/pysim/`）におけるソースコード品質・組込みC++23移植可能性を評価するための公式ルーブリックである。

pysim は「**ヒープ割り当て・例外・RTTI・動的コンテナのない静的型付け言語（C++23）**」を Python 上で事前実証するシミュレータである。一般的な Python イディオムは適用されず、以下の6大評価軸に厳格に適合しなければならない。

---

## 1. 6大評価軸と判定基準

### 1. 仕様書との一致性 (Specification Parity & Invariants)
- **要求・設計書との完全一致**:
  - `docs/components/**` の設計仕様書で策定された状態機械、遷移トリガー、メモリレイアウト、通信規約とコードの制御フローが完全に一致しているか。
- **Gotchas & Invariants（実装の勘所・不変条件）の同期**:
  - クラスや関数の Docstring / コメントに固有識別子（例: `COOS-GOTCHA-01`, `LOAD-GOTCHA-02` 等）が明記されているか。
  - 設計理由（なぜその制約・構造になっているか）が記載されているか。
- **上位正本ルールの遵守**:
  - 仕様書とコードで食い違いがある場合、仕様書側が常に正本である。

### 2. 型が書いてあるか (Strict Static Typing & No Any)
- **`typing.Any` の完全禁止 (アンチパターン I)**:
  - コードベース全体で `typing.Any` の使用が **0 件** であること。
- **全引数・戻り値・属性の完全型付け**:
  - すべての公開・内部関数、メソッド（`__init__` 等を除く）の引数および戻り値に具象型が明示されているか。
  - コンテナや Generic（`Generic[T]`）の型引数が裸（Raw Generic: `list`, `tuple`, `Channel` 等）になっていないか。
- **代数的データ型の活用**:
  - 成功・失敗の表現に `Result[T, Never]` や `Result[Never, E]` を活用し、`Any` や緩い Union を排除しているか。

### 3. set、dict、動的配列を使っていないか (No Dynamic Containers: set/dict/unbounded list)
- **動的コンテナの完全禁止 (`{GLOBAL_Policy_Memory}`)**:
  - Python の素の `dict` や `set` が実行時データ構造（ルックアップ、テーブル、キャッシュ等）として使われていないか。
- **システムコンテナ語彙 (`core/system_containers.py`) の強制**:
  - 固定長配列、`BitView`/`FlatMapView`/`FlatSetView`/`RadixBinaryTreeView`（読み取り専用）、または `MutableFlatMapStorage`/`MutableFlatSetStorage`/`MutableRadixBinaryTreeStorage`（固定容量可変）に置き換えられているか。
- **伸縮リスト（`.append`, `.insert`, `.pop`）の排除**:
  - 無制限に伸縮する `list` を禁止し、順次蓄積には `StaticVector`（LIFO / bounded capacity）、リング巡回には `RingBuffer`（FIFO / bounded capacity）を使用しているか。
  - 容量上限の根拠（`FB_CONF_*` 定数等）が明記されているか。

### 4. 計算量を意識したコードか (Algorithmic Complexity & Determinism)
- **決定論的計算量 ($O(1)$ / $O(\log N)$)**:
  - スケジューリング、ディスパッチ、IPCルーティングが決定論的 $O(1)$ で実行されるか。
  - ホットパスでの線形探索（$O(N)$ ループ探索）を排除し、バイナリサーチ（$O(\log N)$）やインデックス直接参照（$O(1)$）になっているか。
- **ホットパスでの動的メモリ確保の排除**:
  - ディスパッチループ内やイベントハンドラ内で、一時オブジェクト（タプル、リスト、辞書等）のアロケーションを繰り返していないか（GC プレッシャー・ヒープ断片化の排除）。
- **ロード時事前計算とキャッシュ**:
  - ロード時・パース時に確定する静的メタデータ（制御フロー、ローカル変数レイアウト、JIT対象判定等）を実行時に毎回再計算していないか。

### 5. 後方互換性がないか (No Dead Fallbacks & Unintended Regressions)
- **不要な後方互換フォールバックコードの排除 (No Dead Compatibility Paths)**:
  - 仕様改定によって廃止された古い引数、旧フォーマット対応のフォールバック分岐、未整理の二重管理コードが残っていないか。
  - 組み込み C++ では、使われない後方互換コードは単なる ROM 容量の浪費・検証負荷の増大（デッドコード）となるため、速やかに削除・一本化されているか。
- **意図しない互換性破壊の防止 (No Unintended Regressions)**:
  - 11 の統合シナリオ（`scenarios/`）や公開インターフェースが要求する振る舞いを壊していないか。

### 6. リードオンリー（ROM配置）にできるデータをリードライト（RAM配置）にしてないか (ROM vs RAM Placement)
- **イミュータブル定数の ROM 化**:
  - 定数テーブル、オプコード定義、WASM モジュールの不変セクション、JIT ステンシルバイナリ列など、ロード時/コンパイル時に確定して書き換えないデータが、可変な `bytearray` や可変 `list`、可変オブジェクトで保持されていないか。
- **ROM 配置可能構造の採用**:
  - 静的データは `bytes`（不変バイト列）、`tuple`、`ReadOnlyFlatMapStorage`、`ReadOnlyBitStorage`、`MappingProxyType` 等の不変構造に配置し、組み込み Flash ROM に焼き込める設計になっているか。
  - RAM（可変領域）として確保すべきもの（TCB の実行時コンテキスト、可変バッファ、スタック等）と、ROM（不変領域）に配置すべき静的データが物理的に分離されているか。

---

## 2. 重要度（Severity）の判定基準

- **CRITICAL (重大な欠陥 / C++ 移植不可)**:
  - 実行時データ構造としての `dict` / `set` の使用。
  - `typing.Any` の使用。
  - 仕様書との真っ向からの乖離・不変条件（Gotchas）の違反。
  - ホットパスにおける無制限ループや致命的な $O(N)$ ボトルネック。
- **MAJOR (主要な改善項目 / 組込み規約違反)**:
  - 無制限伸縮 `list`（`.append` 等）の使用（`StaticVector` 未移行）。
  - リードオンリーデータを `bytearray` などの可変 RAM 構造で保持（ROM 配置違反）。
  - 型注釈の欠落（引数・戻り値・Raw Generic）。
  - 不要な後方互換用レガシー分岐（デッドコード）の放置。
  - 動的型検査 `isinstance` 等の使用（No RTTI 違反）。
- **MINOR (軽微な指摘 / 推奨改善)**:
  - 容量上限の根拠コメントの不足。
  - エラーメッセージや Docstring の表現揺れ。
  - ローカル変数レイアウト等の事前計算キャッシュ化によるさらなる最適化の余地。
