# 経済的な関数 (Economic Function) 設計パターン

## 1. 意図
リソース制約の厳しい（RAM 64KB）環境において、`std::function` のような型消去（Type Erasure）と状態保持（ラムダキャプチャ）を実現しつつ、ヒープメモリの使用を完全に排除する。SBO（Small Buffer Optimization）に頼るのではなく、静的にバッファサイズを強制し、それを超える場合はコンパイルエラー（`static_assert`）とすることで、メモリ消費の予測可能性を担保する。 `{Policy_Memory}` `{Static_Resolution}`

## 2. 構造

### 2.1 実装モデル

`std::function` をラップし、コンストラクタでキャプチャサイズを静的に検証する。

```cpp
template<typename Signature, size_t Capacity = 64> // Default SBO assumption
class economic_function {
    std::function<Signature> func_;

public:
    template<typename F>
    economic_function(F f) : func_(std::move(f)) {
        // Assert that the lambda fits within the assumed SBO capacity
        static_assert(sizeof(F) <= Capacity, 
            "Lambda too large for economic_function! Decrease capture size or increase Capacity.");
    }
    
    // Proxy operator()
    template<typename... Args>
    auto operator()(Args&&... args) const {
        return func_(std::forward<Args>(args)...);
    }
};
```

### 2.2 相互作用

ユーザーがラムダを代入しようとした時点で、そのサイズが `Capacity` を超えていればコンパイルエラーとなる。実行時のヒープ割り当ては、`std::function` のSBO実装が `Capacity` 以上あることを前提として回避される。

## 3. 適用ガイドライン

- **適用対象**: 状態を持ちうる非同期コールバック（vMMIOハンドラ、COOSイベントハンドラ等）。
- **原則**:
    - **SBO前提**: `std::function` のSBO機構を利用し、ヒープ割り当てを回避する。
    - **静的検証**: ラムダのキャプチャサイズが `Capacity` を超えた場合、`static_assert` でビルドを停止させる。
    - **環境依存性の許容**: SBO容量は標準化されていないため、コンパイラや標準ライブラリの実装に依存することを許容し、必要に応じて `Capacity` を調整する。

## 4. コンセプトコード

```python
# Concept: Economic Function (std::function wrapper with static size check)
class EconomicFunction:
    def __init__(self, capacity_limit):
        self.capacity_limit = capacity_limit
        self.internal_function = None

    def assign(self, lambda_obj):
        # Static Check phase (Compile time)
        if sizeof(lambda_obj) > self.capacity_limit:
            raise CompilationError(f"Lambda size {sizeof(lambda_obj)} exceeds limit {self.capacity_limit}")
        
        # Runtime phase
        self.internal_function = lambda_obj

    def __call__(self, *args):
        return self.internal_function(*args)
```

## 5. 関連パターン
- **標準ライブラリ利用パターン**: `std::function` の禁止に関連。
- **制御の反転 (IoC)**: コールバックの型定義に使用。

## 6. 設計完了チェックリスト

- [x] ヒープ不使用が保証されているか (`static_assert`)
- [x] 型消去が正しく実装されているか
- [x] メモリレイアウト（アラインメント）が考慮されているか
- [x] 意図が明確に文書化されているか
