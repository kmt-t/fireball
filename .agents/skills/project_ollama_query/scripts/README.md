# Ollama Query Script

## 1. 役割と数学的性質
ホストOS上で稼働する Ollama API を通じて、ローカルモデル（phi3:mini 等）に推論を実行させるプロキシスクリプト。
不変条件: 出力は常に厳密な ATC Axiomatic Task Contract フォーマットであり、推論結果は `.agent/brain/` に `scope_target.atc` 形式で永続化される。

## 2. インターフェース

```bash
python3 .agent/skills/project_ollama_query/scripts/query_ollama.py [SCOPE] [INSTRUCTION] [FILES...]
```

### 引数
- `SCOPE`: タスクのスコープ。ファイル名 `scope_target.atc` のベースになります 例: `phase0_review`。
- `INSTRUCTION`: ローカルモデルに与える具体的な指示。
- `FILES`: 解析対象のファイルパス（複数指定可能）。省略した場合は標準入力の内容をコンテキストとして使用します。
- `-m, --model`: 使用する Ollama モデル名。デフォルトは `phi3:mini`。

## 3. 使用方法 パイプ連携

### 標準入力
- 端子出力がパイプ経由の場合、標準入力の内容をコンテキストとして自動的に読み込みます。

### Example: Piping build logs
```bash
cat build.log | python3 .agent/skills/project_ollama_query/scripts/query_ollama.py product_build_fix "エラーの原因を要約せよ"
```

## 4. データ構造
出力される `.atc` ファイルの構造:
```markdown
# Ollama Coagent Generated ATC
# Scope: [SCOPE]
# Model: [MODEL]
# Timestamp: [TIMESTAMP]

@pre: [Initial status]
@inv: [Invariants / Safety properties]
@post: [Final status / Conclusion]
```

## 5. エラーリカバリ
- **Connection Error**: Ollama が起動していない場合に発生。サーバーの状態を確認してください。
- **Model Not Found**: 指定したモデルがローカルに存在しない。`ollama pull` で取得してください。
- **Empty Response**: モデルが停止シーケンスにより途中で終了した場合。
