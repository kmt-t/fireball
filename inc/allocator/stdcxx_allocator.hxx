#pragma once

#include <cstddef>
#include <new>

namespace fireball::allocator {

class stdcxx_allocator {
public:
  static stdcxx_allocator& instance();
  void* allocate(size_t size, size_t alignment = 0);
  void deallocate(void* ptr, size_t size, size_t alignment = 0);
};

} // namespace fireball::allocator
