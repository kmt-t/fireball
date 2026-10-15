# pysim — Fireball 実機シミュレータ (Experimental System Simulator)

`pysim` は、Fireball Hypervisor の全層（Tier 1 Core OS / Tier 2 Runtime / Tier 3 Plugins & Platform）を Python 上で完全動作する形で具象化した、エンドツーエンドの実機実行可能シミュレータです。

仕様書（`docs/components/**`）で策定されたアーキテクチャ・状態機械・メモリレイアウト・ABI 規約が、実際に結合して正しく動作することを C++23 実装前に事前実証（Pressure-test）することを目的としています。

### C++ 移植可能性の制約（本ディレクトリのみ）

`experiments/pysim/` 配下のコードは、この事前実証としての性質上、`.agents/rules/coding-standards-cpp.md` が定める組み込み C++（ヒープ割り当て・例外・RTTI 無効、`std::vector`/`std::map`/`std::unordered_map` 等の動的コンテナ禁止、`{GLOBAL_Policy_Memory}`）の制約を型として引き継ぎます。**pysim は「RTTI（実行時型情報）のない静的型付け言語（C++）」だと思って記述してください。** 具体的には：

- **動的型検査・リフレクションの完全禁止（No RTTI）**:
  - `isinstance`, `type()`, `hasattr`, `getattr` 等のランタイム型検査やリフレクションを一切使用しない。
  - ユニバーサルな引数型（何でも受け取れる万能型・両対応型）にして内部で動的に型を判定して分岐するコードを書かない。関数のシグネチャは意図された具象型に一本化し、異なる型を扱う場合は別名関数として明確に分離する。
- **動的コンテナの完全禁止（`{GLOBAL_Policy_Memory}`）**:
  - Python の `dict`/`set` を実装の型として使わない。固定長配列、`BitView`/`FlatMapView`/`FlatSetView`/`RadixBinaryTreeView`（読み取り専用）、または `MutableFlatMapStorage`/`MutableFlatSetStorage`/`MutableRadixBinaryTreeStorage`/`MutableBitStorage`（可変・固定容量）のような `tier1_core/system_containers.py` の固定容量コンテナに置き換える。
  - `.append()`/`.insert()`/`.pop()` で無制限に伸縮する `list` も同様に禁止。伸縮方向が「末尾のみ・容量に上限がある」構造には `StaticVector`（順次アクセス、LIFO push_back/pop_back）を、「先頭から流れ落ちる／上書きされる」構造には `RingBuffer`（FIFO、オーバーフロー時上書き）を使う。どちらも `tier1_core/system_containers.py` にあり、容量はコンストラクタ引数として必ず明示し、その値の根拠（既存の上限値と一致させた、または `FB_CONF_*` 定数として新規に定義した、等）をコメントで残す。
  - どうしても `[None] * N` + 明示カウンタで自前実装する場合（`control_flow.py` の `open_stack`/`active_openers` の `FB_CONF_MAX_NESTING_DEPTH` 等、既存コンテナのAPIでは表現できない特殊なアクセスパターンのみ）も、伸縮は `.append`/`.pop` ではなくインデックス代入とカウンタ増減で行い、上限超過は明示的にエラーとする。
- **例外制御フローの禁止**:
  - 例外を制御フローに使わない。失敗は戻り値（`None`、`Result`型、`IntEnum` ステータス等）で表現する。
- **厳格な整数型・Enum の使用**:
  - 文字列による状態・ID 比較を行わない。すべて `IntEnum` または整数インデックスで扱う。
- **命令列・ブロック内容の非マテリアライズ**:
  - バイトコードを丸ごとデコードして `Instr` オブジェクトの `list`/`dict` として保持しない。呼び出し側が一度しか消費しないなら、ジェネレータでストリーミングデコードする（`control_flow.iter_scan_instrs`/`iter_block_ops` 等）。
  - コンパイル済みトレースのように、消費し切ったあとも同じ内容を繰り返し評価する必要がある場合は、既知の上限で容量を決めた `StaticVector` に詰め替えて保持する。
  - デコードした値のうち呼び出し側が使わないフィールドは、そもそもアンパックせずバイト位置だけ進めて捨てる（未使用の `const_value`/`memarg` 等をデコードしない）。
- **パース/ロード時に確定する値の実行時再計算禁止**:
  - 関数やブロックの静的メタデータ（制御構造マップ、ローカル変数レイアウト、JIT コンパイル対象として妥当なブロックか等）は、ロード時に一度だけ計算してキャッシュし、ディスパッチのたびに再導出しない。`Function.control_map`、`Function.local_layout_cache`（ローカルごとの32bitワードオフセットとワード数を保持）、`RuntimeEngine.trackable`（`BlockCardMask`: `next_pc is not None and byte_span >= min_trace_bytes` をロード時に1回だけ判定し1bit/ブロックでマスクする）が実例。WASM セクションの件数、要素数、ローカル数、JIT バンクの容量もロード時または入力メタデータから固定容量を決める。
  - 呼び出し元がすでに解決済みのオブジェクト（例: `BasicBlock`）を持っている場合、それを再度 PC からルックアップし直さない。必要なメタデータは具象型の必須引数として渡す。テストや互換経路のために欠損扱いの分岐を製品コードへ追加しない。

**この制約は `experiments/pysim/` のみに適用され、`docs/components/**/concepts/*.py` の参考実装コードには適用されません。** concept コードは仕様の意図を伝えるための説明的なスニペットであり、可読性を優先して `dict` などの通常の Python イディオムを使ってよいものとします。

---

## 1. ディレクトリ構成 (Tier 階層準拠)

リポジトリの 3-Tier アーキテクチャに完全準拠したモジュール構成となっています：

```
experiments/pysim/
├── tier1_core/            # Tier 1 Core OS & システム基盤
│   ├── scheduler.py       # COOS コルーチンスケジューラ, READYキュー, 対称遷移, CSP 直接ハンドオフ
│   ├── system_containers.py # BitView, FlatMapView, RadixBinaryTreeView, RingBuffer
│   ├── fnv1a.py           # URI・シンボル検索キーのハッシュ
│   └── interrupt_event.py # 固定長割り込みイベント
│
├── tier1_interface/       # Tier 1 Interface
│   └── ipc_router.py      # ゼロコピー所有権移譲 & RBAC ルーティング
│
├── tier2_runtime/         # Tier 2 Runtime & WASM 仮想マシン
│   ├── abi/               # Interpreter/JIT ABI定義とNative adapter
│   ├── hal/               # URI解決・IPCコマンド・HALバッファ仲介
│   ├── interpreter/       # Interpreter、実行状態、control flow
│   ├── memory/            # Tier 1メモリ契約のTier 2実装
│   ├── observability/     # Runtimeイベントと構造化ログ
│   ├── runtime/           # RuntimeEngine、RuntimeComposer、JIT plugin契約、recovery
│   ├── syscall/           # ゲストhost callとWASI gateway
│   ├── vmmio/             # ゲストアドレス変換と仮想デバイス領域
│   ├── vsoc/              # vSoC固有の仮想割り込み配送
│   └── wasm/              # WASM読込、Module、opcode、LEB128
│
├── tier3_plugins/         # Tier 3の交換可能な実行拡張・観測プラグイン
│   ├── jit/               # Interpreterへ接続するJIT plugin
│   ├── debugger/          # GDB Remote Serial Protocol debugger
│   ├── logger/            # Runtime event logger
│   └── profiler/          # Guest profiler
│
├── native/                # Tier別C++実装
│   ├── tier2_runtime/
│   │   └── interpreter/   # C++ Interpreterとネイティブビルドスクリプト
│   └── tier3_plugins/
│       └── jit/           # C++ trace compiler、x64ステンシル、ビルドスクリプト
│
├── tier3_platform/        # Tier 3 Platform & ハードウェア依存部
│   └── drivers/           # 静的DIで合成する交換可能なドライバ群
│       ├── platform_config.py # プラットフォームドライバ構成
│       ├── hal/           # HAL標準I/OとHAL結線
│       ├── logging/       # ホスト側ログSink
│       └── wasi/          # Fireball console/log + uvwasi adapter
│
├── qa/                    # 単体・結合・WASMワークロードの品質保証コード
│   ├── run_all.py         # 30本のコンポーネント単体テスト
│   ├── shared/            # 複数スイートが使うテスト専用ヘルパーとfixture
│   ├── private/           # debugger adapter、スイート固有のテストダブル
│   ├── integration/       # HAL/IPC、ペアワイズ結合テストと結合ランナー
│   ├── workloads/         # SDK/libcゲストと公式Core Spec Suite
│   └── scenarios/         # 結合受入シナリオ12本
│       ├── scenario1_loader_and_memory.py
│       ├── scenario2_wasi_syscall_io.py
│       ├── scenario3_recursion_and_tables.py
│       ├── scenario4_hybrid_jit_loop.py
│       ├── scenario5_multimodule_unified_pc.py
│       ├── scenario6_coos_multitask_yield.py
│       ├── scenario7_gdb_socket_debugger.py
│       ├── scenario8_comprehensive_storage_coverage.py
│       ├── scenario9_ipc_router_and_logging.py
│       ├── scenario10_vmmio_virtual_devices.py
│       ├── scenario11_hal_and_wasi_drivers.py
│       ├── scenario12_wasi03p_uri_resolver.py
│       └── run_all.py     # 全シナリオ一括実行ドライバ
│
├── benchmarks/            # ベンチマーク
│   └── aobench/           # 3D レイトレーシング Ambient Occlusion ベンチマーク
│       ├── aobench.py     # f32 / Q8.8 の検証・描画
│       ├── aobench.wasm   # コンパイル済み Q8.8 ワークロード
│       └── bench_aobench.py # Python / C++ / JIT の比較測定
│
├── system.py              # 全 Tier 統合ファサード
└── main.py                # エントリポイント CLI
```

---

## 2. 12 本の結合テストシナリオ (Integration Test Scenarios)

以下の12シナリオを`qa/scenarios/run_all.py`から実行できる。実行結果と未解決の失敗は品質保証資料に記録する。
共通テスト支援は`qa/shared/`、特定の試験向け支援は`qa/private/`または各スイートの配下に置く。製品TierからQAコードをimportしない。

1. **Scenario 1: WASM Loader & Active Data Segments (`qa/scenarios/scenario1_loader_and_memory.py`)**:
   - ROM 上の WASM バイナリのゼロコピー解析、Function 以外の可変長メタデータを `offset/size` と先読みした LEB128 数値で索引化、アクティブデータセグメントのリニアメモリ初期配置。
2. **Scenario 2: WASI System Call & I/O Dispatch (`qa/scenarios/scenario2_wasi_syscall_io.py`)**:
   - `fireball_call` 経由での `wasi_snapshot_preview1.fd_write` (分散ギャザー I/O) および `proc_exit` 終了コード伝播。
3. **Scenario 3: Recursion & Indirect Table Dispatch (`qa/scenarios/scenario3_recursion_and_tables.py`)**:
   - 再帰呼び出し、CallFrame/ControlFrame インライン整合性、`call_indirect` による Table+Element 間接ディスパッチと型シグネチャ照合。
4. **Scenario 4: Hybrid JIT Compilation & Hotspot (`qa/scenarios/scenario4_hybrid_jit_loop.py`)**:
   - 2-bit カードマーキング（UNEXEC → EXEC → HOT → COMPILED）によるホットスポット検出、Clang integrated assemblerによるx64 trace単位ネイティブコード生成、インタープリタと JIT の差分実行検証。
5. **Scenario 5: Multi-Function Code-section PC & Radix (`qa/scenarios/scenario5_multimodule_unified_pc.py`)**:
   - Code section payload相対PCのFoldingミックス（`fold_mix32`）とRadixBinaryTreeViewによるキャッシュ索引、複数関数にまたがるJITトレース実行。
6. **Scenario 6: COOS Cooperative Multitasking (`qa/scenarios/scenario6_coos_multitask_yield.py`)**:
   - コルーチン協調マルチタスク、トレース境界での Yield 判定（`{ADR_LoopBackedgeYield}`）、Producer-Consumer CSP 直接ハンドオフ。
7. **Scenario 7: GDB Remote Debugger Socket Session (`qa/scenarios/scenario7_gdb_socket_debugger.py`)**:
   - 実際の TCP ソケット経由での GDB Remote Serial Protocol (RSP) 対話（`?`, `g`, `m`, `M`, `Z0`, `s`, `c`）、ブレークポイント停止と再開。
8. **Scenario 8: Storage Coverage & GDB Debugger (`qa/scenarios/scenario8_comprehensive_storage_coverage.py`)**:
   - メモリ全幅（8/16/32-bit 符号/ゼロ拡張）、グローバル・ローカル変数の永続性、および稼働中の GDB ソケットデバッグ統合。
9. **Scenario 9: IPC Router & Structured Logging (`qa/scenarios/scenario9_ipc_router_and_logging.py`)**:
   - 3段階ルーティング（Stage 1 URI検索 → Stage 2 RBAC判定 → Stage 3 Zero-Copy CSP Rendezvous 所有権移譲）、RBAC拒否・メッセージサイズ超過の事前拒絶、構造化ログのアイドルフラッシュ。
10. **Scenario 10: vMMIO Virtual Devices & Address Translation (`qa/scenarios/scenario10_vmmio_virtual_devices.py`)**:
    - 2段階ダイレクトデコードページテーブル、Bit 31 ゲスト RAM バイパス、Direct-Mapped ソフトウェア TLB（Folding XOR Hash）、タスク間共有メモリ（FC=0xE）の所有権検証と `TRAP_OWNER_MISMATCH` 遮断、パススルー物理アクセス。
11. **Scenario 11: HAL & uvwasi Drivers (`qa/scenarios/scenario11_hal_and_wasi_drivers.py`)**:
   - HAL 標準入出力ストリーム（stdin/stdout）と Timer、および WASI Preview 1（fd_read, fd_write, fd_seek, random_get, clock_time_get）。
12. **Scenario 12: WASI 0.3p URI Resolver (`qa/scenarios/scenario12_wasi03p_uri_resolver.py`)**:
   - 階層型 URI 解決、ドライバ能力照会、HAL バッファ経由の IPC コマンド、および WASI 0.1p アダプタ委譲。

---

## 3. 主要アーキテクチャの仕様準拠

### A. LOOP後方分岐回数による協調的 Yield (`{ADR_LoopBackedgeYield}`)
C++ InterpreterとHybrid JITは、同じ実行コンテキストのLOOP後方分岐回数を使う。取得した後方分岐ごとにC++ handlerが状態を更新し、回数がしきい値に達するまでC++ dispatcher内で実行を続ける。到達時に両経路は同じyield statusを返し、COOSへ制御を戻す。

### B. JITのRuntime API呼出し契約
x64参照構成のRuntime API tableと呼出規約は [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) と [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) に従う。ARMv8-Mの命令列、ABI、Runtime API配置とROM/RAM使用量はTBDであり、x64の結果から推定しない。

### C. Ambient Occlusion ベンチマーク (`benchmarks/aobench/aobench.py`)
Float32経路とQ8.8固定小数点経路を同じ入力で実行する。WASI `fd_write`の出力と描画結果を比較し、Interpreter/JIT間で結果が一致することを確認する。このワークロードから特定の組み込みCPUや物理メモリ予算は推定しない。

---

## 4. パフォーマンスとベンチマーク

参照実装のベンチマーク実行手順と計測コマンドは [`benchmarks/README.md`](benchmarks/README.md) に集約する。最新の測定結果は [`benchmarks/BENCHMARK_REPORT.md`](benchmarks/BENCHMARK_REPORT.md) を参照する。

## 5. 実行方法

### 全結合テストの実行
```bash
# ペアワイズ結合テストと12シナリオを実行
uv run --offline --no-sync python experiments/pysim/qa/integration/run_all.py

# 通常・-O・-OOの子プロセスで正常操作の結果と状態を比較する
uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/cross_cutting/test_entrypoint.py -k "optimized or runner"

# 製品Tierの静的型検査
uv run --offline --no-sync pyright --project pyrightconfig.json
```

最適化回帰では合法なWASMの算術・JIT結果、READY登録、PTE、PC頻度、値スタック解放を直接比較する。
通常の単体・結合試験は、assertによる不正入力拒否の契約も検査する。
runnerへ`-O`を指定した場合は子プロセスにも伝播するが、拒否を期待する負例は同じ意味を持たない。

### 全単体テストの実行
```bash
uv run --offline --no-sync python experiments/pysim/qa/run_all.py
```

### WASMバイナリワークロードの実行
```bash
# 初回のみ: SDKと固定revisionの公式Core WASTを用意する
uv run --offline --no-sync python tools/guest_bindings/build_wasi_guest.py --prepare-sdk
uv run --system-certs python tools/guest_bindings/fetch_wasm_core_suite.py

# wast2json (WABT 1.0.36) をPATH上で利用可能にして実行する
uv run --offline --no-sync python experiments/pysim/qa/workloads/run_all.py
```

### 3D AO-Bench

実行手順は [`benchmarks/README.md`](benchmarks/README.md) に記載する。

### JIT C++実装とABI
`native/tier3_plugins/jit/trace_compiler.cxx`はWASM命令をcompile時に復号し、型・offset・融合形に対応するステンシルと定数を一つのtrace blobへ連結する。x64の`operations.S`、`trace.S`、`common.S`は`tools/jit_stencil_gen/generate_assembly.py`でPIC raw binaryにアセンブルし、`.text`を抽出してC++ byte arrayへ変換する。操作間の成功slotは次のステンシルで置き換わるため、通常の命令列はfall-throughし、次opcode fetchやoperation dispatcherを持たない。

Runtime APIが必要な操作はtrace内のAPI stencilから共通コード領域にあるAPI tableを呼ぶ。Runtime APIが通常継続を返すと後続のJIT operationを実行する。trapや明示的な境界exitが記録された場合だけ共通abort経路を通る。Interpreter handlerは同じRuntime APIを呼び、テンプレートadapterがCPS継続へ接続する。API呼出しはインタープリタへのフォールバックではない。

Interpreter handlerとJIT trace entryはいずれも`ctx`, `ip*`, `sp*`, `rel*`の4引数を受け取る。Interpreterは基本ブロック入口で注釈プールcursorを設定し、JITはtrace headerの相対offsetからtrace末尾の定数pool cursorを設定する。JITのRuntime API recordを読むたびに`rel`を進め、pool末尾の相対値からtrace headerへ戻る。ローカル基点は実行コンテキストから取得する。Python側は`ctypes`で固定レイアウトのレコードとC ABI関数を扱い、C++コードはCPython APIに依存しない。Linux共有ライブラリには`-g`を付け、AMD uProfとIntel VTuneでC++ソース位置を解決できるようにする。

JIT chainはtrace末尾から共通コード領域のchain dispatcherへ入り、そこから次trace bodyへtail-jumpする経路を指す。C++ handler後にC++ dispatcherが常駐traceを起動する遷移はchainではない。QAの`native_dispatch_trace_transitions`はC++ handlerからJIT traceへのdispatcher遷移数である。診断カウンタ、内部状態ダンプ、dispatch snapshotはQAハーネスが所有する。診断実行はQA専用ライブラリを使う。製品Runtimeは診断API・状態を持たない。ホットスポット検出は製品JIT拡張の責務であり、実行履歴は一つの固定リングへ直接記録する。
```bash
# Windows: clang-cl + Visual Studio Build Tools + Windows SDK が必要
powershell experiments/pysim/native/tier3_plugins/jit/build_native.ps1

# Linux/WSL: Clang 17+ が必要
bash experiments/pysim/native/tier3_plugins/jit/build_native.sh
```

### Tier 2インタープリタのABI
`native/tier2_runtime/interpreter/native_interpreter.cxx`はC ABIのstep／dispatch入口を公開し、utility、handler、runtimeのprivate headerを一つの翻訳単位へ順に取り込む。ユーティリティはデコード・固定スタック・制御プリミティブ、ハンドラはコンパイル時選択の演算と固定256スロット表、runtimeはdispatch制御とABI境界を担当する。Python層は`abi/native_abi.py`で実行コンテキスト、スタック、dispatch metadataを固定レイアウトの引数レコードに組み立て、C ABIを`ctypes`で呼ぶ。C++側はCPython APIを使わず、Python所有bufferをポインタと長さで受け取る。製品のdispatch入口は通常実行と実行拡張付き実行を扱う。診断付きdispatch入口はQA専用ライブラリへ置く。

C++実装済みの命令はC++ handlerが処理する。未対応命令や外部呼出しは現在のPCで実行境界へ戻り、trapと完了もstatusとして返す。Tier 2 Interpreter と Tier 3 JIT のC++実行には、それぞれの共有ライブラリを必須とする。
```bash
# Windows: clang-cl + Visual Studio Build Tools + Windows SDK が必要
powershell experiments/pysim/native/tier2_runtime/interpreter/build_native.ps1

# Linux/WSL: clang が必要
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
```

実行ホットパスはhandler headerの固定表からの直接tail-callと、runtime headerのdispatch loopを通る`fb_native_run_dispatch`系C ABIである。LEB128はロード時デコーダの責務で実行時境界には入らない。

## 6. 参照実装固有の情報

この節は `experiments/pysim/` の現在のコード構成と測定条件を記録する。ここにあるPython API、内部データ構造、ホスト時計、QA経路は組み込み製品のAPIや資源条件を定義しない。製品の契約は `docs/` の要求仕様とコンポーネント設計書を参照する。

### 6.1 Python/C++参照実装の差分

- GDB RSPの参照実装では`sp`は`len(ctx.stack)`、`fp`は固定値`0`に対応する。
- `tier2_runtime/abi/integer_types.py` は、同じ基底整数型のPC、関数内IP、コード範囲、ブロック番号、フレーム内word位置を`typing.NewType`で区別する。
- Schedulerの自動ID割当は`next_id`と固定容量フリーリストを使う。未使用IDの取得と返却済みIDの再利用ではタスク表を検索せず、明示IDは位置索引で予約する。終了済みTCBスロットの探索はタスク表を走査する。QAは生成・終了の反復、待機マスク境界、解放済み共有メモリの移送を直接検査する。
- Tier 1の`SharedBlock`はCSP送受信時の単一所有権移譲をモデル化する。IPC key/valueのscope/type byteは`ipc_router.py`の`ScopeKind`と`DataType`で作り、`FUNCTIONAL` / `UINT32`は`0x01`となる。
- Loaderは不正WASMを`InvalidWasm`としてtrapにし、参照実装上のtrap codeに`INVALID_WASM_BINARY`を使う。Python最適化モードでassertが無効になる条件もQAで分けて検査する。
- Runtime parserは不正WASMをtrapで拒否する。printkのPython decoderは未知IDをassertで検出し、次の境界を推測して復号を続けない。
- Runtimeログの既定時計は`time.monotonic_ns()`で、参照環境の周波数は1 GHzである。32-bit tickの一周は4.294967296秒となり、差分計測は一周未満の区間を前提とする。
- ホスト用platform timerも64-bit `time.monotonic_ns()`を使う。物理32-bit timerの折返しをこのホスト経路で検証するものではない。
- printkのイベントレコード作成とBase64符号化はC++側で行う。Python側には物理Sinkと辞書復号のadapterがある。任意計測構成では同じ同期バイト出力へ`label=value`形式の64-bit値を出力できる。
- `tier3_platform/libfireball.py` は汎用host-call引数の詰め替えと専用vIRQ/vDMA port転送をモデル化する。C++参照wrapperはraw 4 importを扱い、WASI Preview1変換、URI Resolver、HAL buffer処理は未実装である。

### 6.2 JIT参照実装の内部情報

- `BumpAllocator`は実行用bufferとLoader由来のtyped backing viewを管理する。JIT managerは借用期間中それらを保持し、native compilerへ渡す。
- x64 compilerは実行コードregion内のtrace出力領域にassembly生成コード、命令エンコード済みlocal access、即値、Runtime API用定数poolを構成する。decode済み命令列、operation symbol列、最大129件の定数record staging、stack modelは固定長配列で保持する。実行可能コード領域とLoader arenaのbufferは別管理する。
- `jit_runtime<Measurement>`は計測型でQAと製品構成を切り替える。製品側は`void`、QA側は専用計測型を選ぶ。QA用の代替block入力、compiler無効化、自己所有memory、16-bit trace参照IDの検査をQA構成に限定する。

### 6.3 JIT命令プロファイルの根拠

操作対応と融合候補の選定に使う静的データは、`benchmarks/profile/guest/suite.c`をClang 21.1.8の`--target=wasm32 -mcpu=mvp -O2`でWASMへ変換し、`wasm-objdump`の命令列から関数境界をまたがない2〜4命令n-gramと即値を数えたものである。28関数・6,460命令では、`local.get i32.const i32.add`が305回、`i32.const i32.add`が382回、`i32.const i32.mul`が83回、`i32.const i32.and`が60回だった。加算即値`1` / `-1`は97 / 21回、乗算即値`3` / `5`は1 / 3回、AND即値`255` / `65535`は11 / 2回だった。空スタックからの`local.get, local.get, i32.*`融合候補は85件だった。これらはコンパイル済み入力内の静的出現数であり、実行時頻度や生成stencilの機械語命令数ではない。

### 6.4 資源測定の実行方法と記録

ランタイムarena測定と資源一覧は次のentry pointで再取得する。結果はホストABIと指定入力に対する参照値であり、対象MCUのRAM/ROM適合を証明しない。

```bash
bash experiments/pysim/native/tier1_core/printk/build_native.sh
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
bash experiments/pysim/native/tier3_plugins/jit/build_native.sh
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --qa
uv run python experiments/pysim/benchmarks/memory/bench_runtime_memory.py \
  --calls 3 --output /tmp/fireball-runtime-memory.json
uv run python experiments/pysim/benchmarks/memory/bench_resource_inventory.py \
  --output /tmp/fireball-resource-inventory.json
```

最新のarena、資源一覧、stencil ROM、および製品C/C++ SLOCのmachine-readable結果は、[`runtime_memory.json`](benchmarks/results/latest/runtime_memory.json)、[`resource_inventory.json`](benchmarks/results/latest/resource_inventory.json)、[`stencil_rom.json`](benchmarks/results/latest/stencil_rom.json)、[`sloc.json`](benchmarks/results/latest/sloc.json)にある。歴史的な差分測定は次のレポートとデータを参照する。

| 対象 | 実行条件と結果 |
| :--- | :--- |
| BasicBlock index metadata | [`block_index_dispatch_20261013/REPORT.md`](benchmarks/results/block_index_dispatch_20261013/REPORT.md) |
| 注釈poolと座標型 | [`annotation_types_20261013/REPORT.md`](benchmarks/results/annotation_types_20261013/REPORT.md) |
| JIT定数poolと`br_table`注釈 | [`jit_const_pool_20261013/REPORT.md`](benchmarks/results/jit_const_pool_20261013/REPORT.md)、[`br_table_measurement.json`](benchmarks/results/jit_const_pool_20261013/br_table_measurement.json) |
| Interpreter metadataとframeの削除 | [`storage_cleanup_20261013/REPORT.md`](benchmarks/results/storage_cleanup_20261013/REPORT.md)、[`aobench_after.json`](benchmarks/results/storage_cleanup_20261013/aobench_after.json)、[`storage_after.json`](benchmarks/results/storage_cleanup_20261013/storage_after.json) |
| 過去の基準計測metadata | [`20261014/pre_req017_latest/metadata.json`](benchmarks/results/20261014/pre_req017_latest/metadata.json) |

入力WASMのROM byte length、ホストELF section、Python管理領域、32-bit参照レイアウトとx86_64 ABIの組合せ、JIT runtime構造体、およびC++ compile stackは各測定結果のscopeに従って扱う。WASM logical linear memoryをMCU物理RAMと同一視しない。Cortex-M33への容量適合を判定するときは、対象ABI、物理配置、同時生存数、linker map、stack、driver領域を含めて別途計上する。
