---
name: Axiomatic Interface Design
description: >-
  MDAの概念を応用し、WIT（PIM）からC++（PSM）への変換において公理的意味論（Hoare論理）を用いた
  厳密な設計・実装・テストの導出手順を定義する。
WHEN: コンポーネント設計, WIT定義, インターフェイス実装, テストケース生成
SCOPE: アーキテクチャの整合性、不変条件からの定数導出、契約による検証
---

# 公理式インターフェース設計スキル

本スキルは、MDA（モデル駆動アーキテクチャ）の思想に基づき、WIT IDLを「独立モデル(PIM)」、C++ヘッダを「特定モデル(PSM)」と位置づけ、その変換プロセスに「公理的意味論」を導入することで、エージェントの実装精度を極限まで高めるものである。

## 1. 核心概念：Hoare Triple

すべてのメソッド実行を以下の公理式（Hoare Triple）として定義する。

$$ \{Pre\} 	ext{ Method } \{Post\} $$
$$ 	ext{subject to } [Invariant] $$

- **$\{Pre\}$**: 呼び出し側が保証すべき条件。実装冒頭の `FB_ASSERT` に変換される。
- **$\{Post\}$**: 実装側が保証すべき結果。単体テストの `EXPECT_*/ASSERT_*` に変換される。
- **$[Invariant]$**: オブジェクトの全ライフサイクルで維持されるべき状態の憲法。

## 2. MDA変換ルール

### 2.1 型と制約からの実体導出
不変条件（`@inv`）を解析し、パラメータの性質を決定する。

| 不変条件のパターン | 導出される PSM (C++) の性質 |
| :--- | :--- |
| `prop` is immutable after boot | `const` メンバ、または `constexpr` 化の検討 |
| `prop` <= `CONSTANT_MACRO` | 固定長配列（`std::array`）のサイズ決定 |
| `ptr` != 0 | 参照型（`T&`）または `not_null` ポインタの採用 |

### 2.2 契約からの実行時検証コードの生成
生成ツール（`wit_to_cpp.py`）は、WITの注釈を以下のC++構造に変換しなければならない。

```cpp
// PIM: @pre: size > 0
// PSM (Generated Interface Comment):
/** @note Pre-condition: FB_ASSERT(size > 0) */

// Implementation Pattern (Agent's Task):
operation_result Concrete::method(uint32_t size) {
  FB_ASSERT(size > 0); // 公理的意味論に基づくガード
  // ... logic ...
}
```

## 3. テストの自動導出テクニック

エージェントは、WITの契約から以下のテストケースを機械的に抽出する。

1.  **境界値テスト**:
    - `{Pre}` を満たす最小/最大の値。
    - `{Pre}` をわずかに外れる値（`FB_ASSERT` がトリガーされることの確認）。
2.  **正当性テスト**:
    - メソッド実行後、内部状態が `{Post}` と一致するかを検証。
3.  **不変条件テスト**:
    - メソッド実行前後で `[Invariant]` が破壊されていないかを常時監視。

## 4. エージェント用チェックリスト

- [ ] すべての `resource` に、状態の憲法となる `@inv` が記述されているか。
- [ ] すべての `func` に、境界を定める `@pre` と結果を約束する `@post` があるか。
- [ ] `@inv` から導出できる定数を、安易に変数（`std::vector`等）にしていないか。
- [ ] テストコードは、WITの `{Pre}` と `{Post}` を網羅しているか。
