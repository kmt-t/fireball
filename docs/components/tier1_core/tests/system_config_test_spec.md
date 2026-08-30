# システムコンフィグ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier1_core/system_config.md`
参考実装: なし（本コンポーネントは静的定数の定義のみで、実行時ロジックを持たない）
現行実装: `experiments/pysim`内で個別に散在する定数（`system.py`の`FB_CONF_GUEST_RAM_SIZE`等）。統合された「コンフィグモジュール」は存在しない。

コンパイル時定数（`FB_CONF_*`）の値そのものと、それらの間で要求される整合性（`static_assert`相当）を検証する。

## 2. テストケース一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| CFG-01 | メモリパーティション総和の一致 | 既定値 | 各`FB_CONF_*_HEAP_SIZE`等を合計 | `KERNEL(4096)+RUNTIME(2048)+SUBSYS(3072)+JIT_CACHE(6144)+INTERP_STACK(2048)+TASK_HEAP(4096)×MAX_GUEST_VMS(1) == MEMORY_POOL_SIZE(21504)` | §3.3.1 static_assert |
| CFG-02 | 統合プールが物理RAM以下 | 既定値 | 比較 | `FB_CONF_MEMORY_POOL_SIZE(21504) <= FB_CONF_PHYSICAL_RAM_SIZE(32768)` | §3.3.1 static_assert |
| CFG-03 | ゲストRAMサイズの一致 | 既定値 | 比較 | `FB_CONF_GUEST_RAM_SIZE == FB_CONF_TASK_HEAP_SIZE`（共に4096） | §3.3.1 static_assert |
| CFG-04 | ロール間通信許可マトリクスの形状 | - | `FB_CONF_ROUTER_ROLE_MATRIX`を確認 | 4x4の`bool`表で、`ipc_router_concept.py`のrole_matrixと矛盾しない（CLIENT_APP→CORE_SERVICE/PLATFORM_HAL=true、CLIENT_APP→DEBUGGER=false等） | §3.3.2 |
| CFG-05 | タスクID予約値の非衝突 | - | `FB_CONF_MAX_TASKS`(16) ≤ 254であることを確認 | `FB_TASK_ID_FLIGHT`(0xFF=255)と衝突しない | §3.3.6 |
| CFG-06 | JITキャッシュの3等分 | `FB_CONF_JIT_CACHE_SIZE`(6144), `FB_CONF_JIT_NUM_BUFFERS`(3) | 6144/3を計算 | 各バンク2048バイトで割り切れる | §3.3.4, runtime_vsoc.md §7.2 |
| CFG-07 | リトライ回数・待機時間の単一情報源 | 複数コンポーネントがリトライを実装 | `FB_CONF_RETRY_BACKOFF_MS`(10ms)と上限3回の参照元を確認 | すべてのコンポーネントが`system_config.md`のこの1値のみを参照し、独自の待機時間・回数を定義していない | §3.3.7「個別のコンポーネント文書で異なる待機時間・回数を独自に定義しないこと」 |
| CFG-08 | vMMIOアドレス基点の一意性 | - | `FB_CONF_GUEST_RAM_BASE`(0x0)、`FB_CONF_VMMIO_BASE`(0x8000_0000)、`FB_CONF_VSOC_PASSTHROUGH_BASE`(0x4000_0000)を確認 | Bit31の使い分け（RAM=0、vMMIO=1）と矛盾しない | §3.3.4 |

## 3. 現状のギャップ（pysim実装との差分）

- pysimの`system.py`は`FB_CONF_GUEST_RAM_SIZE=4096`、`FB_CONF_VSOC_PASSTHROUGH_BASE=0xF000_0000`を独自定義しているが、**`FB_CONF_VSOC_PASSTHROUGH_BASE`の値がsystem_config.mdの`0x4000_0000`と異なる**（pysimはFC=15のvMMIOアドレス自体、system_config.mdはその先のホスト実ペリフェラル物理アドレス — 意味が異なる可能性があるため要精査、単純な数値不一致ではないかもしれない）。
- CFG-01〜08すべて未検証（テストコードとして存在しない）。C++実装が存在しないため`static_assert`自体も検証できない。
- ロール間マトリクス(CFG-04)はpysimでは`ipc_router_concept.py`が直接保持しており、`system_config.md`の`FB_CONF_ROUTER_ROLE_MATRIX`との一致は目視確認のみ（自動検証なし）。

## 4. 未検証・スコープ外

- C++ `constexpr`/`static_assert`によるコンパイル時検証そのもの（Python実験では原理的に別形での検証が必要）。
