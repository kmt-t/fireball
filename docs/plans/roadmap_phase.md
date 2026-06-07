# 開発ロードマップ

全体の開発フェーズ・工期・目的を定義する。各フェーズの具体的なタスクは `docs/plans/backlog_list.md` を参照。

## フェーズ概要

| フェーズ | 工期 | 目的 |
|---|---|---|
| Phase 0: Foundation | 約6ヶ月 | 設計の固定化・形式検証基盤の確立 |
| Phase 1: vSoC First | 約3ヶ月 | vSoCコア実装（Loader/Interpreter/JIT） |
| Phase 2: Integration | 約4ヶ月 | 周辺コンポーネント実装・統合 |
| Phase 3: PoC | 約2ヶ月 | ターゲットボード移植・性能評価 |
| Phase 4: OSS | 継続 | OSSリリース整備・コミュニティ対応 |

**Phase 0を設計に集中させる理由:**
- vSoCの設計を完璧に固めることを最優先
- 周辺コンポーネントは最小インターフェース定義でOK
- Phase 1での手戻りを防ぎ、実装効率を最大化

---

## Phase 0: Foundation（約6ヶ月）

設計ドキュメント・WIT契約・ビルド基盤・形式検証を完成させる。**vSoCの設計を完璧に固める**ことを最優先とする。`{META_SpecificationFirst}`

| サブフェーズ | 目的 | 状態 |
|---|---|---|
| Phase 0.7: Static DI & Build System | Harnessパターン・静的DI・WIT→C++自動生成・CMakeビルド | DONE |
| Phase 0.75: Constexpr Verification | コード生成のconstexpr対応・コンパイル時計算の実証 | DONE |
| Phase 0.76: SysML Alignment | 既存設計図のSysML準拠化・パラメトリック図導入 | 進行中 → |
| Phase 0.8: vSoC VDD Verification | コアロジック・vSoCサブシステムの形式検証（TLA+）| 進行中 |
| Phase 0.9: Reference Survey | 主要コンポーネントの参考実装調査 | 待機中 |

**Phase 0 完了条件（概要）:**
- 全設計ドキュメント（`docs/components/*.md`）の完成
- 全WITファイルへの契約（`@pre`, `@post`, `@inv`）追加
- ビルド基盤（CMakeビルド・WIT→C++生成）の完成
- コア形式検証（TLA+）の完了
- トレーサビリティマトリクスの完成

---

## Phase 1: vSoC First（約3ヶ月）

スタンドアロンvSoCを実装し、WAMR比較評価を実施する。`{META_AI_Native_Dev}`

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
