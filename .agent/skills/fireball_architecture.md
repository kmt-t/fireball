---
name: Fireball Architecture
description: Fireballプロジェクト固有のアーキテクチャパターン、コーディング規約、設計原則
---

# Fireball アーキテクチャスキル

本プロジェクト（Fireball）の設計・実装において遵守すべき構造的ルールとパターン。

---

## L1: 機械的ルール (常に自動適用)

### 1. 命名規則

| 対象 | 形式 | 例 |
|:---|:---|:---|
| Class / Struct / Enum / Function / Variable | `snake_case` | `ipc_router`, `send_message` |
| Const / Macro | `UPPER_SNAKE_CASE` | `MAX_BUFFER_SIZE` |
| Class Instance Member | `variable_` | `cache_size_` |
| Class Static Member | `variable__` | `instance_count__` |
| typedef | `type_name_t` | `size_t`, `callback_t` |

**ファイル**:
- ヘッダ: `inc/path/to/file.hxx`
- ソース: `src/path/to/file.cxx`

**禁止事項**:
- ❌ struct/class名に `_t` postfix を使用してはならない
- ❌ キャメルケース (`CamelCase`, `camelCase`) は使用してはならない

### 2. コードフォーマット

- **インデント**: スペース2つ（タブ禁止）
- **1行の長さ**: 最大100文字
- **波括弧**: K&R スタイル (Attach)
- **ポインタ・参照**: 左寄せ (`int* ptr`, `const T& obj`)
- **アクセス修飾子**: クラスの波括弧からインデントさせない

### 3. 必須実装規約

- **引数渡し**: クラス・構造体は `const T&` で渡す（値渡し禁止）
- **コンパイル時計算**: `constexpr`, `consteval`, `static_assert` を積極使用
- **型の明示**: `auto` は型が自明な場合のみ使用
- **void* 禁止**: 型安全性のため禁止、構造化データを使用

### 4. 共通ステータスコード

| 定数名 | 値 | 説明 |
|:---|:---|:---|
| `STATUS_OK` | 0 | 成功 |
| `STATUS_ERROR` | 1 | 一般エラー |
| `STATUS_NOT_FOUND` | 2 | 対象が見つからない |
| `STATUS_PERMISSION_DENIED` | 3 | 権限不足 |
| `STATUS_OUT_OF_MEMORY` | 4 | メモリ不足 |
| `STATUS_INVALID_ARGUMENT` | 5 | 引数不正 |

---

## L2: 設計原則


### 1. コア原則

#### メモリ効率最優先 `{Policy_Memory}`
- **RAM 64KB** の制約下で動作
- ヒープメモリの使用を最小化
- メモリパーティション設計によるヒープの隔離

#### 静的解決優先 `{Static_Resolution}`
- 可能な限りコンパイル時に計算・検証を完結
- `constexpr`, `consteval`, `static_assert` 活用
- 動的な型消去が必要な場合は静的バッファ使用

#### 型安全性 `{TypeSafety}`
- `void*` 禁止
- DTOによる構造化データの明示
- インターフェイス境界での型の明記

### 2. アーキテクチャ原則

#### 制御の反転 (IoC) `{IoC}` `{CleanArchitecture}`
- インターフェイス仕様は**利用側（内側の層）**が定義する
- 実装側への依存を逆転させ疎結合を実現する
- URIによるサービス識別とルックアップを行う
- サービスファサードによるIPC隠蔽を行う

**詳細**: [ioc.md](../../docs/orders/patterns/ioc.md)

#### 3-Tier モジュール分離 `{3TierSeparation}`

システム複雑度に応じた抽象化レベルの選択：

| Tier | ドメイン | 分離方式 | 適用対象 |
|:---|:---|:---|:---|
| **Tier 1** | アーキテクチャ | IoC / URI-DI | システム境界（HAL/Kernel等） |
| **Tier 2** | サブシステム | Harness / Stateless IF | 複雑な内部構造（3個以上の責務） |
| **Tier 3** | 実装 | Natural OO | 単一責務・低複雑度 |

**詳細**: [interface.md](../../docs/orders/patterns/interface.md)

#### UNIX哲学
- 単一責務の原則を徹底する
- シンプルさとコンポーザビリティを重視する
- グルーコード層は薄く保つ

### 3. 実装原則

#### Harness / Stateless Interface `{ComponentHarness}` `{StaticDI}`

**4要素の分解**:
1. **Harness (Dependencies)**: DIコンテナとして機能
2. **Data (Context)**: 実行時の可変状態（DTO）
3. **View (Immutable)**: 読み取り専用データのビュー
4. **Interface (Contract)**: 純粋仮想関数のみ（状態を持たない）

**原則**:
- インターフェイスは状態を持たない
- オブジェクトは内部状態（キャッシュ等）を持つ可能がある
- コンテキストは引数で渡す

**詳細**: [harness.md](../../docs/orders/patterns/harness.md)

#### Data/View 分離 `{DataViewSeparation}`
- **View**: ROM上のバイナリは `std::span` でビュー化（コピーしない）
- **Context**: 実行時の可変状態は `context` 構造体に集約

#### RAIIによるリソース管理 `{RAII}`
- すべてのリソース解放はデストラクタに任せる
- 手動の `free()`, `unlock()` 呼び出しは禁止
- 例外安全（本システムではアボート安全）を確保する

### 4. モダンC++20の活用

- **Concepts**: 型制約の明示
- **Coroutines**: 非同期処理の簡潔な記述
- **std::span**: 境界チェック付き安全ビュー

---

## パターンカタログ

設計時に参照すべきパターン一覧：

| パターン | 目的 | ドキュメント |
|:---|:---|:---|
| **制御の反転** | システム境界での疎結合 | [ioc.md](../../docs/orders/patterns/ioc.md) |
| **3-Tier分離** | 複雑度に応じた分離方式 | [interface.md](../../docs/orders/patterns/interface.md) |
| **Harness設計** | Stateless IFとDI | [harness.md](../../docs/orders/patterns/harness.md) |
| **経済的な関数** | ヒープレス型消去 | [economic_function.md](../../docs/orders/patterns/economic_function.md) |
| **ソート済み配列** | `std::map`の代替 | [sorted_indexed_array.md](../../docs/orders/patterns/sorted_indexed_array.md) |
| **標準ライブラリ** | 許可・禁止ライブラリ | [stdlib.md](../../docs/orders/patterns/stdlib.md) |

**フォーマット**: [FORMAT.md](../../docs/orders/patterns/FORMAT.md)
