/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#include <allocator/stdcxx_allocator.hxx>

[[nodiscard]]
void* operator new(std::size_t num) {
  auto ret = fireball::allocator::stdcxx_allocator::instance().allocate(num);
  if (ret == nullptr) {
    throw std::bad_alloc();
  }

  return ret;
}

[[nodiscard]]
void* operator new(std::size_t num, std::align_val_t) {
  auto ret = fireball::allocator::stdcxx_allocator::instance().allocate(num);
  if (ret == nullptr) {
    throw std::bad_alloc();
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
