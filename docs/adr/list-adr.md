# IPC 設計 ADR (Architecture Decision Record) All

Status: Proposed

Date: 2025-12-02

Author: Takuya Matsunaga / Cline

Context
- Fireball の IPC は、固定長レコードを基本とするハイブリッドな Key-Value 方式を採用。現状の仕様ドキュメント群には過去の決定事項や経緯が混在しているため、決定履歴を一本化する ADR を作成する必要がある。
- 本 ADR は、将来の拡張（複数ゲストの統合、コア別ランタイムアフィニティ、メモリ管理の拡張など）を見据え、IPC 設計の基盤を統一的に定義する。

Decision ( ADR-001: Multi-Guest Support )
- ランタイムで複数のゲストを実行できるようにする。マルチコア対応はコアごとにランタイムをアフィニティする構成。
- 目的は将来の拡張性を確保すること。現状は単一ゲスト前提だが、ランタイムを複数ゲストに対応させることで将来のスケールを見込む。

Alternatives considered
- A. ランタイムを現在の単一ゲスト前提のまま維持する。
  - 理由: 実装と検証が単純だが、将来の拡張性を犠牲にする。
- B. マルチゲストを後回しにして、まずは IPC の安定化を優先する。
  - 理由: 現状の安定性を優先するが、長期的には制約となる可能性。
- C. 完全なマルチゲスト対応を先行して設計する。
  - 理由: 将来性は高いが、実装リスクが高く、段階的導入が適切でない可能性。

Rationale
- ADR による一本化は、意思決定の根拠・代替案・影響範囲を明示することで変更管理が容易になる。
- IPC 設計を ADR に集中させ、仕様ファイルは ADR 参照のみを保持する運用によりノイズを軽減する。

Consequences
- 以降の IPC 関連変更は ADR に追記され、仕様ファイルには ADR 参照のみが残る運用になる。
- ADR の運用ルールを継続的に適用する必要がある。

Date: 2025-12-02

Author: Takuya Matsunaga / Cline


Decision ( ADR-002: ISR Safety Mechanism )
- HAL から割り込みフラグを立て、インタープリタから監視する。ゲストのコンテキストスイッチ時にランタイムがフラグをチェックする。
- 結果として ISR の実行時間を最小化し、割り込み時の安全性を担保する。

Alternatives considered
- A. ISR を直接安全に扱える専用 API を提供する。
  - 問題点: 複雑な同期を増やし、実装負荷が増大する可能性。
- B. ISR 側での処理を全面的にランタイム側へ委譲する。
  - 問題点: 遅延や追跡性の難易度が高まる。
- C. ISR を徹底的に回避する設計へ寄せる。
  - 問題点: 現実的にはイベント処理が必要。

Rationale
- ISR からのフラグ立て方式は、軽量で高効率な実装を可能にし、ランタイム側で統一的に処理できる。

Consequences
- HAL の ISR 影響を ADR 化により管理可能。
- 将来の拡張では ISR フローを ADR に追記するだけで管理可能。

Date: 2025-12-02

Author: Takuya Matsunaga / Cline


Decision ( ADR-003: Memory Partitioning )
- ヘッダファイル形式のコンフィグファイルを定義しその中のマクロで容量などは固定する。

Alternatives considered
- A. 動的にメモリを割り当てる設計
  - 問題点: ランタイムの予測性が損なわれる可能性
- B. 完全に静的での運用を行わない
  - 問題点: 柔軟性が失われる

Rationale
- 固定化によりビルド時のメモリマップが確定し、ランタイムの予測可能性を高める。

Consequences
- 配置済みメモリの確定、ランタイムの安定性が向上。

Date: 2025-12-02

Author: Takuya Matsunaga / Cline


Decision ( ADR-004: Maximum Coroutines Configuration )
- ヘッダファイル形式のコンフィグファイルを定義しその中のマクロでヒープサイズなどを固定する。

Alternatives considered
- A. 動的にコルーチン数を拡張する設計
- B. 固定せず、ランタイムが動的に管理する設計
- C. 固定値の最小値だけを定義する

Rationale
- 固定することでリソース管理の predictability を確保。

Consequences
- ヒープサイズと最大コルーチン数の固定化により、安定性が向上。

Date: 2025-12-02

Author: Takuya Matsunaga / Cline


Decision ( ADR-005: JIT Fallback Strategy )
- 基本方針は JIT のレイテンシ最小化。テンプレート展開＋パッチ当て方式。機械語の最適化はしない。

Alternatives considered
- A. 高度な機械語最適化
- B. レイテンシよりも長時間の最適化を優先

Rationale
- レイテンシ最小化が最優先。将来の拡張で柔軟性の拡張を検討。

Consequences
- 生成コードの単純性と高い安定性を確保。

Date: 2025-12-02

Author: Takuya Matsunaga / Cline


Notes
- ADR の追加・運用は他の仕様領域にも拡張する。今後 ADR のファイル命名と整理を一元化していく。
