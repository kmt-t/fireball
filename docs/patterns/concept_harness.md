---
name: Concept-Based Harness Pattern
---

# Conceptベースハーネスパターン

Tier 2コンポーネントにおける依存性注入をゼロコストで実現するC++20 Conceptsベースの設計パターン。

## 概要

### 問題点: 仮想関数ベースの従来設計

WITリソースを単純にC++インターフェースとしてマッピングすると、すべてのメソッドが仮想関数（vtable経由）となり、以下の問題が発生する：

- **実行時オーバーヘッド**: vtableルックアップと間接呼び出し
- **メモリオーバーヘッド**: vtableポインタ（8バイト/オブジェクト）
- **インライン化不可**: 仮想関数呼び出しは最適化されにくい

### 解決策: Conceptベースハーネス

依存関係をConceptで定義し、テンプレート引数としてコンパイル時に注入することで、**ゼロコスト抽象化**を実現する。

- ✅ vtableなし（直接呼び出し）
- ✅ 完全なインライン化
- ✅ コンパイル時型安全性
- ✅ 継承・仮想関数不要

---

## パターン定義

### 1. WIT側: Method Injection

依存関係を `initialize` メソッドのパラメータとして明示的に注入する。

```wit
resource vsoc-runtime {
    /// Initializes runtime with injected dependencies
    /// @pre: All resource handles are valid
    /// @post: Runtime is ready for execution
    initialize: func(
        loader: wasm-loader,
        vmmio: vmmio-manager,
        memory: memory-manager
    ) -> operation-result;

    step: func() -> result<execution-state, recovery-strategy>;
}
```

**アンチパターン（従来型）**:
```wit
// ❌ Service Locator Pattern (依存が隠蔽される)
resource vsoc-runtime {
    initialize: func() -> operation-result;
    get-loader: func() -> wasm-loader;      // 依存関係が不明確
    get-vmmio: func() -> vmmio-manager;     // vtable必須
}
```

---

### 2. C++側: Concept定義

各コンポーネントが必要とする依存関係をConceptとして宣言する。

```cpp
// vsoc_runtime が要求するハーネスの要件
template <typename Harness>
concept vsoc_harness_policy = requires(Harness h) {
    { h.loader() } -> std::convertible_to<wasm_loader*>;
    { h.vmmio() } -> std::convertible_to<vmmio_manager*>;
    { h.memory() } -> std::convertible_to<memory_manager*>;
};

// jit_compiler が要求するハーネスの要件
template <typename Harness>
concept jit_harness_policy = requires(Harness h) {
    { h.detector() } -> std::convertible_to<hotspot_detector*>;
    { h.index() } -> std::convertible_to<jit_entry_index*>;
    { h.engine() } -> std::convertible_to<copy_and_patch_engine*>;
};
```

---

### 3. C++側: コンポーネント実装

テンプレート引数として Harness を受け取る。

```cpp
template <vsoc_harness_policy Harness>
class vsoc_runtime {
    Harness harness_;        // コンパイル時に型が確定
    execution_context ctx_;

public:
    operation_result initialize(
        wasm_loader* loader,
        vmmio_manager* vmmio,
        memory_manager* memory
    ) {
        // ハーネスに依存をセット（実行時）
        harness_.set_loader(loader);
        harness_.set_vmmio(vmmio);
        harness_.set_memory(memory);
        return operation_result::ok();
    }

    result<execution_state, recovery_strategy> step() {
        // Conceptで保証された型安全なアクセス
        // すべてインライン化可能
        auto* loader = harness_.loader();
        auto* vmmio = harness_.vmmio();
        
        // ... ロジック実装
    }
};
```

---

### 4. C++側: ハーネス実装（POD構造体）

継承・仮想関数を一切使わない単純な構造体。

```cpp
// Production用ハーネス
struct production_vsoc_harness {
    wasm_loader* loader_;
    vmmio_manager* vmmio_;
    memory_manager* memory_;
    
    // Conceptを満たすためのアクセサ（インライン化）
    wasm_loader* loader() const { return loader_; }
    vmmio_manager* vmmio() const { return vmmio_; }
    memory_manager* memory() const { return memory_; }
    
    void set_loader(wasm_loader* l) { loader_ = l; }
    void set_vmmio(vmmio_manager* v) { vmmio_ = v; }
    void set_memory(memory_manager* m) { memory_ = m; }
};

// Test用ハーネス（同じConceptを満たす）
struct test_vsoc_harness {
    mock_wasm_loader loader_mock_;
    mock_vmmio_manager vmmio_mock_;
    mock_memory_manager memory_mock_;
    
    wasm_loader* loader() { return &loader_mock_; }
    vmmio_manager* vmmio() { return &vmmio_mock_; }
    memory_manager* memory() { return &memory_mock_; }
    
    // Mockは内部で生成済みのため no-op
    void set_loader(wasm_loader*) {}
    void set_vmmio(vmmio_manager*) {}
    void set_memory(memory_manager*) {}
};
```

---

### 5. 使用例

```cpp
// Production環境
wasm_loader_impl loader;
vmmio_manager_impl vmmio;
memory_manager_impl memory;

vsoc_runtime<production_vsoc_harness> runtime;
runtime.initialize(&loader, &vmmio, &memory);
runtime.step();

// Test環境（全く同じインターフェース）
vsoc_runtime<test_vsoc_harness> test_runtime;
test_runtime.initialize(nullptr, nullptr, nullptr); // モックは内包
test_runtime.step();
```

---

## コンパイル時検証

Conceptにより、型制約違反がコンパイルエラーとなる。

```cpp
// ❌ コンパイルエラー：必要なメソッドがない
struct bad_harness {
    wasm_loader* loader() { return nullptr; }
    // vmmio() と memory() が欠けている
};

vsoc_runtime<bad_harness> rt;
// error: 'bad_harness' does not satisfy 'vsoc_harness_policy'
```

---

## 適用対象

### Tier 2コンポーネント（すべて）

- `vsoc_runtime` (`vsoc.wit`)
- `wasm_loader` (`vsoc.wit`)
- `jit_compiler` (`jit.wit`)
- `scheduler` (`coos.wit`)
- `memory_manager` (`memory.wit`)
- `ipc_router` (`services.wit`)

### Tier 1/Tier 3 は適用外

- **Tier 1**: URIベースDI（実行時解決が必要）
- **Tier 3**: 単純なOO（依存が少なく分離不要）

---

## パフォーマンス比較

| 方式 | 呼び出しコスト | メモリコスト | インライン化 |
|:---|:---:|:---:|:---:|
| 仮想関数（vtable） | ~3-5 cycles | +8B/object | ❌ 不可 |
| Conceptハーネス | 0 cycles | 0B | ✅ 可能 |

**測定環境**: Cortex-M7 @216MHz, GCC 14 `-O3`

---

## 関連ドキュメント

- [Fireball Architecture](file:///w:/mysrc/fireball/.agent/skills/fireball_architecture/SKILL.md): 3-Tier分離の全体像
- [C++ Embedded Optimization](file:///w:/mysrc/fireball/.agent/skills/cpp_embedded/SKILL.md): メモリ最適化技法
