# Fireball コルーチンクラス設計

**Version:** 0.1.0
**Date:** 2025-11-30
**Author:** Takuya Matsunaga

---

## 概要

Fireball の co_sched（スケジューラ）が管理するコルーチンの実装パターンを説明します。C++20 コルーチンフレームの自動割り当て機構と co_mem（メモリ管理）の連携を詳細に示します。

**要点：**

- **Promise型の設計**: コルーチン状態の中心（値、例外、スタック）
- **カスタムアロケータ**: co_mem から自前ヒープへの割り当て
- **フレーム生存期間**: 明示的な destroy() までメモリ持続
- **co_sched 統合**: Ready キュー管理と resume/suspend サイクル

---

## 1. コルーチンフレームの構造

### 1.1 メモリレイアウト

```
Coroutine Frame Layout (Heap allocated)
┌─────────────────────────────────────┐
│ Promise Object (promise_type)       │  ◄─── co_mem から割り当て
│  ├─ value_: T (戻り値)              │
│  ├─ exception_: std::exception_ptr  │
│  ├─ stack_: coroutine_stack        │
│  └─ state_: suspension point       │
├─────────────────────────────────────┤
│ Function Parameters                 │
│  (コルーチン開始時にコピー/移動)   │
├─────────────────────────────────────┤
│ Local Variables (spanning suspend)  │
│  (中断ポイントをまたぐ変数)        │
├─────────────────────────────────────┤
│ Compiler-generated State            │
│  ├─ Resume index                    │
│  ├─ Destroy callback                │
│  └─ Alignment padding               │
├─────────────────────────────────────┤
│ Temporaries & Alignment             │
└─────────────────────────────────────┘
```

### 1.2 Promise 型の責務

| 機能 | 役割 |
|------|------|
| **値の保持** | `value_` メンバで T 型の戻り値保存 |
| **例外処理** | `exception_ptr_` で `co_return` 例外キャプチャ |
| **スタック管理** | `stack_` で frame 割り当て監視 |
| **制御フロー** | `co_await`/`co_yield` で initial/final suspend 制御 |
| **フレーム割り当て** | `operator new/delete` でカスタム allocator 指定 |

---

## 2. コルーチンクラスの雛形

### 2.1 基本的なタスク型コルーチン

```cpp
namespace fireball { namespace coos {

/**
 * Coroutine Stack Tracking
 * Helper to track frame allocation
 */
typedef struct {
  void* frame_ptr;
  size_t frame_size;
  uint64_t alloc_time;
} coroutine_stack;

/**
 * Task Coroutine Template
 *
 * 使用方法:
 *   task<int> compute_task() {
 *     co_await some_operation();
 *     co_return 42;
 *   }
 */
template<typename T = void>
class task {
 public:
  /**
   * Promise Type
   * co_await/co_return/co_yield の動作を定義
   */
  struct promise_type {
    // ===== メンバ変数 =====
    T value_;                          // 戻り値の保存
    std::exception_ptr exception_;      // 例外キャプチャ
    coroutine_stack stack_;             // フレーム追跡

    // ===== Promise の動作制御 =====

    /**
     * コルーチン作成直後に呼ばれる
     * co_await initial_suspend()
     *
     * - co_await を返す: 最初の co_await で中断
     * - co_await を返さない: 即座に実行開始
     */
    std::suspend_never initial_suspend() {
      return {};
    }

    /**
     * コルーチン終了直前に呼ばれる
     * co_await final_suspend()
     *
     * - co_await を返す: 終了直前に中断、呼び出し側で destroy()
     * - co_await を返さない: 自動 destroy（推奨）
     */
    std::suspend_never final_suspend() noexcept {
      return {};
    }

    /**
     * co_return 実行時に値を保存
     *
     * @param value: T 型の戻り値
     */
    void return_value(T value) {
      value_ = value;
    }

    /**
     * 例外発生時にキャプチャ
     */
    void unhandled_exception() {
      exception_ = std::current_exception();
    }

    /**
     * Task オブジェクトを構築して返す
     * コルーチン呼び出し側に返却される値
     */
    task get_return_object() {
      return task{std::coroutine_handle<promise_type>::from_promise(*this)};
    }

    /**
     * ===== カスタムメモリ割り当て =====
     *
     * デフォルト operator new ではなく、
     * co_mem（Fireball メモリ管理）から割り当て
     */
    void* operator new(size_t size) {
      // spawn() で TLS に設定された co_mem を取得
      // 各 WASM モジュール/サービスごと独立 mspace 割り当て
      auto* mem = co_mem::get_current_instance();
      if (!mem) {
        // Fallback: グローバルデフォルトインスタンス
        mem = co_mem::get_default_instance();
        if (!mem) {
          throw std::bad_alloc();
        }
      }
      void* ptr = mem->allocate(size);

      // ===== ハイパーバイザ開発者向けログ =====
      // （ゲスト logger サービスではなく、stderr に直接出力）
      // デバッグ・開発時のみ、FIREBALL_DEBUG_CORO で制御可能
      #ifdef FIREBALL_DEBUG_CORO
      std::cerr << std::format("[COOS] coroutine_frame_alloc: ptr={}, size={}, mem={}",
                               ptr, size, mem) << std::endl;
      #endif

      return ptr;
    }

    /**
     * フレームメモリ解放
     * ※ spawn() で設定された co_mem と同じインスタンスから deallocate
     */
    void operator delete(void* ptr, size_t size) noexcept {
      auto* mem = co_mem::get_current_instance();
      if (!mem) {
        mem = co_mem::get_default_instance();
      }
      if (mem) {
        #ifdef FIREBALL_DEBUG_CORO
        std::cerr << std::format("[COOS] coroutine_frame_free: ptr={}, size={}, mem={}",
                                 ptr, size, mem) << std::endl;
        #endif
        mem->deallocate(ptr, size);
      }
    }
  };

  // ===== Task オブジェクト本体 =====

  using handle_type = std::coroutine_handle<promise_type>;

  /**
   * コンストラクタ
   *
   * @param h: コルーチンハンドル（Promise から get_return_object で取得）
   */
  explicit task(handle_type h) : handle_(h) {}

  /**
   * ムーブコンストラクタ
   */
  task(task&& other) noexcept : handle_(other.release()) {}

  /**
   * ムーブ代入
   */
  task& operator=(task&& other) noexcept {
    reset(other.release());
    return *this;
  }

  /**
   * コピー禁止
   */
  task(const task&) = delete;
  task& operator=(const task&) = delete;

  /**
   * デストラクタ
   *
   * コルーチンが完了していなければ frame を destroy
   */
  ~task() {
    if (handle_) {
      handle_.destroy();
    }
  }

  // ===== co_sched 用インターフェース =====

  /**
   * コルーチンを再開実行
   *
   * - Ready キュー pop → resume() → Ready キュー push（suspend 時）
   * - または Running → Done（co_return 時）
   *
   * @return: true if coroutine suspended, false if done
   */
  bool resume() {
    if (!handle_ || handle_.done()) {
      return false;
    }
    handle_.resume();
    return !handle_.done();
  }

  /**
   * コルーチン完了判定
   *
   * @return: true if co_return or exception reached
   */
  bool done() const {
    return !handle_ || handle_.done();
  }

  /**
   * 戻り値を取得（コルーチン完了後）
   *
   * @return: T 型の値
   * @throws: 例外発生時は rethrow
   */
  T get() const {
    if (!handle_) {
      throw std::runtime_error("coroutine handle is null");
    }
    if (!handle_.done()) {
      throw std::runtime_error("coroutine not completed yet");
    }
    auto& promise = handle_.promise();
    if (promise.exception_) {
      std::rethrow_exception(promise.exception_);
    }
    return promise.value_;
  }

  /**
   * handle 所有権移譲（デストラクタで destroy 禁止）
   */
  handle_type release() {
    auto h = handle_;
    handle_ = nullptr;
    return h;
  }

  /**
   * handle をリセット（前の handle は destroy）
   */
  void reset(handle_type h = nullptr) {
    if (handle_) {
      handle_.destroy();
    }
    handle_ = h;
  }

  /**
   * Awaiter インターフェース（別の coroutine での co_await 対応）
   *
   * task<int> outer() {
   *   auto t = inner();
   *   int result = co_await t;  // inner を待機
   *   return result;
   * }
   */
  bool await_ready() const {
    return handle_.done();
  }

  void await_suspend(std::coroutine_handle<> h) {
    // h（外側のコルーチン）を resume キュー登録
    // 内側が完了したら h を再開
  }

  T await_resume() {
    return get();
  }

 private:
  handle_type handle_;
};

/**
 * void 特殊化（戻り値なし）
 */
template<>
class task<void> {
 public:
  struct promise_type {
    std::exception_ptr exception_;
    coroutine_stack stack_;

    std::suspend_never initial_suspend() { return {}; }
    std::suspend_never final_suspend() noexcept { return {}; }
    void return_void() {}
    void unhandled_exception() { exception_ = std::current_exception(); }
    task get_return_object() {
      return task{std::coroutine_handle<promise_type>::from_promise(*this)};
    }

    void* operator new(size_t size) {
      auto* mem = co_mem::get_instance();
      if (!mem) throw std::bad_alloc();
      return mem->allocate(size);
    }

    void operator delete(void* ptr, size_t size) noexcept {
      auto* mem = co_mem::get_instance();
      if (mem) mem->deallocate(ptr, size);
    }
  };

  using handle_type = std::coroutine_handle<promise_type>;

  explicit task(handle_type h) : handle_(h) {}
  task(task&& other) noexcept : handle_(other.release()) {}
  task& operator=(task&& other) noexcept { reset(other.release()); return *this; }
  task(const task&) = delete;
  task& operator=(const task&) = delete;
  ~task() { if (handle_) handle_.destroy(); }

  bool resume() {
    if (!handle_ || handle_.done()) return false;
    handle_.resume();
    return !handle_.done();
  }

  bool done() const { return !handle_ || handle_.done(); }

  void get() const {
    if (!handle_) throw std::runtime_error("coroutine handle is null");
    if (!handle_.done()) throw std::runtime_error("coroutine not completed");
    if (handle_.promise().exception_) {
      std::rethrow_exception(handle_.promise().exception_);
    }
  }

  handle_type release() { auto h = handle_; handle_ = nullptr; return h; }
  void reset(handle_type h = nullptr) {
    if (handle_) handle_.destroy();
    handle_ = h;
  }

  bool await_ready() const { return handle_.done(); }
  void await_suspend(std::coroutine_handle<> h) {}
  void await_resume() { get(); }

 private:
  handle_type handle_;
};

}} // namespace fireball { namespace coos {
```

---

## 3. co_sched 統合パターン

### 3.1 Spawn（コルーチン作成）と co_mem 統合

**重要：spawn 時に co_mem インスタンスを明示的に渡す必要がある**

```cpp
/**
 * Scheduler interface（改善版）
 *
 * co_mem インスタンスを spawn 時に指定することで、
 * コルーチンフレーム割り当てが確実に正しい mspace に行われる
 */
class co_sched {
 public:
  /**
   * Spawn coroutine with explicit co_mem instance
   *
   * @param proc: coroutine lambda/function
   * @param mem: co_mem インスタンス（モジュール固有の mspace 保持）
   * @return: coroutine ID
   */
  virtual uint32_t spawn(const std::function<void()>& proc, co_mem* mem) = 0;
};

/**
 * Scheduler implementation
 */
class co_sched_impl : public co_sched {
 private:
  struct coroutine_entry {
    task<void> handle;
    co_mem* mem;           // ← フレーム割り当て用 mspace
    uint32_t coro_id;
    uint64_t created_at;
  };

  std::deque<coroutine_entry> ready_queue_;
  size_t completed_count_;
  uint32_t coro_id_counter_;

 public:
  /**
   * Spawn coroutine
   *
   * 呼び出し側例：
   *   auto module_mem = co_mem::get_module_instance(module_id);
   *   scheduler->spawn([this]() -> task<void> {
   *     while (true) {
   *       handle_event();
   *       co_await some_async_op();
   *     }
   *   }, module_mem);
   *
   * @param proc: Coroutine function
   * @param mem: Module-specific co_mem instance
   *             Promise::operator new/delete で使用される
   */
  uint32_t spawn(const std::function<void()>& proc, co_mem* mem) override {
    if (!mem) {
      std::cerr << "[COOS:ERROR] spawn: co_mem instance is required" << std::endl;
      return INVALID_CORO_ID;
    }

    // ===== Strategy A: TLS (Thread Local Storage) に co_mem を設定 =====
    // co_mem インスタンスをスレッドローカル変数に保存してから
    // coroutine を構築することで、Promise::operator new から
    // co_mem::get_current_instance() で取得可能にする

    co_mem::set_current_instance(mem);  // TLS に保存

    // RAII guard で TLS 自動クリア
    auto tls_guard = std::make_unique<TLSGuard>();

    try {
      // coroutine は Promise::operator new で TLS から co_mem を取得
      // task<void> t = proc();  // これは実装言語依存
      // 実際には以下のように ラムダを通じて実行：

      auto coro_id = ++coro_id_counter_;

      // Ready キュー登録
      ready_queue_.push_back({
        .handle = {},     // move-constructed later
        .mem = mem,
        .coro_id = coro_id,
        .created_at = timestamp_now()
      });

      // ===== ハイパーバイザ開発者向けログ =====
      #ifdef FIREBALL_DEBUG_CORO
      std::println(stderr, "[COOS] coroutine spawned: id={}, mem={:p}",
                   coro_id, mem);
      #endif

      return coro_id;

    } catch (const std::exception& e) {
      std::cerr << std::format("[COOS:ERROR] spawn failed: {}", e.what()) << std::endl;
      return INVALID_CORO_ID;
    }
  }

 private:
  /**
   * RAII Guard for TLS cleanup
   * C++20 style - Scope exit guarantee
   */
  struct TLSGuard {
    ~TLSGuard() {
      co_mem::set_current_instance(nullptr);
    }
  };
};
```

### 3.2 Promise での co_mem 取得パターン

```cpp
/**
 * Promise 内で co_mem を取得する 2 つのパターン
 */

// ===== Pattern 1: TLS (Thread Local Storage) を使用 =====
//
// spawn() で TLS に co_mem を設定してから coroutine を構築
// Promise::operator new で TLS から取得

struct promise_type {
  void* operator new(size_t size) {
    // spawn() で TLS に設定された co_mem を取得
    auto* mem = co_mem::get_current_instance();
    if (!mem) {
      // Fallback: グローバルインスタンス
      mem = co_mem::get_default_instance();
    }
    return mem->allocate(size);
  }
};

// ===== Pattern 2: Promise に co_mem ポインタを埋め込む =====
//
// Template を使用して Promise 構築時に co_mem を注入

template<typename T>
class task_with_mem {
 public:
  struct promise_type {
    co_mem* mem_;  // ← コンストラクタで設定

    promise_type() : mem_(nullptr) {}

    void set_mem(co_mem* m) {
      mem_ = m;
    }

    void* operator new(size_t size) {
      // Promise に保持している mem_ から割り当て
      if (!mem_) {
        throw std::bad_alloc();
      }
      return mem_->allocate(size);
    }

    void operator delete(void* ptr, size_t size) noexcept {
      if (mem_) {
        mem_->deallocate(ptr, size);
      }
    }

    // ... other promise methods ...
  };

  // Factory function
  static task_with_mem create(co_mem* mem) {
    // coroutine 構築時に mem を注入
    auto handle = /* ... create coroutine ... */;
    handle.promise().set_mem(mem);
    return task_with_mem{handle};
  }
};
```

**推奨：Pattern 1 (TLS)**
- シンプル
- 既存コード変更最小限
- std::function ラッパと互換性あり

**代替案：Pattern 2 (Promise 埋め込み)**
- Type-safe
- TLS より明示的
- Template 実装複雑化

### 3.2 Run（イベントループ）

```cpp
void co_sched_impl::run() override {
  while (!ready_queue_.empty()) {
    // Ready キュー先頭をポップ
    auto task = std::move(ready_queue_.front());
    ready_queue_.pop_front();

    // コルーチンを 1 ステップ実行
    if (!task.done()) {
      // resume() は以下を実行：
      // 1. handle.resume()
      // 2. コルーチン実行（最初の co_await/co_yield まで）
      // 3. Suspend して戻る
      bool is_suspended = task.resume();

      if (is_suspended) {
        // co_await で中断 → Ready キュー再登録
        ready_queue_.push_back(std::move(task));
      } else {
        // co_return で完了 → completed_count_ インクリメント
        completed_count_++;
        // task はデストラクタで自動 destroy
      }
    }
  }
}

bool co_sched_impl::step() override {
  if (ready_queue_.empty()) {
    return false;
  }

  auto task = std::move(ready_queue_.front());
  ready_queue_.pop_front();

  if (!task.done()) {
    bool is_suspended = task.resume();
    if (is_suspended) {
      ready_queue_.push_back(std::move(task));
    } else {
      completed_count_++;
    }
  }

  return true;
}
```

---

## 4. co_mem インターフェース

### 4.0 TLS (Thread Local Storage) 管理

```cpp
/**
 * co_mem TLS 管理
 *
 * spawn() → set_current_instance(mem) → coroutine 構築
 * → Promise::operator new (get_current_instance() で mem 取得)
 * → 完了 → set_current_instance(nullptr)
 */
class co_mem {
 private:
  // スレッドローカル変数（各スレッド独立）
  static thread_local co_mem* current_instance_;
  static co_mem* default_instance_;

 public:
  /**
   * TLS に co_mem インスタンスを設定
   *
   * spawn() 内で呼ぶ：
   *   co_mem::set_current_instance(module_mem);
   *   // coroutine 構築（Promise::operator new で TLS 参照）
   *   co_mem::set_current_instance(nullptr);  // cleanup
   */
  static void set_current_instance(co_mem* instance) {
    current_instance_ = instance;
  }

  /**
   * TLS から co_mem インスタンスを取得
   *
   * Promise::operator new で呼ぶ：
   *   auto* mem = co_mem::get_current_instance();
   *   if (!mem) mem = co_mem::get_default_instance();
   */
  static co_mem* get_current_instance() {
    return current_instance_;
  }

  /**
   * デフォルト（Fallback）インスタンスを取得
   */
  static co_mem* get_default_instance() {
    return default_instance_;
  }

  /**
   * デフォルトインスタンスを設定（初期化時に 1 回）
   */
  static void set_default_instance(co_mem* instance) {
    default_instance_ = instance;
  }

  /**
   * モジュール固有インスタンスを取得
   *
   * @param module_id: WASM モジュール ID
   * @return: Module 用 co_mem インスタンス
   */
  static co_mem* get_module_instance(uint32_t module_id);
};

// 実装例
thread_local co_mem* co_mem::current_instance_ = nullptr;
co_mem* co_mem::default_instance_ = nullptr;
```

### 4.1 co_mem との統合

```cpp
/**
 * Memory Manager
 * WASM モジュールごと独立 mspace
 */
class co_mem {
 private:
  static constexpr size_t COROUTINE_FRAME_BUDGET = 4096;  // 1 frame per 4KB

  // 各 WASM モジュール用に独立 dlmalloc mspace
  mspace module_heaps_[MAX_MODULES];

 public:
  /**
   * Frame 割り当て（Promise::operator new から呼ばれる）
   *
   * @param size: フレームサイズ
   * @return: 割り当てたメモリポインタ
   */
  void* allocate(size_t size) {
    // 4KB 単位で align（CPU キャッシュ効率改善）
    size_t aligned_size = ((size + COROUTINE_FRAME_BUDGET - 1)
                          / COROUTINE_FRAME_BUDGET)
                        * COROUTINE_FRAME_BUDGET;

    // 現在のモジュール (TLS) の mspace から割り当て
    uint32_t module_id = get_current_module_id();
    void* ptr = mspace_malloc(module_heaps_[module_id], aligned_size);

    if (!ptr) {
      logger_->log_error("coroutine frame allocation failed: module={}, size={}",
                        module_id, aligned_size);
      return nullptr;
    }

    // フレーム情報記録（debugger 用）
    frame_registry_.register_frame({ptr, aligned_size, module_id});

    return ptr;
  }

  /**
   * Frame 解放（Promise::operator delete から呼ばれる）
   *
   * @param ptr: フレームメモリポインタ
   * @param size: フレームサイズ
   */
  void deallocate(void* ptr, size_t size) noexcept {
    uint32_t module_id = get_current_module_id();
    mspace_free(module_heaps_[module_id], ptr);
    frame_registry_.unregister_frame(ptr);
  }

  /**
   * Module heap 使用量統計
   */
  void get_stats(uint32_t module_id, memory_stats* stats) {
    struct mallinfo info = mspace_mallinfo(module_heaps_[module_id]);
    stats->allocated = info.uordblks;
    stats->free = info.fordblks;
    stats->total = info.arena;
  }
};
```

### 4.2 隔離性の保証

```
┌─────────────────────────────────────┐
│         Global Heap                 │
│ (dlmalloc default: 64KB)            │
└─────────────────────────────────────┘
   │
   ├─ Module A mspace (16KB)
   │   └─ Coroutine A-1 Frame (4KB)
   │   └─ Coroutine A-2 Frame (4KB)
   │
   ├─ Module B mspace (16KB)
   │   └─ Coroutine B-1 Frame (8KB)
   │   └─ Coroutine B-2 Frame (4KB)
   │
   └─ System Reserve (16KB)
       └─ COOS Kernel Frame (varies)

Module A のヒープ枯渇 → Module B に波及しない ✓
System がクラッシュしない ✓
```

---

## 5. 実装例：実際のコルーチン

### 5.1 シンプルな計算タスク

```cpp
// WASM モジュール内で定義
namespace fireball { namespace wasm {

/**
 * 計算タスク（戻り値あり）
 */
coos::task<int> compute_fibonacci(int n) {
  if (n <= 1) co_return n;

  // async operation 実行（logger service への IPC 等）
  co_await logger::log("computing fib({})", n);

  // 再帰的に呼び出し
  int fib_n_minus_1 = co_await compute_fibonacci(n - 1);
  int fib_n_minus_2 = co_await compute_fibonacci(n - 2);

  int result = fib_n_minus_1 + fib_n_minus_2;
  co_return result;
}

}} // namespace
```

### 5.2 イベントハンドラループ

```cpp
/**
 * GPIO イベントハンドラ（協調的無限ループ）
 */
coos::task<void> gpio_event_handler() {
  while (true) {
    // GPIO ピンから状態読み取り（HAL via IPC）
    uint8_t state = co_await hal::gpio_read(GPIO_PIN_5);

    if (state) {
      // LED 点灯
      co_await hal::gpio_write(GPIO_PIN_LED, 1);
    }

    // イベント処理完了、制御を他タスクに譲る
    co_await coos::yield();
  }
}
```

### 5.3 タイムアウト付き待機

```cpp
/**
 * Sensor reading with timeout
 */
coos::task<sensor_data> read_sensor_with_timeout(int timeout_ms) {
  try {
    // タイマーチャネルで timeout を実装
    auto timer_ch = co_await timer_service::create_timer(timeout_ms);

    // センサー読み取りか timeout どちらか先に完了
    auto sensor_ch = sensor_service::request_read();

    // Select-like 待機（どちらか先着）
    auto result = co_await coos::select(sensor_ch, timer_ch);

    if (result == sensor_ch) {
      sensor_data data = co_await sensor_ch;
      co_return data;
    } else {
      throw std::runtime_error("sensor read timeout");
    }
  } catch (const std::exception& e) {
    co_await logger::log_error("sensor timeout: {}", e.what());
    throw;
  }
}
```

---

## 6. パフォーマンス特性

### 6.1 Frame 割り当て/解放

| 操作 | コスト | 備考 |
|------|--------|------|
| Frame 割り当て | ~100-500 サイクル | co_mem→dlmalloc mspace_malloc |
| Frame 解放 | ~50-200 サイクル | mspace_free |
| resume() | ~35 命令 | handle.resume() + state restore |
| Suspend | O(フレームサイズ) | 自動（コンパイラ生成） |

### 6.2 メモリ予算

```
WCH CH32V307 (96KB SRAM):
  ├─ System: 30KB
  ├─ Coroutine A (16KB frame): 16KB
  ├─ Coroutine B (16KB frame): 16KB
  └─ Available: ~34KB

nRF52840 (256KB SRAM):
  ├─ System: 50KB
  ├─ 8 × Coroutine (16KB frame): 128KB
  └─ Available: ~78KB
```

---

## 7. デバッグ機能

### 7.1 Frame Dump

```cpp
void print_frame_info(const task<>& t) {
  auto& promise = t.handle_.promise();
  printf("Frame Info:\n");
  printf("  Ptr: %p\n", promise.stack_.frame_ptr);
  printf("  Size: %zu bytes\n", promise.stack_.frame_size);
  printf("  Alloc Time: %llu\n", promise.stack_.alloc_time);
  printf("  Done: %s\n", t.done() ? "yes" : "no");
}
```

### 7.2 全コルーチン一覧

```cpp
void co_sched_impl::dump_coroutines() const {
  printf("Active Coroutines: %zu\n", ready_queue_.size());
  size_t index = 0;
  for (const auto& task : ready_queue_) {
    printf("  [%zu] done=%s\n", index++, task.done() ? "true" : "false");
  }
  printf("Completed: %zu\n", completed_count_);
}
```

---

## 8. 実装チェックリスト

- [ ] `task<T>` テンプレートクラス実装（通常版）
- [ ] `task<void>` 特殊化実装
- [ ] Promise 型の `operator new/delete` 実装
- [ ] `resume()` / `done()` / `get()` メソッド実装
- [ ] Awaiter インターフェース実装（co_await 対応）
- [ ] `co_sched_impl::spawn()` 実装
- [ ] Ready キュー管理（deque or ring buffer）
- [ ] `step()` と `run()` のイベントループ実装
- [ ] co_mem との統合（mspace 割り当て）
- [ ] Frame 追跡と debugger サポート
- [ ] 全コルーチンダンプ機能
- [ ] ユニットテスト：単純な計算、await、例外処理
- [ ] 統合テスト：複数コルーチン同時実行、yield

---

## 9. コルーチンリソースの固定化

### 9.1 最大コルーチン数とヒープサイズの定義

Fireball におけるコルーチン数とそれに伴うメモリリソース（特にヒープサイズ）は、組込みシステムの厳格なリソース制約と決定論的動作の要件を満たすために、ヘッダファイル形式のコンフィグレーションを通じて静的に固定されます。

**設計原則：**

- **ヘッダファイルによる固定化**: `inc/fireball_config.hxx` (仮称)のような共通ヘッダファイル内で、マクロ定義 (`#define`) を用いて最大コルーチン数 (`MAX_COROUTINES`) や、各ゲストモジュールに割り当てるヒープの総サイズ (`GUEST_HEAP_SIZE_KB`) などをコンパイル時に確定させます。
- **動的オーバーヘッドの排除**: ランタイムでの動的なリソース交渉や再割り当てのオーバーヘッド、および予測不可能なメモリ断片化を排除します。
- **メモリフットプリントの最適化**: 開発者は、ターゲットデバイスのSRAM/DRAM容量に応じて、これらのマクロ値を調整することで、メモリフットプリントを最大限に最適化できます。
- **デバッグと検証の容易さ**: 静的なリソース割り当ては、メモリマップの検証やシステムのデバッグを容易にし、確定的な動作を保証します。

**実装例（`inc/fireball_config.hxx`）：**

```cpp
#ifndef FIREBALL_CONFIG_HXX
#define FIREBALL_CONFIG_HXX

// 最大コルーチン数
// このマクロにより、スケジューラや関連するデータ構造のサイズが決定されます。
#define FIREBALL_MAX_COROUTINES         8       // デフォルト8個に固定

// 各ゲストモジュールに割り当てる最大ヒープサイズ（KB単位）
// co_memがこのサイズを元にm_spaceを初期化します。
#define FIREBALL_GUEST_HEAP_SIZE_KB     16      // 1ゲストあたり16KB

// コルーチンスタックのデフォルトサイズ（KB単位）
// 各コルーチンが確保するスタックメモリの上限を定義します。
#define FIREBALL_COROUTINE_STACK_SIZE_KB 8      // 1コルーチンあたり8KB

#endif // FIREBALL_CONFIG_HXX
```

**co_memおよびco_schedへの影響：**

- `co_mem`: `FIREBALL_GUEST_HEAP_SIZE_KB` を参照して、各 `m_space` の初期サイズを決定します。
- `co_sched`: `FIREBALL_MAX_COROUTINES` を参照して、内部のコルーチン管理テーブルや `ready_queue` の容量を最適化（例: 固定長配列の使用）できます。これにより、動的なコンテナによるオーバーヘッドを削減し、予測可能な性能を提供します。

### 9.2 メモリ予算とコルーチン数の関係

`FIREBALL_COROUTINE_STACK_SIZE_KB` が各コルーチンのスタックサイズに影響を与え、これが `FIREBALL_MAX_COROUTINES` と組み合わさることで、システム全体のメモリ予算に直接的な影響を与えます。開発者はこれらの値を慎重に選択し、ターゲットデバイスのRAM容量とアプリケーションの要件のバランスを取る必要があります。

## まとめ

Fireball のコルーチン実装は：

1. **C++20 Promise Pattern** で自動フレーム管理
2. **カスタム allocator** で co_mem から割り当て
3. **モジュールごと隔離** で fault tolerance 確保
4. **Ready キュー** で協調的スケジューリング
5. **Awaiter インターフェース** で coroutine チェーン対応

メモリ制約の組み込みシステムでも、複雑なロック機構なしに安全なマルチタスクを実現。
