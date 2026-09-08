# Tier再構成 実行プロンプト（メモリマネージャ / HAL / WASI 再配置）

このドキュメントは、Fireball のドキュメント体系を「クリーンアーキテクチャと準同型」にするための Tier 再構成作業を実行するためのプロンプトである。
`docs/architecture/document_structure.md` の改訂を起点とし、影響する全ドキュメント・スキル・ツールへ機械的に波及させる。

作業前に必ず [`document_structure.md`](docs/architecture/document_structure.md)、[`keyword_dictionary.md`](docs/architecture/keyword_dictionary.md)、[`.agents/skills/component-review/`](.agents/skills/component-review/) を読むこと。

---

## 1. 背景・目的

仕様の修正漏れ（追随漏れ）を防ぐため、Tier構造を「決定複雑度に基づく分解深度」という単一軸だけでなく、**契約（Interface / What）と実装（Implementation / How）を明示的に別Tierへ分離する**、クリーンアーキテクチャの依存方向規則と準同型な構造にする。

対象は以下3コンポーネント。いずれも「抽象契約を上位Tier、物理実装を下位Tierへ分割する」パターンで再配置する。

---

## 2. 最終Tierマップ（確定）

| コンポーネント | Before | After | 備考 |
| :--- | :--- | :--- | :--- |
| メモリマネージャ・抽象契約（`co_mem`：パーティション貸与ポリシー、独立ヒープ不変条件） | Tier3 (`platform_memory.md` 内) | **Tier1（新規ファイル）** | `{GLOBAL_IndependentHeap}` `{GLOBAL_Policy_Memory}` `{GLOBAL_StrictMemoryLimit}` はこの新ファイルが対象コンポーネントになる |
| メモリマネージャ・実装（`shm_allocator` + `system_allocator`、dlmalloc アリーナ） | Tier3 (`platform_memory.md`) | **Tier2（新規ファイル、`tier2_runtime/`）** | `system_allocator` も含めてTier2へ統合（vMMIOと無関係な部分も含む、ユーザー確認済み） |
| HAL・抽象化層（URI Resolver、トランスポート抽象、`{IPCRouter}` 経由のデバイス仲介） | Tier3 (`platform_hal.md`) | **Tier2（新規ファイル、`tier2_runtime/`）** | |
| HAL・ドライバ実装（UART/SEGGER RTT 物理レジスタ操作、RSPエンコード/デコード） | Tier3 (`platform_hal.md`) | **Tier3のまま（`platform_hal.md` を縮小して残置）** | |
| WASI 0.3p 契約（Fireball固有部分を除く） | Tier1 (`interface_wit.md`) | **Tier3（`docs/specs/` へ抽出・新規ファイル）** | ユーザー確認済み：WASI全体をTier再編対象にする |
| Fireball固有WIT機構（`fireball_call` マッピング、IPC宛先URI命名規則） | Tier1 (`interface_wit.md`) | **Tier1のまま（`interface_wit.md` を縮小して残置）** | |
| WASI Preview1 ABI | Specs（Tier横断・ラベルなし） | **Specs のまま + Tierラベル `Tier3` を付与** | ディレクトリ移動はしない（`docs/specs/` に残置） |
| ロガー（`system_logging.md`） | Tier1 | **変更なし** | 当初案の「IPC経由=Tier2」基準は撤回。Loggerは元々IPC経由だが Tier1 のまま |

### 却下した基準

「IPC経由で通信する場合はTier2」という基準は **採用しない**。IPCルータは Tier1（Logger, os_coos, ipc_router）〜Tier3（HAL）まで全Tierが利用する通信バックボーンであり、通信手段では Tier を判別できないことが判明したため（[`system_logging.md:10`](docs/components/tier1_core/system_logging.md:10)、[`platform_hal.md:13`](docs/components/tier3_platform/platform_hal.md:13) 参照）。

---

## 3. 作業タスク一覧

### 3.1 ルール正本の改訂

- [ ] **`docs/architecture/document_structure.md`**
  - §1 の Tier 図・§1.1 のディレクトリ表を上記マップに更新
  - §2.1 デコンポジション基準に「契約（上位Tier）/ 実装（下位Tier）分割パターン」を明文化する新項目を追加（既存の「自己完結して書けるか」基準とは独立した、意図的な分割ルールとして記述）
  - §2.2 依存方向のルールに、契約側ファイルと実装側ファイルの参照関係（実装側は契約側を `<!-- evidence: -->` または traceability で参照すること）を追記
  - 新設ファイルのパスを §1.1 の表に追加

- [ ] **`docs/architecture/keyword_dictionary.md`**
  - `{GLOBAL_IndependentHeap}` `{GLOBAL_Policy_Memory}` `{GLOBAL_StrictMemoryLimit}` の「対象コンポーネント」列を新ファイル名に更新（分割後、抽象契約側のキーワードは新Tier1ファイル、実装詳細のキーワードは新Tier2ファイルに向くよう再分配）
  - `{IPCRouter}` `{URIAbstraction}` 等 HAL 関連キーワードの対象コンポーネントを新Tier2ファイルに更新（ドライバ固有のものは `platform_hal.md` に残す）

- [ ] **上位/下位キーワード表記規約の追加**（`document_structure.md` §3 または §4 に新設）
  - 定義元コンポーネント（上位Tier）はキーワードをセクション見出しに明示する
  - 参照側コンポーネント（下位Tier）は本文インラインで `` `{Keyword}` `` として引用する
  - 既存の慣行（定義側も参照側も本文インライン）からの変更となるため、`FORMAT.md` および `spec-integrator` のキーワード抽出ロジック（後述 3.3）が見出し形式のキーワードも正しく解釈できるか確認する

### 3.2 コンポーネントドキュメントの分割・新規作成

- [ ] **メモリマネージャの分割**
  - 新規: Tier1 抽象契約ファイル（例: `docs/components/tier1_core/system_memory.md`）— `co_mem` インターフェース、パーティション貸与ポリシー、独立ヒープ不変条件を `platform_memory.md` から移設
  - 新規: Tier2 実装ファイル（例: `docs/components/tier2_runtime/runtime_memory.md`）— `system_allocator` / `shm_allocator`（dlmalloc アリーナ、MPU Region 6 管理）を移設
  - `platform_memory.md`（Tier3）は空になるため削除、または「Tier3配置は廃止」の移行メモを残して整理
  - 付随ファイルの移動: `concepts/platform_memory_concept.py`, `tests/platform_memory_test_spec.md`, `formal/`（`jit_cache_model.py` への evidence リンクは要再確認）を新配置に合わせて分割・移動
  - **`docs/components/tier2_runtime/runtime_vmmio.md` の埋め込み表記修正**: L110「SHM（共有メモリ）— Tier 3」、L143「Tier 3 (SHM / Passthrough) PTE」、L152「Tier 3 共有メモリマネージャ」を新Tier2表記に更新。あわせて DIP（依存性逆転）の説明文がまだ成立するか再検証する（vMMIOと同一Tierになるため、契約定義主体の記述を見直す）

- [ ] **HAL の分割**
  - `platform_hal.md` から URI Resolver・トランスポート抽象部分を新規 Tier2 ファイル（例: `docs/components/tier2_runtime/runtime_hal.md`）へ抽出
  - `platform_hal.md`（Tier3）はドライバ実装（UART/SEGGER RTT 物理層、RSPパケット処理）のみに縮小
  - `tests/platform_hal_test_spec.md` を Tier2/Tier3 の分割に合わせて分割 or 参照関係を整理

- [ ] **interface_wit.md の分割**
  - WASI 0.3p / 0.1p 準拠部分（GPIO/タイマー/バス/ストリームのリソース定義、Preview1互換ラッパーの記述）を `docs/specs/` の新規ファイル（例: `docs/specs/wasi_preview03p_component_model.md`、Tierラベル Tier3）へ抽出
  - Fireball固有部分（`fireball_call` マッピング、IPC宛先URI階層命名規則）を `interface_wit.md`（Tier1）に残置
  - `docs/components/tier1_interface/wit/fireball.wit` との対応関係を再確認（WIT定義ファイル自体は分割しないが、ドキュメント側の参照先が変わる）
  - `tests/interface_wit_test_spec.md` の参照先更新

- [ ] **`docs/specs/wasi_preview1_abi.md`**
  - 冒頭にアーキテクチャ分類セクションを追加し `Tier3` ラベルを明示（ディレクトリ移動なし）

### 3.3 スキルの改訂

- [ ] **`.agents/skills/component-review/references/evaluation_rubric.md`**
  - §2 層間一貫性表に新規項目を追加:
    - **CL-06: Tier間波及チェック**「上位Tierのコンポーネントが更新された場合、それを参照する下位Tierコンポーネントの記述（キーワード引用・定数・インターフェース説明）が追随更新されているか」判定基準: 上位Tier更新日時 > 下位Tier該当箇所の更新日時であれば要確認フラグ
    - **CL-07: キーワード定義/参照配置**「定義元コンポーネント（上位Tier）でキーワードがセクション見出しに明示され、参照側コンポーネント（下位Tier）で本文インライン引用になっているか」判定基準: 定義元が見出しでない、または参照側が見出しになっている場合は指摘
  - CL-05（クリーンアーキテクチャ依存規則）の説明に「契約/実装分割ペア（Tier1契約 ↔ Tier2/3実装）」の具体例（メモリマネージャ、HAL）を追記

- [ ] **`.agents/skills/component-review/SKILL.md`**
  - サブエージェント1（`spec-formal-reviewer`）の検証項目に、CL-06/CL-07 相当のチェック観点を追加

- [ ] **`.agents/skills/architecture-review/references/architecture_review_rubric.md`**
  - Tier1〜3 コンポーネント一覧の記述を新マップに更新（メモリマネージャ・HAL・WASIの配置変更を反映）

### 3.4 ツールの改訂

- [ ] **`tools/spec-integrator/`** 配下で Tier ディレクトリ・階層順序をハードコードしている箇所を洗い出して更新（`config.py`, `models.py`, `anti_sabotage/checks/fmt_hierarchy.py`, `anti_sabotage/checks/fmt_traceability.py` 等が候補。実際の参照箇所は着手時に `grep -rn "tier1\|tier2\|tier3"` で再確認すること）
  - Hierarchy Gate が新ファイルパス（`system_memory.md`, `runtime_memory.md`, `runtime_hal.md`, `docs/specs/wasi_preview03p_component_model.md`）を正しい Tier として認識するか
  - キーワード定義/参照の見出し配置ルール（3.1 参照）を静的チェックが解釈できるか。解釈できない場合はパーサ拡張が必要
- [ ] `tools/README.md` の検証入口説明に変更があれば追記

### 3.5 波及確認（Traceability）

- [ ] `{GLOBAL_IndependentHeap}` 等、移動したキーワードを参照している全ドキュメント（`system_containers.md`, `os_scheduler.md`, `os_coos.md`, `ipc_router.md`, `runtime_vsoc.md`, `runtime_vmmio.md`）の参照先パスが新配置と整合しているか確認
  - `docs/specs/wasi_preview1_abi.md`｜`docs/components/tier1_interface/interface_wit.md`｜`docs/components/tier2_runtime/runtime_vsoc.md`｜`docs/components/tier3_platform/platform_hal.md` 間の WASI 関連相互参照を新配置に合わせて更新

---

## 4. 推奨実行順序

1. `document_structure.md` 改訂（正本を先に確定）
2. `keyword_dictionary.md` 改訂
3. メモリマネージャ分割 → `runtime_vmmio.md` の埋め込み表記修正
4. HAL 分割
5. interface_wit.md 分割 + `wasi_preview1_abi.md` へのラベル付与
6. component-review / architecture-review スキル改訂
7. `tools/spec-integrator` 改修
8. 全体検証（§5）

各ステップ完了後、影響ファイルに対して `powershell tools/check-doc.ps1` を実行し、8大品質ゲートの新規違反がないか確認しながら進めること。

---

## 5. 完了条件・検証コマンド

```powershell
powershell tools/format-doc.ps1
powershell tools/check-doc.ps1
powershell tools/build.ps1
```

- Hierarchy Gate / Traceability Gate が新Tier配置に対して 0 件エラーで通過すること
- 移動・分割した全キーワードについて `keyword_dictionary.md` の定義元正本と実ファイルパスが一致すること
- `runtime_vmmio.md` 内の旧「Tier 3 共有メモリマネージャ」表記が残存していないこと
- `interface_wit.md` の分割後、Tier1側にWASI標準定義が誤って残存していないこと（Fireball固有部分のみであること）
