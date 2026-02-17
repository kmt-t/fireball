# Embedded System Development Gateway (Gemini CLI)

あなたは、リソース制約のある組み込みシステムおよびC++プロジェクトを開発するエージェントである。タスクを開始する際は、本ドキュメントを起点として、遵守すべき規約や設計原則を判断すること。

## 1. 📂 開発リソースの構成と参照判断基準

### 🧠 エージェントの記憶 (Agentic Brain)
この領域はエージェント専用の補助メモリであり、自然言語仕様からの「論理的エッセンス」を形式的に保持する。**自然言語ドキュメント（docs/）が唯一の絶対的な Source of Truth (SoT) であり**、本領域は実装時の認知ドリフトを防止する「アンカー」として機能する。
- **[.agent/brain/](/.agent/brain/)**: エージェント用形式的メモリ（ATC）。必ず上流の自然言語仕様を参照する。

### ⚠️ 実装・レビューの原則 (Rules)
実装やコードレビューにおいて、品質と安全性を担保するための基準。
- **[cpp_coding_style.md](/.agent/rules/cpp_coding_style.md)**: 組み込み向けの命名規則、型語彙、メモリ安全性を高めるための記述制限。
- **[design.md](/.agent/rules/design.md)**: 設計駆動開発の原則、盆栽デザイン哲学、WIT-First、トレーサビリティ、インターフェイス設計ルール。
- **[protocols.md](/.agent/rules/protocols.md)**: 情報のトレーサビリティ確保、エージェントの行動指針、ドキュメント配置ルール。
- **[documentation.md](/.agent/rules/documentation.md)**: 設計情報の構造化、日本語/英語の使い分け、図解による論理的説明のルール。

### 🛠 専門技能 (Skills)
特定の技術領域において、各スキルの `SKILL.md` を参照して高度な自動化や検証を行う。

| スキル名 | パス | 概要 |
|:---|:---|:---|
| **Axiomatic Interface Design** | `.agent/skills/axiomatic_interface_design/` | MDAの概念を応用し、公理的意味論に基づき厳密な設計・実装・テストを導出 |
| **Code Generation** | `.agent/skills/code_generator/` | JSONデータ/WIT IDLからC++コードを自動生成し、品質チェックまで一貫して実行 |
| **Docker Workaround** | `.agent/skills/docker_workaround/` | Docker Composeを使用して安定した開発環境を構築し、コンテナ内ツールを実行する手順 |
| **Embedded C++ Optimization** | `.agent/skills/cpp_embedded/` | RAM 64KB環境における禁止/許可ライブラリ、コンテナ代替、メモリ管理、エラー戦略 |
| **Fireball Architecture** | `.agent/skills/fireball_architecture/` | 3-Tier分離、IoC、Harness/Static DIなどプロジェクト固有の設計原則と構造的ルール |
| **Risk Assessment** | `.agent/skills/risk_assessment/` | 実装リスクに応じた設計詳細度（Tier 1〜3）の決定基準と検証レベルの定義 |
| **Type Vocabulary** | `.agent/skills/fireball_vocabulary/` | 設計仕様書における実装非依存な型システム語彙とC++型エイリアスの対応表 |
| **WASM Development** | `.agent/skills/wasm_development/` | WebAssembly/WASI仕様、WAMR実装、LLVMバックエンド定義のリソース参照と調査 |

### 📚 設計・仕様ドキュメント (Documents)
プロジェクトの構造や要求を理解するために参照すること。各ディレクトリ内の **`FORMAT.md`** には、そのカテゴリのドキュメントが遵守すべき標準フォーマットが定義されている。

- **[docs/requires/](/docs/requires/)**: 満たすべき要求事項とキーワードベースのトレーサビリティ。
- **[docs/architecture/](/docs/architecture/)**: システム全体の構造、メモリマップ、インターフェース境界。
- **[docs/components/](/docs/components/)**: 各モジュールの責務、契約（Contracts）、データ構造。
- **[docs/patterns/](/docs/patterns/)**: 構造設計・実装最適化・システム挙動のパターン集。
- **[docs/concept/](/docs/concept/)**: 設計コンセプトと方法論（VDD等）。
- **[docs/plans/](/docs/plans/)**: 開発計画とマイルストーン。
- **[docs/temp/](/docs/temp/)**: エージェントの自由な作業領域。検討プロセスや一時的な成果物をフラットに配置。
- **[docs/backlog/](/docs/backlog/)**: 未解決の課題やTODOの記録。

### 🔄 開発手順 (Workflows)
標準的な開発サイクルや、継続的な設計改善の手順を確認すること。

| ワークフロー | パス | 概要 |
|:---|:---|:---|
| **Development Cycle** | `.agent/workflows/development_cycle.md` | VDD（形式仕様・TLA+検証→生成→品質保証）の統合開発サイクル |
| **Check Compliance** | `.agent/workflows/check_compliance.md` | 形式仕様・生成コード・コーディング標準への適合性を自動検証する手順 |
| **Progress Meeting** | `.agent/workflows/progress_meeting.md` | 計画と成果物の乖離分析、リスク分析、アクションプラン策定を行う進捗会議 |
| **Summarize** | `.agent/workflows/summarize.md` | docs配下の設計ドキュメントから解像度の高い要約を生成 |
| **Waigaya** | `.agent/workflows/waigaya.md` | 雑談ベースで設計をリファインメントする自由議論モード |
| **Friction Audit** | `.agent/workflows/friction_audit.md` | 仕様・ワークフロー・プロンプト間の矛盾を検出し開発の「詰まり」を解消 |

## 2. 🚦 タスク別・推奨アクション

詳細なディスパッチは [navigation_dispatch.atc](.agent/brain/navigation_dispatch.atc) を参照。

- **新規機能の設計**: `requires` (要求) -> `architecture` (構造) -> `components` (責務) -> `patterns` (パターン適用)
- **コードの実装/修正**: `rules` (規約) -> `skills` (最適化・パターン) -> 実装
- **インターフェース定義**: `rules/design` (契約の記述) -> `code_generator` (自動生成)
- **問題の調査**: `architecture` (依存関係) -> `temp` (原因分析・プロトタイプ)
- **型名の選定**: `fireball_vocabulary` のみ参照
- **コンテナ選択**: `cpp_embedded §4` のみ参照
