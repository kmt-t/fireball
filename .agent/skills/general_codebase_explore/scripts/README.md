# Codebase Explorer

## 1. 概要 Overview

> [!CAUTION]
> **Self-Correction & Memory Discovery Protocol**:
> あなた（エージェント）は、このセッションより前に行われたアドホックな設計判断を「確実に忘れている」ことを前提に行動せよ。
> 自分の推論や直感に頼らず、まず本ドキュメントと `.agent/brain/*.atc` を読み込み、既にある論理的制約を「再発見」すること。
> 本ドキュメントは、記憶の揮発を前提とした「外付けの真実 (External Truth)」である。

## 2. 論理学的基盤 (Logical Foundation)

情報を要約 Abstraction し、述語論理の拡張としての**時相様相論理 Temporal Modal Logic** へ写像する。

- **$\exists$ Existence**: 定義の一意性と所在の証明。
- **$\Diamond$ Reachability/Liveness**: 外部からの可達性 結合・利用 。
- **$\square$ (Necessity)**: システムが常に満たすべき論理契約 (ATC/Safety)。

## 3. 推奨される実行方法 (Zero-Error Patterns)

Windows環境では、PowerShellのクオーティング問題を避けるため、必ず PowerShell から `bash` と入力して **WSL2 (Ubuntu)** シェルに入ってから作業を行え。

### A. Dockerコンテナ内での実行 (推奨)
コンテナ内の `clang` を利用して高精度なAST解析を行う場合に最適。
```bash
# 準備: コンテナの起動
bash .agent/skills/docker_workaround/scripts/docker-cmd.sh hostname

# 実行例: WAMRのAST解析 (JSON)
bash .agent/skills/explorer/docker-explorer.sh ast references/wamr/core/iwasm/include/wasm_export.h --json \
  -I references/wamr/core/iwasm/include \
  -I references/wamr/core/shared/utils

# 実行例: コンテキスト検索
bash .agent/skills/explorer/docker-explorer.sh context "StaticDI"
```

### B. ホスト（Git Bash）での実行
ドキュメントの検索や、ホスト側のPythonでの高速な解析に利用。
```bash
# Git Bash の絶対パス指定が必要な場合
"C:\Program Files\Git\bin\bash.exe" .agent/skills/explorer/scripts/explorer.sh summary docs/architecture/memory_map.md
```

## 4. Modal logic framework (Usage)
解析結果を以下の形式で出力・記録することを標準とする。これを転写 (Transcribe) するのはエージェント（AI）の責務である。

| 様相 | 意味 | 抽出方法 |
| :--- | :--- | :--- |
| **$\exists$ (Existence)** | 定義・所在 | `explorer.sh summary <path>` |
| **$\Diamond$ (Reachability)** | 利用・依存 | `explorer.sh callers <symbol>` |
| **$\square$ (Necessity)** | 不変条件/契約 | `explorer.sh context <Keyword>` および設計書解析 |

## 5. 時相論理への転写規則 Logic Transcription

`docs/*.md` 内の自然言語命題を以下の TLA+ 形式の論理式へ変換する。

| 自然言語パターン Intent | 時相論理式表現 | 意味論的分類 |
| :--- | :--- | :--- |
| 「常に〜である」「不変」 | `□inv: P` | 不変条件 Safety |
| 「〜が必要」「前提」 | `□(@pre: P)` | 必然的前置条件 Necessity |
| 「〜を返す」「〜を更新」 | `Input ⇒ ◊Output` | 活性・事後条件 Liveness |
| 「一意に定まる」 | `∃!x : P(x)` | 存在と一意性 |

## 6. 解析・生成プロセス

1.  **物理層 ($\exists, \Diamond$)**: `grep` による静的解析。
2.  **論理層 ($\square$)**:
    - `docs/components/` 内のキーワードをタイトルに含むセクション（Header）を特定。
    - セクション内容をトークナイズし、助動詞および量化子を解析。
    - 自然言語から LTL/ATC 形式への写像を行い、論理式を構成する。
