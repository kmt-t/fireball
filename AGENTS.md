# Embedded System Development Gateway (Gemini CLI)

あなたは、リソース制約のある組み込みシステムおよびC++プロジェクトを開発するエージェントである。タスクを開始する際は、本ドキュメントを起点として、遵守すべき規約や設計原則を判断すること。

## 1. 開発リソースの構成と参照判断基準

### エージェントの記憶と「儀式」 (Brain Sync Ritual)
セッション開始時に、エージェントは以下の「儀式」を通じてプロジェクトの不変条件を自身のコンテキストに定着させる。これにより、認知の重ね合わせ（Superposition）をプロジェクト固有の設計意図へ収束（Collapse）させ、Driftを防止する。

1.  **Eternal Memory のロード**: `.agent/brain/*.atc` を `view_file` で一括ロードする。
    - **`project_context.atc`**: 全域的不変条件、Security Model（ONE gate）、Physical Time Model（人間との時間同期）を把握する。
    - **`architecture_reference.atc`**: 各コンポーネントの TLA+ 導出用様相論理制約を把握する。
    - **`navigation_dispatch.atc`**: タスクの種類に応じたスキルとドキュメントの最短参照パスを把握する。
2.  **文脈の定着**: 各ATCの内容を自身のワーキングメモリにアンカーし、以後の推論はすべてこれらの制約下で行う。

**ATC記録プロトコル**
新たな設計課題の解決や不変条件の発見の際は、`.agent/brain/*.atc` に様相論理形式で追記し、次世代へ「魂」を継承すること。

### 実装・レビューの原則
実装やコードレビューにおいて、品質と安全性を担保するための基準。

- **cpp_coding_style.md**: 組み込み向けの命名規則、型語彙、メモリ安全性を高めるための記述制限。
- **design.md**: 設計駆動開発の原則、盆栽デザイン哲学、WIT-First、トレーサビリティ、インターフェイス設計ルール。
- **protocols.md**: 情報のトレーサビリティ確保、エージェントの行動指針、ドキュメント配置ルール。
- **documentation.md**: 設計情報の構造化、日本語/英語の使い分け、図解による論理的説明のルール。

### 専門技能
特定の技術領域において、各スキルの `SKILL.md` を参照して高度な自動化や検証を行う。

| スキルカテゴリ | パス | 概要 |
|:---|:---|:---|
| **MDD & Generation** | `.agent/skills/code_generator/` | WIT/契約（公理的設計）からの自動生成と品質チェック |
| **Codebase Explorer** | `.agent/skills/explorer/` | インタラクティブ探索、シンボル要約、コンテキスト要約 |
| **Embedded Optimization** | `.agent/skills/cpp_embedded/` | RAM 64KB環境向け制約、メモリ管理、エラー戦略 |
| **Arch & Design** | `.agent/skills/fireball_architecture/` | 3-Tier分離、型語彙、リスクベース・ティアリング |
| **Verification & Audit** | `.agent/skills/friction_audit/` | ドキュメントの不整合、未定義語、トレーサビリティ監査 |
| **Environment** | `.agent/skills/docker_workaround/` | Dockerを用いた安定した開発環境構築とツール実行 |
| **Domain Expertise** | `.agent/skills/wasm_development/` | WASM/WASI仕様、WAMR実装、LLVM定義の調査 |

### 設計・仕様ドキュメント
プロジェクトの構造や要求を理解するために参照すること。各ディレクトリ内の **`FORMAT.md`** には、そのカテゴリのドキュメントが遵守すべき標準フォーマットが定義されている。

- **docs/requires/**: 満たすべき要求事項とキーワードベースのトレーサビリティ。
- **docs/architecture/**: システム全体の構造、メモリマップ、インターフェース境界。
- **docs/components/**: 各モジュールの責務、契約、データ構造。
- **docs/patterns/**: 構造設計・実装最適化・システム挙動のパターン集。
- **docs/concept/**: 設計コンセプトと方法論。
- **docs/plans/**: 開発計画とマイルストーン。
- **docs/temp/**: エージェントの自由な作業領域。検討プロセスや一時的な成果物をフラットに配置。
- **docs/backlog/**: 未解決の課題やTODOの記録。

### 開発手順
標準的な開発サイクルや、継続的な設計改善の手順を確認すること。

| ワークフロー | パス | 概要 |
|:---|:---|:---|
| **Development Cycle** | `.agent/workflows/development_cycle.md` | VDD（形式仕様・TLA+検証→生成→品質保証）の統合開発サイクル |
| **Check Compliance** | `.agent/workflows/check_compliance.md` | 形式仕様・生成コード・コーディング標準への適合性を自動検証する手順 |
| **Progress Meeting** | `.agent/workflows/progress_meeting.md` | 計画と成果物の乖離分析、リスク分析、アクションプラン策定を行う進捗会議 |
| **Summarize** | `.agent/workflows/summarize.md` | docs配下の設計ドキュメントから解像度の高い要約を生成 |
| **Waigaya** | `.agent/workflows/waigaya.md` | 雑談ベースで設計をリファインメントする自由議論モード |
| **Friction Audit** | `.agent/workflows/friction_audit.md` | 仕様・ワークフロー・プロンプト間の矛盾を検出し開発の「詰まり」を解消 |

### 3. Verification & Analysis (The Toolbox)
エージェントおよび開発者の「ワーキングメモリ」を保護し、1アクションあたりの「解析レバレッジ（成果）」を最大化するためのツール群。

- **Working Memory Optimization (認知負荷の低減)**:
  - `explorer-cli context "{Keyword}"`: 膨大なドキュメントを往復せず、3行の定義・文脈・意図を即座に脳にアンカーする。
  - `explorer-cli summary <file> --json`: 1000行のコードを読まずに「プログラムの骨格」のみを注視し、推論に必要な情報を最小化する。
- **Execution Leverage (1アクションあたりの成果最大化)**:
  - **Unified Container Runners (`docker-*.sh`)**: 環境構築という「無駄な手数」を排除。自動起動と Pipe 連携により、複数ファイルを一括解析するパイプラインを即座に構築。
  - `bash .agent/skills/docker_workaround/scripts/docker-cmd.sh find src -name "*.cxx" | bash .agent/skills/docker_workaround/scripts/docker-explorer.sh pipe summary`: 大規模な変更の影響範囲を、単一のコマンドラインで全件把握。
- **Automated Traceability (整合性の保証)**:
  - `python3 .agent/scripts/check_traceability.py`: 記憶に頼らず、要求キーワード `{Keyword}` の網羅性を機械的に検証。

## 2. タスク別・推奨アクション

タスク開始時の詳細なスキル・ドキュメント選択は **[navigation_dispatch.atc](.agent/brain/navigation_dispatch.atc)** を参照せよ。これは $O(1)$ の時間計算量で最適なリソースへディスパッチされるための「索引」である。

- **新規機能の設計**: `docs/requires` (要求) -> `docs/architecture` (構造) -> `docs/components` (責務) -> `docs/patterns` (パターン適用)
- **コードの実装/修正**: `rules` (規約) -> `skills` (最適化・パターン) -> 実装
- **インターフェース定義**: `rules/design` (契約の記述) -> `code_generator` (自動生成)
- **問題の調査**: `docs/architecture` (依存関係) -> `docs/temp` (原因分析・プロトタイプ)
- **型名の選定**: `fireball_vocabulary` のみ参照
- **コンテナ選択**: `cpp_embedded §4` のみ参照
