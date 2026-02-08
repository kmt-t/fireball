---
name: Fireball Architecture
description: Fireballプロジェクト固有のアーキテクチャパターン、コーディング規約、設計原則
---

# Fireball アーキテクチャスキル

本プロジェクト（Fireball）の設計・実装において遵守すべき構造的ルールとパターン。

## 1. コア原則

### メモリ効率最優先 `{Policy_Memory}`
- **RAM 64KB** の制約下で動作
- ヒープメモリの使用を最小化
- メモリパーティション設計によるヒープの隔離

### 静的解決優先 `{Static_Resolution}`
- 可能な限りコンパイル時に計算・検証を完結
- `constexpr`, `consteval`, `static_assert` 活用
- 動的な型消去が必要な場合は静的バッファ使用

### 型安全性 `{TypeSafety}`
- `void*` 禁止
- DTOによる構造化データの明示
- インターフェイス境界での型の明記

## 2. アーキテクチャ原則

### 制御の反転 (IoC) `{IoC}` `{CleanArchitecture}`
- インターフェイス仕様は**利用側（内側の層）**が定義する
- 実装側への依存を逆転させ疎結合を実現する
- URIによるサービス識別とルックアップを行う
- サービスファサードによるIPC隠蔽を行う

**詳細**: [ioc.md](../../docs/orders/patterns/ioc.md)

### 3-Tier モジュール分離 `{3TierSeparation}`

システム複雑度に応じた抽象化レベルの選択：

| Tier | ドメイン | 分離方式 | 適用対象 |
|:---|:---|:---|:---|
| **Tier 1** | アーキテクチャ | IoC / URI-DI | システム境界（HAL/Kernel等） |
| **Tier 2** | サブシステム | Harness / Stateless IF | 複雑な内部構造 |
| **Tier 3** | 実装 | Natural OO | 単一責務・低複雑度 |

**詳細**: [interface.md](../../docs/orders/patterns/interface.md)

### UNIX哲学
- 単一責務の原則を徹底する
- シンプルさとコンポーザビリティを重視する
- グルーコード層は薄く保つ

## 3. 実装原則

#### Harness / Stateless Interface `{ComponentHarness}` `{StaticDI}`

**4要素の分解**:
1. **Harness (Dependencies)**: DIコンテナとして機能
2. **Data (Context)**: 実行時の可変状態（DTO）
3. **View (Immutable)**: 読み取り専用データのビュー
4. **Interface (Contract)**: 純粋仮想関数のみ（状態を持たない）

**原則**:
- インターフェイスは状態を持たない
- オブジェクトは内部状態（キャッシュ等）を持つ可能性がある
- コンテキストは引数で渡すこと

**詳細**: [harness.md](../../docs/orders/patterns/harness.md)

### Data/View 分離 `{DataViewSeparation}`
- **View**: ROM上のバイナリは `std::span` でビュー化（コピーしない）
- **Context**: 実行時の可変状態は `context` 構造体に集約

### RAIIによるリソース管理 `{RAII}`
- すべてのリソース解放はデストラクタに任せる
- 手動の `free()`, `unlock()` 呼び出しは禁止
- 例外安全（本システムではアボート安全）を確保する

## 4. モダンC++20の活用

- **Concepts**: 型制約の明示
- **Coroutines**: 非同期処理の簡潔な記述
- **std::span**: 境界チェック付き安全ビュー

---

# パターンカタログ

設計時に参照すべきパターン一覧：

| パターン | 目的 | ドキュメント |
|:---|:---|:---|
| **制御の反転** | システム境界での疎結合 | [ioc.md](../../docs/orders/patterns/ioc.md) |
| **3-Tier分離** | 複雑度に応じた分離方式 | [interface.md](../../docs/orders/patterns/interface.md) |
| **Harness設計** | Stateless IFとDI | [harness.md](../../docs/orders/patterns/harness.md) |
| **経済的な関数** | ヒープレス型消去 | [economic_function.md](../../docs/orders/patterns/economic_function.md) |
| **ソート済み配列** | `std::map`の代替 | [sorted_indexed_array.md](../../docs/orders/patterns/sorted_indexed_array.md) |
| **標準ライブラリ** | 許可・禁止ライブラリ | [stdlib.md](../../docs/orders/patterns/stdlib.md) |
