# Ollama Query Script

[Role & Axioms]
ホストOS上で稼働する Ollama API を通じて、ローカルモデル（phi3:mini 等）に推論を実行させるプロキシスクリプト。
不変条件: 出力は常に厳密な ATC (Axiomatic Task Contract) フォーマットであり、推論結果は `.agent/brain/` に `scope_target.atc` 形式で永続化される。

## Full-Spec Interface

```bash
python scripts/query_ollama.py [SCOPE] [INSTRUCTION] [options]
```

### Arguments
- `SCOPE`: タスクのスコープ。ファイル名（`scope_target.atc`）のベースになります（例: `phase0_review`）。
- `INSTRUCTION`: 実行する指示、または `-f` 使用時は解析対象のファイルパス。

### Options
- `-m, --model`: 使用する Ollama モデル名。デフォルトは `phi3:mini`。
- `-f, --file`: 第二引数 `INSTRUCTION` をファイルパスとして扱い、その中身を読み込んでコンテキストとして渡す。

## Composition (Pipe)

### STDIN (Standard Input)
- 端子出力がパイプ経由の場合、標準入力の内容をコンテキストとして自動的に読み込みます。

### Example: Piping build logs
```bash
cat build.log | python scripts/query_ollama.py product_build_fix "エラーの原因を TLA+ で特定せよ"
```

## Schema
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

## Recovery
- **Connection Error**: Ollama が起動していない場合に発生。サーバーの状態を確認してください。
- **Model Not Found**: 指定したモデルがローカルに存在しない。`ollama pull` で取得してください。
- **Empty Response**: モデルが停止シーケンスにより途中で終了した場合。
