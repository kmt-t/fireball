# システムコンフィグ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`system_config.md`](docs/components/tier1_core/system_config.md)
参考実装: なし（本コンポーネントは静的定数の定義のみで、実行時ロジックを持たない）

初期シミュレーション構成の定数（`FB_CONF_*`）と、その内部整合性を検証する。これらの値からARMv8-Mの物理容量や実機適合性を判断しない。

## 2. テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-CFG-01 | メモリパーティション総和の一致 | 既定値 | 各`FB_CONF_*_HEAP_SIZE`等を合計 | `KERNEL(4096)+RUNTIME(2048)+SUBSYS(3072)+JIT_CACHE(8192)+INTERP_STACK(2048)+sum(TASK_HEAP_SIZES)(4096, 要素数=MAX_GUEST_VMS(1)) == MEMORY_POOL_SIZE(23552)` | static_assert |
| TEST-CFG-03 | Stage 1アドレス窓サイズの一致・配列長 | 既定値 | 比較 | `FB_CONF_GUEST_RAM_SIZE == FB_CONF_TASK_HEAP_SIZES[0]`（共に4096）、`FB_CONF_TASK_HEAP_SIZES.size() == FB_CONF_MAX_GUEST_VMS`。この窓サイズはWASMページ数設定から独立する | static_assert |
| TEST-CFG-04 | ロール間通信許可マトリクスの形状 | - | `FB_CONF_ROUTER_ROLE_MATRIX`を確認 | 10x10の`bool`表で、`ipc_router_concept.py`のrole_matrixと矛盾しない（RUNTIME→CORE_SERVICE/HAL_*(7ロール)=true、RUNTIME→DEBUGGER=false等） | - |
| TEST-CFG-05 | タスクID予約値の非衝突 | - | `FB_CONF_MAX_TASKS`(16) ≤ 254であることを確認 | `FB_TASK_ID_FLIGHT`(0xFF=255)と衝突しない | - |
| TEST-CFG-06 | x64参照シミュレーションのJIT領域区画 | cache=8192, page=4096, common=2048, bank=2048, buffers=3 | 総量と各区画の境界を照合 | シミュレーション領域は `8192 = 2*4096 = 2048 + 3*2048`。共通コード・Active・Warm・Oldestは重複せず連続し、共通コードはエビクション対象外。ARMv8-Mの物理領域には適用しない | `system_config.md`, `runtime_vsoc.md` |
| TEST-CFG-07 | リトライ回数・待機時間の単一情報源 | 複数コンポーネントがリトライを実装 | `FB_CONF_RETRY_BACKOFF_MS`(10ms)と上限3回の参照元を確認 | すべてのコンポーネントが`system_config.md`のこの1値のみを参照し、独自の待機時間・回数を定義していない | 「個別のコンポーネント文書で異なる待機時間・回数を独自に定義しないこと」 |
| TEST-CFG-08 | シミュレーション上のゲスト仮想アドレス領域 | - | RAM base (0x0)、vMMIO base (0x8000_0000)、PASSTHROUGH base (0xF000_0000)を確認 | RAMとvMMIOのBit31区分、およびFC=15の仮想PASSTHROUGH窓が一致する。物理デバイスへの対応付けとARMv8-MのアドレスはTBD | - |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- C++ `constexpr`/`static_assert`によるコンパイル時検証そのもの（Python実験では原理的に別形での検証が必要）。
- ARMv8-Mの物理RAM/ROM容量、実機メモリ配置、物理アドレス、JIT領域、保護方式と実機適合性はTBD。
