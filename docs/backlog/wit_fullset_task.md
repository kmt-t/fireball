# WIT フルセット化とIPC自動生成対応タスク

現状の課題を盆栽的に整理し、段階的に詰めていくためのタスクリスト。

## フェーズ1: 現状調査と仕様の整合性確認 🔄

### 1.1 現状把握 (Status Investigation)
- [x] WITファイルの一覧確認
- [x] IPC関連の仕様確認 (`router.md`)
- [x] types.witの現状確認
- [x] ビットフィールド構造の確認
  - `router.md` (L39-41): `kv_pair` は型スコープ(8bit) + 識別キー(24bit) + 属性値(32bit)
  - `types.wit` (L34-37): 簡略化された `key-value` は key(u32) + value(shm-id)
- [x] 既存のコード生成ツールの発見と分析
  - `.agent/skills/code_generator/scripts/wit_to_cpp.py` - WIT→C++完全変換(Contract対応)
  - `.agent/skills/code_generator/scripts/wit_generator.py` - スケルトンのみ生成(未完成)
  - `.agent/skills/code_generator/scripts/generator.py` - JSON→C++生成
- [ ] 既存のC++ヘッダファイルとWIT定義の対応関係確認


### 1.2 仕様の齟齬発見 (Specification Gap Analysis)
- [x] `kv_pair` (router.md) と `key-value` (types.wit) の不一致を整理
  - 仕様書: 8+24+32 bit ビットフィールド構造
  - WIT定義: 32+32 bit 単純構造
- [x] 既存の自動生成ツールの調査
  - `wit_to_cpp.py`: 基本的なWIT→C++変換は完成しているが、ビットフィールド未対応
  - すべてのツールでビットフィールド構造の生成ができない
- [ ] ビットフィールド構造が必要な他のデータ構造を列挙
- [ ] WIT IDLで表現できない項目のリストアップ


## フェーズ2: WIT定義の refinement 🔴

### 2.1 基本型定義の確定
- [ ] プリミティブ型エイリアスのWIT表現確認
  - `address`, `byte_offset`, `byte_count`, `entry_count` 等
- [ ] 複合型エイリアスの表現方法検討
  - `binary_view` (std::span) をWITでどう表現するか
- [ ] `result<T, E>` 型のドメイン別定義の整理

### 2.2 IPC関連型定義の詳細化
- [ ] `kv_pair` のビットフィールド構造をWIT拡張で表現
  - Option 1: WIT recordに備考としてビットレイアウト情報を付与
  - Option 2: カスタムアノテーションで自動生成ヒントを追加
  - Option 3: 別途JSON/YAMLでビットフィールドメタデータを定義
- [ ] `message` 構造 (8個の `kv_pair` 配列) の定義
- [ ] `indexed_array_adapter` の表現検討
- [ ] `registry_entry` の完全な定義

### 2.3 各コンポーネントのWIT完成度チェック
- [ ] `hal.wit` - HAL周辺完全性確認
- [ ] `coos.wit` - COOS/スケジューラインターフェイス確認
- [ ] `services.wit` - IPC Router/Logger定義の詳細化
- [ ] `jit.wit` - JIT関連インターフェイス確認
- [ ] `memory.wit` - メモリ管理インターフェイス確認
- [ ] `vsoc.wit` - vSoCランタイムインターフェイス確認
- [ ] `syscall.wit` - システムコール定義確認

## フェーズ3: 自動生成ツールの設計と実装 🔴

### 3.1 wit_to_cpp.py の設計
- [ ] WIT → C++ 変換ルールの定義
  - `resource` → クラス/インターフェイス
  - `record` → struct
  - `enum` → enum class
  - `flags` → ビットフラグ型
- [ ] ビットフィールドのコード生成戦略
  - C++ bitfield vs. 手動ビット演算
  - constexpr ヘルパー関数の自動生成
- [ ] 型エイリアスマッピングテーブルの定義

### 3.2 Code Generator Skillの適用
- [ ] Code Generator Skillのパターン確認
- [ ] WIT → C++ 変換のメタデータ構造定義
- [ ] 生成スクリプトのテンプレート作成

### 3.3 ツール実装
- [x] WITパーサーの実装 (手動パーサー)
- [x] C++ヘッダ生成ロジックの実装
- [x] ビットフィールド構造の生成対応
- [x] constexpr アサーションの自動挿入 (static_assert)
- [x] コメント(Contract)の変換

## フェーズ4: 検証とリファインメント 🟡

### 4.1 自動生成されたコードの検証
- [ ] 生成されたヘッダファイルのコンパイル確認
- [ ] 命名規則の準拠チェック (snake_case, UPPER_SNAKE_CASE等)
- [ ] 型エイリアスの一貫性確認
- [ ] ビットフィールドサイズの静的アサーション確認

### 4.2 トレーサビリティマトリクスの作成
- [ ] WIT定義 → C++型 の対応表作成
- [ ] キーワードの表記ゆれチェック
- [ ] 要求からの導出関係確認

### 4.3 バックログへのフィードバック
- [ ] 未解決の設計判断の記録
- [ ] WIT仕様の制約による実装できない項目の記録
- [ ] 将来の拡張ポイントの記録

## バックログ候補 (Deferred Items)

> 全体を見て後で検討する項目。無理に今解決しない。

- ビットフィールドの packed attribute 対応
- WITアノテーション拡張のフォーマル定義
- 複数アーキテクチャ対応 (x86/ARM等)
- 逆方向生成 (C++ → WIT) の検討
- CI/CDへの自動生成フロー統合

## 次のアクション

1. フェーズ1の残タスク完了
2. 仕様の齟齬をユーザと確認
3. WIT定義の方向性をユーザと合意
