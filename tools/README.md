# 検証・開発ツールの使い分け

この文書は、変更内容に応じた検証方法と実行入口を示す。コマンドの詳細なオプションと各ゲートの検査項目は [spec-integrator のリファレンス](spec-integrator/README.md) を参照する。

## 正本の分担

| 情報 | 正本 | 役割 |
| :--- | :--- | :--- |
| 開発・実装上の必須条件 | [開発ルール](../.agents/rules/development-policy.md)、各コーディングルール | 守るべき条件を定める。 |
| 検証の反パターン | [verification-antipatterns](../.agents/rules/verification-antipatterns.md) | 証拠のない検証、恒真アサーション、未結線テストなどを防ぐ。 |
| コンポーネントごとの必要証跡と網羅性 | [検証因子・成果物マトリクス](../docs/qa/verification_factor_matrix.md) | 必要な検証成果物と因子の対応を定める。 |
| テスト仕様・結果の配置 | [品質保証資料の案内](../docs/qa/README.md) | テスト仕様と記録済み証跡を分類する。 |
| 検証の選び方と実行入口 | この文書 | 変更範囲に合うコマンドを選ぶ。 |
| ドキュメント検証の運用手順 | [document-validation スキル](../.agents/skills/document-validation/SKILL.md) | 対象を絞り、正本と検査結果を照合する。 |
| CLI の検査項目・オプション | [spec-integrator のリファレンス](spec-integrator/README.md) | ツールの実装に対応する詳細を示す。 |

## 検証の分類

検証は目的で選ぶ。作業段階を示すレベル番号は使わない。

| 分類 | 対象・役割 | 主な入口 |
| :--- | :--- | :--- |
| **準備・更新** | フォーマッタはファイルを変更する。build は DocGraph と用語索引、整合性の基準データを更新する。どちらも合否を示す検査ではない。 | format-doc、format-src、build |
| **決定的な品質ゲート** | ドキュメント構造・要求追跡・Tier・形式モデル・WIT・証跡・検証義務・整合性を機械判定する。コード規約や結線済みテストも対象にする。 | check-doc、check-src |
| **証拠の実行** | 単体・結合テスト、形式モデル、シナリオ、ベンチマークを実行して動作や性質を確かめる。必要な証拠と登録先はマトリクスで確認する。 | 各検証成果物の実行入口。pysim は [experiments/pysim/README.md](../experiments/pysim/README.md) を参照。 |
| **参考警告** | GiNZA の日本語可読性警告など、機械判定だけで合否を決められない箇所をレビュー候補として示す。 | check-doc の文章可読性警告 |
| **意味・リスク監査** | LLM による用語揺れ、リスク、意味整合性を調べる。API 利用を伴う監査はユーザーの明示指示がある場合だけ実行する。 | risk、llm-word、llm-single-review、llm-keyword-review、llm-judge |

check-doc と check-src は、検証因子・成果物マトリクスも確認する。マトリクスだけを調べるときは check-verification-matrix を使う。ゲートの識別子と判定内容は CLI リファレンスに集約する。

## 専門レビューの使い分け

専門レビューは、決定的な品質ゲートや実行証跡を代替しない。変更対象に合うスキルを選ぶ。指摘は要求・設計・実装の正本と照合する。

| 対象 | スキル | 主な確認内容 |
| :--- | :--- | :--- |
| 最上位アーキテクチャと下位Tier | [architecture-review](../.agents/skills/architecture-review/SKILL.md) | 概要設計、下位仕様、形式モデル、WIT、実装の垂直整合性。 |
| 個別コンポーネント | [component-review](../.agents/skills/component-review/SKILL.md) | 仕様、形式モデル、コンセプトコード、テスト仕様・実装の証跡連鎖。 |
| クラス責務と依存設計 | [clean-architecture-solid-review](../.agents/skills/clean-architecture-solid-review/SKILL.md) | Clean Architecture、SOLID、依存方向、ファクトリ配置、計算量と簡潔さ。 |
| pysim の移植性・規約 | [pysim-review](../.agents/skills/pysim-review/SKILL.md) | C++23 移植性、型・コンテナ・決定性・計算量・RAM/ROM 配置。 |
| pysim の性能最適化 | [pysim-vtune-optimization](../.agents/skills/pysim-vtune-optimization/SKILL.md) | VTune の実測ボトルネック、RAM/ROM 予算、事前計算と遅延計算の比較。 |

## 変更範囲から選ぶ

| 変更 | 実行する検証 |
| :--- | :--- |
| Markdown の仕様・設計・検証資料 | 対象ファイルを指定した check-doc。文章可読性は警告として確認する。 |
| C++、Python、コンセプト、形式モデル、pysim | 対象の check-src を使う。変更ファイルに合う -group を指定し、直接関係するテストも実行する。 |
| 検証成果物、因子、水準、シナリオ、登録設定 | check-verification-matrix を使う。check-doc と check-src にも同じ検査が含まれる。 |
| 設計書と要求キーワードの編集 | check-doc で連動修正漏れを確認する。問題を解消してから build で基準データを更新する。先に build すると編集差分を検査できない。 |
| LLM 判定義務 {VERIFY_LLM} の履行 | ユーザーの明示指示を受けて llm-judge を実行する。他のレビュー結果では義務を履行できない。 |

### 検査範囲と報告

回帰テストは変更ファイルと直接関係する範囲に絞る。
検査結果を不具合として報告するときは、要求仕様と対象設計を確認する。違反箇所、技術的理由、要求への影響、修正方向を示す。
処理速度・計算量・RAM・ROM の条件を正本で確認できない場合は推測しない。未確認として報告する。

## 標準コマンド

Windows は PowerShell、Linux / WSL は Bash の入口を使う。[files...] は対象ファイルの省略可能な指定を表す。
<code>-group</code> には <code>cpp</code>、<code>python</code>、<code>concepts</code>、<code>formal</code>、<code>pysim</code>、<code>all</code> を指定する。

| 目的 | Windows | Linux / WSL |
| :--- | :--- | :--- |
| ドキュメント整形 | <code>powershell tools/format-doc.ps1 [files...]</code> | <code>./tools/format-doc.sh [files...]</code> |
| ドキュメント検証 | <code>powershell tools/check-doc.ps1 [files...]</code> | <code>./tools/check-doc.sh [files...]</code> |
| ソース検証 | <code>powershell tools/check-src.ps1 -group &lt;group&gt; [files...]</code> | <code>./tools/check-src.sh -g &lt;group&gt; [files...]</code> |
| 検証マトリクスのみ | <code>powershell tools/check-verification-matrix.ps1</code> | <code>./tools/check-verification-matrix.sh</code> |
| ソース整形 | <code>powershell tools/format-src.ps1 -group &lt;group&gt; [files...]</code> | <code>./tools/format-src.sh -g &lt;group&gt; [files...]</code> |
| DocGraph・索引の更新 | <code>powershell tools/build.ps1</code> | <code>./tools/build.sh</code> |

ドキュメント検証では、Format、Traceability、Hierarchy、Formal、WIT、Evidence、Obligation、Consistency の各ゲートを確認する。
ソース検証では、選んだグループの規約とサボり検査を行う。結線済みの検証も実行する。
詳しい検査項目は [spec-integrator のゲート仕様](spec-integrator/README.md) を参照する。

### 文章可読性の補助警告

check-doc は GiNZA で日本語文を解析する。長文や複雑な節接続をレビュー候補として警告する。
警告は品質ゲートのエラーに数えない。終了コードにも影響しない。

しきい値は <code>spec-integrator.yaml</code> の <code>prose_readability</code> で調整する。初期値は文長100文字超、または90文字以上かつ節接続候補4箇所以上である。
コードブロック・見出し・HTMLコメントは解析しない。表の文章セルは解析する。
GiNZA の構文解析は、冗長性や意味の正しさを判定しない。警告箇所は人が内容を確認する。

文章チェックを含む環境を初回に用意する場合は、<code>uv sync --project tools/spec-integrator --extra dev --extra prose</code> を実行する。

## 任意の補助監査

| 目的 | Windows | Linux / WSL | 区分 |
| :--- | :--- | :--- | :--- |
| 用語候補の静的確認 | <code>powershell tools/llm-word.ps1 -quick</code> | <code>./tools/llm-word.sh --quick</code> | LLM 判定なし（未作成の埋め込みがあれば API 利用） |
| 用語揺れの意味判定 | <code>powershell tools/llm-word.ps1</code> | <code>./tools/llm-word.sh</code> | OpenRouter 経由の Jev を既定で使用 |
| キーワードのリスク評価 | <code>powershell tools/risk.ps1</code> | <code>./tools/risk.sh</code> | OpenRouter 経由の Jev を既定で使用 |
| 文書単体のレビュー | <code>powershell tools/llm-single-review.ps1 -file &lt;path&gt;</code> | <code>./tools/llm-single-review.sh --file &lt;path&gt;</code> | OpenRouter 経由の Jev を既定で使用 |
| キーワード定義・参照ペアのレビュー | <code>powershell tools/llm-keyword-review.ps1 -keyword &lt;name&gt;</code> | <code>./tools/llm-keyword-review.sh --keyword &lt;name&gt;</code> | 定義セクションと参照セクションを1組ずつ評価。OpenRouter 経由の Jev を既定で使用 |
| {VERIFY_LLM} 義務の履行 | <code>powershell tools/llm-judge.ps1</code> | <code>./tools/llm-judge.sh</code> | Jev を既定で使用し、判定を記録 |
| 保存済み判定の確信度検索 | <code>powershell tools/llm-findings.ps1 -minConfidence 0.70</code> | <code>./tools/llm-findings.sh --min-confidence 0.70</code> | DBを検索。API利用なし |

API 利用を伴う監査は、ユーザーの明示指示がある場合だけ実行する。
<code>{VERIFY_LLM}</code> の義務は <code>llm-judge</code> で記録付きで履行する。詳細なオプションは [spec-integrator のリファレンス](spec-integrator/README.md) を参照する。
既定の Jev 判定には <code>OPENROUTER_API_KEY</code> が必要である。Jev は型付きの判定と確率を返し、説明文や引用箇所を生成しない。文章による所見が必要なレビューでは <code>--backend openrouter</code> または設定済みの別のチャット型バックエンドを指定する。<code>llm-word</code> の候補抽出では同じキーを使って OpenRouter の多言語埋め込みモデルも呼び出す。
