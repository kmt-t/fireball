name: coding-standards-embedded
globs: ["src/**", "inc/**"]
instructions: |
  1. メモリ管理: ヒープ禁止(malloc/new禁止)。静的/スタック領域を使用すること。動的コンテナ禁止（std::vector等）。
  2. RAII: リソース管理はRAIIを厳守すること。
  3. 型安全: void* 禁止。型付きビュー（std::span等）を使用すること。
  4. 例外・RTTI: 例外(try/catch/throw)・RTTIの禁止。
  5. 組み込み仕様: RAM < 64KB かつ決定論的なスタックレス設計を維持する。
---
本ルールは、Fireballプロジェクトの組み込みC++実装のための厳格な制約を定義する。
