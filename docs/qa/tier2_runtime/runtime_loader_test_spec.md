# WASMローダ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`runtime_loader.md`](docs/components/tier2_runtime/runtime_loader.md)
参考実装: [`loader_concept.py`](docs/components/tier2_runtime/concepts/loader_concept.py)

ROM上WASM32バイナリのゼロコピー索引化（`ModuleView`）、V1〜V6軽量検証、バンプアロケータのトランザクショナルロールバック、ハッシュ＋`RadixBinaryTreeView`（`fireball::radix_binary_tree_view`）によるインポート解決およびシンボル検索、ファイル内データ位置からのデコード値逆引きを検証する。
Element/Data初期化定義を個別配列へ展開せず、パース時検証と起動時適用をcallbackによる2段階ストリーム処理で行うことも検証対象とする。
各セクションの定義登録をパースイベントコールバック経由で行い、パーサ本体が保持用配列を直接管理しないことも検証対象とする。
物理実装のROM文字列ビュー契約に対し、概念コードは比較可能なPython `str`を意味論上の代替として使用する。

## 2. テストケース一覧

### 軽量検証 (runtime_loader.md (Validation))

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-LOAD-01 | V1 マジックナンバー | 先頭4バイトが`\0asm`でない | `prepare(binary)` | reject（`WasmVerifyError`相当） | runtime_loader.md (Validation) (V1) |
| TEST-LOAD-02 | V2 バージョン | バージョンが`1`以外 | 同上 | reject | runtime_loader.md (Validation) (V2) |
| TEST-LOAD-03 | V3 セクション境界 | セクションsizeがバイナリ末尾を超える | 同上 | reject | runtime_loader.md (Validation) (V3) |
| TEST-LOAD-04 | V4 セクション順 | 非Customセクションが降順・重複 | 同上 | reject（Customセクション(ID=0)のみ順序制約の例外） | runtime_loader.md (Validation) (V4) |
| TEST-LOAD-05 | V5 インポート/エクスポート型整合 | 関数宣言のtype_idxがTypeセクション範囲外 | 同上 | reject | runtime_loader.md (Validation) (V5) |
| TEST-LOAD-06 | V6 メモリセクション境界 | 初期要求メモリサイズが`FB_CONF_GUEST_RAM_SIZE`を超える | 同上 | reject | runtime_loader.md (Validation) (V6) |
| TEST-LOAD-07 | ロード失敗時の完全ロールバック | V1〜V6の検証失敗またはランタイムアリーナ容量超過 | 失敗前後のアロケータwatermarkを比較 | `bump_allocator.offset`がロード開始前の値に完全復元される | 「トランザクション保護」, loader_concept.py `test_wasm_loader_lifecycle_and_verification` |

### ゼロコピー索引化
<!-- traceability: {META_BinarySearch} {ZeroCopyIndexing} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-LOAD-10 | セクション内容をRAMへコピーしない | 正常なバイナリ | パース後、`section_view`の実装を確認 | 開始オフセットとサイズのみを保持し、内容の複製を持たない | 「Zero-Copy Indexing」, `ZeroCopyIndexing` |
| TEST-LOAD-11 | エクスポート名はROM参照 | エクスポート名を持つバイナリ | `exports_dict`の要素を確認 | 文字列はROM上の`string_view`相当であり、RAMコピーがない | - |
| TEST-LOAD-12 | エクスポート名順ソート | 複数エクスポート（非アルファベット順で宣言） | パース後の`exports_dict`を確認 | 名前順にソートされている | 「名前順にソート」 |
| TEST-LOAD-13 | ハッシュ＋RadixBinaryTreeView シンボル検索 | エクスポートシンボル登録済み | `lookup_export(name)` | FNV-1a ハッシュと RadixBinaryTreeView による $O(1)+O(\log n)$ 索引探索後、ROM上の元文字列を照合して正しい`ExportEntry`を返す。未登録名は`None` | 「シンボル検索」, `{META_AccessDictionary}`, `META_BinarySearch` |
| TEST-LOAD-14 | 関数アクセサの遅延デコード | 任意の関数 | `get_function(idx).get_code_stream()` | localsベクタ宣言をスキップした実行本体ストリームを返す | function_accessor |
| TEST-LOAD-15 | グローバルアクセサ | 任意のグローバル変数宣言 | `get_global(idx).get_metadata()` | (valtype, mutable)を正しく返す | global_accessor |
| TEST-LOAD-16 | Element/Data初期化のストリーム処理 | ElementまたはDataセグメントを持つ正常なバイナリ | パース後に起動時初期化を実行 | 個別セグメント配列を生成せず、各定義がcallback経由でテーブルまたはメモリへ適用される。`global.get`のオフセットは起動時のグローバル値で解決される | `runtime_loader.md`「Element/Data初期化の2段階ストリーム処理」 |

### 複数モジュール・インポート解決

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-LOAD-20 | 未解決インポートは実行不可状態 | インポートを持つモジュールをprepare（依存先未登録） | `is_ready`を確認 | `False`（実行不可） | - |
| TEST-LOAD-21 | ハッシュ＋RadixBinaryTreeView インポート解決 | 依存モジュールが先に登録済み | `resolve_imports(module)` | ハッシュ＋RadixBinaryTreeView で $O(1)+O(\log n)$ に候補を絞り、元文字列照合後に `True`（`is_ready == True`）になる | resolve-imports |
| TEST-LOAD-22 | シンボル未発見での解決失敗 | 依存モジュールに該当エクスポートがない | `resolve_imports` | `WasmLinkError`相当（`{MultiModule_Support}`） | loader_concept.py `WasmLinkError` |
| TEST-LOAD-23 | インポート/エクスポートの型シグネチャ不一致 | 型が異なる同名エクスポート | 同上 | 拒否される | loader_concept.py `resolve_imports`の型チェック |
| TEST-LOAD-24 | モジュール登録数上限 | `FB_CONF_MAX_MODULES`（既定4）到達 | 5個目をprepare | `WasmLinkError`（レジストリ上限超過） | runtime_loader.md (Resource Constraints) |

### 容量制約 (runtime_loader.md (Resource Constraints))

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-LOAD-30 | 関数数上限 | `FB_CONF_MAX_FUNCTIONS`（256）超過 | prepare | reject/エラー | runtime_loader.md (Resource Constraints) |
| TEST-LOAD-31 | エクスポート件数はアリーナ容量で決定 | 64件を超えるエクスポートを持ち、メタデータがランタイムアリーナ内に収まる | prepare | 成功し、全エクスポートを検索できる。固定件数上限はない | runtime_loader.md (Resource Constraints) |
| TEST-LOAD-32 | LEB128の5/10バイトガード | 6バイト以上のu32 LEB128、11バイト以上のu64 LEB128 | パース | 即座にパースエラー（無限ループしない） | loader_concept.py `read_leb128_u32/u64` |

### ファイル内データ位置 & シンボルハッシュ RadixBinaryTreeView 索引 ({META_BinarySearch})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-LOAD-40 | デコード済みエンティティのファイル位置登録 | WASMバイナリパース完了 | `decoded_entity_registry` の内容を確認 | 各セクション、関数コード、グローバル、データセグメントが開始・終了ファイルオフセットとともに登録されている | `decoded_entity_registry` |
| TEST-LOAD-41 | RadixBinaryTreeView によるファイルオフセット検索 | デコード済みモジュール | `lookup_by_file_offset(offset)` を実行 | 基数表による $O(1)$ の区間絞り込みと有界二分探索 $O(\log n)$ により、指定オフセットを包含するデコード済みエンティティが正確に返却される | 「ファイル位置逆引き」, `lookup-by-file-offset` |
| TEST-LOAD-42 | 関数バイトコード位置からの関数アクセサ逆引き | 関数コード内オフセット | `lookup_by_file_offset(code_offset)` | 該当する `FunctionAccessor`（関数インデックス・シグネチャ）が即座に特定・返却される | - |
| TEST-LOAD-43 | データセグメント・グローバル位置の特定 | データセグメント内オフセット | `lookup_by_file_offset(data_offset)` | 該当するデータ定義またはグローバルエントリが正確に返却される | - |
| TEST-LOAD-44 | 範囲外・未定義隙間オフセットの境界処理 | ヘッダ以前またはバイナリ終端超過オフセット | `lookup_by_file_offset(invalid_offset)` | エラー/未発見（`None` / `false`）を返しクラッシュしない | 不変条件 |
| TEST-LOAD-45 | インポートテーブルのハッシュ＋RadixBinaryTreeView 検索 | インポートエントリ多数 | `find_import(module, field)` | ハッシュ値から RadixBinaryTreeView を $O(1)+O(\log n)$ で探索し、元のモジュール名・フィールド名を照合して解決される | 「インポートテーブル検索」 |
| TEST-LOAD-46 | シンボルハッシュ衝突時の安全な文字列一致検証 | 同一ハッシュ値を持つ異なるシンボル名 | `lookup_export(name)` | ハッシュ一致後に ROM 上の文字列を 1 回照合し、誤ったシンボルの誤認を確実に防ぐ | 「シンボル検索」 |
| TEST-LOAD-47 | 未定義シンボルの高速不存在判定 | 未エクスポートのシンボル名 | `lookup_export(non_existent)` | ハッシュ索引の $O(1)$ の区間絞り込みと有界探索 $O(\log n)$ の後、候補がなければ `None` を返す。候補がある場合のみ元文字列を照合する | - |
| TEST-LOAD-48 | ローダ所有のベーシックブロック索引と不変メタ情報公開 | パース済み WASM モジュール | `mod.get_block(pc)` / `mod.block_storage` | ランタイム側での再構築なしに、ローダが構築した `ReadOnlyRadixBinaryTreeStorage` と借用viewから $O(1) + O(\log n)$ で `BasicBlock` メタ情報を解決できる | `{Loader_BasicBlockIndex}` |
| TEST-LOAD-49 | int4_t スコアリングによる JIT 候補ビットマップ生成 | WASM モジュールロード | `cand_bm.evaluate_block(bb, table, threshold=9)` | 128B BitView<4> テーブルから命令ごとの機械語短縮スコア（int4_t）を積算し、合計9点以上のブロックの head_pc カードビット（1bit）が正確に 1 にセットされる | `{JIT_StaticBenefitScoring}`, `{JIT_CandidateBitmap}` |
| TEST-LOAD-50 | JITCandidateBitmap 非候補ブロックの touch/履歴バイパス | 非候補ブロック（カードビット 0）の実行 | `eng.run(cold_pc, ctx)` | インタープリタ実行は行われるが、HotspotBitmap.touch() および履歴リングへの記録が完全にバイパスされ、カード状態が UNEXECUTED のまま維持される | `{JIT_CandidateBitmap}` |
| TEST-LOAD-51 | 到達不能な外側フレームの型ポリモーフィズム分離 | `unreachable` の後に値を要求するネストブロック | `parse(module)` | 外側フレームのunreachable状態を内側ブロックへ漏らさず、operand stack underflowとして拒否する | `wasm_reader.py` operand stack validation |
| TEST-LOAD-52 | Custom section 名のセクション境界 | 名前長がCustom sectionの残りバイト数を超える | `parse(module)` | 次セクションのバイトを名前として読まず、section bounds違反で拒否する | `wasm_reader.py` custom section bounds |
| TEST-LOAD-53 | LEB128幅とsection件数の事前検証 | 幅超過LEB128、短いsection内の巨大な型件数 | `parse(module)` | LEB128を最大幅・現在のsection終端で停止し、設定容量を構成する前に不正件数を拒否する | `leb128.py`, `wasm_reader.py` |
| TEST-LOAD-54 | ROM-backed 名称範囲とハッシュ衝突解決 | 異なる名前 `ufbwjn` / `rsksbm`（同一FNV-1a 32-bit値）を持つエクスポート | `lookup_export(name)` と各エントリのROM範囲を確認 | 名前の実体をエントリへ保存せず、各ハッシュ候補をROM上の完全一致で識別する | `{GOTCHA-LOAD-01}` |
| TEST-LOAD-55 | 実行時作成が使うパーサーのWASMページ上限 | 初期メモリが `FB_CONF_MAX_WASM_PAGES + 1` ページ | `wasm_reader.parse(module)` | 設定上限を超える初期メモリを拒否する | `runtime_loader.md` (Resource Constraints) |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）
<!-- traceability: {Loader_BasicBlockIndex} -->

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-LOAD-01 | ハッシュ衝突時の文字列完全一致確認（シンボル誤認防止） | 同一ハッシュ値を持つ異なるシンボル名 | `lookup_export(name)` を実行 | ハッシュ値による探索後に ROM 上の文字列を 1 回比較し、異なる文字列であれば一致と誤判定しない。**実装の勘所**: ハッシュ値の一致のみでシンボル解決を完了させると、ハッシュ衝突時に無関係な関数を呼び出す致命的なセキュリティホールとなる | `runtime_loader.md` |
| GOTCHA-LOAD-02 | ロード失敗時のアロケータ完全ロールバック（メモリリーク防止） | 不正なセクションまたはアリーナ容量を超える WASM バイナリ | `prepare_module` を実行 | パースまたは確保失敗時にバンプポインタがロード開始前の位置へ巻き戻される。**実装の勘所**: 失敗したロードの確保分を残すと、再試行を繰り返すことでメモリ枯渇を引き起こす | `runtime_loader.md` |
| GOTCHA-LOAD-04 | ベーシックブロックメタ情報のローダ側不変保持とゼロコピー解決 | WASM モジュールロード | `mod.get_block(pc)` を実行 | 基本ブロック境界や制御スキップ情報は WASM バイトコードの静的プロパティであり実行時に変化しない。実行時エンジンが動的なミュータブル辞書やツリーで再構築するのではなく、ローダ側が `ReadOnlyRadixBinaryTreeStorage` を一度だけ構築・公開し、実行環境がそれを直接借用・参照する。**実装の勘所**: ランタイム側でブロック走査や動的アロケーションを行うと、JIT ホットスポット追跡時の毎ブロック検索で深刻なオーバーヘッドを招く | `runtime_loader.md` `Loader_BasicBlockIndex` |

## 3. テスト検証実績と網羅状況

- **軽量検証 (TEST-LOAD-01〜07)**: V1〜V6検証および失敗時のアロケータロールバック。
- **ゼロコピー索引化 (TEST-LOAD-10〜15)**: ROM直接参照、ハッシュ＋RadixBinaryTreeView シンボル検索、遅延アクセサ。
- **複数モジュール・インポート解決 (TEST-LOAD-20〜24)**: ハッシュ索引の $O(1)$ の区間絞り込みと有界探索 $O(\log n)$、元文字列照合によるインポート解決、型照合、レジストリ上限。
- **容量制約 (TEST-LOAD-30〜32)**: 固定件数制限、アリーナ容量によるエクスポート受理数、およびLEB128ガード。
- **RadixBinaryTreeView 索引 & JIT候補判定 (TEST-LOAD-40〜50)**: デコード済みエンティティ登録、RadixBinaryTreeView によるファイルオフセット逆引き、ハッシュ＋RadixBinaryTreeView によるインポート/エクスポート高速解決、ハッシュ衝突耐性、不存在判定、ローダ所有のベーシックブロック索引（`TEST-LOAD-48`）、`int4_t` スコアリングによる JIT 候補ビットマップ生成（`TEST-LOAD-49`）、非候補カードにおける touch/履歴バイパス（`TEST-LOAD-50`）。
- **パーサ堅牢性と実行時パーサー上限 (TEST-LOAD-51〜55)**:
  - 型検証の到達不能境界、Custom section長、LEB128とsection件数の上限を確認する。
  - ROM名範囲、FNV衝突解決、実行時パーサーのWASM初期ページ上限を確認する。

## 4. 未検証・スコープ外

- 物理ROM配置・`std::span<const uint8_t>`のメモリレイアウト詳細。
- [`loader_verification_model.py`](docs/components/tier2_runtime/formal/loader_verification_model.py)によるV1〜V6の形式検証そのもの。
