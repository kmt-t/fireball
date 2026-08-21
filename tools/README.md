# Fireball Verification & Specification Tools

Fireball Hypervisor のドキュメント品質、静的トレーサビリティ、形式検証（pyModelChecking）、WIT インターフェース検証、および LLM as a Judge を実行する統合ツール環境です。

コア検証エンジンとして [spec-integrator](spec-integrator/)（submodule）を採用しています。

---

## 1. クイックスタート

### PowerShell (Windows)
```powershell
# 静的・形式・WIT 検証パイプラインの実行
powershell tools/run_all_tests.ps1 -clean

# 複雑度・設計リスク・形式検証トリアージ評価の実行
powershell tools/run_all_tests.ps1 -assess -backend sakura

# LLM as a Judge を有効化して実行
powershell tools/run_all_tests.ps1 -llm -backend sakura
```

### Bash (Linux / macOS / WSL)
```bash
# 静的・形式・WIT 検証パイプラインの実行
./tools/run_all_tests.sh --clean

# 複雑度・設計リスク・形式検証トリアージ評価の実行
./tools/run_all_tests.sh --assess --backend sakura

# LLM as a Judge を有効化して実行
./tools/run_all_tests.sh --llm --backend sakura
```

---

## 2. 品質ゲート (Quality Gates)

`spec-integrator check` コマンドにより、以下の 5 つのゲートを厳格に監査します（エラー検知時は終了コード 1 で CI が失敗）。

1. **Format Gate**: Markdown リンク切れ、アンカー切れの検知
2. **Traceability Gate**: 未定義キーワードの参照、未参照要件の検知
3. **Hierarchy Gate**: Tier（0〜3）間の逆流依存・カプセル化違反の検知
4. **Formal Gate**: 各コンポーネント配下 `docs/components/<tier>/formal/*.py` の pyModelChecking モデル実行
5. **WIT Gate**: 各コンポーネント配下 `docs/components/<tier>/wit/*.wit` の構文・構造検証

---

## 3. 設定ファイル

リポジトリルートの `spec-integrator.yaml` にて、Tier の正規表現パス、LLM バックエンド、モデル名、品質ゲートの有効/無効を設定できます。
