# アーキテクチャレビュー記録

<!-- evidence: architecture: docs/architecture/architecture_overview.md -->

## 1. 状態

最上位アーキテクチャと、現在の作業ツリーにある設計・形式モデル・pysim・QA変更をレビューした。ツール自身の Python 規約・テストは対象外とし、pysim は Fireball の規約に従って評価した。

| 項目 | 内容 |
| :--- | :--- |
| 状態 | レビュー完了。LLM Obligation Gate は誤判定10件を手動確認したが、保存済み判定を変更していないため FAIL のまま |
| 対象 | 現在の HEAD に対する全作業ツリー差分、[architecture_overview.md](docs/architecture/architecture_overview.md)、Tier 1〜3の設計・形式モデル・WIT・pysim・QA |
| 実行日 | 2026-09-29 |
| 構造レビュー | PASS |
| LLM監査 | OpenRouter / `jev`。日次上限 $2 の範囲内で実施 |

## 2. アーキテクチャ構造レビュー

| 評価軸 | 判定 | 根拠 |
| :--- | :--- | :--- |
| Tier 分類 | PASS | [`architecture_overview.md`](docs/architecture/architecture_overview.md) の Tier 表とコンポーネント一覧が `docs/components/tier1_core` から `tier3_plugins` までの6ディレクトリに対応する。 |
| 責務境界 | PASS | 概要書は各コンポーネントの分類に必要な説明と詳細仕様へのリンクを置き、状態・アルゴリズム・ABI 等を再掲しない。 |
| 依存方向 | PASS | 依存図の Tier 間の辺は Tier 3 から Tier 2、Tier 2 から Tier 1 の契約・基盤へ向かう。図に逆方向の Tier 依存および循環はない。 |
| WIT 契約配置 | PASS | [`document_structure.md`](docs/architecture/document_structure.md) の配置規則と各 Tier の `wit/` 配下を照合した。重複・曖昧な契約名はない。 |
| 一覧・図・リンク | PASS | 収集結果でリンク切れ、未掲載コンポーネント、未知のリンクは検出されていない。25コンポーネントのリンク先が存在する。 |

この構造レビュー対象で CRITICAL、MAJOR、MINOR の所見はない。個別コンポーネントのアルゴリズム・ABI・形式モデルの性質は、構造レビューの合否対象に含めない。

## 3. 現在の差分レビュー

### 3.1 規約

| 判定 | 所見と対応 |
| :--- | :--- |
| 解消 | 可変コンテナのインプレース更新に `{GOTCHA-CONT-04}` の識別がなかった。実装、設計、テスト仕様に勘所をそろえた。 |
| 解消 | タイマー待ちタスク終了後の最小期限再計算に GOTCHA がなかった。`{GOTCHA-SCHED-03}` を仕様・実装・QAで定義・参照した。 |
| 解消 | Radix tree の `find` と `find_matching` に下限探索処理が重複していた。共通処理へ抽出した。 |

### 3.2 仕様適合性

| 判定 | 所見と対応 |
| :--- | :--- |
| 解消 | READY キューが空でタイマー待ちがある場合、アイドルフックがタイマー期限まで遅れていた。アイドル区間の開始時に一度実行し、フックがタスクを起こせばスケジューラへ戻す実装とし、実行時刻を直接検証するテストを追加した。 |
| 解消 | FC=13 DYNAMIC の所有者検査、FC=14 SHM の所有者不一致と未登録ページの分類、未定義FCと未登録VPNのQA分類を仕様・形式モデル・実装・QAで一致させた。`guards=False` では SHM と DYNAMIC の所有者ガード除去を反証する。 |
| 解消 | `SYS_YIELD` が成功を返すだけだった経路を、次の安全な Runtime 境界で COOS へ制御を返す要求として実装し、別タスクへのハンドオフをQAで確認する。 |
| 解消 | Runtime 観測の正常終了・ゲストトラップ・ホスト失敗を区別し、選択された Observer に共通イベント列を配送する pysim 構成モデルと対応テストを追加した。Interpreter 内部計測および実機C++統合は未実装範囲として仕様に明記した。 |
| 解消 | Logger の内部診断 API と公開IPCサービスの説明が混在していた。内部型付きAPIに整理し、Runtimeイベントからのログ経路をQAに対応させた。 |
| 解消 | Runtime Plugin の検証表が未検証状態のままだった。pysim Composer で検証する構成選択と、実機C++生成物・Plugin lifecycle の未実装範囲を分けた。 |

## 4. 検証結果

| 検証 | 結果 |
| :--- | :--- |
| `./tools/check-src.sh -g cpp` | PASS、8ファイル、警告0件 |
| `./tools/check-src.sh -g concepts` | PASS、13ファイル、警告0件 |
| `./tools/check-src.sh -g formal` | PASS、21ファイル。形式モデルの `guards=False` 変異検査を含む |
| `./tools/check-src.sh -g pysim` | PASS、152ファイル、警告0件 |
| `uv run pytest experiments/pysim/qa` | PASS、358テスト。続くスケジューラ・コンテナ修正後に該当テスト26件を再実行しPASS |
| `./tools/format-doc.sh` | PASS、88文書を確認、変更0件 |
| `./tools/format-src.sh -g concepts` | PASS、13ファイル、変更0件 |
| `./tools/format-src.sh -g pysim` | PASS、152ファイル、変更0件 |
| `./tools/check-doc.sh` | 8つの文書ゲートと検証マトリクスはPASS。Obligation GateはLLM判定10件でFAIL |
| 検証マトリクス | PASS、25仕様、13概念コード、19形式モデル、24テスト仕様、12シナリオ。組合せ網羅完了 |
| `git diff --check` | PASS。`tools/spec-integrator` 内部のPython規約・テストはレビュー対象外 |

## 5. LLM監査と手動判定

### 5.1 実行量

| 監査 | 結果 |
| :--- | :--- |
| キーワードリスク評価 | 247件を全件評価。高リスク分類は繰り返し実行で変動するため、欠陥数には換算しない。評価エラー0件 |
| 文書レビュー | 23文書。PASS 0、WARN 21、FAIL 2 |
| キーワードリンクレビュー | 607組。PASS 75、WARN 526、FAIL 6 |
| API使用額 | 日次上限 $2 の範囲内 |

高リスク数は監査対象の分類数であり、欠陥数ではない。`jev` の判定結果には根拠本文・行位置・理由が付かないため、各FAILを正本本文とQAの具体的な記述で照合した。

### 5.2 FAILの照合

6件のリンクFAILは、いずれも対象QA仕様の本文またはtraceabilityメタデータにキーワードが存在する。

- `GOTCHA-LOG-01` は [`runtime_logging_test_spec.md`](docs/qa/tier2_runtime/runtime_logging_test_spec.md) の17、30、34行目、`GOTCHA-LOG-04` は26、27、30、37行目にある。`InterruptibleFlush` は同仕様30行目のtraceabilityにある。
- `GOTCHA-SCHED-01` は [`os_coos_test_spec.md`](docs/qa/tier1_core/os_coos_test_spec.md) の29行目のtraceabilityと39行目の `TEST-COOS-07` にある。
- `IPCRegistry` は [`ipc_router_test_spec.md`](docs/qa/tier1_interface/ipc_router_test_spec.md) の11行目と34行目にある。`RoleBasedAccessControl` は同仕様11、18、19、34行目にある。

2件の文書FAILも、参照切れという判定に対して本文にMarkdownリンクが存在する。

- [`system_config.md`](docs/components/tier1_core/system_config.md) の217行目は `config.py`、`system_config_model.py`、`system_config_test_spec.md` へのMarkdownリンクを持つ。
- [`system_memory.md`](docs/components/tier1_interface/system_memory.md) の93行目は `system_memory_contract.wit`、`system_config.md`、`runtime_memory.md` へのMarkdownリンクを持つ。

上記8件（リンク判定6件、文書判定2件）は本文照合上の誤判定と判断したが、LLMの保存済み判定はPASSへ書き換えていない。その結果、`check-doc.sh` のObligation Gateには10件のエラーが残る。監査履歴を保持したままゲートを緑にする方法は未実施であり、リポジトリ品質ゲート全体はPASSと報告しない。

### 5.3 WARNの扱い

高信頼度WARNのうち、`GOTCHA-COOS-03` はCOOS FIFOへ割込みイベントを追加し、協調境界でタスクを起こす実装・概念コード・QA記述を照合した。今回の確認では反証可能な仕様違反を特定しなかった。残るWARNを欠陥件数へ換算していない。

## 6. 保留・対象外

- ARMv8-Mの物理ABI、MPU/W^X、ROM/RAM予算、実機検証は対象ボード確定前の計画項目であり、合格として扱わない。
- Runtime観測とPlugin構成のC++生成物検証、Interpreter内部イベント、Plugin lifecycle は文書上の未実装範囲である。
- ツール自身のPython規約・テストは対象外とした。pysimはFireball独自ルールで検査した。
- ユーザーの指示に従ってキーワードの台帳ファイルは削除済みである。
