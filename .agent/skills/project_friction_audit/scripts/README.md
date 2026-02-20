# Friction & Traceability Audit Scripts

ドキュメントの整合性、表記揺れ、および要求仕様からのトレーサビリティを検証するためのツール群。

## 1. 役割と数学的性質
- **目的**: 設計ドキュメント内のキーワード表記を統一し、すべての要求 `{Keyword}` が適切に設計・実装へリンクされていることを保証する。
- **不変条件**:
    - `audit_friction.py`: 検出されたすべてのキーワードについて、公式リストとの編集距離を用いた類似判定を行い、タイポをゼロに近づける。
    - `check_traceability.py`: リンク漏れ（Missing Keywords）およびリンク切れ（Broken Links）を完全に抽出する。
- **影響範囲**: `docs/temp/` 下にレポートファイル Markdown形式 を生成する。

## 2. インターフェース

### [audit_friction.py](.agent/skills/project_friction_audit/scripts/audit_friction.py)
`python3 audit_friction.py [options] [path...]`
- **オプション (Options)**:
    - `-p, --stdin-paths`: STDIN からパスを読み込む。
    - `-j, --json`: JSON 形式で出力。
    - `--requirements <path>`: 指定された要求仕様リストを使用。

### [check_traceability.py](.agent/skills/project_friction_audit/scripts/check_traceability.py)
`python3 check_traceability.py [options] [path...]`
- **オプション (Options)**:
    - `-p, --stdin-paths`: STDIN からパスを読み込む。
    - `-j, --json`: JSON 形式で出力。

## 3. 使用方法

### パターンA: 総合監査 推奨
```bash
python3 .agent/skills/project_friction_audit/scripts/audit_friction.py docs/
```

### パターンB: キーワード網羅性の検証
```bash
python3 .agent/skills/project_friction_audit/scripts/check_traceability.py docs/architecture/ docs/components/
```

### パターンC: パイプ経由の特定ファイル監査
```bash
find docs/temp -name "*.md" | python3 .agent/skills/project_friction_audit/scripts/audit_friction.py
```

## 4. データ構造
レポートは Markdown 形式で出力され、各セクションには以下の項目が含まれます:
- `## <File Path>`: 違反が見つかったファイル。
- `Line <N>`: `{{Keyword}}` 形式またはその候補。
- `Status`: タイポの可能性、リンク切れ、または情報の劣化（Stale Reference）の別。
