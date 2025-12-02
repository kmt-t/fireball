/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef __COOS_CO_VALUE_HXX__
#define __COOS_CO_VALUE_HXX__

#include <commons.hxx>
#include <cstdint>
#include <utility>

namespace fireball { namespace coos {

/**
 * CSP value container with ownership tracking
 *
 * Transfers ownership of values between processes through channels.
 * Once sent, the value is no longer accessible from the sender.
 * Access from a process other than the owner triggers assertion.
 */
template <typename T>
class co_value {
 public:
  /**
   * Constructor: creates uninitialized value (invalid owner)
   */
  co_value() : owner_id_(0xFFFFFFFFU), value_(), is_valid_(false) {}

  /**
   * Constructor: creates value with initial owner
   */
  explicit co_value(uint32_t owner_id, T value = T())
      : owner_id_(owner_id), value_(std::move(value)), is_valid_(true) {}

  /**
   * Move constructor: transfers ownership
   */
  co_value(co_value&& other) noexcept
      : owner_id_(other.owner_id_),
        value_(std::move(other.value_)),
        is_valid_(other.is_valid_) {
    // Invalidate source
    other.is_valid_ = false;
  }

  /**
   * Move assignment: transfers ownership
   */
  co_value& operator=(co_value&& other) noexcept {
    if (this != &other) {
      owner_id_ = other.owner_id_;
      value_ = std::move(other.value_);
      is_valid_ = other.is_valid_;
      other.is_valid_ = false;
    }
    return *this;
  }

  // Copy operations are deleted
  co_value(const co_value&) = delete;
  co_value& operator=(const co_value&) = delete;

  /**
   * Get value (with ownership verification)
   */
  T& get(uint32_t accessor_id) {
    ASSERT_WITH_BACKTRACE(is_valid_);
    ASSERT_WITH_BACKTRACE(owner_id_ == accessor_id);
    return value_;
  }

  /**
   * Get const value (with ownership verification)
   */
  const T& get(uint32_t accessor_id) const {
    ASSERT_WITH_BACKTRACE(is_valid_);
    ASSERT_WITH_BACKTRACE(owner_id_ == accessor_id);
    return value_;
  }

  /**
   * Release value (transfer ownership, return old owner)
   */
  T release(uint32_t current_owner) {
    ASSERT_WITH_BACKTRACE(is_valid_);
    ASSERT_WITH_BACKTRACE(owner_id_ == current_owner);
    is_valid_ = false;
    return std::move(value_);
  }

  /**
   * Transfer ownership to new owner
   */
  void transfer_ownership(uint32_t new_owner) {
    ASSERT_WITH_BACKTRACE(is_valid_);
    owner_id_ = new_owner;
  }

  /**
   * Check validity
   */
  bool is_valid() const { return is_valid_; }

  /**
   * Get current owner
   */
  uint32_t get_owner() const { return owner_id_; }

  /**
   * Check ownership
   */
  bool is_owned_by(uint32_t id) const {
    return is_valid_ && owner_id_ == id;
  }

 private:
  uint32_t owner_id_;
  T value_;
  bool is_valid_;
};

/**
 * Specialized version for void (no value transfer)
 */
template <>
class co_value<void> {
 public:
  co_value() : owner_id_(0xFFFFFFFFU), is_valid_(false) {}

  explicit co_value(uint32_t owner_id)
      : owner_id_(owner_id), is_valid_(true) {}

  co_value(co_value&& other) noexcept
      : owner_id_(other.owner_id_), is_valid_(other.is_valid_) {
    other.is_valid_ = false;
  }

  co_value& operator=(co_value&& other) noexcept {
    if (this != &other) {
      owner_id_ = other.owner_id_;
      is_valid_ = other.is_valid_;
      other.is_valid_ = false;
    }
    return *this;
  }

  co_value(const co_value&) = delete;
  co_value& operator=(const co_value&) = delete;

  void verify_owner(uint32_t accessor_id) const {
    ASSERT_WITH_BACKTRACE(is_valid_);
    ASSERT_WITH_BACKTRACE(owner_id_ == accessor_id);
  }

  bool is_valid() const { return is_valid_; }

  uint32_t get_owner() const { return owner_id_; }

  bool is_owned_by(uint32_t id) const {
    return is_valid_ && owner_id_ == id;
  }

 private:
  uint32_t owner_id_;
  bool is_valid_;
};

} } // namespace fireball { namespace coos {

#endif // #ifndef __COOS_CO_VALUE_HXX__
