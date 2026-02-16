# Docker Workaround Scripts

Docker CLI経由でコンテナ内のツールを実行するヘルパースクリプト集。

**推奨**: [code_generator/workflows/](../../code_generator/workflows/) のメインワークフローを使用してください。

---

## スクリプト一覧

### docker-gen-wit.sh

WIT自動生成をコンテナで実行。

```bash
# WIT全体生成
bash docker-gen-wit.sh --all

# 単一ファイル
bash docker-gen-wit.sh wit/types.wit
```

**内部実行**:
```bash
python3 .agent/skills/code_generator/scripts/wit_to_cpp.py wit/ inc/gen
```

---

### docker-build.sh

Mesonビルドをコンテナで実行。

```bash
# 通常ビルド
bash docker-build.sh

# テスト付き
bash docker-build.sh --test

# クリーンビルド
bash docker-build.sh --clean

# ビルドディレクトリ指定
bash docker-build.sh build-arm --test
```

**内部実行**:
```bash
meson setup build
ninja -C build
meson test -C build  # --test時
```

---

## 前提条件

1. **Dockerコンテナが起動している**
   ```bash
   docker ps  # コンテナ確認
   ```

2. **Git Bash使用（Windows）**
   - PowerShellは非推奨

3. **プロジェクトルートで実行**
   ```bash
   cd /n/sources/fireball
   ```

---

## VSCode使用時

VSCodeでdevcontainerが動作している場合、これらのスクリプトは不要です。
VSCodeターミナルで直接実行してください:

```bash
# WIT生成
bash .agent/skills/code_generator/workflows/wit_all.sh

# ビルド
meson setup build
ninja -C build
```

---

## トラブルシューティング

### コンテナが見つからない

```bash
docker ps -a           # 全コンテナ確認
docker start <id>      # コンテナ起動
```

### パーミッションエラー

```bash
docker exec <id> bash -c "sudo chown -R developer:developer /workspaces/fireball"
```
