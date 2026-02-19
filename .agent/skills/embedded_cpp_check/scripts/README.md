# Embedded C++ Check Scripts

組み込み環境向けのC++コーディング規則（禁止ライブラリ、禁止パターン）を静的に検証するためのプログラム群。

## 1. 役割と数学的性質 (Role & Axioms)
- **目的**: [組み込みC++ルール](../../../rules/embedded_cpp_rule.md) で定義された禁止事項（ヒープ割り当て、例外、RTTI等）がコード内に混入していないかを検証する。
- **不変条件 (Invariants)**:
    - Exit code 0 は、スキャンされたすべてのファイルにおいて正規表現ベースで禁止パターンが「存在しない」ことを保証する。
    - コメントアウトされた行は誤検知を避けるために無視される。
- **影響範囲 (Side Effects)**: なし（読み取り専用スキャン）。

## 2. インターフェース (CLI & Interface)

### [check_embedded_rules.py](file:///w:/mysrc/fireball/.agent/skills/embedded_cpp_check/scripts/check_embedded_rules.py)
`python3 check_embedded_rules.py [options] [path...]`
- **引数 (Arguments)**:
    - `path`: 検証対象のファイルまたはディレクトリ。
- **オプション (Options)**:
    - `-r`, `--recursive`: ディレクトリを再帰的に探索する。

## 3. 使用方法 (Usage) サンプル

### パターンA: 直接実行
```bash
python3 .agent/skills/embedded_cpp_check/scripts/check_embedded_rules.py src/main.cxx
```

### パターンB: git との連携（変更されたファイルのみチェック）
```bash
git ls-files -m | grep "\.cxx$" | python3 .agent/skills/embedded_cpp_check/scripts/check_embedded_rules.py
```

### パターンC: find と xargs による一括実行
```bash
find inc -name "*.hxx" | xargs python3 .agent/skills/embedded_cpp_check/scripts/check_embedded_rules.py
```

## 4. エラーコード (Recovery)
- **0**: 違反なし。
- **1**: 1つ以上の規則違反を検出。出力メッセージに従い、安全な代替手段（`std::array`, `economic_function` 等）へリファクタリングせよ。
