# Fireball ツール体系 & 検証パイプライン仕様書

Fireball Hypervisor の品質保証、コードフォーマット、静的トレーサビリティ、形式検証（pyModelChecking）、WIT インターフェース検証、Python シミュレータ実機相当テスト、および LLM as a Judge を実行する統合ツール体系です。

Windows（PowerShell）および Linux / WSL（Bash）の双方で完全透過に同一のコマンドライン操作が可能です。

### 前提パッケージのインストール (Prerequisites)
プロジェクトのルート直下にある [requirements.txt](../requirements.txt) を用いて、必要な Python モジュールを一括インストールします：
```bash
# uv を使用する場合 (推奨・高速):
uv pip install -r requirements.txt

# 通常の pip を使用する場合:
pip install -r requirements.txt
```

---

## 1. ツール・スクリプト一覧 (Tool Architecture)

Fireball のツール体系は以下の 9 つの標準コマンド群で構成されています。ドキュメント検証とソースコード検証は完全に分離されており、対象ファイルやグループ（C++, Pythonサブグループ）を明示指定して実行可能です。
Windows（PowerShell）および Linux / WSL（Bash）の双方で同一の操作が可能です。

| コマンド | スクリプト (Windows / Linux) | 種別 | 主な役割 |
| :--- | :--- | :---: | :--- |
| **build** | `tools/build.ps1`<br>`tools/build.sh` | DB構築 | ドキュメントからデータベース（DocGraph, トポロジー）を構築し、TF-IDFによるキーワード・用語リストを作成。引数で対象Markdown指定可能。 |
| **format-doc** | `tools/format-doc.ps1`<br>`tools/format-doc.sh` | 静的整形 | Markdown ドキュメントの静的正規化（改行・末尾空白等）を適用。引数で対象Markdown指定可能。 |
| **check-doc** | `tools/check-doc.ps1`<br>`tools/check-doc.sh` | 静的検査 | ドキュメントの静的検証（8大品質ゲート: Format, Traceability, Hierarchy, Formal, WIT, Evidence, Obligation, Consistency）を実行。引数で対象Markdown指定可能。 |
| **format-src** | `tools/format-src.ps1`<br>`tools/format-src.sh` | 静的整形 | ソースコードの静的フォーマッタ（Python: Ruff / C++: clang-format）を適用。`-group`（`cpp`, `python`, `concepts`, `formal`, `pysim`, `all`）や個別ファイル指定可能。 |
| **check-src** | `tools/check-src.ps1`<br>`tools/check-src.sh` | 静的検査 | ソースコードの静的規約・サボり検証（Anti-Sabotage: TODO放置、空関数、typing.Any完全禁止、C++構文制限、形式モデル変異検査 `guards=False` 必須、実機テスト実行）を実行。`-group` や個別ファイル指定可能。 |
| **risk** | `tools/risk.ps1`<br>`tools/risk.sh` | LLM評価 | LLMによりドキュメントのキーワードの設計複雑度・リスク評価を行う。 |
| **llm-word** | `tools/llm-word.ps1`<br>`tools/llm-word.sh` | LLM検査 | LLMにより単語揺れチェックを行う（エンベディング類似度 + 文脈判定 + レポート出力）。 |
| **llm-single-review** | `tools/llm-single-review.ps1`<br>`tools/llm-single-review.sh` | LLM監査 | 指定されたファイルまたは全ファイルの全セクション単体、およびファイルに含まれる高リスクキーワードのリンクの島に関連するレビューを行う。 |
| **llm-keyword-review** | `tools/llm-keyword-review.ps1`<br>`tools/llm-keyword-review.sh` | LLM監査 | リスクの高いキーワードのリンクの島に関連するレビューを行う。 |

---

## 2. 目的別クイックスタート (Workflow by Purpose)

| 目的 | コマンド（Windows / Linux） | コスト・所要時間 |
| :--- | :--- | :--- |
| **ドキュメントDB・用語インデックス作成** | `powershell tools/build.ps1`<br>`./tools/build.sh` | 0円 / 1〜2秒 |
| **ドキュメント自動フォーマット** | `powershell tools/format-doc.ps1 [files...]`<br>`./tools/format-doc.sh [files...]` | 0円 / 1秒 |
| **ドキュメント静的品質ゲート検証** | `powershell tools/check-doc.ps1 [files...]`<br>`./tools/check-doc.sh [files...]` | 0円 / 5〜10秒 |
| **ソースコード自動フォーマット** | `powershell tools/format-src.ps1 -group <group> [files...]`<br>`./tools/format-src.sh -g <group> [files...]` | 0円 / 1秒 |
| **ソースコード品質・サボり検査** | `powershell tools/check-src.ps1 -group <group> [files...]`<br>`./tools/check-src.sh -g <group> [files...]` | 0円 / 2〜5秒 |
| **用語表記揺れの確認** | `powershell tools/llm-word.ps1 -quick`（静的のみ）<br>`powershell tools/llm-word.ps1`（LLM判定込み） | 0円（quick） / 課金 |
| **キーワードリスク評価 (マイルストーン時)** | `powershell tools/risk.ps1` | 課金 / 30秒〜1分 |
| **単体ドキュメントのレビュー** | `powershell tools/llm-single-review.ps1 -file docs/components/tier1_core/os_scheduler.md` | 課金 |
| **高リスクキーワードの島レビュー** | `powershell tools/llm-keyword-review.ps1` | 課金 |

---

## 3. 品質ゲート詳細 (8 Quality Gates via `check-doc`)

`tools/check-doc.ps1` / `tools/check-doc.sh` が検証する 8 つのドキュメント品質ゲートです（1 件でも違反があれば終了コード 1 で失敗）。

| ゲート名 | ルール | 検査内容 |
| :--- | :--- | :--- |
| **1. Format Gate** | `FMT-*` | Markdown 内部リンク切れ、見出しアンカー切れ、Mermaid構文、**レーベンシュタイン距離による静的タイポ・表記揺れ（`FMT-LEVENSHTEIN-TYPO`）** の検出。 |
| **2. Traceability Gate** | `TRACE-*` | 未定義キーワードの参照、未参照の要求仕様、孤立ノードの検出。 |
| **3. Hierarchy Gate** | `HIERARCHY-*` | Tier（0:要求 $\to$ 1:主要 $\to$ 2:サブ $\to$ 3:リーフ）間の逆流参照・カプセル化違反の検出。 |
| **4. Formal Gate** | `FORMAL-*` | `docs/**/formal/*.py`（pyModelChecking）の実行、LTL/CTL 検証、`BACKS` 契約の検証。 |
| **5. WIT Gate** | `WIT-*` | `wit/*.wit` の構文・型整合性・エラー回復戦略契約の検証。 |
| **6. Evidence Gate** | `EVIDENCE-*` | `<!-- evidence: ... -->` で主張されたベンチマークや実装ファイルの実在性とアサーション検証。 |
| **7. Obligation Gate** | `OBLIG-*` | リスク評価（`risk`）で導出された検証義務（形式検証・LLM監査等）が **100% 履行** されているかの検証。 |
| **8. Consistency Gate** | `CONSIST-*`<br>`TERM_VARIANCE` | 一貫性ベースラインとの差分・シンボル値ズレ、および **TF-IDF + さくらのAI エンベディング・LLM文脈監査による用語表記揺れ（`TERM_VARIANCE`）** の警告。 |
