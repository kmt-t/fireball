# バックログアーカイブ

完了済みのバックログアイテムを記録する。参照目的のみ。

---

## Phase 0.7: Static DI & Build System [DONE]

- [x] **Harnessパターンの確定**: 全コンポーネントのハーネス設計
- [x] **静的DI機構**: テンプレート、マクロ、アロケータの連携方式
- [x] **WIT→C++自動生成（基本機能）**: コード生成スクリプトの基本実装
- [x] **CMakeビルドシステム**: 全ターゲット（ARM, RISC-V, x64 host）のビルド確認

---

## Phase 0.75: Constexpr Verification & Code Gen Enhancement [DONE]

- [x] **コード生成ツールのconstexpr対応**: WIT→C++生成時にconstexpr属性を付与
- [x] **constexprメソッド特定**: どのメソッドをconstexprにすべきか分類
- [x] **コンパイル時計算検証**: constexpr関数が実際にコンパイル時評価されるか確認
- [x] **ルックアップテーブル生成**: constexprによる静的テーブル生成の実証
