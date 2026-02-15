#include "allocator/stdcxx_allocator.hxx"
#include <cstdlib>

namespace fireball::allocator {

stdcxx_allocator& stdcxx_allocator::instance() {
  static stdcxx_allocator instance__;
  return instance__;
}

void* stdcxx_allocator::allocate(size_t size, size_t alignment) {
  (void)alignment;
  return std::malloc(size);
}

void stdcxx_allocator::deallocate(void* ptr, size_t size, size_t alignment) {
  (void)size;
  (void)alignment;
  std::free(ptr);
}

} // namespace fireball::allocator
