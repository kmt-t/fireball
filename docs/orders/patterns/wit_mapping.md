# WIT to Fireball-C++ Mapping Rules `{WIT_Mapping_Rules}`

Fireball follows a "WIT-First" approach for Tier 1 interfaces. This document defines the formal rules for mapping WIT definitions to Fireball's C++ "Stateless Interface + Harness" pattern.

## 1. General Principles

- **Stateless Interface**: WIT `interface` and `resource` map to C++ classes with pure virtual functions and no data members.
- **Harness Injection**: Dependencies are injected via the `Harness` structure (System Harness or Component Harness).
- **No Exceptions**: All methods that can fail must return `result<T, E>`.
- **Domain Namespaces**: Each WIT package/interface name should be mapped to a corresponding C++ namespace.

## 2. Type Mapping Table

| WIT Type | C++ Type | Fireball Alias / Detail |
| :--- | :--- | :--- |
| `u32` | `uint32_t` | `address`, `task_id`, `offset` |
| `u16` | `uint16_t` | `entry_count` |
| `bool` | `bool` | |
| `string` | `std::string_view` | Non-owning ROM strings |
| `list<u8>` | `binary_view` | `std::span<const uint8_t>` |
| `enum` | `enum class` | |
| `record` | `struct` | POD (Plain Old Data) |
| `result<T, recovery-strategy>` | `result<T, recovery_strategy>` | Fireball recovery strategy |
| `operation-result` | `operation_result` | `result<void, recovery_strategy>` |

## 3. Interface Mapping Patterns

### 3.1 WIT Interface -> C++ Interface
A WIT `interface` defines a group of functions.

**WIT:**
```wit
interface trigger {
    set-pin: func(pin: u32, value: bool) -> operation-result;
}
```

**C++:**
```cpp
namespace fireball::hal {
class trigger_if {
public:
    virtual ~trigger_if() = default;
    virtual operation_result set_pin(uint32_t pin, bool value) = 0;
};
}
```

### 3.2 WIT Resource -> C++ Interface
A WIT `resource` maps to a C++ interface class. Handles are managed by the host.

**WIT:**
```wit
resource timer-handle {
    now: func() -> u64;
}
```

**C++:**
```cpp
class timer_handle_if {
public:
    virtual uint64_t now() const = 0;
};
```

## 4. Harness Mapping

The `Harness` structure (Tier 1/2) aggregates these interfaces as raw pointers for static DI.

**C++:**
```cpp
struct vsoc_harness_t {
    hal::trigger_if* trigger;
    hal::timer_if* timer;
    services::ipc_router_if* ipc;
    // ...
};
```

## 5. Style Rules

1. **Snake Case**: Convert all `kebab-case` WIT identifiers to `snake_case` in C++.
2. **Postfix `_if`**: Append `_if` to interface classes.
3. **Postfix `_t`**: Append `_t` to structs and enums. Do NOT append `_t` to `using` aliases (Fireball style).
4. **Member `_`**: Interface classes must NOT have data members (no `_` postfix needed). POD structs members also don't use `_`.
