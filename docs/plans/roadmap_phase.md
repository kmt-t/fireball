# 開発ロードマップ

全体の開発フェーズ・工期・目的を定義する。各フェーズの具体的なタスクは `docs/plans/backlog_list.md` を参照。
品質課題および検証結果は `reports/doc_report.md` および `reports/doc_risk_report.md` を正本とする。

## フェーズ概要

| フェーズ | 工期 | 目的 | 状態 |
|---|---|---|:---:|
| **Phase 0: Quality Gate / Foundation** | 約6ヶ月 | 仕様品質のGO判定・設計固定化・形式検証基盤の確立 | **GO判定中** |
| **Phase 1: vSoC First** | 約3ヶ月 | スタンドアロンvSoCコア実装（Loader/Interpreter/JIT） | 待機中 |
| **Phase 2: Integration** | 約4ヶ月 | 周辺コンポーネント実装・統合（COOS/IPC/vMMIO/HAL） | 未着手 |
| **Phase 3: PoC** | 約2ヶ月 | ターゲットボード移植（Cortex-M33）・性能評価 | 未着手 |
| **Phase 4: OSS** | 継続 | OSSリリース整備・コミュニティ対応 | 未着手 |

**Phase 0を品質ゲートに集中させる理由:**
- 仕様品質がGO判定に達しない状態で実装に入ると、手戻りが増えて開発が停止するため
- vSoCの設計を「作る」前に、矛盾・未確定点・検証不能点を減らす必要があるため
- 周辺コンポーネントは実装ではなく、インターフェースと不変条件の確定を優先するため

---

## Phase 0: Foundation（約6ヶ月）

設計ドキュメント・WIT契約・ビルド基盤・形式検証を完成させる。**仕様のGO判定に必要な品質基準を満たす**ことを最優先とする。`{META_SpecificationFirst}` `{META_Risk_Tiering}`

| サブフェーズ | 目的 | 状態 |
|---|---|:---:|
| Phase 0.7: Static DI & Build System | Harnessパターン・静的DI・WIT→C++自動生成・CMakeビルド | **DONE** |
| Phase 0.75: Constexpr Verification | コード生成のconstexpr対応・コンパイル時計算の実証 | **DONE** |
| Phase 0.76: SysML Alignment | 既存設計図のSysML準拠化・パラメトリック図導入 | **DONE** |
| Phase 0.8: Spec Quality Gate | 仕様の矛盾解消・形式検証（pyModelChecking 5モデル）・WIT契約・LLM監査合格 ➔ **オーナー最終GO判定** | **進行中 (レビュー中)** |

**Phase 0 達成状況 & GO判定基準:**
- [ ] 全設計ドキュメントの整合性確認（0 Errors, 0 Warnings）
- [ ] 全WITファイルへの契約（リカバリー戦略パターン、リソース・ストリーム）定義
- [ ] コア形式検証（pyModelChecking 4モデル: Mutex, CSP, JIT, vSoC）のモデル検査合格
- [ ] トレーサビリティマトリクス（100+キーワード）の一方向追従確立
- [ ] LLM as a Judge（Sakura AI Engine）による意味監査合格（0 Failures）
- [ ] `Resource_Estimation_Model` による制約適合（RAM 64KB に対し静的合計 24.0KB、残余 40KB）
- [ ] **オーナー（アーキテクト）による最終仕様精読 & GO 判定**

---

## Phase 1: vSoC First（GO後に約3ヶ月）

スタンドアロンvSoCを実装し、WAMR比較評価を実施する。Phase 0 の GO 判定後に開始する。`{META_AI_Native_Dev}`

- WASMローダ
- インタープリタ（算術・制御・メモリ）
- JITコンパイラ
- ベンチマーク・ハーネス・WAMR比較評価

---

## Phase 2: Integration（約4ヶ月）

周辺コンポーネントの実装と統合。

- COOSカーネル（スケジューラ・CSP）
- IPCルータ
- HAL（x64上での標準入出力のみ）
- vSoC追加機能（ロギング・デバッガ・vMMIO）

---

## Phase 3: PoC（約2ヶ月）

実機での最終検証。

- ターゲットボード移植（Microbit, Zephyr）
- デバッグ・テスト・性能評価

---

## Phase 4: OSS（継続）

OSSリリースに向けた整備。

- ドキュメンテーション
- CMake設定ジェネレータ
- 開発環境整備（標準ビルド環境・リンカスクリプト）
- コミュニティ対応
