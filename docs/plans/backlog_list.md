# Fireball バックログ

現在進行中フェーズ（**Phase 0**）の具体的なタスクを管理する。完了済みタスクは `docs/plans/backlog_archive.md` を参照。全体の開発プロセスは `docs/plans/roadmap_phase.md` を参照。

「盆栽」のように、全体のバランスを見ながら設計の密度を少しずつ上げていく。

---

## Phase 0.76: SysML Alignment & Model Refinement 【完了】

既存の設計図をSysML準拠（静的・動的・パラメトリック）に修正し、論理的一貫性を高める。

- [x] **既存コンポーネントの図解修正**:
  - [x] **静的モデル (BDD)**: `architecture_overview.md` 等の構造図をSysMLブロック定義図形式に統一
  - [x] **動的モデル (SD/SMD)**: 主要シーケンスをSysMLシーケンス図/状態遷移図形式に修正
- [x] **パラメトリック図 (PAR) の導入**:
  - [x] 32KB RAM予算・15KLOC制約をConstraint Blockとして定義し、パラメトリック図でモデル化
- [x] **フリクション監査の再実行**: 図とドキュメント間の整合性を `audit_friction.py` で最終確認

---

## Phase 0.8: vSoC VDD Verification & Design Formalization 【進行中】

WBSの [Step 0] 盆栽デザイン（SysML）および [Step 1-2] 形式検証（WIT/TLA+）を中心とした、設計の「不変条件」の確立。

### [Tier 1] Core Logic Verification

- [ ] **COOS / IPC 協調モデル**:
  - [x] [Step 1-2] タスク状態遷移、割り込み通知、Handoffの形式検証 `{GLOBAL_UseCpp20Coroutine}` `{CSP_Handoff}`
  - [x] [Step 0] IPCルータの名前解決・所有権移譲のSysMLモデル化 → Complete (ipc_router.md 4.1.1, 4.2.1)
- [x] **IPCパニック・デッドロック回避**:
  - [x] [Step 1-2] In-flightパニック時のDropハンドラとメモリリーク防止の整合検証完了 `{IPC_ZeroCopy}` `{Challenge_CspHandoffStarvation}` `{IPC_DropHandler}`

### [Tier 2] vSoC Subsystem Verification

- [ ] **vSoC Engine (JIT/Intp) 一貫性**:
  - [x] [Step 0-2] JITキャッシュ (Active/Old) とデバッガ割り込み (Safepoint) の協調モデル → Design specification completed (runtime_vsoc.md 4.2.1)
- [x] **vMMIOセキュリティゲート (TLB)**:
  - [x] [Step 0-2] 3-Tier安全性、ソフトウェアTLBキャッシュ整合性の形式検証完了 `{UnifiedAccessModel}` `{RoleBasedAccessControl}` `{FastAddressCheck}`
- [x] **Loaderロールバック機構**:
  - [x] [Step 0-2] バンプアロケータの順序とパース失敗時の安全な巻き戻しの形式検証完了 `{ROMParsing}` `{META_BumpAllocator}` `{MultiModule_Support}`
- [ ] **リソース制約検証**:
  - [x] [Step 0] SysMLパラメトリック図によるRAM/SLOC予算の遵守検証 → Constraint relationship diagram added (resource_budget.md 4.1.1)

---

## Phase 0.9: Component Reference Implementation Survey 【待機中】

主要コンポーネントの参考実装を調査し、設計の定石との整合性を確認する。

- [ ] **[Interpreter]** 参考調査: WAMR, WASM3（命令ハンドラ最適化）
- [ ] **[JIT Compiler]** 参考調査: Cranelift, DynASM（JITアセンブラ）
- [ ] **[COOS Scheduler]** 参考調査: Zephyr Scheduler, FreeRTOS
- [ ] **[IPC Router]** 参考調査: Fiasco.OC, seL4（Capability IPC）
- [ ] **[Memory Manager]** 参考調査: Bare-metal bump allocators
- [ ] **[Platform HAL]** 参考調査: CMSIS-HAL, libopencm3

---

## 次フェーズの方向性（Phase 1 予告）

Phase 1（～2026年6月末）でスタンドアロンvSoCを実装する。詳細は `docs/plans/roadmap_phase.md` を参照。

- Story: [Low-Latency physical I/O]
- Story: [Secure & Zero-Copy Inter-Service Communication]
- Story: [Observability & Non-Intrusive Debugging]

---

## ステータス管理

- **Step 0**: 盆栽デザイン・SysML 完了
- **Step 1-2**: 形式検証（TLC）パス
- **Step 3-4**: 実装生成・テスト通過・ターゲット統合完了
