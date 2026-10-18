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
| TEST-LOAD-07 | ロード失敗時の完全ロールバック | V1〜V6の検証失敗またはランタイムアリーナ容量超過 | 失敗前後のアロケータwatermarkを比較 | `bump_allocator.offset`がロード開始前の値に完全復元される | 「トランザクション保護」, loader_concept.py `` |

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
| TEST-LOAD-22 | シンボル未発見での解決失敗 | 依存モジュールに該当エクスポートがない | `resolve_imports` | `WasmLinkError`相当（`{MultiModule_Support}`）。モジュールは未Readyのままで、仮登録した解決エントリを残さない | loader_concept.py `WasmLinkError` |
| TEST-LOAD-23 | インポート/エクスポートの型シグネチャ不一致 | 型が異なる同名エクスポート | 同上 | パラメータ・結果の型と個数を照合して拒否する。モジュールは未Readyのままで、仮登録した解決エントリを残さない | loader_concept.py `resolve_imports`の型チェック |
| TEST-LOAD-24 | モジュール登録数上限 | `FB_CONF_MAX_MODULES`（既定4）到達 | 5個目をprepare | `WasmLinkError`（レジストリ上限超過） | runtime_loader.md (Resource Constraints) |

### 容量制約 (runtime_loader.md (Resource Constraints))

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-LOAD-30 | 関数数上限 | `FB_CONF_MAX_FUNCTIONS`（256）超過 | prepare | reject/エラー | runtime_loader.md (Resource Constraints) |
| TEST-LOAD-31 | エクスポート件数はアリーナ容量で決定 | 64件を超えるエクスポートを持ち、メタデータがランタイムアリーナ内に収まる | prepare | 成功し、全エクスポートを検索できる。固定件数上限はない | runtime_loader.md (Resource Constraints) |
| TEST-LOAD-32 | LEB128の5/10バイトガード | 6バイト以上のu32 LEB128、11バイト以上のu64 LEB128 | パース | 即座にパースエラー（無限ループしない） | loader_concept.py `read_leb128_u32/u64` |

### ファイル内データ位置 & シンボルハッシュ RadixBinaryTreeView 索引 ({META_BinarySearch})
<!-- traceability: {GOTCHA-LOAD-01} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-LOAD-41 | RadixBinaryTreeView によるファイルオフセット検索 | デコード済みモジュール | `lookup_by_file_offset(offset)` を実行 | 基数表による $O(1)$ の区間絞り込みと有界二分探索 $O(\log n)$ により、指定オフセットを包含するデコード済みエンティティが正確に返却される | 「ファイル位置逆引き」, `lookup-by-file-offset` |
| TEST-LOAD-42 | 関数バイトコード位置からの関数アクセサ逆引き | 関数コード内オフセット | `lookup_by_file_offset(code_offset)` | 該当する `FunctionAccessor`（関数インデックス・シグネチャ）が即座に特定・返却される | - |
| TEST-LOAD-43 | データセグメント・グローバル位置の特定 | データセグメント内オフセット | `lookup_by_file_offset(data_offset)` | 該当するデータ定義またはグローバルエントリが正確に返却される | - |
| TEST-LOAD-44 | 範囲外・未定義隙間オフセットの境界処理 | ヘッダ以前またはバイナリ終端超過オフセット | `lookup_by_file_offset(invalid_offset)` | エラー/未発見（`None` / `false`）を返しクラッシュしない | 不変条件 |
| TEST-LOAD-45 | インポートテーブルのハッシュ＋RadixBinaryTreeView 検索 | インポートエントリ多数 | `find_import(module, field)` | ハッシュ値から RadixBinaryTreeView を $O(1)+O(\log n)$ で探索し、元のモジュール名・フィールド名を照合して解決される | 「インポートテーブル検索」 |
| TEST-LOAD-46 | シンボルハッシュ衝突時の安全な文字列一致検証 | 同一ハッシュ値を持つ異なるシンボル名 | `lookup_export(name)` | ハッシュ一致後に ROM 上の文字列を 1 回照合し、誤ったシンボルの誤認を確実に防ぐ | 「シンボル検索」 |
| TEST-LOAD-47 | 未定義シンボルの高速不存在判定 | 未エクスポートのシンボル名 | `lookup_export(non_existent)` | ハッシュ索引の $O(1)$ の区間絞り込みと有界探索 $O(\log n)$ の後、候補がなければ `None` を返す。候補がある場合のみ元文字列を照合する | - |
| TEST-LOAD-48 | Python参照用BasicBlock検索とネイティブ索引の分離 | 10,000件以上のBasicBlockを持つパース済みWASMモジュール | `Module.get_block()`でPythonの`block_storage`を検索し、Native ModuleExecutionViewに検索用行・radix表がないことをABIとNative dispatch経路で確認する | Pythonの`block_storage`は自然順`module.blocks`のindexを返し、元PCとの完全一致確認後に正しいBasicBlockを得る。Native ModuleExecutionViewへPC→block検索表を転送せず、実行中のblock indexと注釈済み遷移先で進む。WASM PCのCode section payload相対仕様とJIT PC cache keyは維持する | `{Loader_BasicBlockIndex}` |
| TEST-LOAD-51 | 到達不能な外側フレームの型ポリモーフィズム分離 | `unreachable` の後に値を要求するネストブロック | `parse(module)` | 外側フレームのunreachable状態を内側ブロックへ漏らさず、operand stack underflowとして拒否する | [`reader.py`](experiments/pysim/tier2_runtime/wasm/reader.py) operand stack validation |
| TEST-LOAD-52 | Custom section 名のセクション境界 | 名前長がCustom sectionの残りバイト数を超える | `parse(module)` | 次セクションのバイトを名前として読まず、section bounds違反で拒否する | [`reader.py`](experiments/pysim/tier2_runtime/wasm/reader.py) custom section bounds |
| TEST-LOAD-53 | LEB128幅とsection件数の事前検証 | 幅超過LEB128、短いsection内の巨大な型件数 | `parse(module)` | LEB128を最大幅・現在のsection終端で停止し、設定容量を構成する前に不正件数を拒否する | [`leb128.py`](experiments/pysim/tier2_runtime/wasm/leb128.py), [`reader.py`](experiments/pysim/tier2_runtime/wasm/reader.py) |
| TEST-LOAD-54 | ROM-backed 名称範囲とハッシュ衝突解決 | 異なる名前 `ufbwjn` / `rsksbm`（同一FNV-1a 32-bit値）を持つエクスポート | `lookup_export(name)` と各エントリのROM範囲を確認 | 名前の実体をエントリへ保存せず、各ハッシュ候補をROM上の完全一致で識別する | `GOTCHA-LOAD-01` |
| TEST-LOAD-55 | 実行時作成が使うパーサーのWASMページ上限 | 初期メモリが `FB_CONF_MAX_WASM_PAGES + 1` ページ | `wasm_reader.parse(module)` | 設定上限を超える初期メモリを拒否する | `runtime_loader.md` (Resource Constraints) |

| TEST-LOAD-56 | メモリ命令の整列指定上限 | `i32.load`の整列指定がアクセス幅を超える | `parse(module)` | 4バイト指定を許可し、8バイト指定を拒否する | [WebAssembly Core・memarg検証](https://webassembly.github.io/spec/core/valid/instructions.html#valid-memarg)、pysim `` |
| TEST-LOAD-57 | Runtime API命令を含むtrace範囲 | `i32.const`の後ろに`i32.popcnt`を置く関数 | パース後に関数先頭PCの`BasicBlock`をRadix検索し、関数コードと範囲を比較する | `byte_span`が`i32.const`と末尾の`i32.popcnt`を含み、終端`end`を含まない。ブロックのInterpreter定数pool開始offsetが設定される | `{Loader_BasicBlockIndex}`、pysim `` |
| TEST-LOAD-63 | BasicBlockの分割位置と構造命令の所属 | `block`、`loop`、`if`/`else`、`0xFC`、分岐を含む関数 | Loaderの`BasicBlock` head、span、入れ子深さ、構造命令後の分岐先を確認する | `block`・`loop end`・`0xFC`では分割せず、分岐命令・`if`・`else`・`loop`入口・`block`/`if end`の規則に従ってheadを作る。`loop end`を含むblockは入れ子深さを保つ | `{Loader_BasicBlockIndex}`、pysim `test_wasm_reader.py::test_load_63_splits_at_control_transfers_and_branch_destinations` |
| TEST-LOAD-58 | BasicBlock単位の導出値注釈プール構築 | 複数ブロックにLEB128即値、`br_table`、固定幅`f32.const`/`f64.const`を含む関数 | Loaderの各ブロック別pool範囲、連結順、BasicBlockの開始byte offsetを検査する | 各BasicBlockのコード範囲を個別にパースし、実行時の導出値だけを8bit-packed関数poolへブロック順に追加する。LEB128復号値は保存しない。localはu16フレーム語offsetとu8幅、indexはu16、定数はi32=u32/i64=u64、メモリ実効offsetはu32とする。可変LEB128命令だけu8幅recordを持ち、固定幅命令はopcodeから長さを求める。`br_table`はu16 label countとu16 target列を格納し、label countのu16超過は`ERR_WASM_UNSUPPORTED_FEATURE`で拒否する。fixed-width float payloadとtable indexはpoolへ入れず、PC/注釈offset表も生成しない | `{Loader_BasicBlockIndex}`, `interpreter.md`「Interpreter注釈プール」 |

### 実装上の注意点に対応する検証
<!-- traceability: {Loader_BasicBlockIndex} {GOTCHA-LOAD-01} {GOTCHA-LOAD-02} {GOTCHA-LOAD-04} -->

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-LOAD-01 | ハッシュ衝突時の文字列完全一致確認（シンボル誤認防止） | 同一ハッシュ値を持つ異なるシンボル名 | `lookup_export(name)` を実行 | ハッシュ値による探索後に ROM 上の文字列を 1 回比較し、異なる文字列であれば一致と誤判定しない。 | [`runtime_loader.md`](docs/components/tier2_runtime/runtime_loader.md) |
| GOTCHA-LOAD-02 | ロード失敗時のアロケータ完全ロールバック（メモリリーク防止） | 不正なセクションまたはアリーナ容量を超える WASM バイナリ | `prepare_module` を実行 | パースまたは確保失敗時にバンプポインタがロード開始前の位置へ巻き戻される。 | [`runtime_loader.md`](docs/components/tier2_runtime/runtime_loader.md) |
| GOTCHA-LOAD-04 | 基本ブロックメタ情報の所有と可変JIT参照の分離 | WASMモジュールをロード済み | 同じPCをRadix検索し、境界・制御情報とJIT参照をキャッシュ変更の前後で比較する | 境界・制御情報・注釈プールoffsetは不変である。`jit_cache_reference`だけはJITキャッシュの挿入・移動・退役で更新または無効化される。索引の再構築や動的確保は発生しない | [`runtime_loader.md`](docs/components/tier2_runtime/runtime_loader.md), `Loader_BasicBlockIndex` |

| TEST-LOAD-59 | assert無効時の不正バイナリtrap | 不正ヘッダ、短い整数・float即値、短いmemory index、未対応subopcode、型スタック違反 | Python `-O`の別プロセスから`parse`する | `InvalidWasm`を送出し、`INVALID_WASM_BINARY`を保持する | `{GOTCHA-LOAD-02}`、pysim `` |
| TEST-LOAD-60 | 内部契約違反の原因維持 | Loader内部で領域を確保した後に契約assertが失敗する | `parse`の内部手続きから`AssertionError`を送出する | 同じ例外を伝播し、trapへ変換せずwatermarkを復元する | `{GOTCHA-LOAD-02}`、pysim `` |
| TEST-LOAD-61 | 型検証表の不変性 | opcode対応表の構築完了 | 対応表の上書きを試み、その後でWASMを解析する | 書き換えを拒否し、解析に使うhandlerを維持する | `{ROMParsing}`、pysim `` |
| TEST-LOAD-62 | u16 BasicBlock index上限とNative PC索引の非保持 | 65,536件のBasicBlockを生成するWASM、および複数blockを持つ通常WASM | overflow入力をparseし、通常入力の`Module.get_block()`とNative ModuleExecutionViewのlayoutを確認する | 65,535件を超えるblock数を`ERR_WASM_UNSUPPORTED_FEATURE`で拒否する。Python `block_storage`はPC検索を解決する。Native ModuleExecutionViewはPC index entry・radix表を保持せず、実行中のBasicBlock indexと注釈済み遷移先を使う | `{Loader_BasicBlockIndex}`、pysim ``, ``, `` |

## 3. テスト検証実績と網羅状況

### 3.1 Pysim参照パーサの現行QA範囲

この文書の前半は、C++ `runtime_loader` の目標テスト契約である。ロード済み`ModuleView`、複数モジュール登録・リンク、ファイル位置のRadix索引は現行Pysim製品コードの機能ではない。バックログ上もC++ローダー実装は未着手である。


| ケースID | 現行Pysim QA | 直接観測する結果 |
| :--- | :--- | :--- |
| TEST-LOAD-01〜07 | ``〜`` | 不正ヘッダ、section境界・順序、型・メモリ上限の拒否とアロケータwatermark復元 |
| TEST-LOAD-10 | `` | 型レコードの範囲、引数型・戻り型・関数コードと原本バイナリの同一所有者参照 |
| TEST-LOAD-16 | `` | 原本上のData/Elementセクション範囲、インポート済みimmutable globalを使う初期化、メモリとテーブル状態 |
| TEST-LOAD-20〜24 | ``〜`` | QA専用ハーネスによる未解決状態、関数リンク、シグネチャ拒否、部分リンク防止、登録上限 |
| TEST-LOAD-30〜32 | ``〜`` | 関数上限、64件超のexport保持、u32/u64 LEB128のバイト上限 |
| TEST-LOAD-46 | `` | 既知のハッシュ衝突名をパーサのエクスポート検索で区別 |
| TEST-LOAD-48、57、58、63 | ``、``、``、`test_wasm_reader.py::test_load_63_splits_at_control_transfers_and_branch_destinations` | 大規模BasicBlock索引、Runtime API命令を含むtrace範囲、BasicBlock単位のLEB128プール、分割位置・構造命令所属 |
| TEST-LOAD-51〜53、55〜56 | 対応する`*` | 到達不能時の型検証、Custom名境界、LEB128/件数、メモリページ・整列上限 |

| TEST-LOAD-59〜60 | 対応する`*` | assert無効時の検証trapと内部assertの原因維持・watermark復元 |
| TEST-LOAD-61 | `` | 不変のopcode対応表の上書き拒否と解析結果 |

## 4. 未検証・スコープ外

- 物理ROM配置・`std::span<const uint8_t>`のメモリレイアウト詳細。
- [`loader_verification_model.py`](docs/components/tier2_runtime/formal/loader_verification_model.py)によるV1〜V6の形式検証そのもの。

- TEST-LOAD-43のDataセグメント位置登録・逆引きは、本pysimスイートでは未検証である。関数とグローバルの逆引き成功をDataの証拠にしない。
- TEST-LOAD-45の多数インポート、TEST-LOAD-46の未登録名が既存名と衝突する条件は、本pysimスイートでは未検証である。
- TEST-LOAD-49〜50のJIT候補判定と非候補touch/履歴バイパスは、本Pysimスイートでは検証しない。
- 名前・ファイル位置検索の計算量、物理ROMのコピー回数、全WASM命令の完全検証は、上記の局所機能検査では証明しない。

可変LEB128命令の幅は255 byte以下に限る。256 byte以上は`ERR_WASM_UNSUPPORTED_FEATURE`で拒否し、確保領域を巻き戻す。固定幅命令はopcodeから長さを求め、`br_table`はlabel countとtarget列から長さを求める。型解析は関数ごとの最大オペランド高を記録し、i64/f64を2語として数える。 <!-- traceability: {DirectBytecodeExecution} -->
