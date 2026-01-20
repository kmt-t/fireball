# ソート済みインデックス付き配列のコンセプトコード

このドキュメントは、[`docs/oders/patterns/stdlib.md`](../../oders/patterns/stdlib.md) で定義された「std::map/std::unordered_map の代替手法」のコンセプトを Python で記述したものです。

## コンセプト

1.  **データの更新がなく想定される検索の回数が10回以上の場合**: 事前に Key でソートしておき、二分探索（`std::lower_bound` 相当）を行う。
2.  **データがROMにある、コピーコストが高い、または更新がある場合**: インデックス配列をソートし、検索時はインデックス配列を用いて二分探索を行う。

## サンプルコード (Python)

```python
import bisect

class SortedArrayMap:
    """
    パターン 1: Key-Value ペアの直接ソート
    データの更新がなく、検索回数が多い場合に使用。
    C++ では構造体の std::array または std::span に相当。
    """
    def __init__(self, data_dict):
        # Key でソートして保持
        self.data = sorted(data_dict.items())
        self.keys = [item[0] for item in self.data]

    def get(self, key):
        # std::lower_bound に相当する二分探索
        idx = bisect.bisect_left(self.keys, key)
        if idx < len(self.keys) and self.keys[idx] == key:
            return self.data[idx][1]
        return None

class IndexedArrayMap:
    """
    パターン 2: インデックス配列を用いたソートと検索
    要素が 100 要素以下の場合に使用。
    元のデータ（ROM 等）の順序を変えずに、検索用のインデックスのみを RAM 上に作成する。
    """
    def __init__(self, data_list):
        # 元のデータはそのまま保持
        self.data = data_list
        # データ配列を指すインデックス配列を Key でソート
        # C++ では std::array<uint8_t, N> 等の軽量な配列に相当
        self.indices = sorted(range(len(data_list)), key=lambda i: data_list[i][0])

    def get(self, key):
        # インデックス配列上で二分探索
        low = 0
        high = len(self.indices)
        
        while low < high:
            mid = (low + high) // 2
            # インデックスを介して元のデータの Key を比較
            if self.data[self.indices[mid]][0] < key:
                low = mid + 1
            else:
                high = mid
        
        if low < len(self.indices) and self.data[self.indices[low]][0] == key:
            return self.data[self.indices[low]][1]
        return None

# 使用例
if __name__ == "__main__":
    # サンプルデータ (Key, Value)
    raw_data = [
        ("uri_hal", 101),
        ("uri_vsoc", 102),
        ("uri_log", 103),
        ("uri_service", 104)
    ]

    print("--- SortedArrayMap ---")
    sa_map = SortedArrayMap(dict(raw_data))
    print(f"Lookup 'uri_vsoc': {sa_map.get('uri_vsoc')}")
    print(f"Lookup 'uri_none': {sa_map.get('uri_none')}")

    print("\n--- IndexedArrayMap ---")
    idx_map = IndexedArrayMap(raw_data)
    print(f"Lookup 'uri_log': {idx_map.get('uri_log')}")
    print(f"Lookup 'uri_none': {idx_map.get('uri_none')}")
```
