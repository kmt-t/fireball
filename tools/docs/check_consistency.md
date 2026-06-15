# Check Consistency - コンポーネント仕様整合性チェッカー

コンポーネント仕様書間の形式的整合性と意味的一貫性を検証するスクリプト。 `{META_AI_Native_Dev}` `{META_Risk_Tiering}`

判定基準の正本は `.claude/rules/development-policy.md` と `.claude/rules/documentation_format.md` に置く。

---

## 1. コンセプト

Fireball プロジェクトでは、複数の仕様書（`docs/components/` 配下）が要求仕様書（`requirement_list.md`）の要求キーワード `{Keyword}` を参照している。本スクリプトは、以下の多角的な検証を通じて、仕様書群の一貫性を保証する：

- **F (FORMAT)**: 仕様書フォーマット規約準拠
- **T (TRACEABILITY)**: 要求キーワード整合性
- **A (ARCHITECTURE)**: API 表記ゆれ検出
- **LLM**: テーマ一貫性と明示的な矛盾の確認（オプション）

---

## 2. 検証項目

### 2.1 機械的チェック（常時実行）

#### F グループ: フォーマット規約

| 項目 | 検証内容 | 検出ケース |
| :--- | :--- | :--- |
| **F1** | 見出し規約 | `####` セクション見出しが C++ 識別子（バッククォート囲み）で始まっていない |
| **F2** | コードブロック禁止 | `docs/components/` 内で ````cpp`, `````js` など言語指定コードブロックを検出 |
| **F3** | Mermaid 記法の強制 | 図表が Mermaid 以外（PlantUML 等）で記述されている、または言語タグが漏れている |

#### T グループ: トレーサビリティ

| 項目 | 検証内容 | 検出ケース |
| :--- | :--- | :--- |
| **T1** | 未定義キーワード | コンポーネント仕様書が `requirement_list.md` に未定義の `{Keyword}` を参照 |
| **T2** | キーワード利用率 | `requirement_list.md` のキーワードがいずれかの仕様書から参照されていない（警告） |
| **T3** | コンポーネント網羅性 | キーワードがコンポーネント仕様書から参照されていない（警告） |

#### A グループ: アーキテクチャ整合性

| 項目 | 検証内容 | 検出ケース |
| :--- | :--- | :--- |
| **A1** | API 表記ゆれ | Tier 1 公開 API が他の仕様書で `camelCase` / `kebab-case` / `snake_case` の混在 |

### 2.2 LLM チェック（オプション）

`consistency_checklist.csv` を用いた意味的一貫性検証。

- **仕様書ペア間のテーマ・責務の一貫性**
- **状態遷移・ライフサイクルの齟齬**
- **エラーハンドリング方針の明示的な矛盾**

---

## 3. 入出力

### 3.1 入力

- `docs/requires/requirement_list.md` - 要求仕様書
- `docs/components/**/*.md` - コンポーネント仕様書群
- `docs/components/spec_matrix.csv` - 仕様書 × キーワード行列（`--llm` / `--gentable` 時）
- `docs/components/consistency_checklist.csv` - LLM チェックリスト（`--llm` 時）

### 3.2 出力

| ファイル | 生成タイミング | 内容 |
| :--- | :--- | :--- |
| `docs/components/spec_matrix.csv` | 常時 / `--gentable` | コンポーネント仕様書 × 要求キーワード 行列 |
| `docs/components/consistency_checklist.csv` | `--gentable` | LLM 用チェックリスト（仕様書ペア、共有キーワード、検証項目を列挙） |
| コンソール出力 | 常時 | F/T/A 検査結果、エラー・警告一覧 |

---

## 4. 使用方法

### 4.1 基本実行

```bash
./tools/scripts/check_consistency/run.sh
```

**実行内容:**
1. 機械的チェック F/T/A グループ
2. 結果をコンソール出力
3. `spec_matrix.csv` を生成

### 4.2 LLM チェック

```bash
./tools/scripts/check_consistency/run.sh --llm
```

前提: `docs/components/consistency_checklist.csv` が存在する（`--gentable` で事前生成）

### 4.3 テーブル再生成

```bash
./tools/scripts/check_consistency/run.sh --gentable
```

**実行内容:**
1. `spec_matrix.csv` 再生成
2. LLM に問い合わせ → `consistency_checklist.csv` 生成
3. 終了

### 4.4 オプション組み合わせ

```bash
# テーブル生成後、LLMチェック実行
./tools/scripts/check_consistency/run.sh --gentable --llm

# 特定モデル指定
./tools/scripts/check_consistency/run.sh --llm --model gpt-oss-120b

# 詳細ログ
./tools/scripts/check_consistency/run.sh --verbose --debug
```

---

## 5. 環境設定

### 5.1 LLM バックエンド選択（優先順位順）

1. `SAKURA_AI_API_KEY` 設定 → **Sakura AI** (gpt-oss-120b)
2. `OPEN_ROUTER_API_KEY` 設定 → **OpenRouter** (google/gemma-4-31b-it)
3. いずれもなし → **Ollama** (localhost:11434, qwen2.5-coder:3b)

```bash
export SAKURA_AI_API_KEY="your-key"
# または
export OPEN_ROUTER_API_KEY="your-key"
```

---

## 6. 検証アルゴリズム

### 6.1 Spec Matrix 生成

```
FOR each component file in docs/components/
  FOR each {Keyword} in file
    spec_matrix[file][keyword] += 1
  ENDFOR
ENDFOR

Output: spec_matrix.csv (components × keywords)
```

### 6.2 F グループ検査

```
FOR each component file
  IF #### heading does NOT start with `identifier`
    → F1 ERROR
  IF code block with language tag detected
    → F2 ERROR
  IF figure is not Mermaid
    → F3 ERROR
ENDFOR
```

### 6.3 T グループ検査

```
# T1: 未定義キーワード
known_keywords = extract_keywords(requirement_list.md)
FOR each component file
  found_keywords = extract_keywords(file)
  IF found_keyword NOT IN known_keywords
    → T1 ERROR
  ENDFOR

# T2/T3: 利用率警告
(詳細: コンソール出力参照)
```

### 6.4 A グループ検査

```
tier1_apis = extract_public_apis(Tier 1 specs)
snake_case_apis = normalize_to_snake_case(tier1_apis)

FOR each component file in Tier 2/3
  found_apis = extract_api_calls(file)
  normalized = normalize_to_snake_case(found_apis)
  IF normalized NOT MATCH snake_case_apis
    → A1 WARNING (表記ゆれ)
  ENDFOR
```

### 6.5 特殊キーワード（META_ / GLOBAL_）の処理仕様

本ツールでは、キーワードのプレフィックスに基づいて、検証時の挙動を自動的に制御する。

1. **メタキーワード (`META_` プレフィックス)**:
   - 階層間トレーサビリティ検証（親子マッチング）および、仕様書ペア間一貫性チェックリスト（`consistency_checklist.csv`）の自動生成対象から**常に除外**される。
   - これらは開発規約や制御用のマーカーであるため、純粋な仕様整合性の文脈では評価されない。
2. **グローバルキーワード (`GLOBAL_` プレフィックス)**:
   - 仕様書単体の要求適合性チェック（`S-TRACE-ALIGN`）の検証対象には**含められる**（各コンポーネントがグローバルポリシーを遵守しているか検証するため）。
   - 仕様書ペア間整合性チェックリスト（`consistency_checklist.csv`）の自動生成対象からは**除外される**。これにより、ファイルペア間での無駄な重複チェック（ノイズ）の発生を防止する。

---

## 7. 出力フォーマット

### 7.1 コンソール出力例

```
F グループ (フォーマット規約)
  ✓ F1: C++ 識別子見出し → OK
  ✓ F2: C++ コードブロック → OK
  ✓ F3: Mermaid 図表 → OK

T グループ (トレーサビリティ)
  ✗ T1: 未定義キーワード検出
    - docs/components/core/os_coos.md: {UnknownKeyword} は未定義
  ⚠ T2: キーワード利用率
    - {Unused_Keyword_A}: どの仕様書からも参照されていません

A グループ (アーキテクチャ)
  ⚠ A1: API 表記ゆれ
    - os_coos → os-coos の表記ゆれが検出されました
```

### 7.2 Spec Matrix CSV フォーマット

```csv
Component File,{Keyword1},{Keyword2},{Keyword3},...
docs/components/core/os_coos.md,1,0,1,...
docs/components/core/system_config.md,0,2,0,...
...
```

### 7.3 Consistency Checklist CSV フォーマット

```csv
pair_id,file_a,file_b,shared_keywords,check_num,aspect,check_content,llm_result,llm_reason
1,os_coos.md,runtime_interpreter.md,"{CooperativeMultitasking}",1,テーマ一貫性,"...",PASS/FAIL,"..."
...
```

---

## 8. トラブルシューティング

### LLM チェックが実行されない

```
Error: consistency_checklist.csv not found
```

→ 先に `--gentable` を実行してください：
```bash
./tools/scripts/check_consistency/run.sh --gentable
```

### API キー認証エラー

```
Error: LLM API Error (HTTP 401)
```

→ 環境変数を確認：
```bash
echo $SAKURA_AI_API_KEY  # 空なら OPEN_ROUTER 等を設定
```

---

## 9. 参考実装

このツールのロジックと LLM バックエンド実装は、`tools/scripts/check_consistency/check_consistency.py` に記述されている。
