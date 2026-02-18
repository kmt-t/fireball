---
name: Embedded C++ Optimization
description: >-
  組み込み環境（RAM 64KB）における禁止/許可ライブラリ、コンテナ代替、メモリ管理パターン、エラーハンドリング。
  WHEN: C++実装, ライブラリ選定, コンテナ選択, メモリ戦略決定, エラー処理設計
  SCOPE: 実装レベルの技術判断。アーキテクチャ構造はfireball_architectureを参照。
  RELATED: fireball_architecture（構造設計）, cpp_linting（スタイル検証）, fireball_vocabulary（型エイリアス定義）
---

# Embedded C++ Optimization

## 1. 概要 (Overview)

リソース制約の厳しい環境（RAM 64KB等）で要求される特殊なC++実装技術と設計判断基準を定義します。

## 2. 環境・前提条件

本スキルで定義されるチェックツールを実行するには **Dockerコンテナ** の使用を強く推奨します。

- **Docker Workaround**: 詳細は [Docker Workaround](../docker_workaround/SKILL.md) を参照してください。
- **Windowsユーザー**: お使いの環境で直接実行するのではなく、**Git Bash** を経由してスクリプトを実行してください。

## 3. L1: 禁止・許可ライブラリ (Libraries)

### 禁止ライブラリ・機能

#### コンテナ
❌ **禁止**:
- `std::vector`, `std::map`, `std::unordered_map`
- `std::list`, `std::deque`
- `std::set`, `std::unordered_set`
- `std::string` (動的確保が必要な場合)

✅ **許可**:
- `std::array` (固定サイズ)
- `std::span` (ビュー)
- `std::string_view` (読み取り専用)
- Sorted Indexed Array パターン

#### スマートポインタ
❌ **禁止**: `std::unique_ptr`, `std::shared_ptr`, `std::weak_ptr`  
✅ **許可**: 静的ライフサイクル設計、独自 `Ref` 構造体

#### I/O・並行処理
❌ **禁止**: `<iostream>`, `<fstream>`, `<thread>`, `<future>`, `<exception>`

#### 型消去
❌ **禁止**: `std::function` (ヒープ確保の可能性)  
✅ **許可**: `economic_function<Capacity>`

### 許可される標準ライブラリ

- **基本**: `<cstdint>`, `<cstddef>`, `<limits>`, `<cassert>`, `<version>`, `<source_location>`
- **構造・型**: `<array>`, `<span>`, `<string_view>`, `<optional>`, `<variant>`, `<tuple>`, `<bitset>`, `<initializer_list>`
- **ロジック**: `<algorithm>`, `<utility>`, `<iterator>`, `<bit>`, `<compare>`, `<concepts>`, `<numbers>`
- **言語機能**: `<coroutine>`, `<type_traits>`, `<new>` (placement new目的のみ)

## 2. 自動チェックツール (Automated Check)

L1規則（禁止ライブラリ・機能）への準拠を自動的に検証するためのスクリプトが用意されている。

```bash
python3 .agent/skills/cpp_embedded/scripts/checker.py <ソースファイルまたはディレクトリ>
```

### 環境・実行 (Environment)
- **推奨**: VSCode DevContainer または Git Bash (Windows)。
- **コンテナ実行**: 環境が整っていない場合は、**[Docker Workaround](../docker_workaround/SKILL.md)** を参照してください。

## 3. 判断基準 (Decision Criteria)

### 3-Tier分離の選択
システムの複雑度に応じた適切な分離レベルを選択する。
- **Tier 1 (Architecture)**: システム間境界。URI-DI/IPC。
- **Tier 2 (Subsystem)**: 内部サブシステム。Harness/Static DI。
- **Tier 3 (Implementation)**: 単一モジュール。直接参照。

### インターフェイス分離の判断
- **YES**: 試験性（JIT/Interpreter切替）、モック必要、複数実装あり。
- **NO**: それ以外はYAGNI原則に従い直接実装。

### メモリ戦略の選択
- **constexpr 配列**: コンパイル時定数。
- **std::array**: 固定バッファ。
- **std::span<const T>**: ROM参照。
- **パーティション確保**: 動的リスト（実行時決定）。

### コンテナの選択
- **std::array**: 静的配列。
- **std::span**: ビュー。
- **sorted_indexed_array_map**: Key-Value検索（ROM）。

### 型消去の戦略
- **テンプレート化**: 型が静的に決定可能。
- **economic_function<N>**: コールバック（キャプチャ小）。
- **インターフェイス (Pure Virtual)**: 複数型の動的扱い。

### エラーハンドリングの戦略
- **assert / panic**: プログラミングエラー（回復不可）。
- **std::optional<T>**: リソース不足（回復可）。
- **status_code**: 入力検証失敗（回復可）。
- **coos::task<Result<T>>**: 非同期エラー（回復可）。

## 4. メモリ管理パターン (Memory Patterns)

### ポリシー・メモリ管理 `{Policy_Memory}`
- **原則**: ヒープ禁止、パーティション管理（`dlmalloc`, `mspace`）、隔離。
- **定石**: バンプアロケータ、配置new。

### 経済的な関数
`std::function` 代替のヒープレス・ラムダ活用技術。`economic_function<Capacity>` を使用し、SBOを強制する。

### コンテナ最適化
`std::map` の代替として、ソート済み配列（インデックスソート）と二分探索を組み合わせ、ROM上のデータを効率的に検索する。

## 5. エラーハンドリング・リカバリーパターン

### 公理的意味論に基づく契約設計
- **Pre-condition**: `FB_ASSERT`
- **Post-condition**: 戻り値、状態更新
- **Invariant**: クラス不変条件

### リカバリー戦略
`Result<T, E>` パラダイムを採用し、`E` を「リカバリー戦略」とする。
- **IGNORE**: 無視して続行。
- **RETRY**: バックオフ後に再試行。
- **RESTART**: モジュール再初期化。
- **PANIC**: システム停止。
