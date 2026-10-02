# 静的コンテナ語彙 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`system_containers.md`](docs/components/tier1_core/system_containers.md)
参考実装: [`flat_view_concept.py`](docs/components/tier1_core/concepts/flat_view_concept.py)

検索ビューの単調縮小、ビット書き込みの近傍非破壊、探索範囲と参照回数を検証する。可変ストレージでは、固定容量、拒否時の状態保全、削除後の再利用、借用ビューへの追従を検証する。

実行テストは [`test_containers.py`](experiments/pysim/qa/tier1_core/test_containers.py) とする。例示ケースと生成された操作列を併用する。期待値は設計正本から導き、標準の集合・辞書・整数表現で計算する。

## 2. テストケース一覧

<!-- traceability: {META_BinarySearch} {PackedBitView} {FlatViewNarrowing} {GOTCHA-CONT-04} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-CONT-01 | 登録値、不在キー、対数探索 | 0〜1024要素の整列済み表 | 先頭・中間・末尾・不在キーを検索する | 独立辞書と結果が一致する。実体参照回数は `n.bit_length() + 2` 以下である | {META_BinarySearch}、`test_cont_01_flat_map_view_find_binary_search` |
| TEST-CONT-02 | `narrow`の単調縮小性 | 任意のビュー | `narrow(lo, hi)`を連続適用 | 各段の区間が前段の部分集合になる（決して広がらない） | narrow 不変条件 |
| TEST-CONT-03 | `slice`の単調縮小性・境界クランプ | 任意のビュー | 区間外のfirst/lastを指定 | デバッグ時はassert、リリース相当では現在区間へクランプ | slice |
| TEST-CONT-04 | `flat_set_view.contains`は値を返さない | 集合ビュー | `contains(key)` | bool のみを返し、値列を保持しない | contains |
| TEST-CONT-05 | `bit_view`の隣接要素非破壊 | 2bit幅、複数要素 | 1要素を`put`で書き換え | 隣接要素のビットが変化しない | at/put 不変条件, flat_view_concept.py |
| TEST-CONT-06 | `bit_view`のバイト境界非依存slice | 非バイト境界のfirstでslice | slice実行 | ビット原点が端数を吸収し、正しく動作する | bit_view「バイト境界の制約を課さない」 |
| TEST-CONT-07 | `Bits`は1,2,4のみ許容 | Bits=3等の不正値 | BitView構築 | `static_assert`相当（Python実装では`assert`）で拒否される | 「8ビットの約数のみ」 |
| TEST-CONT-08 | 基数表による局所探索 | 空バケットを含む2範囲の表 | 登録値、空バケット、表外のキーを検索する | 結果が一致する。実体参照は選択範囲内で対数上限以下となる。空バケットと表外では実体を読まない | `radix_binary_tree_view`、`test_cont_08_radix_binary_tree_view_coarse_radix_lookup` |
| TEST-CONT-09 | カード状態による検索の事前拒否 | 疎な登録PCと2ビットのカード表 | 0・1・2・3の状態、同じカードの不在PC、表外PCを検索する | 状態3だけ登録値を返す。状態0〜2と表外では疎マップを読まない。同じカードの不在PCは空を返す | `lookup_jit_entry`、`test_cont_09_jit_entry_lookup_card_table_prefilter` |
| TEST-CONT-10 | mapとsetの型分離 | - | 型定義を確認 | `flat_set_view`は値列フィールドを持たない（`flat_map_view`の特殊形として実装されていない） | 「なぜ4つに分けるか」 |
| TEST-CONT-11 | ペア配列データ所有権と非所有Viewの完全分離 | ストレージ配列構築 | `storage.view()` | ストレージ（Owner）が実体ペア配列を所有し、Viewは所有権を持たず単一スパンとして借用参照する（多重生成しても同一配列参照） | 「所有コンテナは定義しない」 |
| TEST-CONT-12 | 生成時の整列と全要素保存 | 未整列でキーが一意の入力 | 読み取り専用ストレージを生成して全キーを検索する | キー順となり、全てのキーと値の対応を保存する。呼び出し側の入力を変更しない | Storage生成時のキー順、`test_cont_12_read_only_flat_map_sorts_input_and_preserves_pairs` |
| TEST-CONT-13 | 静的ソート配列コンテナのソート維持挿入・削除 | 構築済みマップ | 要素挿入 / 削除 | 任意順序での挿入・削除後も常にペア配列の昇順ソート状態が維持され、二分探索の不変条件が保たれる | 静的ソート配列挿入・削除 |
| TEST-CONT-14 | 可変ストレージの固定長配列事前確保とエントリカウンタ管理 | 容量指定で可変ストレージ構築 | 新規挿入、既存キー更新、容量超過挿入および削除 | 固定長バッファと有効エントリ数を保ち、既存キー更新と新規挿入は`True`、容量超過の新規挿入は状態を変えず`False`、削除はインプレースシフトされて空き末尾スロットをクリアする | 「固定長配列と有効エントリカウント規約」, `flat_view_concept.py` `test_static_flat_map_update_and_capacity_rejection`, `{GLOBAL_Policy_Memory}`, `{META_NoStdVector}` |
| TEST-CONT-15 | 読み取り専用ビットビュー | 幅1・2・4の不変な状態列 | 全体と部分範囲を読み、公開APIと境界を検査する | 整数表現から導いた値と一致する。両ビューに`put`がない。範囲外の読み出しと拡張を拒否する | `ReadOnlyBitStorage`、`test_cont_15_read_only_bit_views_have_no_write_api` |
| TEST-CONT-16 | 非単調射影の索引順 | 元のキー順と射影順が異なる3キー | 生成・挿入・削除後に検索する | 登録値と不在結果が正しい。基数表の固定容量を保つ | `test_cont_16_radix_projection_keeps_lookup_ranges_ordered_and_bounded` |
| TEST-CONT-17 | マップと基数表の操作履歴 | 容量0〜8、射影なし・バイト順反転の索引 | 最大60回の挿入・更新・拒否・削除・クリアを行う | 各段で全内容と検索結果が辞書モデルに一致する。固定バッファ、末尾クリア、基数境界、借用追従を保つ | {GOTCHA-CONT-04}、`test_cont_17_lookup_operation_histories_preserve_contract` |
| TEST-CONT-18 | 集合の操作履歴 | 容量0〜8、重複を含むキー | 最大60回の挿入・拒否・削除・クリアを行う | 各段で全キーと所属判定が集合モデルに一致する。拒否と重複挿入では内容を保つ。固定バッファと借用追従を保つ | {GOTCHA-CONT-04}、`test_cont_18_set_operation_histories_preserve_contract` |
| TEST-CONT-19 | 多段ビット部分範囲への書き込み | 幅1・2・4、任意の初期バイト列 | 非バイト境界を含む多段sliceへ最大30回書き込む。範囲外添字と不正値も指定する | 整数モデルから導いた全バイト列に一致する。指定要素以外は保存される。拒否時は全バイトが不変である | {PackedBitView}、`test_cont_19_nested_bit_writes_preserve_every_other_element` |
| TEST-CONT-20 | 絞り込みの連続適用 | 空表を含む一意なキー列 | 2〜6回のキー範囲による絞り込みを行う | 線形フィルタで求めた部分集合と一致する。除外済みのキーを復元しない | {FlatViewNarrowing}、`test_cont_20_narrowed_views_never_restore_excluded_keys` |
| TEST-CONT-21 | ビット状態列の一括更新 | 最終バイトに未使用領域を持つ幅1・2・4の表 | 借用後にfill・put・clearを行う | 全論理要素と借用ビューへ更新が反映される。裏打ちバッファの同一性と容量を保つ | {PackedBitView}、`test_cont_21_bit_storage_fill_clear_updates_borrowed_view` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）
<!-- traceability: {GOTCHA-CONT-04} -->

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-CONT-01 | `bit_view` の 8 の約数ビット幅制約（境界跨ぎの完全排除） | `bits in (1, 2, 4)` | 任意のビット幅で構築を試行 | 3bit や 5bit など 8 の約数以外のビット幅は即座に拒絶される。**実装の勘所**: 1要素がバイト境界を跨ぐと、2回のメモリアクセスと複雑なビット合成が必要になり、ロード性能とアトミック性が著しく劣化する | `system_containers.md` |
| GOTCHA-CONT-02 | ビューの単調縮小性（親スパン拡張の絶対禁止） | 任意の構築済みビュー | 親ビューの境界外（`first < self.first` または `last > self.last`）を指定して `slice` | デバッグビルドではアサーション違反で停止し、リリースビルドでは未定義動作を避けるため現在の区間へクランプする（いずれの場合もビューは縮小のみ可能で親境界を超えて拡張されない）。**実装の勘所**: ビューが親の境界を超えて拡張できると、メモリ外アクセス（バッファオーバーラン）を引き起こす | `system_containers.md` , `{FlatViewNarrowing}` |
| GOTCHA-CONT-03 | `flat_set_view` と `flat_map_view` の型分離（不要メンバの排除） | 集合判定コンテナの構築 | フィールド構成を走査 | `flat_set_view` は値列スパンを持たず、ポインタと長さ（計2ワード）のみで構成される。**実装の勘所**: `flat_set_view` を `flat_map_view` の特殊形（ダミー値付き）として実装すると、キャッシュ効率が半減しレジスタ渡し最適化が阻害される | `system_containers.md` 「なぜ4つに分けるか」 |
| GOTCHA-CONT-04 | ミュータブルストレージの動的拡張禁止と借用中Viewへの動的伝播 | 可変ストレージ構築とView借用 | 要素追加・削除 | 動的な再確保（list.append/insert）を一切行わず固定長バッファ内でインプレースシフトが完結する。借用中の非所有ViewはStorageのcount更新とシフト済みバッファに即座に追従して正しく二分探索できる。**実装の勘所**: 動的再確保を許すと組み込みのメモリ決定論が崩壊し、View側も古いダングリングバッファを参照する危険がある | `system_containers.md` , `{GLOBAL_Policy_Memory}`, `{META_NoStdVector}` |

## 3. テスト検証実績と網羅状況

通常ケースはTEST-CONT-01〜21とする。生成試験は17〜20に対応する。17は3構成、18は集合、19は3ビット幅を各80例まで探索する。20は100例まで探索する。固定の容量到達・更新・拒否・削除・再利用の操作列も必ず実行する。

Hypothesisの失敗出力には縮小された入力と再現情報を含める。探索の上限は実行時間を限定するための設定値であり、全入力の網羅率ではない。行・分岐カバレッジは合否条件にしない。

実行コマンドを示す。

```bash
uv run pytest -q experiments/pysim/qa/tier1_core/test_containers.py
```

通常ランナーのコンテナ登録も同じファイルを実行する。実行結果と変異検査の証跡は [`test_reconstruction_review.md`](docs/qa/test_reconstruction_review.md) に記録する。

## 4. 未検証・スコープ外

- C++23実装のゼロコスト性、物理配置、標準ソート関数の採用は本試験では検証しない。参照シミュレータの動作適合と区別する。
- 生成試験は容量0〜8、有限のキー領域、有限長の操作列に限定する。全入力や全履歴の完全探索は主張しない。
- 親ストレージ変更中の絞り込み済み検索ビューの区間追従は、位置とキーのどちらを保持する契約か未確定である。全件の借用ビューへの追従だけを合格根拠とする。
- `RingBuffer`と`StaticVector`の個別API契約は本ケースIDへ混在させない。ログのFIFO・上書きはロギングのテスト仕様を正本とする。
