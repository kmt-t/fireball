# バックログアーカイブ

完了済みのバックログアイテムを記録する。参照目的のみ。

---

## Phase 0.7: Static DI & Build System [DONE]

- [x] **Harnessパターンの確定**: 全コンポーネントのハーネス設計
- [x] **静的DI機構**: テンプレート、マクロ、アロケータの連携方式
- [x] **WIT→C++自動生成（基本機能）**: コード生成スクリプトの基本実装
- [x] **CMakeビルドシステム**: 全ターゲット（ARM, RISC-V, x64 host）のビルド確認

---

## Phase 0.75: Constexpr Verification & Code Gen Enhancement [DONE]

- [x] **コード生成ツールのconstexpr対応**: WIT→C++生成時にconstexpr属性を付与
- [x] **constexprメソッド特定**: どのメソッドをconstexprにすべきか分類
- [x] **コンパイル時計算検証**: constexpr関数が実際にコンパイル時評価されるか確認
- [x] **ルックアップテーブル生成**: constexprによる静的テーブル生成の実証

---

## Phase 0.8: Tier再構成（契約/実装分割パターン導入） [DONE]

メモリマネージャ・HAL・WASIをクリーンアーキテクチャの依存方向規則と準同型な「契約（上位Tier）/実装（下位Tier）分割パターン」へ再配置した。実行プロンプトとして使われた plans 配下の一時ドキュメントは、全項目完了によりファイル自体が陳腐化（旧ファイル名への参照が spec-integrator の Evidence/Format ゲートを毎回失敗させる)したため削除し、要点のみ本アーカイブへ集約する。

- [x] **メモリマネージャ分割**: 旧・物理メモリ設計書（Tier3、単一ファイル）を [`system_memory.md`](docs/components/tier1_interface/system_memory.md)（Tier1 Interface契約）と [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md)（Tier2実装）に分割
- [x] **HAL分割**: 旧・HAL設計書（Tier3、単一ファイル）を [`hal_dispatch.md`](docs/components/tier2_runtime/hal_dispatch.md)（Tier2契約：URI Resolver・コマンドプロトコル）と [`platform_driver.md`](docs/components/tier3_platform/platform_driver.md)（Tier3実装：物理ドライバ）に分割
- [x] **HAL＝WASI 0.3p統合**: per-device WITリソース（trigger/timer/bus/streaming/console）を廃止し、URI解決 + HALバッファプール + IPCコマンドIDの汎用機構に一本化（[`interface_wit.md`](docs/components/tier1_interface/interface_wit.md) 全面改訂、`fireball.wit` を6インターフェースから3（types/resolver/trap）へ縮小）
- [x] **IPCルータのHALデバイスインスタンス別ロール化**: 「1チャネル1待機者」制約下で複数の同種HALインスタンスを区別するため、単一の共有 HAL ロールをデバイス/サービスインスタンスごとの専用ロール（`HAL_UART`/`HAL_STDOUT`/`HAL_GPIO`/`HAL_TIMER`/`HAL_I2C`/`HAL_SPI`）に分割し、`FB_CONF_ROUTER_ROLE_MATRIX` を4x4→9x9へ拡張。`hal_task` を「1タスク=1ドライバインスタンス」設計に変更（pysim・`system_config.md`・`ipc_router.md`・`hal_dispatch.md`・`platform_driver.md` を同期）。当初は重複GPIOエイリアスURI（`fireball://hal/gpio/0`）用に`HAL_GPIO_LEGACY`ロールも追加していたが不要と判断し削除、`fireball://device/gpio/0`の`HAL_GPIO`ロールへ統一
- [x] **サービス/サブシステム用語の復元**: WASMで実行される常駐タスクを「サービス」、HAL/Loggingのようなネイティブ常駐インフラを「サブシステム」と呼び分ける規約（`{META_ServiceIsWasmResident}`）を `architecture_overview.md` / `document_structure.md` に明文化し、IPCルータのWIT定義ファイル名の「サブシステム」誤用（[`ipc_router.wit`](docs/components/tier1_interface/wit/ipc_router.wit) へ改名）等を是正
- [x] **`docs/specs/` の Tier分類**: `spec-integrator.yaml` に `tier: "meta"` エントリを追加し `docs/specs/**` を階層ゲートの逆依存チェック対象外（メタ）として分類
- [x] **component-review / architecture-review スキル改訂**: CL-06（Tier間波及チェック）・CL-07（キーワード定義/参照配置）を評価基準に追加、`architecture-review` の対象ファイルリストのスクリプト内ハードコードパスを更新
