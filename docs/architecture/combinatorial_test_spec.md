# システム全体 ペアワイズ組み合わせテスト仕様書 (Combinatorial Test Specification)

<!-- traceability: {Pairwise_Combinatorial_Testing} -->

## 1. 目的と網羅基準

本書は、Fireball ハイパーバイザの主要機能コンポーネント（Tier 1 Core、Tier 1 Interface、Tier 2 Runtime、Tier 3 JIT / Platform）が連携動作する際のエッジケースおよび潜在的な機能干渉（Feature Interaction）を検知するため、**直交パラメータ群に対するペアワイズ法（2-way All-Pairs Testing）**に基づく組み合わせテストの仕様を定義する。

### 1.1 網羅基準 (Coverage Criteria)
- **ペアワイズ（2-way）完全被覆**: 定義された全 7 因子（各 3〜5 水準、全数 8,640 通り）における任意の 2 因子間の組み合わせ全 21 組（計 288 ペア）を **100% 網羅** する。
- **不変条件の保持（Invariant Preservation）**: どのような因子の組み合わせにおいても、メモリ境界検査、RBAC/所有権分離、CPS スタック整合性、および 2-bit カードマーキングの単調性が破綻しないことを実証する。

---

## 2. 因子（Factors）および水準（Levels）定義

| 因子 ID | 因子名 (Factor Name) | 水準数 | 水準 0 (L0) | 水準 1 (L1) | 水準 2 (L2) | 水準 3 (L3) | 水準 4 (L4) |
| :--- | :--- | :---: | :--- | :--- | :--- | :--- | :--- |
| **F1** | **実行エンジン (Engine)** | 3 | `interp` (CPS 純粋解釈) | `jit` (Native x64) | `hybrid` (Interp $\leftrightarrow$ JIT) | - | - |
| **F2** | **JIT キャッシュ (Cache)** | 4 | `cold` (初回コンパイル) | `warm` (Active ヒット) | `evict` (3面世代交代) | `flush` (デバッガ全破棄) | - |
| **F3** | **メモリ幅 (MemWidth)** | 4 | `8bit` (`store8`/`load8`) | `16bit` (`store16`/`load16`) | `32bit` (`store`/`load`) | `grow` (`memory.grow`) | - |
| **F4** | **ストレージ (Storage)** | 4 | `locals` (スタック変数) | `globals` (モジュール大域) | `ram` (リニア RAM) | `shm` (vMMIO 共有メモリ) | - |
| **F5** | **ホスト連携 (HostCall)** | 5 | `none` (純粋演算) | `wasi_console` (`fd_write`) | `wasi_vfs` (In-Memory VFS) | `ipc` (Zero-Copy ルーティング) | `hal` (GPIO/I2C/SPI/Timer) |
| **F6** | **スケジューラ (Scheduler)** | 3 | `noint` (一括完走) | `yield` (Fuel 境界中断) | `multi` (協調マルチタスク) | - | - |
| **F7** | **デバッガ (Debugger)** | 3 | `detached` (非接続) | `inspect` (レジスタ/メモリ読出) | `active` (ブレーク/ステップ/改変) | - | - |

---

## 3. ペアワイズ テストケース マトリクス (Test Matrix)

全 288 ペアを 100% 網羅する 26 テストケース：

| Test ID | F1: Engine | F2: Cache | F3: MemWidth | F4: Storage | F5: HostCall | F6: Scheduler | F7: Debugger | 主な検証責務・期待動作 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **PAIR-01** | `hybrid` | `cold` | `8bit` | `ram` | `wasi_console` | `noint` | `detached` | 8-bit RAM 書き込み $\to$ JIT 初回昇格 $\to$ WASI コンソール出力の一括完走 |
| **PAIR-02** | `jit` | `evict` | `8bit` | `globals` | `ipc` | `yield` | `inspect` | JIT 3面キャッシュ代謝下での Global 変更と IPC 送信、Fuel 境界 yield と GDB レジスタ読出 |
| **PAIR-03** | `interp` | `warm` | `32bit` | `locals` | `none` | `yield` | `detached` | 32-bit ローカル変数演算、CPS インタプリタの Fuel yield 境界中断とスタック復元 |
| **PAIR-04** | `hybrid` | `evict` | `16bit` | `locals` | `wasi_vfs` | `multi` | `active` | 16-bit 演算、JIT キャッシュ退縮、VFS `fd_read`、コルーチン交代、GDB ブレークポイント捕捉 |
| **PAIR-05** | `jit` | `warm` | `grow` | `shm` | `hal` | `noint` | `active` | JIT ネイティブ実行中の `memory.grow`、共有メモリ、HAL GPIO 駆動、GDB 変数改変 |
| **PAIR-06** | `interp` | `flush` | `16bit` | `shm` | `wasi_console` | `multi` | `inspect` | インタプリタ実行中のデバッガによるキャッシュ破棄、SHM 読み書き、WASI 出力、タスク交代 |
| **PAIR-07** | `hybrid` | `cold` | `grow` | `globals` | `none` | `multi` | `inspect` | `memory.grow` 後の大域変数操作、JIT コールド生成、マルチタスク並行実行、GDB メモリ検査 |
| **PAIR-08** | `jit` | `flush` | `16bit` | `ram` | `none` | `yield` | `active` | デバッガのメモリパッチによる JIT 全破棄 $\to$ インタプリタ安全フォールバック $\to$ 再コンパイル |
| **PAIR-09** | `jit` | `evict` | `32bit` | `ram` | `hal` | `multi` | `detached` | JIT 3面世代交代、32-bit RAM、HAL SPI/EEPROM 連携、協調マルチタスク |
| **PAIR-10** | `interp` | `flush` | `grow` | `locals` | `ipc` | `noint` | `detached` | `memory.grow` 後のローカル変数退避、Zero-Copy IPC ルーティング、キャッシュ無効化耐性 |
| **PAIR-11** | `hybrid` | `warm` | `32bit` | `globals` | `wasi_vfs` | `noint` | `inspect` | Warm JIT キャッシュでの 32-bit Global 保持、VFS `fd_seek`、GDB パッシブ監視 |
| **PAIR-12** | `interp` | `cold` | `32bit` | `shm` | `wasi_vfs` | `yield` | `active` | インタプリタによる SHM アクセス、VFS `config.ini` 読み出し、GDB シングルステップ |
| **PAIR-13** | `interp` | `flush` | `8bit` | `locals` | `hal` | `multi` | `inspect` | 8-bit ローカル変数、HAL I2C 温度読み出し、マルチタスク yield、デバッガ接続 |
| **PAIR-14** | `interp` | `evict` | `grow` | `globals` | `wasi_console` | `yield` | `detached` | ページ拡張後の Global 永続化、WASI `fd_write` 出力、Fuel 枯渇中断 |
| **PAIR-15** | `hybrid` | `warm` | `16bit` | `ram` | `ipc` | `multi` | `active` | Hybrid 昇格後の 16-bit RAM、IPC メッセージ交換、マルチタスク、GDB ブレークポイント |
| **PAIR-16** | `hybrid` | `evict` | `16bit` | `shm` | `hal` | `yield` | `detached` | 16-bit SHM アクセス、JIT キャッシュ代謝、HAL Timer 読み出し、Fuel 境界 yield |
| **PAIR-17** | `hybrid` | `flush` | `16bit` | `globals` | `hal` | `noint` | `active` | HAL ドライバ呼び出し、デバッガによる JIT フラッシュ、Global 値の完全保持 |
| **PAIR-18** | `hybrid` | `evict` | `8bit` | `shm` | `none` | `noint` | `active` | 8-bit SHM アクセス、JIT キャッシュ退縮、GDB レジスタ直接書換と継続実行 |
| **PAIR-19** | `jit` | `cold` | `grow` | `locals` | `wasi_console` | `noint` | `active` | JIT 初回生成、`memory.grow`、ローカル変数整合性、WASI 出力、GDB 監視 |
| **PAIR-20** | `jit` | `evict` | `grow` | `ram` | `wasi_vfs` | `noint` | `detached` | JIT 3面キャッシュ代謝、拡張 RAM への VFS データ書き込みと読出検証 |
| **PAIR-21** | `interp` | `flush` | `8bit` | `ram` | `wasi_vfs` | `multi` | `inspect` | 8-bit 符号付き/符号なし RAM 読み書き、VFS 連携、キャッシュ無効化、マルチタスク |
| **PAIR-22** | `hybrid` | `cold` | `16bit` | `shm` | `ipc` | `multi` | `detached` | 16-bit SHM、IPC 3段階ルーティング、コルーチン並行実行、JIT 初回昇格 |
| **PAIR-23** | `jit` | `warm` | `32bit` | `shm` | `wasi_console` | `multi` | `active` | Warm JIT、32-bit SHM、WASI `fd_write`、マルチタスク、GDB ブレークポイント |
| **PAIR-24** | `jit` | `flush` | `32bit` | `shm` | `ipc` | `noint` | `active` | 32-bit SHM、IPC 所有権移譲、デバッガメモリパッチによる JIT フラッシュと復旧 |
| **PAIR-25** | `hybrid` | `cold` | `grow` | `globals` | `hal` | `yield` | `active` | `memory.grow`、Global 永続化、HAL GPIO、Fuel yield、GDB ステップ実行 |
| **PAIR-26** | `hybrid` | `warm` | `8bit` | `locals` | `wasi_vfs` | `multi` | `active` | 8-bit ローカル変数、Warm JIT、VFS ファイル読み出し、マルチタスク、GDB 監視 |

---

## 4. テスト実行環境・ランナー構成

- **テスト実装モジュール**: `experiments/pysim/tests/test_pairwise_combinations.py`
- **統合テストランナー**: `experiments/pysim/tests/run_all.py`
- **実行コマンド**:
  ```bash
  uv run --project tools/spec-integrator --with wasmtime python experiments/pysim/tests/run_all.py
  ```
