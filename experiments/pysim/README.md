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
  - コンパイル済みトレースのように、消費し切ったあとも同じ内容を繰り返し評価する必要がある場合は、既知の上限で容量を決めた `StaticVector` に詰め替えて保持する（`WASMTraceCompiler.compile_trace` が `TraceBlock.byte_span` を容量に使う実例）。
  - デコードした値のうち呼び出し側が使わないフィールドは、そもそもアンパックせずバイト位置だけ進めて捨てる（未使用の `const_value`/`memarg` 等をデコードしない）。
- **パース/ロード時に確定する値の実行時再計算禁止**:
  - 関数やブロックの静的メタデータ（制御構造マップ、ローカル変数レイアウト、JIT コンパイル対象として妥当なブロックか等）は、ロード時に一度だけ計算してキャッシュし、ディスパッチのたびに再導出しない。`Function.control_map`（遅延ビルド後キャッシュ）、`Function.locals_layout_cache`（`list(params) + list(locals_extra)` を初回呼び出し時に1度だけ計算しキャッシュする）、`RuntimeEngine.trackable`（`BlockCardMask`: `next_pc is not None and byte_span >= min_trace_bytes` をロード時に1回だけ判定し1bit/ブロックでマスクする）が実例。
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
│   ├── interpreter.py     # CPS 4引数 スレッド化インタープリタ (Threaded Interpreter)
│   ├── runtime_engine.py  # vSoC実行制御とTier 3 JITサービス連携
│   ├── vmmio.py           # 2段階ダイレクトデコード ページテーブル & ソフトウェア TLB
│   ├── logger.py          # 構造化ログカタログ & アイドルフラッシュ
│   ├── memory.py          # Tier 1メモリ契約のTier 2実装
│   ├── hal_dispatch.py    # HAL抽象ディスパッチ
│   ├── wasi.py            # WASIホスト境界
│   ├── recovery.py        # リカバリ戦略
│   ├── debugger.py        # 統合デバッガコントローラ
│   └── gdb_server.py      # GDB Remote Serial Protocol (RSP) ソケットサーバー
│
├── tier3_jit/             # Tier 3 JIT コンパイラ & ネイティブ生成
│   ├── jit_cache.py       # ホットスポット状態・トレース記述子・3面キャッシュ
│   ├── trace_compiler.py  # インタープリタ互換のフォールバックトレース生成
│   ├── x64_jit.py         # Copy-and-Patch JIT コンパイラ (x64)
│   ├── x64_asm.py         # constexpr x64 アセンブラ
│   ├── x64_stencils.py    # 事前コンパイル済み JIT ネイティブステンシルカタログ
│   └── exec_memory.py     # MPU W^X トランザクション & 実行可能メモリ (mprotect/VirtualProtect)
│
├── tier3_platform/        # Tier 3 Platform & ハードウェア依存部
│   ├── hal_dummy_drivers.py # HAL ダミードライバ (GPIO/I2C/SPI/Timer)
│   └── wasi_dummy_fs.py   # インメモリ VFS ファイルシステム
│
├── scenarios/             # 全 11 コンポーネント統合シナリオ (End-to-End Scenarios)
│   ├── scenario1_loader_and_memory.py
│   ├── scenario2_wasi_syscall_io.py
│   ├── scenario3_recursion_and_tables.py
│   ├── scenario4_hybrid_jit_loop.py
│   ├── scenario5_multimodule_unified_pc.py
│   ├── scenario6_coos_multitask_yield.py
│   ├── scenario7_gdb_socket_debugger.py
│   ├── scenario8_comprehensive_storage_coverage.py
│   ├── scenario9_ipc_router_and_logging.py
│   ├── scenario10_vmmio_virtual_devices.py
│   ├── scenario11_hal_and_wasi_drivers.py
│   └── run_all.py         # 全シナリオ一括実行ドライバ
│
├── tests/                 # 単体テストスイート (9 テストファイル)
│   └── run_all.py         # 全単体テスト一括実行ドライバ
│
├── system.py              # 全 Tier 統合ファサード
├── aobench.py             # 3D レイトレーシング Ambient Occlusion ベンチマーク (f32 / Q8.8)
└── main.py                # エントリポイント CLI
```

---

## 2. 実証された 11 の統合シナリオ (Integration Scenarios)

`pysim` は以下の全 11 シナリオ（`scenarios/run_all.py`）を 100% パスし、Fireball 仕様の実現可能性を実証しています：

1. **Scenario 1: WASM Loader & Active Data Segments (`scenarios/scenario1_loader_and_memory.py`)**:
   - ROM 上の WASM バイナリのゼロコピー解析、Type/Func/Memory/Export セクション展開、アクティブデータセグメントのリニアメモリ初期配置。
2. **Scenario 2: WASI System Call & I/O Dispatch (`scenarios/scenario2_wasi_syscall_io.py`)**:
   - `fireball_call` 経由での `wasi_snapshot_preview1.fd_write` (分散ギャザー I/O) および `proc_exit` 終了コード伝播。
3. **Scenario 3: Recursion & Indirect Table Dispatch (`scenarios/scenario3_recursion_and_tables.py`)**:
   - 再帰呼び出し、CallFrame/ControlFrame インライン整合性、`call_indirect` による Table+Element 間接ディスパッチと型シグネチャ照合。
4. **Scenario 4: Hybrid JIT Compilation & Hotspot (`scenarios/scenario4_hybrid_jit_loop.py`)**:
   - 2-bit カードマーキング（UNEXEC → EXEC → HOT → COMPILED）によるホットスポット検出、Copy-and-Patch x64 ネイティブコード生成、インタープリタと JIT の差分実行検証。
5. **Scenario 5: Multi-Function UnifiedPC & Radix (`scenarios/scenario5_multimodule_unified_pc.py`)**:
   - `UnifiedPC`（`func_idx << 16 | pc`）の `bswap32` RadixBinaryTreeView による $O(1)$ キャッシュ索引、複数関数にまたがる JIT トレース実行。
6. **Scenario 6: COOS Cooperative Multitasking (`scenarios/scenario6_coos_multitask_yield.py`)**:
   - コルーチン協調マルチタスク、トレース境界での Yield 判定（`{ADR_TraceBoundaryYield}`）、Producer-Consumer CSP 直接ハンドオフ。
7. **Scenario 7: GDB Remote Debugger Socket Session (`scenarios/scenario7_gdb_socket_debugger.py`)**:
   - 実際の TCP ソケット経由での GDB Remote Serial Protocol (RSP) 対話（`?`, `g`, `m`, `M`, `Z0`, `s`, `c`）、ブレークポイント停止と再開。
8. **Scenario 8: Storage Coverage & GDB Debugger (`scenarios/scenario8_comprehensive_storage_coverage.py`)**:
   - メモリ全幅（8/16/32-bit 符号/ゼロ拡張）、グローバル・ローカル変数の永続性、および稼働中の GDB ソケットデバッグ統合。
9. **Scenario 9: IPC Router & Structured Logging (`scenarios/scenario9_ipc_router_and_logging.py`)**:
   - 3段階ルーティング（Stage 1 URI検索 → Stage 2 RBAC判定 → Stage 3 Zero-Copy CSP Rendezvous 所有権移譲）、RBAC拒否・メッセージサイズ超過の事前拒絶、構造化ログのアイドルフラッシュ。
10. **Scenario 10: vMMIO Virtual Devices & Address Translation (`scenarios/scenario10_vmmio_virtual_devices.py`)**:
    - 2段階ダイレクトデコードページテーブル、Bit 31 ゲスト RAM バイパス、Direct-Mapped ソフトウェア TLB（Folding XOR Hash）、タスク間共有メモリ（FC=0xE）の所有権検証と `TRAP_OWNER_MISMATCH` 遮断、パススルー物理アクセス。
11. **Scenario 11: HAL & WASI Dummy Drivers (`scenarios/scenario11_hal_and_wasi_drivers.py`)**:
    - HAL GPIO（割り込み通知）、I2C（LM75 温度センサ）、SPI（EEPROM）、Timer、および WASI Preview 1（fd_read, fd_write, fd_seek, random_get, clock_time_get）。

---

## 3. 主要アーキテクチャの仕様準拠

### A. トレース境界での協調的 Yield (`{ADR_TraceBoundaryYield}`)
命令単位での精密な割り込みチェックを廃止し、**トレースの切れ目（基本ブロック末尾、ループバックエッジ、関数呼出/復帰、または JIT トレース脱出境界）でのみ `yield_threshold` を評価して `co_yield` を発行**します。ディスパッチループ内のオーバーヘッドをゼロ化し、最速の実行速度を達成しています。

### B. i64 / f32 / f64 の Libgcc ランタイムヘルパー連携 (`{Libgcc_Runtime_Helper}`)
32-bit 極小組み込み環境において、64-bit 整数演算（除算・剰余・ビットシフト）および浮動小数点（`f32`/`f64`）演算は、`libgcc` のヘルパー関数（`__divdi3`, `__adddf3` 等）を呼び出す専用ハンドラ（`fireball_rt_*`）経由で実行します。JIT コンパイラはこれらをインライン展開せずランタイムヘルパースタブ呼び出しに委譲することで、JIT ステンシルカタログの極小化（ROM 8KB 遵守）と FPU 有無のハードウェア差異の完全隠蔽を実現しています。

### C. 3D Ambient Occlusion ベンチマーク (`aobench.py`)
- **Float32 レンダラー**: IEEE 754 単精度浮動小数点（`f32.add`, `f32.sub`, `f32.mul`, `f32.div`, `f32.sqrt` 等）を用いた 3D 球体・平面の交差判定と Ambient Occlusion シェーディング。
- **Q8.8 固定小数点レンダラー**: 浮動小数点非搭載の極小環境向けに最適化された整数固定小数点レイトレーサー。
- WASI `fd_write` 経由でコンソールへアスキーグラデーションを出力し、Tier 2（インタープリタ）と Tier 3（JIT）のバイト完全一致を差分検証。

---

## 4. パフォーマンス & ベンチマーク評価 (Performance & Benchmark Evaluation)

`experiments/pysim/benchmarks/` 配下には、リニアメモリアクセス、vMMIO アドレス変換、Copy-and-Patch JIT コンパイラ、JIT キャッシュ代謝（3面ローテーション）、および 3D レイトレーシング Ambient Occlusion (AO-Bench) の全 5 系統のベンチマークスイートが用意されています。

実測測定値、ホットスポット解析、JIT トレースチェイニング診断、および C++23 実機実装への性能予測の詳細レポートは、以下を参照してください：

👉 **[PySIM 統合ベンチマーク詳細レポート (`benchmarks/BENCHMARK_REPORT.md`)](benchmarks/BENCHMARK_REPORT.md)**

```bash
# 全ベンチマーク一括実行
uv run python experiments/pysim/benchmarks/run_all.py

# 3D AO-Bench 単体・ランタイム内部状態ダンプ
uv run python experiments/pysim/benchmarks/aobench/bench_aobench.py --debug
```

---

## 5. 実行方法

### 全シナリオの実行
```bash
# Windows (PowerShell) — pysim ソース品質・テストを実行
powershell tools/check-src.ps1 -group pysim

# Python 直接実行
uv run --system-certs --with wasmtime python experiments/pysim/scenarios/run_all.py
```

### 全単体テストの実行
```bash
uv run --system-certs --with wasmtime python experiments/pysim/tests/run_all.py
```

### 3D AO-Bench ベンチマークの実行
```bash
uv run --system-certs --with wasmtime python experiments/pysim/aobench.py
```

### （任意）JIT トレース呼び出しの Cython ネイティブアクセラレータ
`RuntimeEngine._invoke_trace` は既定で `ctypes.CFUNCTYPE`（libffi トランポリン、~1.1us/call）経由でコンパイル済みトレースを呼ぶ。`experiments/pysim/tier3_jit/native_trace_call.pyx` をビルドすると、同じ CPS 4引数呼び出し規約のまま生の C 関数ポインタ呼び出しに置き換わり、`bench_jit.py` の JIT 対インタープリタ比が実測で ~1.2x → ~1.8x に改善する。未ビルドでも自動的に ctypes 経路へフォールバックするため、素の Python 環境（`.pyd`/`.so` なし）でも通常どおり動作する。
```bash
# Windows: clang-cl + Visual Studio Build Tools + Windows SDK が必要
powershell experiments/pysim/tier3_jit/build_native.ps1

# Linux/WSL: clang が必要
./experiments/pysim/tier3_jit/build_native.sh
```

### （任意）インタープリタホットパスの Cython Pure-Python モードアクセラレータ
`experiments/pysim/tier2_runtime/leb128.py`・`interpreter.py`・`runtime_engine.py` のディスパッチループ（`Interpreter.step()`）と最頻出ハンドラ（`local.get`/`local.set`/`i32.const`/`i32.add`/`i32.sub`/`i32.ge_s`/`br_if` 等）、および `_to_i32`/`_to_u32` 等のラップ関数は、Cython Pure-Python モード（`@cython.locals(...)` によるローカル変数の C 型付け）で書かれている。`import cython` は未ビルド時は無害な no-op シムとして動作するため、この 3 ファイルは常に素の Python として動作し、動作は一切変わらない。ビルドすると同じディレクトリに `.pyd`/`.so` が生成され、Python の import 解決が同名の `.py` より優先して読み込むため、他コードの変更なしに透過的に高速化される。`bench_jit.py` のインタープリタ実行時間が実測で無型コンパイル比 ~15〜20% 改善する（更なる高速化には `_HANDLERS` テーブル経由の多態的呼び出し自体の再設計が必要）。
```bash
# Windows: clang-cl + Visual Studio Build Tools + Windows SDK が必要
powershell experiments/pysim/tier2_runtime/build_native.ps1

# Linux/WSL: clang が必要
./experiments/pysim/tier2_runtime/build_native.sh
```
