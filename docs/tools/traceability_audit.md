# Traceability Audit - トレーサビリティ監査スクリプト

セクション × 要求キーワード の紐付けを検証し、設計漏れと矛盾を検出する。 `{AI_Native_Dev}` `{Risk_Tiering}`

---

## 1. コンセプト

Fireball の仕様書は、章節項（`##` / `###` / `####`）ごとに要求キーワード `{Keyword}` と紐付けられている。本スクリプトは、この紐付けの完全性を機械的に検証し、以下を検出する：

- **S1**: セクション → キーワード マッピング（正常系）
- **S2**: 出所不明セクション - キーワード紐付けのないセクション（要求から降りてきていない仕様）
- **S3**: 要求漏れ - セクション未紐付けのキーワード（仕様書に実装されていない要求）
- **L1**: 意味的不整合 - セクションとキーワードの意味の矛盾（LLM チェック）

---

## 2. 検証項目

### 2.1 機械的チェック（常時実行）

#### S2: 出所不明セクション

各コンポーネント仕様書のセクション（見出し）が、いずれかの `{Keyword}` と紐付けられているか検証。

```
各セクション に対して:
  IF セクション内に {Keyword} がない
    → S2 ERROR: 「このセクションは何の要求を実装していますか？」
```

**検出例:**
```
docs/components/core/os_coos.md
  ## 4. 内部実装詳細
    → キーワードなし → S2 ERROR
```

#### S3: 要求漏れ

`requirement_list.md` のすべてのキーワードが、いずれかのコンポーネント仕様書で参照されているか検証。

```
各 {Keyword} in requirement_list.md に対して:
  IF キーワードがどの仕様書からも参照されていない
    → S3 WARNING: 「この要求は実装されていません」
```

**検出例:**
```
{JIT_LazyChaining}
  → runtime/jit_runtime.md, jit/jit_compiler.md でも参照されていない
  → S3 WARNING: 要求実装漏れ
```

### 2.2 LLM チェック（オプション）

#### L1: 意味的不整合

セクションテキストとキーワード定義の意味的ズレを LLM で検証。

```
FOR each (section, keyword) pair:
  prompt = """
    セクション: {section_text}
    キーワド定義: {keyword_definition}
    このセクションはこのキーワードの要求を満たしていますか？
  """
  result = call_llm(prompt)
  IF result == "NO" or "PARTIAL"
    → L1 WARNING
```

---

## 3. データ構造

### 3.1 マッピングマトリックス

セクション × キーワードの紐付けを 2D テーブルで表現。

| Section ID | Section Heading | {Keyword1} | {Keyword2} | {Keyword3} | ... |
| :--- | :--- | :--- | :--- | :--- | :--- |
| core_os_coos_1 | 1. コンセプト | ✓ | | ✓ | |
| core_os_coos_2 | 2. 静的モデル | | ✓ | | |
| core_os_coos_3 | 3. 動的モデル | ✓ | ✓ | | |

**抽出ルール:**
- 見出しレベル: `##` (章) / `###` (節) / `####` (項)
- キーワード: セクション内の `{Keyword}` パターンを全抽出
- セクション ID: `{tier}_{component}_{number}` (自動生成)

---

## 4. 入出力

### 4.1 入力

- `docs/requires/requirement_list.md` - 要求仕様書
- `docs/components/**/*.md` - コンポーネント仕様書群

### 4.2 出力

| ファイル | 生成タイミング | 内容 |
| :--- | :--- | :--- |
| `docs/components/traceability_matrix.csv` | 常時 | セクション × キーワード マッピング行列 |
| `tmp/traceability_YYYYMMDD_HHMMSS.txt` | 常時 | コンソール出力ログ（検査結果） |
| コンソール出力 | 常時 | S2/S3 エラー・警告 |

---

## 5. 使用方法

### 5.1 基本実行

```bash
./tools/scripts/traceability_audit/run.sh
```

**実行内容:**
1. セクション抽出・キーワード紐付け
2. S2/S3 検査
3. マッピング行列（CSV）生成
4. 結果をコンソール出力 + ログファイル保存

### 5.2 LLM チェック

```bash
./tools/scripts/traceability_audit/run.sh --llm
```

S2/S3 に加えて、L1（意味的不整合）を検査。

### 5.3 詳細ログ表示

```bash
./tools/scripts/traceability_audit/run.sh --verbose
```

S1（正常系）マッピングの詳細を表示。

### 5.4 オプション組み合わせ

```bash
# LLM チェック + 詳細ログ
./tools/scripts/traceability_audit/run.sh --llm --verbose

# 特定モデル指定
./tools/scripts/traceability_audit/run.sh --llm --model gpt-oss-120b

# デバッグ出力（LLM 生レスポンス等）
./tools/scripts/traceability_audit/run.sh --debug
```

---

## 6. 環境設定

### 6.1 LLM バックエンド（優先順位順）

1. `SAKURA_AI_API_KEY` → Sakura AI (gpt-oss-120b)
2. `OPEN_ROUTER_API_KEY` → OpenRouter
3. なし → Ollama (localhost:11434, qwen2.5-coder:3b)

```bash
export SAKURA_AI_API_KEY="your-key"
```

---

## 7. 検証アルゴリズム

### 7.1 セクション抽出

```python
def extract_sections(doc_path):
  sections = []
  for heading in doc.findall(r"^(#{2,4}) (.+)$"):
    level = len(heading.group(1))
    title = heading.group(2)
    section_id = f"{tier}_{component}_{number}"
    
    # セクション開始からバッファ読み込み
    body = doc[heading.end : next_heading.start]
    keywords = re.findall(r"\{([A-Za-z0-9_]+)\}", body)
    
    sections.append({
      "id": section_id,
      "heading": title,
      "level": level,
      "keywords": keywords,
      "body": body
    })
  return sections
```

### 7.2 S2 検査（出所不明セクション）

```
FOR each section in all_components:
  IF len(section.keywords) == 0:
    → S2 ERROR: {section_id}: "{section.heading}"
```

### 7.3 S3 検査（要求漏れ）

```
all_found_keywords = set()
FOR each section in all_components:
  all_found_keywords |= set(section.keywords)

all_required_keywords = extract_keywords(requirement_list.md)

FOR each keyword in all_required_keywords:
  IF keyword NOT IN all_found_keywords:
    → S3 WARNING: {keyword}
```

### 7.4 L1 検査（意味的不整合）

```
FOR each (section, keyword) pair:
  prompt = f"""
    セクション: {section.heading}
    \n{section.body[:500]}
    
    キーワード定義: {keyword_definition}
    
    このセクション全体は、このキーワードの要求を満たしていますか？
    回答: YES / PARTIAL / NO
  """
  result = call_llm(prompt)
  
  IF result != "YES":
    → L1 WARNING: {section_id} ← {keyword}
```

---

## 8. 出力フォーマット

### 8.1 コンソール出力例

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Traceability Audit Results
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

S1: セクション ← キーワード マッピング
  ✓ core_os_coos_1: "1. コンセプト" ← {CooperativeMultitasking}
  ✓ core_os_coos_2: "2. 静的モデル" ← {CSPCommunication}
  ...
  Total S1: 127 mappings

S2: 出所不明セクション
  ✗ core_os_coos_5: "5. 内部実装詳細" (キーワードなし)
  ✗ interface_wit_2: "2. API定義補足" (キーワードなし)
  Total S2: 2 errors

S3: 要求漏れ
  ⚠ {UnusedKeyword_A}: コンポーネント仕様書から参照されていません
  ⚠ {UnusedKeyword_B}: ...
  Total S3: 3 warnings

L1: 意味的不整合（--llm 実行時）
  ⚠ core_runtime_3 ← {JIT_LazyChaining}: PARTIAL
    理由: セクションに言及がありますが、詳細な仕様が不足しています
  Total L1: 1 warning

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 8.2 Traceability Matrix CSV フォーマット

```csv
Section ID,Component,Heading,Level,{CooperativeMultitasking},{CSPCommunication},{BumpAllocator},...
core_os_coos_1,os_coos,"1. コンセプト",2,1,0,0,...
core_os_coos_2,os_coos,"2. 静的モデル",2,0,1,1,...
core_os_coos_3,os_coos,"2.1 データ構造",3,1,1,0,...
```

---

## 9. トラブルシューティング

### セクション抽出がうまくいかない

→ 見出しが `##` / `###` / `####` 形式か確認：
```markdown
## 2. 正しい見出し
=== 間違い: Setext 形式は非対応
```

### LLM チェックでタイムアウト

```
Error: LLM API timeout (120s exceeded)
```

→ `--model` で軽量モデル指定：
```bash
./tools/scripts/traceability_audit/run.sh --llm --model qwen2.5-coder:3b
```

---

## 10. 参考実装

このツールの実装は `tools/scripts/traceability_audit/traceability_audit.py` に記述されている。
