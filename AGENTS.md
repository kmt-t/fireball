# エージェントへの指示

本プロジェクトのエージェントガイドラインは、メンテナンス性とスケーラビリティを向上させるためにモジュール化されました。
以下のディレクトリ構造に従って詳細なルール、ワークフロー、スキルを参照してください。

## 📁 ディレクトリ構造

- **`.agent/rules/`**: プロジェクト全体のルール、規約、プロトコル
- **`.agent/workflows/`**: 特定のタスクを実行するための手順書（ワークフロー）
- **`.agent/skills/`**: 特殊な技能や判断基準（スキル）

## 📜 ルール (Rules)

まずは以下の基本ルールを確認してください。

- **[プロトコルと配置パス](.agent/rules/protocols.md)**: 情報の探し方、ドキュメントの配置場所、ディレクトリ構造のルール。
- **[設計原則とルール](.agent/rules/design_principles.md)**: 設計アプローチ、インターフェイス設計の原則。
- **[ドキュメント作成と品質](.agent/rules/documentation.md)**: ドキュメントの書き方、コード生成のルール、品質基準。

## 🔄 ワークフロー (Workflows)

タスクに応じて以下のワークフローを使用してください。

| ワークフロー | 説明 |
| :--- | :--- |
| **[ワイガヤ](.agent/workflows/waigaya.md)** | 雑談ベースでのアイデア出しと設計リファインメント |
| **[進捗会議](.agent/workflows/progress_meeting.md)** | スケジュールと成果物の確認、アクションプラン策定 |
| **[議論マージ](.agent/workflows/discussion_merge.md)** | 会議のアウトプットを設計ドキュメントに統合 |
| **[サマライズ](.agent/workflows/summarize.md)** | ドキュメントの要約作成 |
| **[盆栽デザイン](.agent/workflows/bonsai_design.md)** | 反復的な全体設計プロセス |
| **[開発サイクル](.agent/workflows/development_cycle.md)** | 設計 -> 実装 -> デバッグ -> 振り返りの詳細サイクル |

## 🧠 スキル (Skills)

| スキル | 説明 |
| :--- | :--- |
| **[リスクベース・ティアリング](.agent/skills/risk_assessment.md)** | 実装リスクに基づいた設計詳細度の決定手法 |
| **[組み込みC++最適化](.agent/skills/embedded_cpp.md)** | ヒープ禁止、コンテナ最適化、Economic Function等の実装技術 |
| **[Fireballアーキテクチャ](.agent/skills/fireball_architecture.md)** | 3-Tier分離、IoC、Harnessパターン等の設計原則 |

---

## CRITICAL PROTOCOLS (SUMMARY)

1. **必読**: `.agent/rules/protocols.md` を参照。
2. **Coding Style**: `.agent/rules/coding_style.md` を厳守。
3. **Design First**: 実装の前に必ず設計を行うこと（`.agent/workflows/development_cycle.md`参照）。
