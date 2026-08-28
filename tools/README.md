# Fireball Verification & Specification Tools

Fireball Hypervisor のドキュメント品質、静的トレーサビリティ、形式検証（pyModelChecking）、WIT インターフェース検証、ベンチマーク実測、および LLM as a Judge を実行する統合ツール環境です。

コア検証エンジンとして [spec-integrator](spec-integrator/)（submodule）を採用しています。

---

## 1. 段階的クイックスタート (Staged Operations)

目的に応じて最適なレベルのコマンドを実行することで、無駄な全件監査や待機時間を排除します。

| レベル | タイミング | 実行コマンド | コスト・所要時間 |
| :--- | :--- | :--- | :--- |
| **Level 0** (秒速) | 日常の編集・個別コード確認 | `uv run python docs/components/.../concepts/*_concept.py` | 0円 / 0.1秒〜数秒 |
| **Level 1** (Pre-Commit) | コミット前の静的整合性確認 | `powershell tools/run_all_tests.ps1 -sync`<br>`powershell tools/run_all_tests.ps1` | 0円 / 5〜10秒 |
| **Level 2** (Milestone) | 仕様変更・ADR 追加時の意味監査 | `powershell tools/run_all_tests.ps1 -assess -backend sakura`<br>`powershell tools/run_all_tests.ps1 -llm -backend sakura` | 無料 / 30秒〜1分 |
| **Level 3** (Release) | PR・リリース前の完全全量監査 | `powershell tools/run_all_tests.ps1 -full -backend sakura` | 完全パス確認用 |

### PowerShell (Windows)
```powershell
# Level 1: コミット前同期 & 高速静的チェック
powershell tools/run_all_tests.ps1 -sync
powershell tools/run_all_tests.ps1

# Level 2: さくら Qwen 3.6 による意味監査
powershell tools/run_all_tests.ps1 -assess -backend sakura
powershell tools/run_all_tests.ps1 -llm -backend sakura

# Level 3: 全量完全監査（リリース判定用）
powershell tools/run_all_tests.ps1 -full -backend sakura
```

### Bash (Linux / macOS / WSL)
```bash
# Level 1: コミット前同期 & 高速静的チェック
./tools/run_all_tests.sh --sync
./tools/run_all_tests.sh

# Level 2: さくら Qwen 3.6 による意味監査
./tools/run_all_tests.sh --assess --backend sakura
./tools/run_all_tests.sh --llm --backend sakura

# Level 3: 全量完全監査（リリース判定用）
./tools/run_all_tests.sh --full --backend sakura
```

---

## 2. 実行順序 (Phase Ordering)

| Phase | コマンド | 役割 | 省略時 |
| :---: | :--- | :--- | :--- |
| 1 | `assess` | **何を検証すべきか**を決定し、義務台帳（`doc_risk_report.json`）を出力 | 保存済み台帳を再利用 |
| 2 | `judge` | 意味的整合性・ADR のセマンティック監査 | 保存済みレポートを再利用 |
| 3 | `concepts / bench` | Python 概念コード・実測ベンチマーク・ARM エミュレータの実行 | 常に実行 |
| 4 | `check` | **8 つの品質ゲート**。Phase 1〜3 の結果を包括して最終合否を判定 | 常に実行 |

---

## 3. 品質ゲート (Quality Gates)

`spec-integrator check` により以下 8 ゲートを監査します（エラー検知時は終了コード 1 で CI が失敗）。

1. **Format Gate**: Markdown リンク切れ、アンカー切れの検知
2. **Traceability Gate**: 未定義キーワードの参照、未参照要件の検知
3. **Hierarchy Gate**: Tier（0〜3）間の逆流依存・カプセル化違反の検知
4. **Formal Gate**: `formal/*.py` の pyModelChecking 実行、妥当性監査、および `BACKS` 双方向照合
5. **WIT Gate**: `wit/*.wit` の構文・型安全性・エラー回復契約の検証
6. **Evidence Gate**: `<!-- evidence: ... -->` 宣言ファイルの実在性と未裏付け主張の検知
7. **Obligation Gate**: リスク評価から導出された全検証義務の 100% 充足（Discharge）監査
8. **Consistency Gate**: `spec-consistency.lock` との差分・波及漏れの検知
*(Topology Verifier)*: 循環依存のない静的メッセージングトポロジーの保証

ゲート 4/6/7 は「検証に失敗したこと」ではなく **「検証すべきものをやらなかったこと」** を落とすためのものです。
ゲート 8 は **「直したつもりで直っていないこと」** を落とすためのものです。
形式検証モデルが満たすべき契約は [spec-integrator/docs/formal_model_contract.md](spec-integrator/docs/formal_model_contract.md) を参照。

---

## 4. 修正漏れの検知 (Consistency Gate)

仕様変更で最も高くつくのは「影響範囲の特定」です。Consistency Gate は 3 つの独立した機構でこれを機械化します。

| ルール | 検知する状況 | 設定 |
| :--- | :--- | :--- |
| `CONSIST-SYMBOL-DRIFT` | 同一シンボルが場所によって違う値を持つ（例: `FB_CONF_JIT_CACHE_SIZE` が仕様書で 6144、ヘッダで 4096） | **不要**（`FB_CONF_*` を自動追跡） |
| `CONSIST-COCHANGE-STALE` | キーワードの**定義**を変更したのに、それを**参照**する節が旧記述のまま | **不要**（`{Keyword}` の既存トレーサビリティを流用） |
| `CONSIST-STALE-VALUE` | 移行済みの旧値が残存（例: 3面化したのに `2KB x 2` / `ダブルバッファ` が残る） | `spec-integrator.yaml` の `consistency.invariants` |

値の表記ゆれ（`6144` / `6KB` / `6.0 KB` / `6144バイト` / `0x1800`）は正規化して比較されます。
`inc/**/*.hxx` も走査対象なので、**仕様書とコードの定数ズレも同じ仕組みで落ちます**。

### 運用フロー

```bash
./tools/run_all_tests.sh --clean          # 1. 直す → 漏れが列挙される
```
```bash
./tools/run_all_tests.sh --sync           # 2. 全部直したら基準を更新
```

`spec-consistency.lock` は**コミットしてください**。`check` はこれと比較して「伝播しなかった編集」を見つけます。
`sync` をパイプラインに組み込んでいないのは意図的です — 自動更新すると、漏れを暴くための記録そのものが消えるためです。

---

## 4. 設定ファイル

リポジトリルートの `spec-integrator.yaml` にて、Tier の正規表現パス、LLM バックエンド、モデル名、品質ゲートの有効/無効を設定できます。

---

## 5. レポート成果物 (reports/)

検証実行時に以下の成果物が `reports/` ディレクトリ配下に自動出力されます：

- `reports/doc_report.md`: 7ゲートの監査レポート（違反一覧・プロパティ単位の形式検証結果・検証義務の充足状況）
- `reports/doc_graph.json`: ドキュメント全体のトポロジー・依存関係グラフ
- `reports/doc_risk_report.md`: コンテンツ複雑度・設計リスク・形式検証トリアージレポート
- `reports/doc_risk_report.json`: **検証義務台帳**。`check` の Obligation Gate が消費する（文書ハッシュを含み、陳腐化を検知する）
- `reports/doc_judge_report.json`: LLM as a Judge による意味的一貫性監査レポート

これらのレポートは検証の**入力**でもある。手で編集してはならない。
