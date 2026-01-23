# 標準ライブラリ利用パターン (改定案)

## 1. 意図 (Intent)
リソース制約の厳しい組み込み環境において、メモリ断片化や実行時オーバーヘッドを最小化しつつ、C++20のモダンな機能を安全に利用するためのガイドラインを定義する。

## 2. 構造 (Structure)

### 2.1 利用可能ライブラリ分類

| 分類 | 利用可能要素 | 禁止要素 |
| :--- | :--- | :--- |
| **コンテナ** | `std::array`, `std::span` | `std::vector`, `std::map`, `std::list` |
| **ランタイム** | コルーチン, `std::chrono` | `std::thread`, `std::filesystem` |
| **ユーティリティ** | `std::optional`, `std::variant` | 例外 (`try-catch`) |

### 2.2 共通ステータスコード (Status)
システム全体で統一して使用するステータスコード。

| 定数名 | 値 | 説明 |
| :--- | :--- | :--- |
| `STATUS_OK` | 0 | 成功 |
| `STATUS_ERROR` | 1 | 一般エラー |
| `STATUS_NOT_FOUND` | 2 | 対象が見つからない |
| `STATUS_PERMISSION_DENIED` | 3 | 権限不足 |
| `STATUS_OUT_OF_MEMORY` | 4 | メモリ不足 |
| `STATUS_INVALID_ARGUMENT` | 5 | 引数不正 |

## 3. 適用ガイドライン

### 3.1 メモリ管理ポリシー
- **動的確保**: `dlmalloc` の `mspace` を使用し、ヒープパーティションごとに隔離する。 `{Policy_Memory}`
- **アロケータ**: `new`/`delete` をオーバーロードし、コンパイル時に決定されたパーティションから確保する。 `{StaticDI}`
- **バンプアロケータ**: 解放が不要な一時的なメモリ確保にはバンプアロケータを優先する。

### 3.2 データアクセス
- **バイナリデータ**: `std::span` を用いて境界チェックを行い、不正アクセスを防止する。
- **文字列**: `std::string_view` を積極的に用い、コピーを避ける。

## 4. 設計完了チェックリスト（網羅性確認）

- [x] パターンの解決する問題（意図）が明確か
- [x] 利用可能・禁止ライブラリのリストが明示されているか
- [x] メモリ管理の方針がアーキテクチャと整合しているか
- [x] コンセプトコード（Python）が提供されているか

## 5. コンセプトコード

```python
# Concept of Memory Partitioning and Allocation Policy
class memory_partition:
    def __init__(self, name, size):
        self.name = name
        self.size = size
        self.used = 0

    def allocate(self, amount):
        if self.used + amount <= self.size:
            self.used += amount
            return True
        return False

class system_allocator:
    def __init__(self):
        self.partitions = {
            "kernel": memory_partition("Kernel", 8192),
            "guest": memory_partition("Guest", 24576)
        }

    def new_object(self, partition_name, size):
        partition = self.partitions.get(partition_name)
        if partition and partition.allocate(size):
            print(f"Allocated {size} bytes from {partition_name}")
            return True
        print(f"Allocation failed in {partition_name}")
        return False

# Usage
allocator = system_allocator()
allocator.new_object("kernel", 1024)
```

## 6. 関連パターン
- **ソート済みインデックス付き配列**: `std::map` の代替。
- **インターフェイス設計パターン**: DTOの定義。
