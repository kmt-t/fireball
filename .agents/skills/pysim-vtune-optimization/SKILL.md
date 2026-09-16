---
name: pysim-vtune-optimization
description: "VTune で experiments/pysim の実測ボトルネックを特定し、RAM・ROM 予算、組み込み制約、既存設計と両立する最適化案を比較するときに使う。"
---

# PySIM VTune Optimization

experiments/pysim の代表ワークロードを Intel VTune Profiler で測定し、実測根拠のある最適化案を組み込みターゲットの資源制約と設計境界に沿って提案する。通常の pysim コードレビューには ../pysim-review/SKILL.md を使う。

## 手順

1. 対象処理、代表入力、成功条件を特定する。ベンチマークと統合シナリオを読み、計測前後で同じワークロードと結果検証を使う。
2. .agents/rules/coding-standards-python.md、.agents/rules/development-policy.md、docs/components/tier1_core/system_containers.md、docs/components/tier1_core/system_config.md、docs/architecture/resource_budget_estimation.md を確認する。対象機能の Tier 仕様とテストも読む。バックログや予算資料の数値に食い違いがあれば、余剰予算を仮定せず、正本を照合して不確実性を記録する。
3. Intel VTune の利用可否と版を調べ、その版の vtune -help collect で対応分析・オプションを確認する。ホットスポットには Hotspots、Python の割当て起点には Linux 対象時の Memory Consumption、キャッシュ／メモリ待ちが疑われる場合は対応プラットフォームで Memory Access または Microarchitecture Exploration を選ぶ。最初から重い分析を一括実行しない。
4. 同一環境・同一入力で基準値を複数回取り、変動幅と計測オーバーヘッドを確認する。サンプリング結果は原因候補として読み、冷たい処理や総時間にほとんど寄与しない関数を改善対象の上位にしない。生の VTune 結果は、依頼がなければリポジトリに追加せず一時領域へ置く。
5. [最適化判断フレームワーク](references/optimization-decision-framework.md)で候補を比較する。VTune のホスト側 Python 計測は MCU 上の C++ の実行時間・SRAM・Flash 使用量を直接測らない。コードパスの頻度や計算形状を示す仮説として使い、サイズとターゲット性能は別途、型・容量・レイアウト・対象ビルドから見積もる。
6. 提案だけを求められた場合はコードを変更しない。実装も明示的に依頼された場合は、仕様・所有権・Tier 依存を保った最小変更を行い、対象テストと同条件ベンチマークで結果・副作用・資源差を検証する。

## 出力

優先度順に、各案の「VTune上の根拠」「期待する実行時間への効果」「ROM / RAM の増減見積もり」「容量・最悪時時間・複雑さ・Tier 境界の影響」「既存設計と採用可否」「検証方法」を記す。測定値、静的なサイズ見積もり、未確認の推定を区別する。改善案が資源予算・仕様・制約を満たす根拠を示せない場合は、採用を推奨せず未確認事項を明示する。
