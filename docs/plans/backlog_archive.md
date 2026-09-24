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
- [x] **HAL＝WASI 0.3p統合**: per-device WITリソース（trigger/timer/bus/streaming/console）を廃止した。URI解決、HALバッファプール、IPCコマンドIDを使う汎用機構に一本化した。公開契約を `fireball_hostcall_contract.wit`（hostcall）と `fireball_hal_contract.wit`（HAL型・Resolver）に分割し、[`interface_wit.md`](docs/components/tier3_platform/interface_wit.md) を全面改訂した。
- [x] **IPCルータのHALデバイスインスタンス別ロール化**: 「1チャネル1待機者」の制約下で、同種のHALインスタンスを区別できるようにした。共有 HAL ロールを、各デバイス／サービス向けの `HAL_UART`、`HAL_STDOUT`、`HAL_GPIO`、`HAL_TIMER`、`HAL_I2C`、`HAL_SPI` に分割した。`FB_CONF_ROUTER_ROLE_MATRIX` を4x4から9x9へ拡張し、`hal_task` を「1タスク=1ドライバインスタンス」に変更した。pysim と `system_config.md`、`ipc_router.md`、`hal_dispatch.md`、`platform_driver.md` を同期した。
  当初は `fireball://hal/gpio/0` 用の `HAL_GPIO_LEGACY` ロールも追加したが、不要と判断して削除した。`fireball://device/gpio/0` の `HAL_GPIO` ロールへ統一した。
- [x] **サービス/サブシステム用語の復元**: WASM 上の常駐タスクを「サービス」と呼ぶ。HAL や Logging のようなネイティブ常駐インフラは「サブシステム」と呼ぶ。この規約（`META_ServiceIsWasmResident`）を `architecture_overview.md` と `document_structure.md` に明記した。
  IPC ルータのWIT定義ファイル名にあった「サブシステム」の誤用も是正した。ファイル名を [`ipc_router_contract.wit`](docs/components/tier1_interface/wit/ipc_router_contract.wit) に変更した。
- [x] **`docs/specs/` の Tier分類**: `spec-integrator.yaml` に `tier: "meta"` エントリを追加し `docs/specs/**` を階層ゲートの逆依存チェック対象外（メタ）として分類
- [x] **component-review / architecture-review スキル改訂**: CL-06（Tier間波及チェック）・CL-07（キーワード定義/参照配置）を評価基準に追加、`architecture-review` の対象ファイルリストのスクリプト内ハードコードパスを更新

---

## Phase 0: Quality Gate & Early Validation [DONE items archived]

- [x] **Step 0: 仕様策定・動的図解・ルール体系刷新**: 全13コンポーネント設計書の静的・動的ペアリング、複雑な動的アルゴリズムへのシーケンス図／アクティビティ図配備、`.agents/rules/`の4コア体系への再編とClaude Code互換frontmatter付与
- [x] **Step 1: コンセプトコード・初期テスト仕様・形式検証**: 全16コンセプトコードの仕様同期と`typing.Any`排除、全16形式検証モデルのCTL証明および`guards=False`変異検査、全22テストスイートのPASS
- [x] **READYキューの計算量是正**: `BoundedReadyQueue`をタスク内リンクによる侵入型循環リストとし、末尾／先頭追加、先頭取り出し、任意タスクdetachをO(1)化した。FIFO順・リンク整合性・容量境界を検証した。CSPハンドオフ経路の線形検索は対象外として記録した
- [x] **`Blocked`タスクの外部終了（`task_killed`）**: CSP/selectと割り込みの待機登録を解除し、終了タスクが再起床しないこと、未登録ID・終了済みタスクへの再要求、実行中タスク終了の拒否を検証した
- [x] **WASM関数戻り値の共有オペランド領域化と復帰境界**: JITはC/AAPCS戻り値レジスタを使わず`return`直前でトレースを終了し、共有オペランド領域へ戻り値を残す。AAPCS準拠エピローグからInterpreterのreturnハンドラへ戻し、ハンドラだけがRETURN sentinelを生成する。呼出し先の関数復帰、sentinel消費、トップレベル完了判定、およびi32/i64/f32/f64を検証した
- [x] **import／host callのInterpreter境界統一**: WASM内部callとimport／host callをInterpreter／RuntimeEngine境界で実行し、JIT専用stackやC戻り値経路を追加しないことを検証した
- [x] **RuntimeEngine同期境界のTrap伝播**: 確定済み`InterpreterCall.trap`を呼び出し側へfail-fastで伝え、trap時に`None`を正常結果として返さないことを整数除算ゼロの回帰テストで確認した
- [x] **JITキャッシュ物理配置と疎キー索引の確定**: キャッシュを4KBページ2枚の連続8KBとし、共通コード領域2KBとActive/Warm/Oldest各2KBへ分割した。JITエントリ数が少ないためRadix表を設けず、カード表／4スロットFolding XOR／ソート配列二分探索に統一した。PySim・コンセプト・仕様・ベンチマークを同期し、JIT関連単体テスト、JIT形式モデル、12統合シナリオを実行した。AR-05の昇格条件とchain抽象度は未解決として別管理する
