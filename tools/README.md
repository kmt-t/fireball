# Fireball Verification & Specification Tools

Fireball Hypervisor のドキュメント品質、静的トレーサビリティ、形式検証（pyModelChecking）、WIT インターフェース検証、および LLM as a Judge を実行する統合ツール環境です。

コア検証エンジンとして [spec-integrator](spec-integrator/)（submodule）を採用しています。

---

## 1. クイックスタート

### PowerShell (Windows)
```powershell
# 品質ゲートのみ（保存済みリスク評価を再利用）
powershell tools/run_all_tests.ps1 -clean

# リスク評価 → 品質ゲート
powershell tools/run_all_tests.ps1 -assess -backend sakura

# 全フェーズを全量で実行（リリース判定用）
powershell tools/run_all_tests.ps1 -full -backend sakura
```

### Bash (Linux / macOS / WSL)
```bash
# 品質ゲートのみ（保存済みリスク評価を再利用）
./tools/run_all_tests.sh --clean

# リスク評価 → 品質ゲート
./tools/run_all_tests.sh --assess --backend sakura

# 全フェーズを全量で実行（リリース判定用）
./tools/run_all_tests.sh --full --backend sakura
```

---

## 2. 実行順序 (Phase Ordering)

| Phase | コマンド | 役割 | 省略時 |
| :---: | :--- | :--- | :--- |
| 1 | `assess` | **何を検証すべきか**を決定し、義務台帳（`doc_risk_report.json`）を出力 | 保存済み台帳を再利用（無ければ Obligation Gate が失敗） |
| 2 | `judge` | 意味的整合性の監査 | 保存済みレポートを再利用 |
| 3 | `check` | **7つの品質ゲート**。Phase 1/2 の結論を消費する唯一の合否判定 | 常に実行 |

`check` を先に走らせてはならない。「検証すべき」と評価された項目が、既に合格を出した後に判明することになるため。

`assess` は既定で全セクションを評価しない限り失敗する（`--max-sections` を上げるか `--no-strict`）。
部分評価では未評価セクションの検証義務が不明のままになり、それは「合格」ではないため。

---

## 3. 品質ゲート (Quality Gates)

`spec-integrator check` により以下 7 ゲートを監査します（エラー検知時は終了コード 1 で CI が失敗）。

1. **Format Gate**: Markdown リンク切れ、アンカー切れの検知
2. **Traceability Gate**: 未定義キーワードの参照、未参照要件の検知
3. **Hierarchy Gate**: Tier（0〜3）間の逆流依存・カプセル化違反の検知
4. **Formal Gate**: `docs/components/<tier>/formal/*.py` の実行に加え、**モデル自体の妥当性**を監査
   （空虚な命題・到達不能状態・単一経路モデル・1モデルの二重計上）
5. **WIT Gate**: `docs/components/<tier>/wit/*.wit` の構文・構造検証
6. **Evidence Gate**: 「検証済み」「証明完了」「測定環境」等の主張が成果物に裏付けられているかの検証
7. **Obligation Gate**: リスク評価が要求した検証の未実施・評価の陳腐化の検知
8. **Consistency Gate**: **修正漏れ**（1つの事実が場所によって違う値を持つ／定義を直したのに参照側が旧記述のまま）の検知

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
