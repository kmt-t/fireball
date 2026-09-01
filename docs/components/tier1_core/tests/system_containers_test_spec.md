# 静的コンテナ語彙 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier1_core/system_containers.md`
参考実装: `docs/components/tier1_core/concepts/flat_view_concept.py`

`flat_map_view`/`flat_set_view`/`radix_binary_tree_view`/`bit_view`の4型の不変条件（単調縮小、非破壊書き込み、探索計算量）を検証する。

## 2. テストケース一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| CONT-01 | `flat_map_view.find`はO(log n)二分探索 (SoA構造) | ソート済みキー列と値列のSoA | `find(key)` | キー列上のみを二分探索し、存在すれば添字対応する値を返し、なければ空を返す。区間長nに対し比較回数がO(log n) | §5.1 find, §3.1 SoA |
| CONT-02 | `narrow`の単調縮小性 | 任意のビュー | `narrow(lo, hi)`を連続適用 | 各段の区間が前段の部分集合になる（決して広がらない） | §5.1 narrow 不変条件 |
| CONT-03 | `slice`の単調縮小性・境界クランプ | 任意のビュー | 区間外のfirst/lastを指定 | デバッグ時はassert、リリース相当では現在区間へクランプ | §5.1 slice |
| CONT-04 | `flat_set_view.contains`は値を返さない | 集合ビュー | `contains(key)` | bool のみを返し、値列を保持しない | §5.1 contains |
| CONT-05 | `bit_view`の隣接要素非破壊 | 2bit幅、複数要素 | 1要素を`put`で書き換え | 隣接要素のビットが変化しない | §5.1 at/put 不変条件, flat_view_concept.py |
| CONT-06 | `bit_view`のバイト境界非依存slice | 非バイト境界のfirstでslice | slice実行 | ビット原点が端数を吸収し、正しく動作する | §3.3 bit_view「バイト境界の制約を課さない」 |
| CONT-07 | `Bits`は1,2,4のみ許容 | Bits=3等の不正値 | BitView構築 | `static_assert`相当（Python実装では`assert`）で拒否される | §3.3「8ビットの約数のみ」 |
| CONT-08 | `radix_binary_tree_view`のO(1)粗索引 | Radix Table構築済み | `find(key)` | プレフィックスで範囲を即座に絞り込み、範囲内のみ二分探索する | §3.1 radix_binary_tree_view, flat_view_concept.py `RadixBinaryTreeView.find` |
| CONT-09 | JITエントリ検索のカードマーキング事前フィルタ | カードがCOMPILEDでない | `lookup_jit_entry`相当 | 二分探索/Radix探索を行わずNoneを返す（O(1)事前フィルタ） | §4.1「JIT entry lookup」, flat_view_concept.py `lookup_jit_entry` |
| CONT-10 | mapとsetの型分離 | - | 型定義を確認 | `flat_set_view`は値列フィールドを持たない（`flat_map_view`の特殊形として実装されていない） | §1「なぜ4つに分けるか」 |
| CONT-11 | 配列データ所有権と非所有Viewの完全分離 | ストレージ配列構築 | `storage.view()` | ストレージ（Owner）が実体配列を所有し、Viewは所有権を持たず借用参照する（多重生成しても同一配列参照） | §1「所有コンテナは定義しない」, §3.3 |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- C++23 `std::span`ベースの実装そのもののゼロコスト性（`{META_ZeroCostAbstraction}`）。
