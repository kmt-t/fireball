# WASMローダ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier2_runtime/runtime_loader.md`
参考実装: `docs/components/tier2_runtime/concepts/loader_concept.py`

ROM上WASM32バイナリのゼロコピー索引化（`ModuleView`）、V1〜V6軽量検証、バンプアロケータのトランザクショナルロールバック、ハッシュ＋`RadixBinaryTreeView`（`fireball::radix_binary_tree_view`）によるインポート解決およびシンボル検索、ファイル内データ位置からのデコード値逆引きを検証する。

## 2. テストケース一覧

### 軽量検証 (§4.3 V1-V6)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| LOAD-01 | V1 マジックナンバー | 先頭4バイトが`\0asm`でない | `prepare(binary)` | reject（`WasmVerifyError`相当） | §4.3 V1 |
| LOAD-02 | V2 バージョン | バージョンが`1`以外 | 同上 | reject | §4.3 V2 |
| LOAD-03 | V3 セクション境界 | セクションsizeがバイナリ末尾を超える | 同上 | reject | §4.3 V3 |
| LOAD-04 | V4 セクション順 | 非Customセクションが降順・重複 | 同上 | reject（Customセクション(ID=0)のみ順序制約の例外） | §4.3 V4 |
| LOAD-05 | V5 インポート/エクスポート型整合 | 関数宣言のtype_idxがTypeセクション範囲外 | 同上 | reject | §4.3 V5 |
| LOAD-06 | V6 メモリセクション境界 | 初期要求メモリサイズが`FB_CONF_GUEST_RAM_SIZE`を超える | 同上 | reject | §4.3 V6 |
| LOAD-07 | 検証失敗時の完全ロールバック | V1〜V6いずれかで失敗 | 失敗前後のアロケータwatermarkを比較 | `bump_allocator.offset`が失敗前の値に完全復元される | §4.1「トランザクション保護」, loader_concept.py `test_wasm_loader_lifecycle_and_verification` |

### ゼロコピー索引化 (§4.1)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| LOAD-10 | セクション内容をRAMへコピーしない | 正常なバイナリ | パース後、`section_view`の実装を確認 | 開始オフセットとサイズのみを保持し、内容の複製を持たない | §4.1「Zero-Copy Indexing」, `{ZeroCopyIndexing}` |
| LOAD-11 | エクスポート名はROM参照 | エクスポート名を持つバイナリ | `exports_dict`の要素を確認 | 文字列はROM上の`string_view`相当であり、RAMコピーがない | §4.1 |
| LOAD-12 | エクスポート名順ソート | 複数エクスポート（非アルファベット順で宣言） | パース後の`exports_dict`を確認 | 名前順にソートされている | §4.1「名前順にソート」 |
| LOAD-13 | ハッシュ＋RadixBinaryTreeView シンボル検索 | エクスポートシンボル登録済み | `lookup_export(name)` | FNV-1a ハッシュと RadixBinaryTreeView による $O(k)$ 探索で正しい`ExportEntry`を返す。未登録名は`None` | §4.1「シンボル検索」, `{META_AccessDictionary}`, `{META_BinarySearch}` |
| LOAD-14 | 関数アクセサの遅延デコード | 任意の関数 | `get_function(idx).get_code_stream()` | localsベクタ宣言をスキップした実行本体ストリームを返す | §3.3 function_accessor |
| LOAD-15 | グローバルアクセサ | 任意のグローバル変数宣言 | `get_global(idx).get_metadata()` | (valtype, mutable)を正しく返す | §3.3 global_accessor |

### 複数モジュール・インポート解決 (§4.1「インポートテーブル検索と依存関係解決」)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| LOAD-20 | 未解決インポートは実行不可状態 | インポートを持つモジュールをprepare（依存先未登録） | `is_ready`を確認 | `False`（実行不可） | §4.1 |
| LOAD-21 | ハッシュ＋RadixBinaryTreeView インポート解決 | 依存モジュールが先に登録済み | `resolve_imports(module)` | 文字列走査ではなくハッシュ＋RadixBinaryTreeView で $O(k)$ 解決され、`True`（`is_ready == True`）になる | §4.1, §5.1 resolve-imports |
| LOAD-22 | シンボル未発見での解決失敗 | 依存モジュールに該当エクスポートがない | `resolve_imports` | `WasmLinkError`相当（`{MultiModule_Support}`） | loader_concept.py `WasmLinkError` |
| LOAD-23 | インポート/エクスポートの型シグネチャ不一致 | 型が異なる同名エクスポート | 同上 | 拒否される | loader_concept.py `resolve_imports`の型チェック |
| LOAD-24 | モジュール登録数上限 | `FB_CONF_MAX_MODULES`（既定4）到達 | 5個目をprepare | `WasmLinkError`（レジストリ上限超過） | §4.2 |
| LOAD-25 | アンロードのLIFO制約 | 複数モジュールをロード | ロード順の逆順以外でunload | レジストリからは削除されるが、バンプアロケータのメモリは完全回収されない（ロード逆順でのみ完全回収） | §4.1「アンロード」, §5.1 unload補足 |

### 容量制約 (§4.2)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| LOAD-30 | 関数数上限 | `FB_CONF_MAX_FUNCTIONS`（256）超過 | prepare | reject/エラー | §4.2 |
| LOAD-31 | エクスポート数上限 | `FB_CONF_MAX_EXPORTS`（64）超過 | prepare | reject/エラー | §4.2 |
| LOAD-32 | LEB128の5/10バイトガード | 6バイト以上のu32 LEB128、11バイト以上のu64 LEB128 | パース | 即座にパースエラー（無限ループしない） | loader_concept.py `read_leb128_u32/u64` |

### ファイル内データ位置 & シンボルハッシュ RadixBinaryTreeView 索引 (§1, §3.1, §4.1, §5.1, {META_BinarySearch})

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| LOAD-40 | デコード済みエンティティのファイル位置登録 | WASMバイナリパース完了 | `decoded_entity_registry` の内容を確認 | 各セクション、関数コード、グローバル、データセグメントが開始・終了ファイルオフセットとともに登録されている | §3.1 `decoded_entity_registry` |
| LOAD-41 | RadixBinaryTreeView によるファイルオフセット検索 | デコード済みモジュール | `lookup_by_file_offset(offset)` を実行 | 基数表＋有界二分探索（$O(k)$ / $O(\log n)$）により、指定オフセットを包含するデコード済みエンティティが正確に返却される | §4.1「ファイル位置逆引き」, §5.1 `lookup-by-file-offset` |
| LOAD-42 | 関数バイトコード位置からの関数アクセサ逆引き | 関数コード内オフセット | `lookup_by_file_offset(code_offset)` | 該当する `FunctionAccessor`（関数インデックス・シグネチャ）が即座に特定・返却される | §4.1, §5.1 |
| LOAD-43 | データセグメント・グローバル位置の特定 | データセグメント内オフセット | `lookup_by_file_offset(data_offset)` | 該当するデータ定義またはグローバルエントリが正確に返却される | §4.1, §5.1 |
| LOAD-44 | 範囲外・未定義隙間オフセットの境界処理 | ヘッダ以前またはバイナリ終端超過オフセット | `lookup_by_file_offset(invalid_offset)` | エラー/未発見（`None` / `false`）を返しクラッシュしない | §5.1 不変条件 |
| LOAD-45 | インポートテーブルのハッシュ＋RadixBinaryTreeView 検索 | インポートエントリ多数 | `find_import(module, field)` | 文字列比較走査を行わずハッシュ値から RadixBinaryTreeView を $O(k)$ で探索して即座に解決される | §4.1「インポートテーブル検索」 |
| LOAD-46 | シンボルハッシュ衝突時の安全な文字列一致検証 | 同一ハッシュ値を持つ異なるシンボル名 | `lookup_export(name)` | ハッシュ一致後に ROM 上の文字列を 1 回照合し、誤ったシンボルの誤認を確実に防ぐ | §4.1「シンボル検索」 |
| LOAD-47 | 未定義シンボルの高速不存在判定 | 未エクスポートのシンボル名 | `lookup_export(non_existent)` | $O(k)$ のツリー走査で即座に `None` を返し、不要な文字列比較を行わない | §4.1, §5.1 |

## 3. テスト検証実績と網羅状況

- **軽量検証 (LOAD-01〜07)**: V1〜V6検証および失敗時のアロケータロールバック。
- **ゼロコピー索引化 (LOAD-10〜15)**: ROM直接参照、ハッシュ＋RadixBinaryTreeView シンボル検索、遅延アクセサ。
- **複数モジュール・インポート解決 (LOAD-20〜25)**: ハッシュ＋RadixBinaryTreeView による $O(k)$ インポート解決、型照合、レジストリ上限、LIFOアンロード。
- **容量制約 (LOAD-30〜32)**: 最大数制限およびLEB128ガード。
- **RadixBinaryTreeView 索引 (LOAD-40〜47)**: デコード済みエンティティ登録、RadixBinaryTreeView によるファイルオフセット逆引き、ハッシュ＋RadixBinaryTreeView によるインポート/エクスポート高速解決、ハッシュ衝突耐性、不存在判定。

## 4. 未検証・スコープ外

- 物理ROM配置・`std::span<const uint8_t>`のメモリレイアウト詳細。
- `formal/loader_verification_model.py`によるV1〜V6の形式検証そのもの。
