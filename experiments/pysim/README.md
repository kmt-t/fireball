# pysim — Fireball 実機シミュレータ (Experimental System Simulator)

`pysim` は、Fireball Hypervisor の全層（Tier 1 Core OS / Tier 2 Runtime / Tier 3 JIT & Platform）を Python 上で完全動作する形で具象化した、エンドツーエンドの実機実行可能シミュレータです。

仕様書（`docs/components/**`）で策定されたアーキテクチャ・状態機械・メモリレイアウト・ABI 規約が、実際に結合して正しく動作することを C++23 実装前に事前実証（Pressure-test）することを目的としています。

---

## 1. ディレクトリ構成 (Tier 階層準拠)

リポジトリの 3-Tier アーキテクチャに完全準拠したモジュール構成となっています：

```
experiments/pysim/
├── core/                  # Tier 1 Core OS & システム基盤
│   ├── os_coos.py         # スタックレスコルーチンスケジューラ (COOS)
│   ├── scheduler.py       # READYキュー, 対称遷移, CSP 直接ハンドオフ
│   ├── ipc_router.py      # ゼロコピー所有権移譲 & RBAC ルーティング
│   ├── system_logging.py  # 構造化ログカタログ & アイドルフラッシュ
│   ├── system_containers.py # BitView, FlatMapView, RadixBinaryTreeView, RingBuffer
│   ├── recovery.py        # 4つのリカバリー戦略 (ignore, retry, restart, panic)
│   └── system.py          # システム統合ファサード
│
├── runtime/               # Tier 2 Runtime & WASM 仮想マシン
│   ├── wasm_reader.py     # WASM バイナリパーサ & セクション検証 (ゼロコピー)
│   ├── wasm_module.py     # Module, Function, Table, Memory, Global, Export
│   ├── wasm_opcodes.py    # WASM 全オプコード定義 (i32, i64, f32, f64, 制御, メモリ)
│   ├── leb128.py          # uleb128 / sleb128 デコーダ
│   ├── control_flow.py    # 静的ブロック解析 & 制御構造デコーダ
│   ├── interpreter.py     # CPS 4引数 スレッド化インタープリタ (Threaded Interpreter)
│   ├── runtime_engine.py  # 2-bit カードマーキング Hotspot 検出 & 3面キャッシュ管理
│   ├── vmmio.py           # 2段階ダイレクトデコード ページテーブル & ソフトウェア TLB
│   └── debugger.py        # GDB Remote Serial Protocol (RSP) ソケットサーバー
│
├── jit/                   # Tier 3 JIT コンパイラ & ネイティブ生成
│   ├── x64_jit.py         # Copy-and-Patch JIT コンパイラ (x64)
│   ├── x64_assembler.py   # constexpr x64 アセンブラ
│   ├── x64_stencils.py    # 事前コンパイル済み JIT ネイティブステンシルカタログ
│   └── exec_mem.py        # MPU W^X トランザクション & 実行可能メモリ (mprotect/VirtualProtect)
│
├── platforms/             # Tier 3 Platform & ハードウェア抽象化
│   ├── platform_memory.py # 物理メモリパーティション (RAM/ROM) & PMSAv8 MPU
│   ├── hal.py             # HAL インターフェース & ダミードライバ (GPIO/I2C/SPI/Timer)
│   └── wasi.py            # WASI Preview 1 実装 & インメモリ VFS
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
└── aobench.py             # 3D レイトレーシング Ambient Occlusion ベンチマーク (f32 / Q8.8)
```

---

## 2. 実証された 11 の統合シナリオ (Integration Scenarios)

`pysim` は以下の全 11 シナリオ（`scenarios/run_all.py`）を 100% パスし、Fireball 仕様の実現可能性を実証しています：

1. **Scenario 1: WASM Loader & Active Data Segments (`scenario1_loader_and_memory.py`)**:
   - ROM 上の WASM バイナリのゼロコピー解析、Type/Func/Memory/Export セクション展開、アクティブデータセグメントのリニアメモリ初期配置。
2. **Scenario 2: WASI System Call & I/O Dispatch (`scenario2_wasi_syscall_io.py`)**:
   - `fireball_call` 経由での `wasi_snapshot_preview1.fd_write` (分散ギャザー I/O) および `proc_exit` 終了コード伝播。
3. **Scenario 3: Recursion & Indirect Table Dispatch (`scenario3_recursion_and_tables.py`)**:
   - 再帰呼び出し、CallFrame/ControlFrame インライン整合性、`call_indirect` による Table+Element 間接ディスパッチと型シグネチャ照合。
4. **Scenario 4: Hybrid JIT Compilation & Hotspot (`scenario4_hybrid_jit_loop.py`)**:
   - 2-bit カードマーキング（UNEXEC $	o$ EXEC $	o$ HOT $	o$ COMPILED）によるホットスポット検出、Copy-and-Patch x64 ネイティブコード生成、インタープリタと JIT の差分実行検証。
5. **Scenario 5: Multi-Function UnifiedPC & Radix (`scenario5_multimodule_unified_pc.py`)**:
   - `UnifiedPC`（`func_idx << 16 | pc`）の `bswap32` RadixBinaryTreeView による $O(1)$ キャッシュ索引、複数関数にまたがる JIT トレース実行。
6. **Scenario 6: COOS Cooperative Multitasking (`scenario6_coos_multitask_yield.py`)**:
   - コルーチン協調マルチタスク、トレース境界での Yield 判定（`{ADR_TraceBoundaryYield}`）、Producer-Consumer CSP 直接ハンドオフ。
7. **Scenario 7: GDB Remote Debugger Socket Session (`scenario7_gdb_socket_debugger.py`)**:
   - 実際の TCP ソケット経由での GDB Remote Serial Protocol (RSP) 対話（`?`, `g`, `m`, `M`, `Z0`, `s`, `c`）、ブレークポイント停止と再開。
8. **Scenario 8: Storage Coverage & GDB Debugger (`scenario8_comprehensive_storage_coverage.py`)**:
   - メモリ全幅（8/16/32-bit 符号/ゼロ拡張）、グローバル・ローカル変数の永続性、および稼働中の GDB ソケットデバッグ統合。
9. **Scenario 9: IPC Router & Structured Logging (`scenario9_ipc_router_and_logging.py`)**:
   - 3段階ルーティング（Stage 1 URI検索 $	o$ Stage 2 RBAC判定 $	o$ Stage 3 Zero-Copy 所有権移譲）、キュー満杯ロールバック、サービスフォールト回収（`RECLAIMED_BY_DROP`）、構造化ログのアイドルフラッシュ。
10. **Scenario 10: vMMIO Virtual Devices & Address Translation (`scenario10_vmmio_virtual_devices.py`)**:
    - 2段階ダイレクトデコードページテーブル、Bit 31 ゲスト RAM バイパス、Direct-Mapped ソフトウェア TLB（Folding XOR Hash）、タスク間共有メモリ（FC=0xE）の所有権検証と `TRAP_OWNER_MISMATCH` 遮断、パススルー物理アクセス。
11. **Scenario 11: HAL & WASI Dummy Drivers (`scenario11_hal_and_wasi_drivers.py`)**:
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

## 4. 実行方法

### 全シナリオの実行
```bash
# Windows (PowerShell)
powershell tools/run_all_tests.ps1 -pysim

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
