---
description: 盆栽ワークフロー。全体から細部へ、反復的な設計プロセス。
---

# 盆栽デザイン（設計）ワークフロー

※ バックログは @docs/backlog/list.md に保存すること。
※ 解決していない設計の交差点は @docs/backlog/challenge.md に保存すること。
※ トレーサビリティマトリクスのファイル名は「matrix_日付_時間.md」とすること。

進め方のイメージは「盆栽」です。全体をみて気になるところを少し整える、それを繰り返してゆっくり作る。整えたら観察し、フィードバックを求める。整えた結果、課題が出ればそれをすぐに解決するのではなく、一旦棚上げにし、全体に戻る。雑にならない。ステップバイステップで方向性を調整し、進める。互いに影響し合う領域を少しずつ整えて全体を進める流れです。

```mermaid
graph TB
    Start([Start]) --> Overview[View Overall System]
    Overview --> Identify[Identify Area to Refine]
    Identify --> Adjust[Make Small Adjustments]
    Adjust --> Observe[Observe and Gather Feedback]
    Observe --> Decision{Issues Found?}
    Decision -->|Yes| Defer[Defer to Backlog]
    Decision -->|No| Complete{All Areas<br/>Refined?}
    Defer --> Overview
    Complete -->|No| Overview
    Complete -->|Yes| End([Complete])
    
    style Start fill:#90EE90
    style End fill:#FFB6C6
    style Overview fill:#87CEEB
    style Adjust fill:#FFD700
    style Observe fill:#DDA0DD
```

## 原則
原則として設計駆動開発です。すべてにおいて設計が先です。エージェントにとって設計が明確であれば実装は派生物でしかありません。派生物の構文と実装、トレーサビリティの確保はラピッド開発の回転を遅くするだけであり、設計フェーズでは不要です。

## ユーザからの質問への対応
ユーザのオープンクエスチョンに対し複数の選択肢がある場合、積極的にユーザにどうしたいか想定されるシナリオを提示し、質問を返してください。ほとんどの場合、ユーザにクローズドクエスチョンで返してもその中に解はないので、オープンクエスチョンで返すのが望ましいです。
