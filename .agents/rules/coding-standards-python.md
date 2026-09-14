---
name: coding-standards-python
description: Python コーディング標準（pysim、コンセプトコード、形式検証、テストコード共通: typing.Any 完全禁止、Gotchas同期、変異検査義務化）
globs: ["experiments/**", "docs/**/concepts/**", "docs/**/formal/**", "tools/**", "tests/**/*.py"]
scope: GLOBAL
---

# Fireball Python コーディング標準 (Python Coding Standards)

本ドキュメントは、Fireball プロジェクトにおける参照シミュレータ（`experiments/pysim`）、コンセプトコード（`docs/**/concepts/`）、形式検証モデル（`docs/**/formal/`）、およびテストコードの Python 実装規約を定義する。

- `docs/**/concepts/` のコンセプトコードでは、Python 標準の `dict` / `set` / `list` を使用してよい。
- `experiments/pysim/` には専用の `pysim-review` 規約を適用し、素の `dict` / `set` / `list` を禁止する。リテラル、`dict()` / `set()` / `list()`、型注釈、および内包表記も対象とし、`ReadOnly*Storage`、`Mutable*Storage`、`StaticVector`、`RingBuffer` 等のシステムコンテナを使用する。両者の規約を混同しない。
- 例外として、`spec-integrator.yaml` の `builtin_container_exclude_paths` に指定したシステムコンテナ実装内部、テスト、シナリオ、ベンチマークでは、組み込み `dict` / `set` / `list` を使用してよい。製品コードには適用しない。
- ストレージの所有権は常に単一インスタンスに限定する。所有側は実体ストレージだけを保持し、検索用のviewを二重に保持しない。別インスタンスが所有ストレージを検索する場合だけ、借用viewを渡す。

## 0.1 検索コンテナの選択規約

- 読み取り回数とエントリ数が大きい表では、全域二分探索によるキャッシュ汚染を避けるため `RadixBinaryTreeView` を使い、基数表で探索範囲を先に絞る。
- エントリ数が小さい表は基数表を持たず、ソート済みの `FlatMapView` を使う。小さい表へRadix索引を追加してはならない。
- アクセスに局所性がある固定表は、キーをXORで折りたたんだダイレクトマップキャッシュを使う。32ビットキーを4ビットへ折りたたむ場合は、16、8、4ビット右シフトとのXORを順に適用してから `& 0xF` とする。エントリ数に合わせてキャッシュ容量を設定し、キーの単純な下位ビットだけを使わない。

## 1.1 pysim の即時失敗とアサーション規約

- `experiments/pysim/` は参照シミュレータであり、不変条件・事前条件・事後条件・型契約に違反した場合は `assert` で直ちに停止させる。pysim で不具合を握りつぶしたり、暗黙のフォールバックで処理を継続したりしてはならない。
- pysim の製品コードでは明示的な `raise` を禁止する。`try` / `except` による境界での捕捉は許可するが、エラーは `Result` 等の戻り値へ集約し、事前条件・不変条件違反は `assert` で停止させる。
- `isinstance`、`type`、`hasattr`、`getattr`、`setattr` および型・リフレクション用の特殊属性を使用しない。実行時型判定ではなく、静的な型・専用メソッド・明示的な状態を使う。
- `in` / `not in` 演算子を製品コードで使用しない。線形探索を隠蔽するため、固定キーは分岐またはインデックス、テーブルは専用ビューの検索メソッドを使う。
- `pytest` / `unittest` / `mock`、`test_*` / `_test_*` の製品シンボル、テスト専用フックやバックドアを `experiments/pysim` の製品コードへ持ち込まない。テスト専用セットアップは `experiments/pysim/tests` 側に置く。
- テストは戻り値や件数だけでなく、仕様が要求する状態・副作用・境界条件を `assert` で検証する。`print`、非空判定、固定件数の確認だけを成功根拠にしてはならない。
- 失敗を期待するテストでは、対象処理を `try` ブロック内で実行し、対象が送出する具体的な例外だけを捕捉する。テスト自身が失敗用に送出した `AssertionError` を同じ `except` で捕捉して成功扱いにする偽陽性パターンを禁止する。
- 仕様上スキップが許可された外部依存の `ImportError` を除き、広すぎる `except Exception` や検証失敗を無条件に無視する例外処理を禁止する。

## 1.2 pysim のimport依存方向規約

- `experiments/pysim/` の製品コードは、`docs/architecture/document_structure.md` のTier依存方向に従う。上位Tier（小さいTier番号）は下位Tierの実装モジュールをimportしてはならず、下位Tierから上位Tierの契約・インターフェースを参照する。
- Interpreter、WASMタスクアダプタ、テスト用ハーネスなど特定ランタイムに固有の処理をCOOS・IPC RouterなどTier 1の汎用コンポーネントへ持ち込んではならない。必要なアダプタはランタイムまたはテスト側に置く。
- bare importが`sys.path`の順序で別Tierの同名モジュールへ解決される構成を禁止する。各ローカルimportは所属Tierと実ファイルが一意に確認できなければならない。
- レビュー時は `powershell tools/check-src.ps1 -group pysim` を実行し、`spec-integrator.yaml` の `pysim_imports` 設定に基づく違反を0件にする。テスト・シナリオ固有の補助importは製品コードの依存グラフへ混入させない。

## 1. 型安全性と `Any` 完全禁止規約 (Strict Type Safety)

- **`typing.Any` / `object` の完全禁止 (アンチパターン I の防止)**:
  - 静的型解析の形骸化を防止するため、コードベース全体で `typing.Any` の使用を **0 件（完全禁止）** とする。
  - pysimの関数引数、戻り値、構造体フィールドには、必ず**具体的なクラス名**または**具象型**（`int`, `bytes`, `str` 等）を明示し、`object` と `typing.Any` を使用しない。
  - pysimの型注釈では `T | None` / `Optional[T]` 以外のUnionを禁止する。異なる非None型のUnionは、境界の型を曖昧にするため使用しない。
  - 成功・失敗を表現する代数的データ型（`Result` 等）では、エラーなしを `Result[T, Never]`、値なしを `Result[Never, E]` として `typing.Never` を活用し、`Any` を一切使わずに静的型検査を満たす。

---

## 2. Gotchas & Invariants（実装の勘所・不変条件）の同期義務

- **docstring およびコメントへの明記**:
  - シミュレーションやテストから得られた「実装の勘所（Gotchas）」および「システム不変条件（Invariants）」は、関連するクラスや関数の docstring / コメントに固有識別子（例: `GOTCHA-COOS-01`, `GOTCHA-LOAD-02` 等）と共に設計理由を明記する。
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
