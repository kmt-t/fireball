---
description: >
  VDD品質検証ワークフロー。形式仕様・生成コード・コーディング標準の自動検証手順。
  WHEN: コード生成後, リリース前, /check_compliance
  RELATED: development_cycle（開発サイクル）, code_generator（WIT生成）, cpp_coding_style.md（規約）
---

# VDD Compliance Check

**Verification Driven Development** における品質検証手順。

---

## 検証レベル

```
Level 1: 形式仕様検証 → Level 2: 生成コード検証 → Level 3: 統合検証
```

---

## Level 1: 形式仕様検証

### 目的

形式仕様自体の正しさを検証。

### 検証項目

#### 1. TLA+モデル検査

```bash
cd tla/
tlc coos.tla
```

**チェック内容**:
- [ ] 不変条件 (TypeInvariant)
- [ ] デッドロック検出
- [ ] 時相論理プロパティ
- [ ] 網羅性

**合格基準**: `No error has been found`

#### 2. WIT構文検証

```bash
wasm-tools component wit wit/ --json > /dev/null
```

**チェック内容**:
- [ ] kebab-case識別子
- [ ] 予約語回避
- [ ] interface分離
- [ ] 依存関係解決

**合格基準**: エラー出力なし

#### 3. Contract整合性

**手動チェック**:
- [ ] @pre/@post の論理的整合性
- [ ] @inv の実現可能性
- [ ] 型制約の一貫性

---

## Level 2: 生成コード検証

### 目的

WITから生成されたC++コードの品質保証。

### 自動検証スクリプト

```bash
bash .agent/skills/code_generator/workflows/wit_check.sh
```

### 検証項目

#### 1. 禁止パターン検出

**スクリプト**: `check_violations.py`

```bash
python .agent/skills/code_generator/scripts/check_violations.py inc/gen
```

**チェック内容**:
- [ ] `void*` 型の使用
- [ ] `malloc/free/new/delete` の直接呼び出し
- [ ] `std::vector/map/string` 等の動的コンテナ
- [ ] `try/catch/throw` の使用
- [ ] `using namespace std;` の使用

**合格基準**: `[OK] No violations found`

#### 2. 命名規則検証

**スクリプト**: `check_naming.py`

```bash
python .agent/skills/code_generator/scripts/check_naming.py inc/gen
```

**チェック内容**:
- [ ] Type (struct/class/enum): `snake_case`
- [ ] Enum値: `UPPER_SNAKE_CASE`
- [ ] 関数/メソッド: `snake_case`
- [ ] using宣言: `snake_case`

**合格基準**: `[OK] All naming conventions correct`

#### 3. Contract埋め込み確認

**手動チェック**:
- [ ] @pre/@post がコメントに変換されているか
- [ ] @inv が適切に記述されているか

---

## Level 3: 統合検証

### 目的

生成コードが実際にビルド・実行可能かを検証。

### 検証項目

#### 1. ビルドテスト

```bash
bash .agent/skills/code_generator/workflows/wit_build.sh
```

**チェック内容**:
- [ ] Mesonセットアップ成功
- [ ] Ninjaビルド成功
- [ ] コンパイルエラーなし
- [ ] リンクエラーなし

**合格基準**: `[OK] Build successful`

#### 2. 単体テスト (オプション)

```bash
meson test -C build
```

**チェック内容**:
- [ ] 全テスト通過
- [ ] Contract違反なし

---

## 統合チェック（推奨）

### ワンコマンド実行

```bash
bash .agent/skills/code_generator/workflows/wit_all.sh
```

**実行内容**:
1. WIT → C++生成
2. Level 2検証（禁止パターン + 命名規則）
3. Level 3検証（ビルドテスト）

**出力例**:
```
[*] Generating C++ headers from WIT package...
[OK] Generation complete

[*] Running quality checks on generated code...
  [1/2] Checking prohibited patterns...
[OK] No violations found
  [2/2] Checking naming conventions...
[OK] All naming conventions correct

[OK] All quality checks passed!
```

---

## チェックリスト

### リリース前検証

#### Level 1: 形式仕様
- [ ] TLC検証通過
- [ ] WIT構文エラーなし
- [ ] Contract整合性確認

#### Level 2: 生成コード
- [ ] 禁止パターン: 0件
- [ ] 命名規則違反: 0件
- [ ] Contract埋め込み確認

#### Level 3: 統合
- [ ] ビルド成功
- [ ] テスト通過

### コーディング標準

詳細は以下を参照:
- [cpp_coding_style.md](../rules/cpp_coding_style.md) - コーディング規約
- [design.md](../rules/design.md) - 設計ルール
- [cpp_embedded](../skills/cpp_embedded/SKILL.md) - 組み込み最適化

---

## 違反検出時の対処

### Level 1失敗

```
TLC: デッドロック検出
  ↓
仕様を修正（TLA+）
  ↓
再検証
```

### Level 2失敗

```
違反検出: void*使用
  ↓
WIT仕様を修正
  ↓
再生成 + 再検証
```

**原則**: 生成コードを直接修正しない。仕様を修正して再生成。

---

## 自動化

### CI/CD統合

```yaml
# .github/workflows/vdd-check.yml
steps:
  - name: VDD Compliance Check
    run: |
      # Level 1
      tlc tla/*.tla
      wasm-tools component wit wit/ --json
      
      # Level 2-3
      bash .agent/skills/code_generator/workflows/wit_all.sh
```

---

## メトリクス

### 品質指標

| メトリクス | 目標 | 現状 |
|:---|:---:|:---:|
| 禁止パターン | 0件 | 0件 ✅ |
| 命名規則違反 | 0件 | 0件 ✅ |
| ビルド成功率 | 100% | 100% ✅ |
| TLC検証通過 | 100% | - |

---

## トラブルシューティング

### WIT構文エラー

```
error: expected kebab-case identifier
```

**解決**: `device_id` → `device-id`

### 禁止パターン検出

```
[ERROR] void* detected in inc/gen/types.hxx:42
```

**解決**: WIT仕様を修正してbinary_view使用

### ビルド失敗

```
error: undefined reference to `foo`
```

**解決**: WIT仕様でinterface/exportを確認

---

**VDD Compliance = 形式仕様から品質を保証**

詳細: [docs/concept/vdd_methodology.md](../../docs/concept/vdd_methodology.md)