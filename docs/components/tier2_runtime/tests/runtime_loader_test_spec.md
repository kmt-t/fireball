# WASMローダ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier2_runtime/runtime_loader.md`
参考実装: `docs/components/tier2_runtime/concepts/loader_concept.py`
現行実装: `experiments/pysim/wasm_reader.py`, `wasm_module.py`

ROM上WASM32バイナリのゼロコピー索引化（`ModuleView`）、V1〜V6軽量検証、バンプアロケータのトランザクショナルロールバック、複数モジュールのインポート解決を検証する。

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
| LOAD-13 | エクスポート二分探索 | ソート済みエクスポート | `lookup_export(name)` | O(log N)の二分探索で正しい`ExportEntry`を返す。未登録名は`None` | §4.1「シンボル検索」, `{META_AccessDictionary}` |
| LOAD-14 | 関数アクセサの遅延デコード | 任意の関数 | `get_function(idx).get_code_stream()` | localsベクタ宣言をスキップした実行本体ストリームを返す | §3.3 function_accessor |
| LOAD-15 | グローバルアクセサ | 任意のグローバル変数宣言 | `get_global(idx).get_metadata()` | (valtype, mutable)を正しく返す | §3.3 global_accessor |

### 複数モジュール・インポート解決 (§4.1「依存関係解決」)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| LOAD-20 | 未解決インポートは実行不可状態 | インポートを持つモジュールをprepare（依存先未登録） | `is_ready`を確認 | `False`（実行不可） | §4.1 |
| LOAD-21 | インポート解決成功 | 依存モジュールが先に登録済み | `resolve_imports(module)` | `True`を返し、`is_ready`が`True`になる | §5.1 resolve-imports |
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

## 3. 現状のギャップ（pysim実装との差分）

- **重大**: `experiments/pysim/wasm_reader.py` はV1（マジック）・V2（バージョン、暗黙）以外のV3〜V6検証を一切実装していない。不正なセクション境界・セクション順・型インデックス・メモリページ超過があっても**黙って受理してしまう**か、Pythonの例外（`IndexError`等、意味不明瞭）で落ちる。
- pysimはバンプアロケータもトランザクショナルロールバックも実装していない（Pythonのオブジェクト生成に任せている）。LOAD-07は現状原理的に検証不能。
- pysimは単一モジュールのみを対象としており、`module_registry`・複数モジュールのインポート解決（LOAD-20〜24）に相当する機構が存在しない。`main.py`は`fireball_call`という単一のホストインポートを都度`host_trampolines`辞書で解決しており、WASMモジュール間の解決ではない。
- LOAD-10〜15（ゼロコピー索引化）はpysimでは**意図的に不採用**: `wasm_module.py`はROM参照ではなく、パース結果を通常のPythonオブジェクト（リスト等）へ展開する設計であり、そもそも「ゼロコピー」というメモリ制約目標自体がPython実験の対象外（README「Outside this experiment's scope」に追記が必要）。

## 4. 未検証・スコープ外

- 物理ROM配置・`std::span<const uint8_t>`のメモリレイアウト詳細。
- `formal/loader_verification_model.py`によるV1〜V6の形式検証そのもの。
