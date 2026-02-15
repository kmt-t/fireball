# Embedded System Development Gateway (Gemini CLI)

あなたは、リソース制約のある組み込みシステムおよびC++プロジェクトを開発するエージェントである。タスクを開始する際は、本ドキュメントを起点として、遵守すべき規約や設計原則を判断すること。

## 1. 📂 開発リソースの構成と参照判断基準

### ⚠️ 実装・レビューの原則 (Rules)
実装やコードレビューにおいて、品質と安全性を担保するための基準。
- **[cpp_coding_style.md](/.agent/rules/cpp_coding_style.md)**: 組み込み向けの命名規則、型語彙、メモリ安全性を高めるための記述制限。
- **[protocols.md](/.agent/rules/protocols.md)**: 情報のトレーサビリティ確保、エージェントの行動指針、ドキュメント配置ルール。
- **[documentation.md](/.agent/rules/documentation.md)**: 設計情報の構造化、日本語/英語の使い分け、図解による論理的説明のルール。

### 🛠 専門技能 (Skills)
特定の技術領域において、各スキルの `SKILL.md` を参照して高度な自動化や検証を行う。

| スキル名 | パス | 概要 |
|:---|:---|:---|
| **Code Generation** | `.agent/skills/code_generator/` | JSONデータ/WIT IDLからPythonスクリプトでC++コードを自動生成 |
| **Embedded C++ Optimization** | `.agent/skills/cpp_embedded/` | RAM 64KB環境における禁止/許可ライブラリ、コンテナ代替、メモリ管理パターン |
| **Fireball Architecture** | `.agent/skills/fireball_architecture/` | 3-Tier分離、IoC、Harness/Static DIの設計原則 |
| **Type Vocabulary** | `.agent/skills/fireball_vocabulary/` | 仕様書における型システム語彙とC++型エイリアスの対応 |
| **Risk Assessment** | `.agent/skills/risk_assessment/` | 実装リスクに応じた設計詳細度（Tier 1〜3）の決定基準 |
| **WASM Development** | `.agent/skills/wasm_development/` | WebAssembly/WASI仕様、WAMR実装、LLVMバックエンド参照 |

### 📚 設計・仕様ドキュメント (Documents)
プロジェクトの構造や要求を理解するために参照すること。各ディレクトリ内の **`FORMAT.md`** には、そのカテゴリのドキュメントが遵守すべき標準フォーマットが定義されている。

- **[docs/architecture/](/docs/architecture/)**: システム全体の構造、メモリマップ、インターフェース境界。
- **[docs/components/](/docs/components/)**: 各モジュールの責務、契約（Contracts）、データ構造。
- **[docs/requires/](/docs/requires/)**: 満たすべき要求事項とキーワードベースのトレーサビリティ。
- **[docs/temp/](/docs/temp/)**: エージェントの自由な作業領域。検討プロセスや一時的な成果物をフラットに配置。

### 🔄 開発手順 (Workflows)
標準的な開発サイクルや、継続的な設計改善の手順を確認すること。

| ワークフロー | パス | 概要 |
|:---|:---|:---|
| **Development Cycle** | `.agent/workflows/development_cycle.md` | 設計→インターフェース定義→実装→検証のサイクル |
| **Bonsai Design** | `.agent/workflows/bonsai_design.md` | 全体から細部へ、反復的な設計リファインメント |
| **Check Compliance** | `.agent/workflows/check_compliance.md` | コーディング標準・設計方針への適合性チェック |
| **Progress Meeting** | `.agent/workflows/progress_meeting.md` | 計画と成果物の乖離分析、リスク分析、アクション策定 |
| **Summarize** | `.agent/workflows/summarize.md` | docs配下ドキュメントの情報密度の高い要約生成 |
| **Waigaya** | `.agent/workflows/waigaya.md` | 雑談ベースで設計をリファインメントする自由議論 |

## 2. 🚦 タスク別・推奨アクション

- **新規機能の設計**: `requires` (要求) -> `architecture` (構造) -> `components` (責務)
- **コードの実装/修正**: `rules` (規約) -> `skills` (最適化・パターン) -> 実装
- **インターフェース定義**: `rules/documentation` (契約の記述) -> `code_generator` (自動生成)
- **問題の調査**: `architecture` (依存関係) -> `temp` (原因分析・プロトタイプ)
