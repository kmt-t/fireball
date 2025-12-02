# Fireball バイナリサーチ最適化：エントリ検索

**Version:** 0.1.0
**Date:** 2025-11-30
**Author:** Takuya Matsunaga

---

## 概要

Fireball は **O(log N) バイナリサーチ** を使用して、サービス発見とルーティングを <15 μs で実現します。本ドキュメントでは以下を規定します：

1. **実装戦略**：`std::lower_bound` と生配列による O(log N) ルックアップ
2. **メモリ効率**：100 サービス = 8.5KB（Phase 2 予算 16KB 以内）
3. **2 段階展開**：Phase 0-1（静的ビルド時生成）と Phase 2-3（動的生配列）

---

## 1. 問題ステートメント

### 1.1 線形探索 vs バイナリサーチ

**線形探索（素朴）:**
```
時間計算量: O(N)
例：100 個のサービス → 平均 50 回の比較
レイテンシ：~50 μs（ホットパスには不可能）
```

**バイナリサーチ（最適化）:**
```
時間計算量: O(log N)
例：100 個のサービス → 最大 7 回の比較
レイテンシ：~1 μs（ホットパスに許容）
```

### 1.2 パフォーマンス予算

architecture.md から：

| 操作 | 予算 | 現状 | ギャップ |
|-----------|--------|---------|-----|
| ルータ サービス クエリ | <15 μs | ??? | 最適化必要 |
| ログ記録（O(1) 辞書） | <5 μs | ✅ | 検索不要 |
| GPIO 書き込み | <50 μs | ✅ | 直接 MMIO |

**目標**：バイナリサーチで <15 μs のサービス クエリを実現

---

## 2. データ構造設計

### 2.1 コア概念：事前ソート済みレジストリ

O(log N) バイナリサーチ検索を実現するため、`std::lower_bound` を使用した生配列実装を使用します：

```cpp
#include <algorithm>

namespace fireball { namespace router {

// サービス レジストリ エントリ
struct service_entry {
  const char* uri;              // ソート キー（例："service://gpio/output/5"）
  uint32_t route_id;            // コルーチン ID
  access_policy* policy;        // 権限ルール
};

// URI 字句順序比較器
struct uri_compare {
  bool operator()(const service_entry& a, const service_entry& b) const {
    return strcmp(a.uri, b.uri) < 0;
  }
  bool operator()(const service_entry& a, const char* b) const {
    return strcmp(a.uri, b) < 0;
  }
  bool operator()(const char* a, const service_entry& b) const {
    return strcmp(a, b.uri) < 0;
  }
};

// 生配列 + std::lower_bound によるレジストリ
class service_registry {
 private:
  static constexpr int MAX_SERVICES = 256;
  service_entry entries_[MAX_SERVICES];     // ソート済み生配列
  int count_;                                // 現在のエントリ数

 public:
  service_registry() : count_(0) {}

  // O(log N) ルックアップ（std::lower_bound）
  const service_entry* find(const char* uri) const {
    auto it = std::lower_bound(entries_, entries_ + count_, uri, uri_compare());

    if (it != entries_ + count_ && strcmp(it->uri, uri) == 0) {
      return it;
    }
    return nullptr;
  }

  // O(log N) 検索 + O(N) 挿入
  int register_service(const char* uri,
                       uint32_t route_id,
                       access_policy* policy) {
    if (count_ >= MAX_SERVICES) {
      return -ENOMEM;
    }

    auto it = std::lower_bound(entries_, entries_ + count_, uri, uri_compare());
    int pos = it - entries_;

    // 既に存在するか確認
    if (it != entries_ + count_ && strcmp(it->uri, uri) == 0) {
      return -EEXIST;  // 既に登録済み
    }

    // 配列をシフト：pos 以降を後ろへ
    for (int i = count_; i > pos; --i) {
      entries_[i] = entries_[i - 1];
    }

    // 新規エントリを挿入
    entries_[pos] = {uri, route_id, policy};
    count_++;

    logger_->log("Service registered: {} → {}", uri, route_id);
    return 0;
  }

  // O(log N) 検索 + O(N) 削除
  int unregister_service(const char* uri) {
    auto it = std::lower_bound(entries_, entries_ + count_, uri, uri_compare());

    if (it == entries_ + count_ || strcmp(it->uri, uri) != 0) {
      return -ENOENT;  // サービス未検出
    }

    int pos = it - entries_;

    // 配列をシフト：pos+1 以降を前へ
    for (int i = pos; i < count_ - 1; ++i) {
      entries_[i] = entries_[i + 1];
    }

    count_--;
    logger_->log("Service unregistered: {}", uri);
    return 0;
  }
};

} } // namespace
```

**設計判断：**
- ✅ **std::lower_bound**: STL の信頼性、バグ回避
- ✅ **生配列**: std::vector 不使用、メモリ効率
- ✅ **uri_compare**: 複数型の比較に対応
- ✅ **O(log N) ルックアップ**: バイナリサーチ保証
- ⚠️ **登録・削除の O(N) シフト**: Phase 2 以降、サービス数が安定しているため許容

### 2.2 特殊化：Phase 0-1 完全静的レジストリ

最初の段階でサービス構成が固定されている場合、ビルド時生成を使用：

```cpp
namespace fireball { namespace router {

// ビルド時生成：tools/gen_service_registry.py
// 例：サービスリスト (URI, route_id) をコンパイル時にソート

const char REGISTRY_URIS[] = {
  "service://adc/channel/0\0"
  "service://gpio/input/3\0"
  "service://gpio/output/5\0"
  "service://uart/tx/0\0"
};

const uint32_t REGISTRY_ROUTE_IDS[] = {5, 10, 20, 30};
const access_policy* REGISTRY_POLICIES[] = {&adc_policy, &gpio_in_policy, &gpio_out_policy, &uart_policy};
const int REGISTRY_COUNT = 4;

// ルックアップ：完全にコンパイル時最適化、O(1) または O(log N)
const service_entry* static_find(const char* target_uri) {
  // constexpr で実行される二分探索
  int pos = lower_bound_static(target_uri);
  if (pos < REGISTRY_COUNT && strcmp_static(target_uri, REGISTRY_URIS, pos) == 0) {
    return &SERVICE_ENTRIES[pos];
  }
  return nullptr;
}

} } // namespace
```

**使用時期：**
- Phase 0-1：デバイスドライバとシステムサービスが固定
- Phase 2-3：動的レジストリに移行（登録・削除が必要になったら）

---

## 3. 実装戦略の選択

### 3.1 戦略比較表

| 戦略 | 検索 | 挿入 | 削除 | メモリ | 使用時期 |
|------|------|------|------|--------|---------|
| **動的配列** | O(log N) | O(N) | O(N) | ~12.8KB（100 service） | Phase 2-3 |
| **完全静的** | O(log N) | ビルド時 | ビルド時 | ~1.6KB（100 service） | Phase 0-1 |

### 3.2 戦略 1：動的生配列（推奨：Phase 2-3）

サービス数が時間とともに増減する場合、固定サイズ生配列を使用：

```cpp
// service_registry クラス（前述）を使用
service_registry registry;

// 実行時に動的登録（生配列にシフト挿入）
registry.register_service("service://gpio/output/5", gpio_coro_id, &gpio_policy);
registry.register_service("service://uart/tx/0", uart_coro_id, &uart_policy);

// ルックアップ：O(log N)
const service_entry* entry = registry.find("service://gpio/output/5");
if (entry) {
  route_id = entry->route_id;
}

// 実行時に削除（ハードウェア削除時など）
registry.unregister_service("service://gpio/output/5");
```

**メリット：**
- ✅ 動的登録・削除可能（std::lower_bound + 手動シフト）
- ✅ メモリ効率（生配列、std::vector 不使用）
- ✅ キャッシュ局所性良好

**デメリット：**
- ⚠️ 登録・削除が O(N)（配列シフト）
- ⚠️ MAX_SERVICES 固定制限
- ⚠️ サービス数頻繁に変動する場合は適さない

### 3.3 戦略 2：完全静的登録（Phase 0-1）

デバイスドライバとシステムサービスが固定されている初期段階：

```cpp
// ビルド時生成スクリプト：tools/gen_service_registry.py
// 入力：サービスマニフェスト（YAML/JSON）
// 出力：ソート済み C++ コード

# 例：services.yaml
services:
  - uri: "service://adc/channel/0"
    route_id: 5
  - uri: "service://gpio/input/3"
    route_id: 10
  - uri: "service://gpio/output/5"
    route_id: 20
```

生成されるコード：

```cpp
// 自動生成：inc/generated/service_registry_static.hxx
namespace fireball { namespace router {

constexpr const char REGISTRY_URIS[] = {
  "service://adc/channel/0\0"
  "service://gpio/input/3\0"
  "service://gpio/output/5\0"
};

constexpr const uint32_t REGISTRY_ROUTE_IDS[] = {5, 10, 20};
constexpr const int REGISTRY_COUNT = 3;

// O(log N) ルックアップ、constexpr で実行可能
constexpr const service_entry* static_registry_find(const char* uri) {
  int left = 0, right = REGISTRY_COUNT;
  while (left < right) {
    int mid = left + (right - left) / 2;
    int cmp = strcmp(REGISTRY_URIS[mid], uri);
    if (cmp < 0) {
      left = mid + 1;
    } else {
      right = mid;
    }
  }
  if (left < REGISTRY_COUNT && strcmp(REGISTRY_URIS[left], uri) == 0) {
    return &REGISTRY_ENTRIES[left];
  }
  return nullptr;
}

} } // namespace
```

使用例：

```cpp
// ルックアップ：O(log N)
const service_entry* entry = static_registry_find("service://gpio/output/5");
if (entry) {
  route_id = entry->route_id;
}
```

**メリット：**
- ✅ ルックアップ超高速（キャッシュ最適化可能）
- ✅ メモリ最小
- ✅ 登録・削除不要（静的構成）

**デメリット：**
- ❌ ビルド時のみ変更可能
- ❌ 動的デバイス追加不可

---

## 4. バイナリサーチ実装：std::lower_bound と生配列

### 4.1 推奨パターン：std::lower_bound + 生配列

生配列に対して `std::lower_bound` を使用。バグ回避のため手動実装は避ける：

```cpp
#include <algorithm>

namespace fireball { namespace router {

// URI 比較器
struct uri_compare {
  bool operator()(const service_entry& a, const service_entry& b) const {
    return strcmp(a.uri, b.uri) < 0;
  }
  bool operator()(const service_entry& a, const char* b) const {
    return strcmp(a.uri, b) < 0;
  }
  bool operator()(const char* a, const service_entry& b) const {
    return strcmp(a, b.uri) < 0;
  }
};

// O(log N) ルックアップ：生配列での std::lower_bound
const service_entry* registry_find(
    const service_entry* services, int count, const char* target_uri) {

  auto it = std::lower_bound(services, services + count, target_uri, uri_compare());

  if (it != services + count && strcmp(it->uri, target_uri) == 0) {
    return it;
  }
  return nullptr;
}

// 登録：O(log N) 検索 + O(N) 手動シフト
int registry_register(
    service_entry* services, int& count, int max_count,
    const char* uri, uint32_t route_id, access_policy* policy) {

  if (count >= max_count) return -ENOMEM;

  auto it = std::lower_bound(services, services + count, uri, uri_compare());
  if (it != services + count && strcmp(it->uri, uri) == 0) {
    return -EEXIST;
  }

  int pos = it - services;
  for (int i = count; i > pos; --i) {
    services[i] = services[i - 1];
  }

  services[pos] = {uri, route_id, policy};
  count++;
  return 0;
}

// 削除：O(log N) 検索 + O(N) 手動シフト
int registry_unregister(
    service_entry* services, int& count, const char* uri) {

  auto it = std::lower_bound(services, services + count, uri, uri_compare());
  if (it == services + count || strcmp(it->uri, uri) != 0) {
    return -ENOENT;
  }

  int pos = it - services;
  for (int i = pos; i < count - 1; ++i) {
    services[i] = services[i + 1];
  }
  count--;
  return 0;
}

} } // namespace
```

**重要：**
- ✅ `std::lower_bound` で O(log N) 検索保証
- ✅ 生配列（std::vector 不要）
- ✅ バグ回避（手動実装のリスク排除）
- ⚠️ 登録・削除は手動シフト O(N)

### 4.2 URI の字句順序付けルール

すべてのサービス URI は `strcmp()` の字句順で整列：

```
service://adc/channel/0
service://gpio/input/3
service://gpio/output/5     ← クエリ対象、std::lower_bound で O(log N) 検出
service://uart/rx/0
service://uart/tx/0
```

**比較ルール：** 標準 C の `strcmp()` に準拠。階層的なセマンティクスなし。

---

## 5. Fireball における応用例

### 5.1 サービス発見（ルータ）

```cpp
// URI でサービス クエリ：O(log N)
auto result = router_->query("service://gpio/output/5", caller_id);
// ├─ サービス レジストリでバイナリサーチ：O(log N)
// ├─ アクセス ポリシーを確認：O(1)
// └─ route_id を返却
```

### 5.2 ログ辞書 ルックアップ

既に O(1)（constexpr オフセット使用）だが、必要に応じてバイナリサーチで最適化可能：

```cpp
// ログ エントリを名前で検索：O(log N)
auto offset = logger_->find_log_key("HAL_DEVICE_READ_SUCCESS");
// ├─ 事前ソート済みログキーでバイナリサーチ：O(log N)
// └─ O(1) 文字列アクセス用 ROM オフセットを返却
```

### 5.3 ルート テーブル ルックアップ（HAL）

```cpp
// URI でデバイス ドライバを検索：O(log N)
auto driver = hal_->find_device("device://uart/0");
// ├─ デバイス レジストリでバイナリサーチ：O(log N)
// └─ ドライバ関数ポインタを返却
```

---

## 6. パフォーマンス特性

### 6.1 レイテンシ分析

| 操作 | 回数 | 時間/操作 | 合計 | 備考 |
|-----------|-------|---------|-------|-------|
| **文字列比較** | O(log N) | ~0.1 μs | O(log N) | "service://xxx" を比較 |
| **バイナリサーチ ループ** | O(log N) | ~0.05 μs | O(log N) | ツリー走査 |
| **アクセス制御確認** | 1 | ~0.5 μs | 0.5 μs | ポリシー評価 |
| **返却** | 1 | <0.1 μs | <0.1 μs | ポインタ演算 |
| **合計（N=100）** | - | - | **~7 回の比較 × 0.15 μs = 1 μs** | ✅ <15 μs 予算内 |

### 6.2 メモリ オーバーヘッド

#### 動的生配列戦略

| コンポーネント | サイズ | 備考 |
|-----------|------|-------|
| URI ポインタ（const char*） | 8B | 各エントリ |
| Route ID | 4B | uint32_t |
| Policy ポインタ | 8B | access_policy* |
| **エントリあたり** | **~20B** | ポインタのみ、構造体 packed |
| 固定生配列（MAX_SERVICES=256） | 5.1KB | 256 × 20B |
| URI 文字列データ（典型値） | 40B × N | "service://gpio/output/5" |
| count_ カウンタ | 4B | int |
| **典型値（100 サービス）** | **~8.5KB** | 配列 5.1KB + URI 3.4KB |
| **Phase 2 予算** | **16KB** | ✅ 十分な余裕 |

#### 完全静的戦略

| コンポーネント | サイズ | 備考 |
|-----------|------|-------|
| URI 文字列データ | ~4KB | 100 サービス × 40B 平均 |
| Route ID 配列 | 400B | 100 × 4B |
| Policy ポインタ配列 | 800B | 100 × 8B |
| **合計（100 サービス）** | **~5.2KB** | ROM に配置、ほぼ圧縮可能 |

---

## 7. 実装チェックリスト

### Phase 1：動的生配列実装

- [ ] `service_registry` クラス実装：生配列 + std::lower_bound
- [ ] `uri_compare` 比較器実装
- [ ] `find(uri)` → std::lower_bound + O(log N) ルックアップ
- [ ] `register_service(uri, route_id, policy)` → std::lower_bound + O(N) 手動シフト挿入
- [ ] `unregister_service(uri)` → std::lower_bound + O(N) 手動シフト削除
- [ ] 10、100、256 サービスでテスト

### Phase 2：コード生成ツール（静的レジストリ）

- [ ] `tools/gen_service_registry.py` を実装
  - [ ] YAML/JSON サービスマニフェスト入力
  - [ ] URI でソート
  - [ ] C++ コード生成（constexpr 対応）
- [ ] ビルド統合（CMake）

### テスト

- [ ] ユニット テスト：既存サービス検索
- [ ] ユニット テスト：未検出サービス検索
- [ ] ユニット テスト：動的登録・削除
- [ ] ストレス テスト：256 個の同時サービス登録・削除
- [ ] パフォーマンス テスト：<15 μs クエリレイテンシ確認（100 サービス）
- [ ] 統合テスト：ルータが URI を正しく解決
- [ ] メモリ プロファイリング：8.5KB 未満（100 サービス）

---

## 8. コード例：完全な実装

### バイナリサーチ付きサービス レジストリ（生配列戦略）

```cpp
#include <algorithm>

// inc/router/service_registry.hxx

namespace fireball { namespace router {

// サービス レジストリ エントリ（軽量）
struct service_entry {
  const char* uri;              // URI ポインタ（外部管理）
  uint32_t route_id;            // コルーチン ID
  access_policy* policy;        // アクセス制御ルール
};

// URI 比較器
struct uri_compare {
  bool operator()(const service_entry& a, const service_entry& b) const {
    return strcmp(a.uri, b.uri) < 0;
  }
  bool operator()(const service_entry& a, const char* b) const {
    return strcmp(a.uri, b) < 0;
  }
  bool operator()(const char* a, const service_entry& b) const {
    return strcmp(a, b.uri) < 0;
  }
};

// O(log N) バイナリサーチ対応レジストリ
class service_registry {
 private:
  static constexpr int MAX_SERVICES = 256;
  service_entry entries_[MAX_SERVICES];     // ソート済み生配列
  int count_;                                // 現在のエントリ数

 public:
  service_registry() : count_(0) {}

  // O(log N) ルックアップ：std::lower_bound + 生配列
  const service_entry* find(const char* uri) const {
    auto it = std::lower_bound(entries_, entries_ + count_, uri, uri_compare());

    if (it != entries_ + count_ && strcmp(it->uri, uri) == 0) {
      return it;
    }
    return nullptr;
  }

  // O(log N) 検索 + O(N) 挿入
  int register_service(const char* uri, uint32_t route_id,
                       access_policy* policy) {
    if (count_ >= MAX_SERVICES) {
      return -ENOMEM;  // レジストリ満杯
    }

    auto it = std::lower_bound(entries_, entries_ + count_, uri, uri_compare());
    int pos = it - entries_;

    // 重複チェック
    if (it != entries_ + count_ && strcmp(it->uri, uri) == 0) {
      return -EEXIST;
    }

    // 配列シフト：pos 以降を後ろへ
    for (int i = count_; i > pos; --i) {
      entries_[i] = entries_[i - 1];
    }

    // 新規エントリ挿入
    entries_[pos] = {uri, route_id, policy};
    count_++;

    logger_->log("Service registered: {} → {}", uri, route_id);
    return 0;
  }

  // O(log N) 検索 + O(N) 削除
  int unregister_service(const char* uri) {
    auto it = std::lower_bound(entries_, entries_ + count_, uri, uri_compare());

    if (it == entries_ + count_ || strcmp(it->uri, uri) != 0) {
      return -ENOENT;  // 未検出
    }

    int pos = it - entries_;

    // 配列シフト：pos+1 以降を前へ
    for (int i = pos; i < count_ - 1; ++i) {
      entries_[i] = entries_[i + 1];
    }

    count_--;
    logger_->log("Service unregistered: {}", uri);
    return 0;
  }

  // デバッグ：すべてのサービスをリスト
  void list_services(void (*callback)(const char* uri, uint32_t route_id)) const {
    // ロック不要（読み取り専用）
    for (int i = 0; i < count_; ++i) {
      callback(entries_[i].uri, entries_[i].route_id);
    }
  }

  int service_count() const {
    return count_;
  }
};

} } // namespace fireball { namespace router
```

**設計メモ：**
- ✅ `std::lower_bound`：バグ回避、生配列ポインタ対応
- ✅ 生配列：std::vector 不要
- ✅ `const char*` URI：文字列リテラルまたは静的領域で管理
- ✅ ロック最小化：find() は読み取り専用、ロック不要
- ✅ キャッシュ局所性：配列メモリウォーク、CPU キャッシュ効率
- ✅ メモリ効率：100 サービス = 8.5KB

---

## まとめ

**Fireball 向けバイナリサーチ最適化戦略：**

1. **コア実装：std::lower_bound + 生配列**
   - ホットパス（find）：`std::lower_bound` で O(log N) バイナリサーチ
   - コールドパス（register/unregister）：std::lower_bound + 手動配列シフト
   - バグ回避：STL バイナリサーチ採用、手動実装なし

2. **メモリ効率**
   - 動的生配列：100 サービス = 8.5KB（std::vector 不使用）
   - 完全静的レジストリ：100 サービス = 5.2KB（ビルド時生成）
   - Phase 2 予算 16KB に十分に収まる

3. **2 段階展開**
   - **Phase 0-1**：完全静的レジストリ（ビルド時生成、変更不可）
   - **Phase 2-3**：動的生配列レジストリ（実行時登録・削除、MAX_SERVICES=256）

4. **パフォーマンス特性**
   - クエリレイテンシ：<15 μs（100 サービス）
   - 検索：O(log N)、~7 回の比較
   - 登録・削除：O(N) シフト（サービス数安定時は許容）
   - CPU キャッシュ効率：生配列メモリウォーク、局所性良好

**結果：** STL バイナリサーチと生配列を組み合わせた、低オーバーヘッドで O(log N) を保証したサービス発見。

