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
  - 関数やブロックの静的メタデータ（制御構造マップ、ローカル変数レイアウト、JIT コンパイル対象として妥当なブロックか等）は、ロード時に一度だけ計算してキャッシュし、ディスパッチのたびに再導出しない。`Function.control_map`、`Function.local_width_map_cache`（ローカルごとの幅を2ビットで保持）、`RuntimeEngine.trackable`（`BlockCardMask`: `next_pc is not None and byte_span >= min_trace_bytes` をロード時に1回だけ判定し1bit/ブロックでマスクする）が実例。WASM セクションの件数、要素数、ローカル数、JIT バンクの容量もロード時または入力メタデータから固定容量を決める。
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
   - 2-bit カードマーキング（UNEXEC → EXEC → HOT → COMPILED）によるホットスポット検出、Copy-and-Patch x64 ネイティブコード生成、インタープリタと JIT の差分実行検証。
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

### B. JITの共通helper呼出し契約
x64参照構成のhelper入口と呼出規約は [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) と [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) に従う。ARMv8-Mの命令列、ABI、helper配置とROM/RAM使用量はTBDであり、x64の結果から推定しない。

### C. Ambient Occlusion ベンチマーク (`benchmarks/aobench/aobench.py`)
Float32経路とQ8.8固定小数点経路を同じ入力で実行する。WASI `fd_write`の出力と描画結果を比較し、Interpreter/JIT間で結果が一致することを確認する。このワークロードから特定の組み込みCPUや物理メモリ予算は推定しない。

---

## 4. パフォーマンス & ベンチマーク評価 (Performance & Benchmark Evaluation)

`experiments/pysim/benchmarks/` 配下には、リニアメモリアクセス、vMMIO アドレス変換、Copy-and-Patch JIT コンパイラ、JIT キャッシュ代謝（3面ローテーション）、JIT カードエイジング（キャッシュ圧迫下のコールド関数混入）、および 3D レイトレーシング Ambient Occlusion (AO-Bench) の全 6 系統のベンチマークスイートが用意されています。

実測測定値、ホットスポット解析、JIT トレースチェイニング診断、および C++23 実機実装への性能予測の詳細レポートは、以下を参照してください：

👉 **[PySIM 統合ベンチマーク詳細レポート (`benchmarks/BENCHMARK_REPORT.md`)](benchmarks/BENCHMARK_REPORT.md)**

```bash
# uvキャッシュを一時領域へ置き、プロジェクトの .python-version / .venv を使う
export UV_CACHE_DIR=/tmp/fireball-uv-cache

# 依存関係の初回導入または変更時
uv sync
export UV_OFFLINE=true
export UV_NO_SYNC=true

# Linux/WSL: Tier 2 Interpreter と Tier 3 JIT のC++共有ライブラリをビルド
bash experiments/pysim/native/tier1_core/printk/build_native.sh
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
bash experiments/pysim/native/tier3_plugins/jit/build_native.sh

# 診断ベンチマークとQAは検査専用ライブラリも使う
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --qa
bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --qa

# 全ベンチマーク一括実行（wasmtime は JIT カードエイジング測定に使用）
uv run --offline --no-sync python experiments/pysim/benchmarks/run_all.py

# 3D AO-Bench 単体・ランタイム内部状態ダンプ
uv run --offline --no-sync python experiments/pysim/benchmarks/aobench/bench_aobench.py --debug

# Intel VTune / AMD uProf Hotspots用に算術ループの1経路だけを反復
uv run --offline --no-sync python -u experiments/pysim/benchmarks/jit/profile_arithmetic_path.py --path native-interpreter
```

`uv run`はリポジトリの`.python-version`と`.venv`を使う。`--path` は `python-handler`、`native-interpreter`、`hybrid-jit` から選ぶ。通常の速度比較には `bench_jit.py` の中央値を使い、プロファイラ収集中の実行時間は比較に使わない。Linuxの`perf stat cycles:u`で動的WASM命令あたりのホストサイクル数を測る方法、Intel VTuneとAMD uProfの収集コマンドは[JITベンチマーク仕様書](../../docs/components/tier3_plugins/benchmarks/jit_runtime_bench_spec.md)を参照する。

WindowsのVisual Studio developer shellでは`native/tier1_core/printk/build_native.ps1`を先に実行する。続いて `native/tier2_runtime/interpreter/build_native.ps1` と `native/tier3_plugins/jit/build_native.ps1` を実行してから、同じ `uv run` コマンドでベンチマークを実行します。

算術ループはPythonハンドラ、C++インタープリタ、JITの3経路を測定します。JITの速度比はPythonハンドラを基準に算出し、C++インタープリタの結果は別基準として表示します。

---

## 5. 実行方法

### 全結合テストの実行
```bash
# ペアワイズ結合テストと12シナリオを実行
uv run --offline --no-sync python experiments/pysim/qa/integration/run_all.py

# Python 最適化モード（assert 副作用の回帰検査）
uv run --offline --no-sync python -O experiments/pysim/qa/run_all.py
uv run --offline --no-sync python -O experiments/pysim/qa/integration/run_all.py

# 製品Tierの静的型検査
uv run --offline --no-sync pyright --project pyrightconfig.json
```

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

### 3D AO-Bench ベンチマークの実行
```bash
uv run --offline --no-sync python experiments/pysim/benchmarks/aobench/aobench.py
```

### JIT C++実装とABI
`native/tier3_plugins/jit/trace_compiler.cxx` はx64 Copy-and-Patchトレースをコンパイルし、固定バイト列のステンシルは`stencils_x64.hxx`に置く。Python側にある過去のx64ステンシル／アセンブラ試作は実行経路で使われないため削除した。Python側は`ctypes`で固定レイアウトのレコードとC ABI関数を扱い、C++コードはCPython APIに依存しない。実行時はC++ dispatcherがJITトレースのCPS 4引数ABI関数ポインタを呼ぶ。Linux共有ライブラリには`-g`を付け、AMD uProfとIntel VTuneでC++ソース位置を解決できるようにする。

JIT chainはtrace末尾から共通コード領域のchain dispatcherへ入り、そこから次trace bodyへtail-jumpする経路を指す。C++ handler後にC++ dispatcherが常駐traceを起動する遷移はchainではない。QAの`native_dispatch_trace_transitions`はC++ handlerからJIT traceへのdispatcher遷移数である。診断カウンタ、内部状態ダンプ、dispatch snapshotはQAハーネスが所有する。診断実行はQA専用ライブラリを使う。製品Runtimeは診断API・状態を持たない。ホットスポット検出は製品JIT拡張の責務であり、実行履歴は一つの固定リングへ直接記録する。
```bash
# Windows: clang-cl + Visual Studio Build Tools + Windows SDK が必要
powershell experiments/pysim/native/tier3_plugins/jit/build_native.ps1

# Linux/WSL: Clang 17+ が必要
bash experiments/pysim/native/tier3_plugins/jit/build_native.sh
```

### Tier 2インタープリタのABI
`native/tier2_runtime/interpreter/native_interpreter.cxx`はC++の固定256スロットハンドラ表とstep／dispatch入口を持つ。Python層は`abi/native_abi.py`で実行コンテキスト、スタック、dispatch metadataを固定レイアウトの引数レコードに組み立て、C ABIを`ctypes`で呼ぶ。C++側はCPython APIを使わず、Python所有bufferをポインタと長さで受け取る。製品のdispatch入口は通常実行と実行拡張付き実行を扱う。診断付きdispatch入口はQA専用ライブラリへ置く。

C++実装済みの命令はC++ handlerが処理する。未対応命令や外部呼出しは現在のPCで実行境界へ戻り、trapと完了もstatusとして返す。Tier 2 Interpreter と Tier 3 JIT のC++実行には、それぞれの共有ライブラリを必須とする。
```bash
# Windows: clang-cl + Visual Studio Build Tools + Windows SDK が必要
powershell experiments/pysim/native/tier2_runtime/interpreter/build_native.ps1

# Linux/WSL: clang が必要
bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
```

実行ホットパスは`native_interpreter.cxx`のhandler tableと`fb_native_run_dispatch`系C ABIであり、LEB128はロード時デコーダの責務で実行時境界には入らない。
