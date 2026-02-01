---
name: Embedded C++ Optimization
description: 組み込み環境（メモリ制約、ヒープ禁止、実行効率）におけるC++実装スキルの概要
---

# 組み込みC++最適化スキル

リソース制約の厳しい環境（RAM 64KB等）で要求される特殊なC++実装技術と標準ライブラリの利用制限の概要です。
詳細は各パターンドキュメントを参照してください。

## 1. ポリシー・メモリ管理 (Policy & Memory)

ヒープ使用の原則禁止と、必要な場合のパーティション管理について。

- **参照**: [docs/orders/patterns/stdlib.md](../../docs/orders/patterns/stdlib.md)
  - 利用可能な標準ライブラリ一覧
  - メモリ管理ポリシー (`dlmalloc`, `mspace`)

## 2. 経済的な関数 (Economic Function)

`std::function` 代替のヒープレス・ラムダ活用技術。

- **参照**: [docs/orders/patterns/economic_function.md](../../docs/orders/patterns/economic_function.md)
  - 固定サイズバッファによる配置
  - `static_assert` によるサイズ検証

## 3. コンテナ最適化

重いコンテナの回避と最適化された代替コンテナ。

- **参照**: [docs/orders/patterns/stdlib.md](../../docs/orders/patterns/stdlib.md)
  - `std::vector`, `std::map` の禁止と代替 (`std::span`, `std::string_view`)
- **参照**: [docs/orders/patterns/sorted_indexed_array.md](../../docs/orders/patterns/sorted_indexed_array.md)
  - `std::map` 代替のソート済み配列パターン
