# pysim — Fireball 実機シミュレータ (Experimental System Simulator)

`pysim` は、Fireball Hypervisor の全層（Tier 1 Core OS / Tier 2 Runtime / Tier 3 JIT & Platform）を Python 上で完全動作する形で具象化した、エンドツーエンドの実機実行可能シミュレータです。

仕様書（`docs/components/**`）で策定されたアーキテクチャ・状態機械・メモリレイアウト・ABI 規約が、実際に結合して正しく動作することを C++23 実装前に事前実証（Pressure-test）することを目的としています。

### C++ 移植可能性の制約（本ディレクトリのみ）

`experiments/pysim/` 配下のコードは、この事前実証としての性質上、`.agents/rules/embedded_cpp.md` / `stdlib_policy.md` が定める組み込み C++（ヒープ割り当て・例外・RTTI 無効、`std::vector`/`std::map`/`std::unordered_map` 等の動的コンテナ禁止）の制約を型として引き継ぎます。**pysim は「RTTI（実行時型情報）のない静的型付け言語（C++）」だと思って記述してください。** 具体的には：

- **動的型検査・リフレクションの完全禁止（No RTTI）**:
  - `isinstance`, `type()`, `hasattr`, `getattr` 等のランタイム型検査やリフレクションを一切使用しない。
  - ユニバーサルな引数型（何でも受け取れる万能型・両対応型）にして内部で動的に型を判定して分岐するコードを書かない。関数のシグネチャは意図された具象型に一本化し、異なる型を扱う場合は別名関数として明確に分離する。
- **動的コンテナの禁止**:
  - Python の `dict`/`set` を実装の型として使わない。固定長配列、`FlatMapView`/`FlatSetView`/`RadixBinaryTreeView`/`BitView`（`core/system_containers.py`）、または `MutableFlatMapStorage`/`MutableFlatSetStorage` のような固定容量コンテナに置き換える。
- **例外制御フローの禁止**:
  - 例外を制御フローに使わない。失敗は戻り値（`None`、`Result`型、`IntEnum` ステータス等）で表現する。
- **厳格な整数型・Enum の使用**:
  - 文字列による状態・ID 比較を行わない。すべて `IntEnum` または整数インデックスで扱う。

**この制約は `experiments/pysim/` のみに適用され、`docs/components/**/concepts/*.py` の参考実装コードには適用されません。** concept コードは仕様の意図を伝えるための説明的なスニペットであり、可読性を優先して `dict` などの通常の Python イディオムを使ってよいものとします。

---

## 1. ディレクトリ構成 (Tier 階層準拠)

リポジトリの 3-Tier アーキテクチャに完全準拠したモジュール構成となっています：

```
experiments/pysim/
├── core/                  # Tier 1 Core OS & システム基盤
│   ├── scheduler.py       # COOS コルーチンスケジューラ, READYキュー, 対称遷移, CSP 直接ハンドオフ
│   ├── ipc_router.py      # ゼロコピー所有権移譲 & RBAC ルーティング
│   ├── logger.py          # 構造化ログカタログ & アイドルフラッシュ
│   ├── system_containers.py # BitView, FlatMapView, RadixBinaryTreeView, RingBuffer
│   └── recovery.py        # 4つのリカバリー戦略 (ignore, retry, restart, panic)
│
├── runtime/               # Tier 2 Runtime & WASM 仮想マシン
│   ├── wasm_reader.py     # WASM バイナリパーサ & セクション検証 (ゼロコピー)
│   ├── wasm_module.py     # Module, Function, Table, Memory, Global, Export
│   ├── wasm_opcodes.py    # WASM 全オプコード定義 (i32, i64, f32, f64, 制御, メモリ)
│   ├── leb128.py          # uleb128 / sleb128 デコーダ
│   ├── control_flow.py    # 静的ブロック解析 & 制御構造デコーダ
│   ├── loader.py          # WASM モジュールローダー & アクティブセグメント展開
│   ├── interpreter.py     # CPS 4引数 スレッド化インタープリタ (Threaded Interpreter)
│   ├── runtime_engine.py  # 2-bit カードマーキング Hotspot 検出 & 3面キャッシュ管理
│   ├── vmmio.py           # 2段階ダイレクトデコード ページテーブル & ソフトウェア TLB
│   ├── debugger.py        # 統合デバッガコントローラ
│   └── gdb_server.py      # GDB Remote Serial Protocol (RSP) ソケットサーバー
│
├── jit/                   # Tier 3 JIT コンパイラ & ネイティブ生成
│   ├── x64_jit.py         # Copy-and-Patch JIT コンパイラ (x64)
│   ├── x64_asm.py         # constexpr x64 アセンブラ
│   ├── x64_stencils.py    # 事前コンパイル済み JIT ネイティブステンシルカタログ
│   └── exec_memory.py     # MPU W^X トランザクション & 実行可能メモリ (mprotect/VirtualProtect)
│
├── platforms/             # Tier 3 Platform & ハードウェア抽象化
│   ├── memory.py          # 物理メモリパーティション (RAM/ROM) & PMSAv8 MPU
│   ├── hal.py             # HAL バス & メモリプール
│   ├── hal_dummy_drivers.py # HAL ダミードライバ (GPIO/I2C/SPI/Timer)
│   ├── wasi.py            # WASI Preview 1 ホストコンテキスト & システムコール
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

## 4. 3D AO-Bench 実行結果と性能・設計評価 (Benchmark Evaluation & Analysis)

`aobench.py` は、標準的な 3D レイトレーシング Ambient Occlusion レンダラーを WASM 上で実行し、Fireball の Tier 2（CPS 4引数インタープリタ）および Tier 3（2-bit カードマーキング Hotspot + Copy-and-Patch JIT）の正確性と性能特性を実測・評価するベンチマークです。

### 4.1 実測測定結果

```
================================================================================
                     3D AO-Bench Performance Results (Genuine Measured)         
================================================================================
  * Resolution:               32 x 16 (512 primary rays)
  * Hit Pixels:               272 (1088 AO sample rays)
  * Total Rays Traced:        1,600 Rays / Frame
  * Output Verified:          528 bytes (Exact match: 33 B x 16 rows, 0 NULs)
  * Differential Check:       PASS (Tier 2 & Tier 3 match byte-for-byte)
--------------------------------------------------------------------------------
  * Tier 2 (Threaded CPS):    4211.73 ms / frame  (380 Rays / Sec)
  * Tier 3 (Hybrid + JIT):    6126.91 ms / frame  (261 Rays / Sec)
  * Measured Speedup Ratio:   0.69x (Python Simulation FFI Overhead)
  * JIT Traces Compiled:      8 traces in Active cache bank
================================================================================
```

### 4.2 正確性と機能検証の評価 (Correctness: PASS)

1. **3D 幾何演算とシェーディングの完全性**:
   - 3D 空間上の球体・平面との交差判定（2次方程式判別式 $b^2 - c$、平方根計算）、法線ベクトル計算、各交点からの 4 本の半球サンプリングレイ追跡、遮蔽率積分、アスキー階調（`@` $\to$ `#` $\to$ `+` $\to$ `:` $\to$ ` `）マッピングまで、すべてのパイプラインが破綻なく動作。
2. **差分検証（Differential Verification）の完全一致**:
   - Tier 2 インタープリタと Tier 3 JIT のレンダリング出力（528 バイト）が **1 バイトの狂いもなく完全一致**。
   - IEEE 754 単精度浮動小数点（Float32）版でも 32x32 グリッド（1,024 rays）の球体交差判定が正常に完走。

### 4.3 シミュレータ性能特性 (Speedup 0.69x) の技術的分析

Python シミュレータ上において JIT 側が見かけ上遅くなっている理由は、**Python 特有のシミュレーション・オーバーヘッド**に起因するものです：

1. **Python $\leftrightarrow$ Ctypes FFI 境界遷移の支配的オーバーヘッド**:
   - `pysim` の JIT 実行は、生成した x64 マシンコードを実行するために `ctypes.CFUNCTYPE` 経由でネイティブ関数を呼び出します。
   - 短いトレース（数命令〜十数命令）ごとに Python インタープリタ $\leftrightarrow$ Ctypes の境界を数十万回またぐため、Ctypes の関数呼び出し・引数マーシャリングコスト（Python 側で数百 ns 〜 数 $\mu$s / 回）がネイティブ実行の高速性を相殺しています。
2. **オンデマンド・コンパイルコスト**:
   - ホットスポット検知後の Copy-and-Patch（ステンシルコピー、リロケーション解決、バックパッチ）を Python 上で逐次実行しているため、コンパイル処理時間がフレーム実行時間に含まれています。

### 4.4 実機 C++23 実装 (Cortex-M33 / x64) での評価と予測

実機 C++23 実装（Phase 1 以降）では、このボトルネックが原理的に消滅します：

1. **FFI コストのゼロ化（同一アドレス空間・同一レジスタ規約）**:
   - インタープリタのハンドラと JIT トレースは、全く同一の `__fastcall` CPS 4引数レジスタ規約（`R0: ip`, `R1: stack_bot`, `R2: local_base`, `R3: tos`）で直結します。
   - `[[clang::musttail]]` または単一のジャンプ命令（`BX` / `JMP`）で遷移するため、言語間境界の FFI オーバーヘッドは 0 サイクル（単一ジャンプ）となります。
2. **予測される実機高速化**:
   - トレース境界でのみ協調的 Yield（`{ADR_TraceBoundaryYield}`）を行うため、ホットループ内のダイレクト実行により、実機上では **JIT がインタープリタに対して 3x〜8x の実測高速化を達成**する見込みです。
3. **リソース効率**:
   - 3D レイトレーシングのような計算集約型タスクであっても、生成された JIT トレースはわずか **8 トレース（約 2〜3 KB）** でループのコアパスを完全に網羅しており、RAM 32KB（JIT キャッシュ予算 6.63KB）の範囲に余裕を持って収まることが確認されました。

---

## 5. 実行方法

### 全シナリオの実行
```bash
# Windows (PowerShell) — Level 2 以上で pysim スイートも実行される
powershell tools/run_all_tests.ps1 -level 2

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
`RuntimeEngine._invoke_trace` は既定で `ctypes.CFUNCTYPE`（libffi トランポリン、~1.1us/call）経由でコンパイル済みトレースを呼ぶ。`experiments/pysim/jit/native_trace_call.pyx` をビルドすると、同じ CPS 4引数呼び出し規約のまま生の C 関数ポインタ呼び出しに置き換わり、`bench_jit.py` の JIT 対インタープリタ比が実測で ~1.2x → ~1.8x に改善する。未ビルドでも自動的に ctypes 経路へフォールバックするため、素の Python 環境（`.pyd`/`.so` なし）でも通常どおり動作する。
```bash
# Windows: clang-cl + Visual Studio Build Tools + Windows SDK が必要
powershell experiments/pysim/jit/build_native.ps1

# Linux/WSL: clang が必要
./experiments/pysim/jit/build_native.sh
```
