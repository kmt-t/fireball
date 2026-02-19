# Standard Library & Utilities

本ドキュメントは、組み込み環境において `std::` ライブラリの代わりに使用される、Fireball独自のユーティリティおよびパターンを定義する。 `{NoStdVector}` `{Policy_Memory}`

## 1. メモリ管理ユーティリティ

### `bump_allocator`
- **目的**: Loader や JIT メタデータ構築時の高速なメモリ割り当て。 `{BumpAllocator}`
- **特性**: 破棄時に全領域を一度に解放する。断片化が発生しない。
- **配置**: `inc/common/bump_allocator.hxx`

### `binary_view` / `mutable_binary_view`
- **目的**: `void*` を排除した型安全なメモリ参照。
- **基盤**: `std::span<const std::byte>` またはそのエイリアス。 `{PhysicalPassthrough}`

## 2. 制御・エラー処理

### `result<T, E>`
- **目的**: 例外を禁止した環境でのエラー伝播。 `{RecoveryStrategy}`
- **実装**: `EXPECTED` ライクな構造体。 `T` または `recovery_strategy` を保持する。

### `economic_function<Sig>`
- **目的**: `std::function` の代用。
- **特性**: ヒープ割り当てを行わず、関数ポインタと `void* context` のペアを型安全に保持する。

## 3. 検索・データ構造

### `indexed_array_adapter`
- **目的**: 元のデータの順序を変えず、索引配列のみをソート。 `{SortedIndexedArray}`
- **特性**: ROM上のWASMバイナリに対する二分探索などに使用。 `{BinarySearch}` `{AccessDictionary}`
