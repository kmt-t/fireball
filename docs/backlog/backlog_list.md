# Fireball 統合バックログ

「盆栽」のように、全体のバランスを見ながら設計の密度を少しずつ上げていく。
エージェント向けの構造化インデックスは `.agent/brain/backlog.atc` を参照。

## Phase 0: Foundation (ビルド基盤・検証基盤の確立)

Phase 0では実装ではなく、ビルド基盤とvSoC形式検証を完了させる。`{SpecificationFirst}`

### Phase 0.7: Static DI & Build System [DONE]
- [x] **Harnessパターンの確定**: 全コンポーネントのハーネス設計
- [x] **静的DI機構**: テンプレート、マクロ、アロケータの連携方式
- [x] **WIT→C++自動生成（基本機能）**: コード生成スクリプトの基本実装
- [x] **Mesonビルドシステム**: 全ターゲット（ARM, RISC-V, x64 host）のビルド確認

### Phase 0.75: Constexpr Verification & Code Gen Enhancement [DONE]
- [x] **コード生成ツールのconstexpr対応**: WIT→C++生成時にconstexpr属性を付与
- [x] **constexprメソッド特定**: どのメソッドをconstexprにすべきか分類
- [x] **コンパイル時計算検証**: constexpr関数が実際にコンパイル時評価されるか確認
- [x] **ルックアップテーブル生成**: constexprによる静的テーブル生成の実証

### Phase 0.76: SysML Alignment & Model Refinement
既存の設計図を SysML 準拠（静的・動的・パラメトリック）に修正し、論理的一貫性を高める。

- [ ] **既存コンポーネントの図解修正**:
  - [ ] **静的モデル (BDD)**: `architecture_overview.md` 等の構造図を SysML ブロック定義図形式に統一。
  - [ ] **動的モデル (SD/SMD)**: 主要シーケンスを SysML シーケンス図/状態遷移図形式に修正。
- [ ] **パラメトリック図 (PAR) の導入**:
  - [ ] 32KB RAM 予算、および 15KLOC 制約を Constraint Block として定義し、パラメトリック図でモデル化。
- [ ] **フリクション監査の再実行**: 図とドキュメント間の整合性を `audit_friction.py` で最終確認。

### Phase 0.8: vSoC VDD Verification & Design Formalization
WBS の [Step 0] 盆栽デザイン（SysML）および [Step 1-2] 形式検証（WIT/TLA+）を中心とした、設計の「不変条件」の確立。

#### [Tier 1] Core Logic Verification
- [ ] **COOS / IPC 協調モデル**:
  - [x] [Step 1-2] タスク状態遷移、割り込み通知、Handoff の形式検証 `{UseCpp20Coroutine}` `{CSP_Handoff}`
  - [ ] [Step 0] IPC ルータの名前解決・所有権移譲の SysML モデル化
- [ ] **IPC パニック・デッドロック回避**:
  - [ ] [Step 1-2] In-flight パニック時の Drop ハンドラとメモリリーク防止の整合検証

#### [Tier 2] vSoC Subsystem Verification
- [ ] **vSoC Engine (JIT/Intp) 一貫性**:
  - [ ] [Step 0-2] JIT キャッシュ (Active/Old) とデバッガ割り込み (Safepoint) の協調モデル
- [ ] **vMMIO セキュリティゲート (TLB)**:
  - [ ] [Step 0-2] 3-Tier 安全性、ソフトウェア TLB キャッシュ整合性の形式検証 `{UnifiedAccessModel}`
- [ ] **Loader ロールバック機構**:
  - [ ] [Step 0-2] バンプアロケータの順序とパース失敗時の安全な巻き戻しの形式検証 `{ROMParsing}`
- [ ] **リソース制約検証**:
  - [ ] [Step 0] SysML パラメトリック図による RAM/SLOC 予算の遵守検証

### Phase 0.9: Component Reference Implementation Survey
主要コンポーネントの参考実装を調査し、設計の定石との整合性を確認する。

- [ ] **[Interpreter]** 参考調査: WAMR, WASM3 (命令ハンドラ最適化)
- [ ] **[JIT Compiler]** 参考調査: Cranelift, DynASM (JITアセンブラ)
- [ ] **[COOS Scheduler]** 参考調査: Zephyr Scheduler, FreeRTOS
- [ ] **[IPC Router]** 参考調査: Fiasco.OC, seL4 (Capability IPC)
- [ ] **[Memory Manager]** 参考調査: Bare-metal bump allocators
- [ ] **[Platform HAL]** 参考調査: CMSIS-HAL, libopencm3

---

## 1. 周辺設計の深化 (Phase 1: Deepening Design via Value Stories)
WBS の [Step 3-4] 自律導出（Codegen/Impl）および 実装検証（Test/Integration）を中心とした、価値の具現化。

- Story: [Low-Latency physical I/O]
- Story: [Secure & Zero-Copy Inter-Service Communication]
- Story: [Observability & Non-Intrusive Debugging]

---

## ステータス管理
- **Step 0**: 盆栽デザイン・SysML 完了
- **Step 1-2**: 形式検証（TLC）パス
- **Step 3-4**: 実装生成・テスト通過・ターゲット統合完了
