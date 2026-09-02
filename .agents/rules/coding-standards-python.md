# Fireball Python コーディング標準 (Python Coding Standards)

本ドキュメントは、Fireball プロジェクトにおける参照シミュレータ（`experiments/pysim`）、コンセプトコード（`docs/**/concepts/`）、形式検証モデル（`docs/**/formal/`）、およびテストコードの Python 実装規約を定義する。
globs: ["experiments/**", "docs/**/concepts/**", "docs/**/formal/**", "tools/**"]
scope: GLOBAL

## 1. 型安全性と `Any` 完全禁止規約 (Strict Type Safety)

- **`typing.Any` の完全禁止 (アンチパターン I の防止)**:
  - 静的型解析の形骸化を防止するため、コードベース全体で `typing.Any` の使用を **0 件（完全禁止）** とする。
  - 関数の引数、戻り値、構造体フィールドには、必ず**具体的なクラス名**、**具象型**（`int`, `bytes`, `str` 等）、または汎用コンテナであれば `object` を明示する。
  - 成功・失敗を表現する代数的データ型（`Result` 等）では、エラーなしを `Result[T, Never]`、値なしを `Result[Never, E]` として `typing.Never` を活用し、`Any` を一切使わずに静的型検査を満たす。

---

## 2. Gotchas & Invariants（実装の勘所・不変条件）の同期義務

- **docstring およびコメントへの明記**:
  - シミュレーションやテストから得られた「実装の勘所（Gotchas）」および「システム不変条件（Invariants）」は、関連するクラスや関数の docstring / コメントに固有識別子（例: `COOS-GOTCHA-01`, `LOAD-GOTCHA-02` 等）と共に設計理由を明記する。
- **三位一体の同期**:
  - 設計仕様書（Markdown）、シミュレータ/コンセプトコード（Python）、およびテストコード（テスト仕様書・ユニットテスト）の 3 者間で Gotchas 識別子と不変条件の記述を常に一致させる。
- **自然言語コメントと最新設計準拠 (アンチパターン J の防止)**:
  - コード内のコメントは最新の設計思想（3-Stage Routing, ADR_RendezvousChannel, Zero-Allocation, 3-Bank Cache, Symmetric Transfer 等）に準拠させ、古い内部用語（`(AoS)` 等）を混入させない。

---

## 3. 形式検証モデル規約 (`docs/**/formal/*.py`)

- **pyModelChecking の準拠**:
  - Kripke 構造による状態空間定義と、CTL（計算樹論理: `AG`, `AF`, `AX`, `EF`, `Imply`, `Not` 等）による論理式を記述する。
- **変異検査（guards=False）による反証性の担保**:
  - 単に正常系モデルで CTL 式が PASS することを確認するだけでなく、必ず `guards=False` 引数によるガード無効化経路を設け、仕様違反状態へ遷移した際に CTL 式が確実に FAIL すること（保護証明）を実装・検証する。

---

## 4. 外部依存と実行環境規約

- **Python バージョン**:
  - Python 3.11+ / 3.14+ 準拠。
  - パッケージ管理およびスクリプト実行は `uv` を標準とする。
- **外部任意依存の安全ガード**:
  - `unicorn` 等のプラットフォーム依存・任意インストールのツールは必ず `try-except ImportError` でガードする。
  - 非同梱環境において単体実行された場合は、エラーで異常終了するのではなく、クリーンにスキップ（SKIP）して終了コード 0 で復帰させる。
- **コードフォーマット & Lint**:
  - Ruff 規約に準拠し、コミット前にフォーマッタ・リンタを実行してクリーンな状態を維持する。
