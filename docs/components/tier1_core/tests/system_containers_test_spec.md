# 静的コンテナ語彙 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier1_core/system_containers.md`
参考実装: `docs/components/tier1_core/concepts/flat_view_concept.py`

`flat_map_view`/`flat_set_view`/`radix_binary_tree_view`/`bit_view`の4型の不変条件（単調縮小、非破壊書き込み、探索計算量）を検証する。

## 2. テストケース一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| CONT-01 | `flat_map_view.find`はO(log n)二分探索 | ソート済み (key, value) ペア配列 | `find(key)` | キーを二分探索し、存在すればペアの値を返し、なければ空を返す。区間長nに対し比較回数がO(log n) | §5.1 find |
| CONT-02 | `narrow`の単調縮小性 | 任意のビュー | `narrow(lo, hi)`を連続適用 | 各段の区間が前段の部分集合になる（決して広がらない） | §5.1 narrow 不変条件 |
| CONT-03 | `slice`の単調縮小性・境界クランプ | 任意のビュー | 区間外のfirst/lastを指定 | デバッグ時はassert、リリース相当では現在区間へクランプ | §5.1 slice |
| CONT-04 | `flat_set_view.contains`は値を返さない | 集合ビュー | `contains(key)` | bool のみを返し、値列を保持しない | §5.1 contains |
| CONT-05 | `bit_view`の隣接要素非破壊 | 2bit幅、複数要素 | 1要素を`put`で書き換え | 隣接要素のビットが変化しない | §5.1 at/put 不変条件, flat_view_concept.py |
| CONT-06 | `bit_view`のバイト境界非依存slice | 非バイト境界のfirstでslice | slice実行 | ビット原点が端数を吸収し、正しく動作する | §3.3 bit_view「バイト境界の制約を課さない」 |
| CONT-07 | `Bits`は1,2,4のみ許容 | Bits=3等の不正値 | BitView構築 | `static_assert`相当（Python実装では`assert`）で拒否される | §3.3「8ビットの約数のみ」 |
| CONT-08 | `radix_binary_tree_view`のO(1)粗索引 | Radix Table構築済み | `find(key)` | プレフィックスで範囲を即座に絞り込み、範囲内のみ二分探索する | §3.1 radix_binary_tree_view, flat_view_concept.py `RadixBinaryTreeView.find` |
| CONT-09 | JITエントリ検索のカードマーキング事前フィルタ | カードがCOMPILEDでない | `lookup_jit_entry`相当 | 二分探索/Radix探索を行わずNoneを返す（O(1)事前フィルタ） | §4.1「JIT entry lookup」, flat_view_concept.py `lookup_jit_entry` |
| CONT-10 | mapとsetの型分離 | - | 型定義を確認 | `flat_set_view`は値列フィールドを持たない（`flat_map_view`の特殊形として実装されていない） | §1「なぜ4つに分けるか」 |
| CONT-11 | ペア配列データ所有権と非所有Viewの完全分離 | ストレージ配列構築 | `storage.view()` | ストレージ（Owner）が実体ペア配列を所有し、Viewは所有権を持たず単一スパンとして借用参照する（多重生成しても同一配列参照） | §1「所有コンテナは定義しない」, §3.3 |
| CONT-12 | 静的ソート配列コンテナの標準ソート（constexpr対応） | 未ソートのペア配列 | ソート実行 | C++標準の`std::sort`相当でキー昇順にソートされ、自前ソート関数を抱えずにソート済み状態になり`view().find()`で全要素探索可能 | §3.3, §4.1 静的ソート配列 |
| CONT-13 | 静的ソート配列コンテナのソート維持挿入・削除 | 構築済みマップ | 要素挿入 / 削除 | 任意順序での挿入・削除後も常にペア配列の昇順ソート状態が維持され、二分探索の不変条件が保たれる | §3.3 静的ソート配列挿入・削除 |
| CONT-14 | 可変ストレージの固定長配列事前確保とエントリカウンタ管理 | 容量指定で可変ストレージ構築 | 要素挿入（容量上限まで / 超過時）および削除 | バッファ長が初期化時から常にCapacity固定でリサイズされず、countが現在有効エントリ数を正確に追跡する。容量到達時の新規挿入はFalseを返して拒絶され、削除時はインプレースシフトされて空き末尾スロットがクリアされる | §1「固定長配列と有効エントリカウント規約」, `{GLOBAL_Policy_Memory}`, `{META_NoStdVector}` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| CONT-GOTCHA-01 | `bit_view` の 8 の約数ビット幅制約（境界跨ぎの完全排除） | `bits in (1, 2, 4)` | 任意のビット幅で構築を試行 | 3bit や 5bit など 8 の約数以外のビット幅は即座に拒絶される。**実装の勘所**: 1要素がバイト境界を跨ぐと、2回のメモリアクセスと複雑なビット合成が必要になり、ロード性能とアトミック性が著しく劣化する | `system_containers.md` §3.3 |
| CONT-GOTCHA-02 | ビューの単調縮小性（親スパン拡張の絶対禁止） | 任意の構築済みビュー | 親ビューの境界外（`first < self.first` または `last > self.last`）を指定して `slice` | アサーション違反で停止する（ビューは縮小のみ可能）。**実装の勘所**: ビューが親の境界を超えて拡張できると、メモリ外アクセス（バッファオーバーラン）を引き起こす | `system_containers.md` §5.1, `{FlatViewNarrowing}` |
| CONT-GOTCHA-03 | `flat_set_view` と `flat_map_view` の型分離（不要メンバの排除） | 集合判定コンテナの構築 | フィールド構成を走査 | `flat_set_view` は値列スパンを持たず、ポインタと長さ（計2ワード）のみで構成される。**実装の勘所**: `flat_set_view` を `flat_map_view` の特殊形（ダミー値付き）として実装すると、キャッシュ効率が半減しレジスタ渡し最適化が阻害される | `system_containers.md` §1「なぜ4つに分けるか」 |
| CONT-GOTCHA-04 | ミュータブルストレージの動的拡張禁止と借用中Viewへの動的伝播 | 可変ストレージ構築とView借用 | 要素追加・削除 | 動的な再確保（list.append/insert）を一切行わず固定長バッファ内でインプレースシフトが完結する。借用中の非所有ViewはStorageのcount更新とシフト済みバッファに即座に追従して正しく二分探索できる。**実装の勘所**: 動的再確保を許すと組み込みのメモリ決定論が崩壊し、View側も古いダングリングバッファを参照する危険がある | `system_containers.md` §1, `{GLOBAL_Policy_Memory}`, `{META_NoStdVector}` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- C++23 `std::span`ベースの実装そのもののゼロコスト性（`{META_ZeroCostAbstraction}`）。
