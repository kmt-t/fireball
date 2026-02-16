---
description: >
  VDD (Verification Driven Development) ワークフロー。形式仕様→検証→生成→品質保証の統合開発サイクル。
  WHEN: 新機能開発, コンポーネント設計, /development_cycle
  RELATED: bonsai_design（設計詳細度判断）, check_compliance（品質検証）, code_generator（自動生成）
---

# VDD Development Cycle

**Verification Driven Development** - 形式検証を中核とした開発サイクル。

---

## 開発フロー

```
Phase 1: 形式化 → Phase 2: 検証 → Phase 3: 生成 → Phase 4: 統合
    ↓              ↓              ↓              ↓
  WIT/TLA+       TLC検証       AI生成      品質保証
```

---

## Phase 1: 仕様の形式化

### ステップ

1. **要求分析**
   ```
   自然言語の要求 → 原理・原則の抽出
   ```

2. **形式仕様作成** (AI支援)
   - WIT: インターフェイス定義
   - TLA+: 状態遷移・不変条件
   - Contract: @pre/@post/@inv

3. **仕様レビュー** (人間)
   - 原則に合致しているか
   - 不変条件は適切か
   - 網羅性は十分か

### 成果物

- `wit/` - WIT仕様
- `specs/` - TLA+仕様（状態機械のみ）
- 設計ドキュメント

---

## Phase 2: 仕様の検証

### ステップ

1. **TLA+モデル検査**
   ```bash
   tlc scheduler.tla
   # 不変条件検証
   # デッドロック検出
   # 網羅性確認
   ```

2. **WIT構文検証**
   ```bash
   wasm-tools component wit wit/ --json > /dev/null
   ```

3. **Contract整合性確認**
   - @pre/@post の論理的整合性
   - @inv の実現可能性

### 合格基準

- ✅ TLC: No error found
- ✅ WIT: 構文エラーなし
- ✅ Contract: 矛盾なし

---

## Phase 3: 実装の生成

### ステップ

1. **コード自動生成**
   ```bash
   bash .agent/skills/code_generator/workflows/wit_gen.sh
   ```

2. **品質自動チェック**
   ```bash
   bash .agent/skills/code_generator/workflows/wit_check.sh
   ```
   - 禁止パターン検出 (void*, malloc等)
   - 命名規則検証 (snake_case等)

3. **生成結果確認**
   - 14ファイル生成完了
   - Contract埋め込み確認

### 合格基準

- ✅ 生成: 全ファイル成功
- ✅ チェック: 違反0件

---

## Phase 4: 統合検証

### ステップ

1. **ビルドテスト**
   ```bash
   bash .agent/skills/code_generator/workflows/wit_build.sh
   ```

2. **統合テスト** (オプション)
   - 単体テスト実行
   - 結合テスト実行

3. **最終レビュー**
   - 生成コードのSpot Check
   - ドキュメント整合性確認

### 合格基準

- ✅ ビルド成功
- ✅ テスト通過

---

## フェーズ遷移判断

### Phase 1 → 2

**条件**:
- [ ] WIT仕様作成完了
- [ ] TLA+仕様作成完了（状態機械の場合）
- [ ] Contract記述完了
- [ ] 人間レビュー完了

### Phase 2 → 3

**条件**:
- [ ] TLC検証通過
- [ ] WIT構文検証通過
- [ ] Contract矛盾なし

### Phase 3 → 4

**条件**:
- [ ] コード生成成功（14ファイル）
- [ ] 品質チェック通過（違反0件）

### Phase 4 → 完了

**条件**:
- [ ] ビルド成功
- [ ] テスト通過
- [ ] 最終レビュー完了

---

## 問題発生時の対処

### Phase 2で検証失敗

```
TLC: デッドロック検出
  ↓
Phase 1に戻る（仕様修正）
```

### Phase 3で品質チェック失敗

```
違反検出: void*使用
  ↓
Phase 1に戻る（WIT仕様修正）
```

### Phase 4でビルド失敗

```
コンパイルエラー
  ↓
Phase 1に戻る（Contract修正）
```

**原則**: 実装を直接修正しない。仕様を修正して再生成。

---

## ツール

### 形式仕様

- WIT編集: VSCode
- TLA+編集: VSCode + TLA+ extension

### 検証

- `tlc` - TLA+ Model Checker
- `wasm-tools` - WIT検証

### 生成・品質チェック

- `wit_gen.sh` - 生成
- `wit_check.sh` - 品質チェック
- `wit_build.sh` - ビルド
- `wit_all.sh` - 統合実行 ⭐

---

## 統合実行（推奨）

```bash
# Phase 3-4を一括実行
bash .agent/skills/code_generator/workflows/wit_all.sh

# 出力:
# [*] Generating C++ headers...
# [OK] Generation complete
# [*] Running quality checks...
# [OK] No violations found
# [OK] All naming conventions correct
# [*] Testing build...
# [OK] Build successful
```

---

## チェックリスト

### Phase 1: 形式化
- [ ] 原理・原則の抽出完了
- [ ] WIT仕様作成
- [ ] TLA+仕様作成（状態機械）
- [ ] Contract記述
- [ ] 人間レビュー

### Phase 2: 検証
- [ ] TLC検証通過
- [ ] WIT構文検証
- [ ] Contract整合性確認

### Phase 3: 生成
- [ ] コード生成成功
- [ ] 品質チェック通過

### Phase 4: 統合
- [ ] ビルド成功
- [ ] テスト通過
- [ ] 最終レビュー

---

**VDD = 検証可能性を中核とした開発手法**

詳細: [docs/concept/vdd_methodology.md](../../docs/concept/vdd_methodology.md)
