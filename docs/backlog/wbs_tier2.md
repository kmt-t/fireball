# WBS: 階層設計 Tier 2 VDD開発プロセス構造化

本ドキュメントは、Fireball アーキテクチャの論理コア（Tier 1）およびサブシステム層（Tier 2）の開発タスクを、**VDD (Verification Driven Development)** のプロセスに基づいて階層化したものである。

## 1. VDD 標準開発フェーズ (Standard Phases)

各コンポーネントの開発は以下のフェーズを経て完了とする。

1. **[Step 0] 盆栽デザイン (Req / NL / SysML)**: 要件の抽出、自然言語による設計、および SysML（静的・動的・パラメトリック）モデル化。
2. **[Step 1-2] 形式検証 (Formal Verification)**: WIT によるインターフェイス定義、および TLA+/Apalache による論理不変条件の証明。
3. **[Step 3-4] 実装検証 (Test & Integration)**: 形式仕様からのテスト導出と実行、およびターゲット環境での統合。

---

## 2. コンポーネント別タスク構造化

### 2.1 [Tier 1] COOS カーネル & IPC 基礎

- **COOS カーネル・スケジューラ**
  - [ ] [Step 1] C++23 コルーチン・スケジューラの WIT/TLA+ 定義
  - [ ] [Step 2] タスク状態遷移、割り込み通知モデルの形式検証
  - [ ] [Step 3] 実装生成、**契約ベースのテストケース導出**
  - [ ] [Step 4] **仕様-実装整合テストの実行**、性能測定
- **IPC ルータ・共有メモリ**
  - [ ] [Step 1] URI ベースの名前解決、所有権移譲モデルの WIT 定義
  - [ ] [Step 2] Handoff 後の所有権不変条件の TLA+ 検証
  - [ ] [Step 3] 実装生成、**所有権遷移シナリオテストの導出**
  - [ ] [Step 4] **セキュリティドメイン隔離テストの実行**

### 2.2 [Tier 2] vSoC ランタイム・サブシステム

- **WASM Loader**
  - [ ] [Step 1] ゼロコピー索引化、バンプアロケータの WIT 契約定義
  - [ ] [Step 2] ロールバック時のメモリ一貫性検証
  - [ ] [Step 3] 実装生成、**パース境界条件テストの導出**
  - [ ] [Step 4] **不正 WASM バイナリ拒絶テストの実行**
- **JIT & Interpreter (vSoC Engine)**
  - [ ] [Step 1] Copy-and-Patch 命令テンプレート、ホットスポット判定の WIT 定義
  - [ ] [Step 2] JIT キャッシュ（Active/Old）整合性の形式検証
  - [ ] [Step 3] 実装生成、**命令エンコーディング整合テストの導出**
  - [ ] [Step 4] **CoreMark-PRO 等による実行正確性テストの実行**
- **vMMIO & 仮想周辺機器**
  - [ ] [Step 1] 統一アクセスモデル、vMMIO 許可テーブルの WIT 仕様
  - [ ] [Step 2] 許可テーブルによる境界チェック（Secure Gate）の検証
  - [ ] [Step 3] 実装生成、**アクセス権限違反トラップテストの導出**
  - [ ] [Step 4] **物理デバイス透過アクセス整合テストの実行**

### 2.3 [Tier 2] システム・デバッグ

- **システムロギング & デバッグ基盤**
  - [ ] [Step 1] Buffered Logging, GDB RSP コマンドの WIT 定義
  - [ ] [Step 2] RSP トランスポート層のブロッキング回避検証
  - [ ] [Step 3] ログ辞書生成、非介入型「覗き窓」インターフェイスの実装
  - [ ] [Step 4] VSCode(GDB) による実機デバッグ・ロギングのドミネーション

---

## 3. DoD (完了定義)

各コンポーネントの [Step 4] 完了には以下のエビデンスを必須とする。
- **Formal Spec**: `/inc/core/*.hxx` (WIT からの自動生成ヘッダ)
- **Proof**: `apalache_report.md` (Apalache 検証結果の要約)
- **Compliance**: `compliance_report.md` (命名規則、禁止パターン、SLOC 予算の遵守証明)
