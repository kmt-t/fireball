# Fireball IPC ルータ: URI ベース サービス発見とアクセス制御

**Version:** 0.1.0
**Date:** 2025-11-30
**Author:** Takuya Matsunaga

---

## 概要

Fireball IPC ルータは **URI ベースのサービス発見** と **権限チェック付きルート解決** を提供します。クライアントが URI 経由でサービスリクエストを行う際、ルータはそれを Route ID（コルーチン ID）に解決し、同時にアクセス権限をチェックします。

**主要機能：** URI ベースサービス発見、権限チェック、O(log N) ルックアップ、原子的クエリ

---

## 1. 設計原則

### 1.1 3 つの重要概念

| 概念 | 定義 | 例 |
|------|------|-----|
| **URI** | サービス識別子（人間可読） | `"service://dsp/fft/0"` |
| **Route ID** | サービス実行コルーチン ID | `42`（コルーチン #42） |
| **Permission** | アクセス制御決定 | `ALLOW`、`DENY`、`DELEGATED` |

### 1.2 設計目標

1. **シンプル**: 単一クエリでRoute ID と アクセス決定を返却
2. **効率**: O(log N) ルックアップ（線形探索なし）
3. **セキュリティ**: 呼び出し元コンテキストをクエリ毎に検証
4. **原子性**: TOCTOU（Time-Of-Check-Time-Of-Use）レース回避

---

## 2. URI スキーム設計

### 2.1 URI フォーマット

```
service://[namespace]/[component]/[instance]
```

| 部分 | 意味 | 例 |
|------|------|-----|
| `service://` | プロトコル固定 | 固定 |
| `namespace` | サービスカテゴリ | `gpio`、`uart`、`dsp`、`sensor`、`app` |
| `component` | 論理グループ（オプション） | `output`、`adc`、`channel` |
| `instance` | 特定インスタンス番号 | `0`、`5`、`all` |

**例：**

| URI | サービス | 目的 |
|-----|---------|------|
| `service://gpio/output/5` | GPIO 出力ドライバ | GPIO ピン 5 出力 |
| `service://gpio/input/3` | GPIO 入力ドライバ | GPIO ピン 3 入力 |
| `service://uart/tx/0` | UART 送信 | UART デバイス 0 TX |
| `service://dsp/fft/0` | FFT アクセラレータ | FFT プロセッサ #0 |
| `service://app/led_controller` | ユーザー アプリ | LED 制御アプリ |

---

## 3. ルータ データ構造

### 3.1 サービス レジストリ

ルータは **ソート済みバイナリサーチツリー** でサービス登録を管理：

```cpp
struct service_entry {
  std::string uri;              // サービス URI（ソート キー）
  uint32_t route_id;            // サービス実行コルーチン ID
  uint32_t owner_coroutine;     // 登録したコルーチン
  access_policy* policy;        // アクセス制御ルール
  uint64_t registration_time;   // 登録時刻
};

class router_registry {
 private:
  std::map<std::string, service_entry> services_;  // URI でソート

 public:
  struct query_result {
    int status;           // 0=成功、-1=未検出、-2=アクセス拒否
    uint32_t route_id;    // サービス コルーチン ID
    access_policy* policy;
  };

  query_result query(const std::string& uri, uint32_t caller_coro_id);
  int register_service(const std::string& uri, uint32_t route_id, access_policy* policy);
  int unregister_service(const std::string& uri);
};
```

### 3.2 アクセス ポリシー構造

```cpp
enum access_decision {
  ALLOW = 0,
  DENY = 1,
  DELEGATED = 2,        // スケジューラに判定委譲
};

struct access_policy {
  enum rule_type {
    RULE_ANY = 0,            // すべて許可
    RULE_SPECIFIC_CORO = 1,  // 特定コルーチンのみ
    RULE_SPECIFIC_APP = 2,   // 特定アプリのみ
    RULE_OWNER_ONLY = 3,     // 登録者のみ
  };

  rule_type type;
  uint32_t value;  // Coroutine ID または App ID
  bool allow_delegation;

  access_decision check(uint32_t caller_coro_id) const;
};
```

---

## 4. クエリとルート解決

### 4.1 クエリ フロー

```
クライアント コルーチン（例：coro #10）
    │
    ├─ router.query("service://gpio/output/5", caller_id=10) 呼び出し
    │
    └─ Router.query():
         ├─ バイナリサーチツリーで URI 検索
         │  └─ 見出し: route_id=35、policy=...
         │
         ├─ access_policy.check(caller_id=10)
         │  ├─ RULE_ANY: 許可
         │  ├─ RULE_SPECIFIC_CORO: caller_id == rule.value か確認
         │  ├─ RULE_OWNER_ONLY: caller_id == 登録者 か確認
         │  └─ DELEGATED: スケジューラに判定委譲
         │
         └─ 返却: {status=0, route_id=35, policy=...}
```

### 4.2 実装例

```cpp
query_result router_registry::query(const std::string& uri, uint32_t caller_coro_id) {
  // ステップ 1: バイナリサーチ検索
  auto it = services_.find(uri);
  if (it == services_.end()) {
    return {status: -1, route_id: 0, policy: nullptr};
  }

  service_entry& entry = it->second;

  // ステップ 2: アクセス権限チェック
  access_decision decision = entry.policy->check(caller_coro_id);

  switch (decision) {
    case ALLOW:
      return {status: 0, route_id: entry.route_id, policy: entry.policy};

    case DENY:
      logger_->log_access_denied(uri, caller_coro_id);
      return {status: -2, route_id: 0, policy: nullptr};

    case DELEGATED:
      uint32_t parent_decision = co_sched_->ask_parent_permission(caller_coro_id, uri);
      if (parent_decision) {
        return {status: 0, route_id: entry.route_id, policy: entry.policy};
      } else {
        return {status: -2, route_id: 0, policy: nullptr};
      }
  }
}
```

### 4.3 Route ID = Coroutine ID

**重要：** Fireball ではサービス **がコルーチン**。URI クエリで得られるのはそのコルーチン ID。

```
サービス登録:
  coro #35: GPIO 出力サービス実行
  → register("service://gpio/output/5", route_id=35)

サービス使用:
  coro #10: サービスリクエスト
  → router.query("service://gpio/output/5", caller_id=10)
  → 返却: route_id=35
  → coro #10 が coro #35 に CSP チャネル経由でメッセージ送信
```

---

## 5. 権限モデル例

### 5.1 例 1: GPIO 出力 - 登録者のみアクセス

```cpp
access_policy gpio_policy;
gpio_policy.type = RULE_OWNER_ONLY;
gpio_policy.allow_delegation = false;

router.register_service("service://gpio/output/5", gpio_output_coro_id, &gpio_policy);

// アプリ コルーチン（coro #10）からのクエリ
auto result = router.query("service://gpio/output/5", caller_id=10);
// 結果: status=0（クエリは許可、書き込みはサービス内で権限確認可能）
```

### 5.2 例 2: Logger サービス - 全員アクセス可能

```cpp
access_policy logger_policy;
logger_policy.type = RULE_ANY;

router.register_service("service://logger", logger_coro_id, &logger_policy);

// 任意のコルーチンからのクエリが許可
auto result = router.query("service://logger", caller_id=any);
// 結果: status=0（常に許可）
```

### 5.3 例 3: 高権限 DSP - 委譲制御

```cpp
access_policy dsp_policy;
dsp_policy.type = RULE_DELEGATED;
dsp_policy.allow_delegation = true;

router.register_service("service://dsp/fft/0", dsp_coro_id, &dsp_policy);

// ルータが親（スケジューラ）に判定を委譲
auto result = router.query("service://dsp/fft/0", caller_id=app_coro);
// ルータ: co_sched_->ask_parent_permission(app_coro, "service://dsp/fft/0")
// スケジューラが app_coro の権限レベルを確認、status=0 または status=-2 返却
```

---

## 6. ライフサイクル

### 6.1 サービス登録

```cpp
void gpio_output_service_main() {
  // ハードウェア 初期化
  uint32_t my_coro_id = co_sched_->current_coro_id();
  access_policy policy = create_gpio_policy();

  int status = router_->register_service("service://gpio/output/5", my_coro_id, &policy);
  if (status < 0) {
    logger_->log_error("GPIO サービス登録失敗");
    return;
  }

  // サービス起動
  while (true) {
    auto msg = my_channel_->receive();
    handle_gpio_request(msg);
  }
}
```

### 6.2 サービス 使用

```cpp
void app_main() {
  // ルータでサービス クエリ
  auto result = router_->query("service://gpio/output/5", my_coro_id);

  if (result.status < 0) {
    if (result.status == -1) logger_->log("サービス未検出");
    if (result.status == -2) logger_->log("アクセス拒否");
    return;
  }

  // result.route_id = 35（GPIO サービス コルーチン）
  // メッセージ送信
  ipc_record_t msg;
  msg.key_id = result.route_id;
  msg.value = 1;
  router_->route(msg);
}
```

---

## 7. パフォーマンス特性

### 7.1 ルックアップ パフォーマンス

| 操作 | 計算量 | 典型時間 |
|-----------|----------|------------|
| **クエリ（完全一致）** | O(log N) | <10 μs（N=100） |
| **バイナリサーチ** | O(log N) | <5 μs |
| **アクセス チェック** | O(1) | <1 μs |
| **合計クエリ** | O(log N) | <15 μs |

### 7.2 メモリ オーバーヘッド

| コンポーネント | サイズ | 備考 |
|-----------|------|-------|
| サービス エントリ | ~256B | URI 文字列 + メタデータ |
| ツリー構造 | 32B × ノード数 | ポインタ、比較情報 |
| ハッシュテーブル（Route ID） | 64B × エントリ | 逆ルックアップ用 |
| **典型値（100 サービス）** | **~26KB** | Phase 2 予算内 |

---

## 8. 原子性と TOCTOU 防止

### 8.1 TOCTOU 対策

クエリ操作は **原子的**：サービスはルックアップと権限チェック間で削除されない。

```cpp
query_result router_registry::query(const std::string& uri, uint32_t caller_coro_id) {
  auto lock = services_mutex_.read_lock();  // 読み取りロック取得

  auto it = services_.find(uri);
  if (it == services_.end()) {
    return {status: -1, ...};
  }

  service_entry& entry = it->second;
  access_decision decision = entry.policy->check(caller_coro_id);
  // ロック保持中に両操作実行
  // ロック解放
}
```

---

## 9. 実装チェックリスト

- [ ] サービス URI フォーマット定義と検証
- [ ] バイナリサーチツリー サービス レジストリ実装
- [ ] access_policy::check() ロジック実装
- [ ] query() 権限チェック付き実装
- [ ] 読み書き ロック スレッド安全性実装
- [ ] 委譲メカニズム実装（スケジューラに要求）
- [ ] サービス登録 API
- [ ] サービス削除 API
- [ ] プレフィックス マッチング サービス発見
- [ ] アクセス決定監査ログ
- [ ] クエリ ロジック ユニットテスト
- [ ] マルチサービス 統合テスト
- [ ] O(log N) ルックアップ パフォーマンス検証
- [ ] 監視 API（サービス一覧）

---

## まとめ

Fireball IPC ルータは **URI ベースのサービス発見と統合アクセス制御** を提供。重要イノベーション：

1. **Route ID = Coroutine ID**: 直接マッピング、ルーティング簡潔化
2. **バイナリサーチ ルックアップ**: O(log N) で 100+ サービス高速検索
3. **原子的クエリ**: ルックアップと権限チェックが同時実行、TOCTOU レース回避
4. **柔軟権限モデル**: 複数権限パターン対応（所有者のみ、全員、委譲）
5. **階層 URI**: サービスを namespace/component/instance で組織化

リソース制約組み込みシステムで**安全で効率的なサービス発見**を実現。
