# pysim — Fireball 実機シミュレータ (Experimental System Simulator)

`pysim` は、Fireball Hypervisor の全層（Tier 1 Core OS / Tier 2 Runtime / Tier 3 JIT & Platform）を Python 上で完全動作する形で具象化した、エンドツーエンドの実機実行可能シミュレータです。

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
  - 呼び出し元がすでに解決済みのオブジェクト（例: `BasicBlock`）を持っている場合、それを再度 PC からルックアップし直さない（該当関数に `block: T | None = None` のような省略可能引数を足し、渡された側を優先する）。

**この制約は `experiments/pysim/` のみに適用され、`docs/components/**/concepts/*.py` の参考実装コードには適用されません。** concept コードは仕様の意図を伝えるための説明的なスニペットであり、可読性を優先して `dict` などの通常の Python イディオムを使ってよいものとします。

---

## 1. ディレクトリ構成 (Tier 階層準拠)

リポジトリの 3-Tier アーキテクチャに完全準拠したモジュール構成となっています：

```
experiments/pysim/
├── tier1_core/            # Tier 1 Core OS & システム基盤
│   ├── scheduler.py       # COOS コルーチンスケジューラ, READYキュー, 対称遷移, CSP 直接ハンドオフ
│   ├── system_containers.py # BitView, FlatMapView, RadixBinaryTreeView, RingBuffer
│   └── interrupt_event.py # 固定長割り込みイベント
│
├── tier1_interface/       # Tier 1 Interface
│   └── ipc_router.py      # ゼロコピー所有権移譲 & RBAC ルーティング
│
├── tier2_runtime/         # Tier 2 Runtime & WASM 仮想マシン
│   ├── wasm_reader.py     # WASM バイナリパーサ & セクション検証 (ゼロコピー)
│   ├── wasm_module.py     # Module, Function, Table, Memory, Global, Export
│   ├── wasm_opcodes.py    # WASM 全オプコード定義 (i32, i64, f32, f64, 制御, メモリ)
│   ├── leb128.py          # uleb128 / sleb128 デコーダ
│   ├── control_flow.py    # 静的ブロック解析 & 制御構造デコーダ
│   ├── loader.py          # WASM モジュールローダー & アクティブセグメント展開
│   ├── jit_runtime_contract.py # Tier 2からTier 3 JITへの呼出し契約
│   ├── interop_abi.py     # Interpreter/JIT共有Native ABI
│   ├── vmmio.py           # 2段階ダイレクトデコード ページテーブル & ソフトウェア TLB
│   ├── logger.py          # 構造化ログカタログ & アイドルフラッシュ
│   ├── memory.py          # Tier 1メモリ契約のTier 2実装
│   ├── hal_dispatch.py    # HAL抽象ディスパッチ
│   ├── recovery.py        # リカバリ戦略
│   └── jit_abi.py         # 共有実行コンテキストABI定数
│
├── tier3_executer/             # Tier 3 Interpreter/JIT実行系
│   ├── interpreter/
│   │   └── interpreter.py # WASM命令ハンドラと実行状態
│   └── jit/
│       ├── runtime_engine.py # Interpreter/JIT統合ドライバ
│       ├── jit_runtime.py # JIT有効Interpreterと公開実行境界
│       ├── jit_manager.py # ホットスポット・コンパイル待ち列・検索
│       ├── jit_cache.py   # トレース記述子・3面キャッシュ
│       └── x64_jit.py     # Copy-and-Patch JITコンパイラ
│
├── tier3_platform/        # Tier 3 Platform & ハードウェア依存部
│   └── drivers/           # 静的DIで合成する交換可能なドライバ群
│       ├── platform_config.py # プラットフォームドライバ構成
│       ├── hal/           # HAL標準I/OとHAL結線
│       ├── logging/       # ホスト側ログSink
│       └── wasi/          # Fireball console/log + uvwasi adapter
│
├── qa/                    # 単体テスト・統合シナリオなど品質保証コード
│   ├── run_all.py         # 全単体テスト一括実行ドライバ
│   └── scenarios/         # 全 12 コンポーネント統合シナリオ
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
├── system.py              # 全 Tier 統合ファサード
├── aobench.py             # 3D レイトレーシング Ambient Occlusion ベンチマーク (f32 / Q8.8)
└── main.py                # エントリポイント CLI
```

---

## 2. 12 の統合シナリオ (Integration Scenarios)

以下の12シナリオを`qa/scenarios/run_all.py`から実行できる。実行結果と未解決の失敗は品質保証資料に記録する。

1. **Scenario 1: WASM Loader & Active Data Segments (`qa/scenarios/scenario1_loader_and_memory.py`)**:
   - ROM 上の WASM バイナリのゼロコピー解析、Function 以外の可変長メタデータを `offset/size` と先読みした LEB128 数値で索引化、アクティブデータセグメントのリニアメモリ初期配置。
2. **Scenario 2: WASI System Call & I/O Dispatch (`qa/scenarios/scenario2_wasi_syscall_io.py`)**:
   - `fireball_call` 経由での `wasi_snapshot_preview1.fd_write` (分散ギャザー I/O) および `proc_exit` 終了コード伝播。
3. **Scenario 3: Recursion & Indirect Table Dispatch (`qa/scenarios/scenario3_recursion_and_tables.py`)**:
   - 再帰呼び出し、CallFrame/ControlFrame インライン整合性、`call_indirect` による Table+Element 間接ディスパッチと型シグネチャ照合。
4. **Scenario 4: Hybrid JIT Compilation & Hotspot (`qa/scenarios/scenario4_hybrid_jit_loop.py`)**:
   - 2-bit カードマーキング（UNEXEC → EXEC → HOT → COMPILED）によるホットスポット検出、Copy-and-Patch x64 ネイティブコード生成、インタープリタと JIT の差分実行検証。
5. **Scenario 5: Multi-Function UnifiedPC & Radix (`qa/scenarios/scenario5_multimodule_unified_pc.py`)**:
   - `UnifiedPC`（`func_idx << 16 | pc`）の乗算Foldingミックス（`fold_mix32`）RadixBinaryTreeView による $O(1)$ キャッシュ索引、複数関数にまたがる JIT トレース実行。
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
x64参照構成のhelper入口と呼出規約は [`jit_runtime.md`](docs/components/tier3_executer/jit_runtime.md) と [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) に従う。ARMv8-Mの命令列、ABI、helper配置とROM/RAM使用量はTBDであり、x64の結果から推定しない。

### C. Ambient Occlusion ベンチマーク (`aobench.py`)
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

# Linux/WSL: Tier 3 実行に必要なネイティブ拡張をビルド
bash experiments/pysim/tier3_executer/interpreter/build_native.sh
bash experiments/pysim/tier3_executer/jit/build_native.sh

# 全ベンチマーク一括実行（wasmtime は JIT カードエイジング測定に使用）
uv run --offline --no-sync python experiments/pysim/benchmarks/run_all.py

# 3D AO-Bench 単体・ランタイム内部状態ダンプ
uv run --offline --no-sync python experiments/pysim/benchmarks/aobench/bench_aobench.py --debug

# Intel VTune / AMD uProf Hotspots用に算術ループの1経路だけを反復
uv run --offline --no-sync python -u experiments/pysim/benchmarks/jit/profile_arithmetic_path.py --path native-interpreter
```

`uv run`はリポジトリの`.python-version`と`.venv`を使う。`--path` は `python-handler`、`native-interpreter`、`hybrid-jit` から選ぶ。通常の速度比較には `bench_jit.py` の中央値を使い、プロファイラ収集中の実行時間は比較に使わない。Linuxの`perf stat cycles:u`で動的WASM命令あたりのホストサイクル数を測る方法、Intel VTuneとAMD uProfの収集コマンドは[JITベンチマーク仕様書](../../docs/components/tier3_executer/benchmarks/jit_runtime_bench_spec.md)を参照する。

Windows では `tier3_executer/interpreter/build_native.ps1` と `tier3_executer/jit/build_native.ps1` を実行してから、同じ `uv run` コマンドでベンチマークを実行します。

算術ループはPythonハンドラ、C++インタープリタ拡張、JITの3経路を測定します。JITの速度比はPythonハンドラを基準に算出し、C++拡張の結果は別基準として表示します。これにより拡張の有無でOS間の比較条件が変わるのを防ぎます。

---

## 5. 実行方法

### 全シナリオの実行
```bash
# Windows (PowerShell) — pysim ソース品質・テストを実行
powershell tools/check-src.ps1 -group pysim

# uv 経由で全シナリオを実行
uv run --offline --no-sync python experiments/pysim/qa/scenarios/run_all.py

# Python 最適化モード（assert 副作用の回帰検査）
uv run --offline --no-sync python -O experiments/pysim/qa/run_all.py
uv run --offline --no-sync python -O experiments/pysim/qa/scenarios/run_all.py

# 製品Tierの静的型検査
uv run --offline --no-sync pyright --project pyrightconfig.json
```

### 全単体テストの実行
```bash
uv run --offline --no-sync python experiments/pysim/qa/run_all.py
```

### 3D AO-Bench ベンチマークの実行
```bash
uv run --offline --no-sync python experiments/pysim/aobench.py
```

### JIT ネイティブコンパイラと呼び出し経路
`tier3_executer/jit/native_trace_call.cxx` は x64 Copy-and-Patch トレースのコンパイルを実装する。JIT コンパイラがこの拡張を直接 import するため、Tier 3 の JIT 実行にはビルドが必須である。実行時は `JITTrace` が所有するCPS 4引数ABIの関数ポインタをC++ dispatcherが呼ぶ。LOOP後方`BR` / `BR_IF`はC++ Interpreter handlerを通して制御状態を更新し、その取得回数を共通コンテキストに記録する。dispatcherは共有設定の回数に達するまでJIT traceとC++ handlerを連続実行してからRuntimeEngineへyieldを返す。C++ Interpreter単独経路も同じdispatcherと回数条件を使う。Linux拡張には `-g` を付け、AMD uProfとIntel VTuneでC++ソース位置を解決できるようにする。

JIT chainはtrace末尾から共通コード領域のchain dispatcherへ入り、そこから次trace bodyへtail-jumpする経路を指す。C++ handler後にC++ dispatcherが常駐traceを起動する遷移はchainではない。`native_dispatch_trace_transitions`はC++ handlerからJIT traceへのdispatcher遷移数であり、chain指標ではない。現状、共通コードchain dispatcherの実行回数を数える専用指標はない。`FB_CONF_RUNTIME_PROFILE_STATS`は既定で無効であり、通常のClangビルドでは統計収集コードを除外する。QAまたは診断用拡張を作る場合は`FIREBALL_BUILD_RUNTIME_PROFILE_STATS=1 bash experiments/pysim/tier3_executer/interpreter/build_native.sh`を実行し、実行時の収集はQA補助または`--collect-runtime-stats`で選ぶ。通常ビルドは環境変数なしで行う。`FB_CONF_JIT_HOTSPOT_PROFILING`は動的compileに必要な候補block観測を制御する。コンパイル済みtraceの定常状態を測る場合は、warm-up後に候補観測を無効化できる。dispatch snapshotはcache世代が変わったときだけ再生成する。
```bash
# Windows: clang-cl + Visual Studio Build Tools + Windows SDK が必要
powershell experiments/pysim/tier3_executer/jit/build_native.ps1

# Linux/WSL: Clang 17+ が必要
bash experiments/pysim/tier3_executer/jit/build_native.sh
```

### Tier 3インタープリタの境界
`tier3_executer/interpreter/native_interpreter.cxx`は、本番インタープリタに対応するC++の固定256スロットハンドラ表とstep／dispatch入口を持つ。handlerは`ctx, sp, local_base, tos`の4論理引数を共有し、ホストx64で検証したABIを使う。ARMv8-Mの物理引数配置と関数ABIはTBDであり、このシミュレータはARM適合を主張しない。Python側はネイティブ実行コンテキスト、operand/local/controlの固定領域を`memoryview`で渡し、C++ dispatcherがJIT traceとC++ handlerを後方分岐yield境界まで連続実行する。C++ Interpreter単独経路も同じ共有しきい値を使う。`bytes`のコピーや整数アドレス化は行わない。

C++実装済みの命令はC++ handlerが処理する。未対応命令や外部呼出しは現在のPCでPython境界へフォールバックし、trapと完了も境界statusとして返す。このフォールバックは後方互換層ではなく、実装が定める実行境界である。Tier 3実行にはC++拡張のビルドを必須とする。
```bash
# Windows: clang-cl + Visual Studio Build Tools + Windows SDK が必要
powershell experiments/pysim/tier3_executer/interpreter/build_native.ps1

# Linux/WSL: clang が必要
bash experiments/pysim/tier3_executer/interpreter/build_native.sh
```

実行ホットパスは`native_interpreter.cxx`のhandler tableと`run_native_dispatch`であり、LEB128はロード時デコーダの責務で実行時境界には入らない。旧Cython/CPS互換入口は提供しない。
