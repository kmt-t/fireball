---
name: clean-architecture-solid-review
description: "Clean Architecture と SOLID に基づき、クラス責務、依存方向、ファクトリ配置、計算量と設計の簡潔さをレビューするときに使う。"
---

# Clean Architecture and SOLID Review

C++ / Python を中心とするクラス設計を、Fireball の Clean Architecture、責務境界、SOLID 原則に沿ってレビューする。クラスの生成元・利用側・呼び出し経路を追い、設計の妥当性と計算量、時間・空間・コードサイズの釣り合いを評価する。

## 進め方

1. 対象範囲と差分を確定し、既存の作業ツリー変更を保護する。クラス宣言だけで判断せず、生成元、利用側、呼び出し経路、所有権・寿命を追う。
2. プロジェクト正本の .agents/rules/**、docs/requires/requirement_list.md、docs/architecture/architecture_overview.md、docs/architecture/document_structure.md と対象コンポーネント仕様を読む。Tier 番号をそのまま Clean Architecture の内外とみなさず、ポリシー、契約、実装、Harness の役割を確認する。
3. クラスごとの変更理由、状態・振る舞いのまとまり、公開契約、具体型への依存、生成箇所、依存注入経路を整理する。include/import に加えてテンプレート、関数ポインタ、コールバック、サービスロケータ、具象型返却も追跡する。
4. [レビュー基準](references/review-rubric.md)を適用する。SOLID や「コンパクトさ」を機械的なチェックリストにしない。Factory／Composition Root は通常レイヤの外で組み立てる前提で確認し、内側に置く正当な Domain Factory は区別する。
5. 重大度順に、根拠となるファイル・行、実際の影響、最小の改善案、必要な検証を報告する。仕様と実装が食い違う場合は正本を示す。修正は明示的に依頼された場合に限る。

組み込み設計では、抽象化のために動的確保、仮想ディスパッチ、例外、RTTI を新たに要求しない。既存仕様が Harness、関数テーブル、テンプレート等による静的 DI を定める場合、その境界で依存性逆転と置換可能性を評価する。複雑度・実行時コスト・ROM/RAM・コードの簡潔さを一緒に扱う詳細基準はレビュー基準を参照する。
