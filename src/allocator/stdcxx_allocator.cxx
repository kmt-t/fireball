/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */

/**
 * stdcxx_allocator.cxx - Global operator new/delete overrides for C++ standard library.
 *
 * This file implements the global operator new/delete functions to redirect all C++
 * standard library memory allocations to the stdcxx_allocator (specified_allocator).
 * This ensures that all standard library containers and objects use the dedicated
 * host heap partition, enabling fine-grained memory management and isolation from
 * other heap partitions (COOS kernel, WASM runtime, subsystem, service, guest).
 *
 * Error Handling:
 *   - Allocation failures report an error with a backtrace using the
 *     THROW_NESTED_BACKTRACE macro.
 *   - When __cpp_exceptions is defined, this throws std::bad_alloc with a nested
 *     exception_with_backtrace.
 *   - When exceptions are disabled, this prints the backtrace and calls std::terminate().
 *   - nothrow variants return nullptr on allocation failure.
 *
 * Alignment Support:
 *   - All alignment-aware operator new/delete variants properly forward alignment
 *     requirements to the underlying allocator.
 */
#include <allocator/stdcxx_allocator.hxx>
#include <utils/backtrace.hxx>

[[nodiscard]]
void* operator new(std::size_t num) {
  auto ret = fireball::allocator::stdcxx_allocator::instance().allocate(num);
  if (ret == nullptr) {
    THROW_NESTED_BACKTRACE("std::bad_alloc", std::bad_alloc);
  }

  return ret;
}

[[nodiscard]]
void* operator new(std::size_t num, std::align_val_t align) {
  const auto a = static_cast<std::underlying_type<std::align_val_t>::type>(align);
  auto ret = fireball::allocator::stdcxx_allocator::instance().allocate(num, a);
  if (ret == nullptr) {
    THROW_NESTED_BACKTRACE("std::bad_alloc", std::bad_alloc);
  }

  return ret;
}

[[nodiscard]]
void* operator new(std::size_t num, const std::nothrow_t&) noexcept {
  return fireball::allocator::stdcxx_allocator::instance().allocate(num);
}

[[nodiscard]]
void* operator new(std::size_t num, std::align_val_t align, const std::nothrow_t&) noexcept {
  const auto a = static_cast<std::underlying_type<std::align_val_t>::type>(align);
  return fireball::allocator::stdcxx_allocator::instance().allocate(num, a);
}

void operator delete(void* ptr) noexcept {
  fireball::allocator::stdcxx_allocator::instance().deallocate(ptr, 0U);
}

void operator delete(void* ptr, std::size_t num) noexcept {
  fireball::allocator::stdcxx_allocator::instance().deallocate(ptr, num);
}

void operator delete(void* ptr, std::align_val_t align) noexcept {
  const auto a = static_cast<std::underlying_type<std::align_val_t>::type>(align);
  fireball::allocator::stdcxx_allocator::instance().deallocate(ptr, 0U, a);
}

void operator delete(void* ptr, std::size_t num, std::align_val_t align) noexcept {
  const auto a = static_cast<std::underlying_type<std::align_val_t>::type>(align);
  fireball::allocator::stdcxx_allocator::instance().deallocate(ptr, num, a);
}

void operator delete(void* ptr, const std::nothrow_t&) noexcept {
  fireball::allocator::stdcxx_allocator::instance().deallocate(ptr, 0U);
}

void operator delete(void* ptr, std::align_val_t align, const std::nothrow_t&) noexcept {
  const auto a = static_cast<std::underlying_type<std::align_val_t>::type>(align);
  fireball::allocator::stdcxx_allocator::instance().deallocate(ptr, 0U, a);
}
