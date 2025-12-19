/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef FIREBALL_ALLOCATOR_BUMP_ALLOCATOR_HXX
#define FIREBALL_ALLOCATOR_BUMP_ALLOCATOR_HXX

#include <commons.hxx>
#include <memory_resource>

namespace fireball {
namespace allocator {

/**
 * bump_allocator - Monotonic buffer resource for fixed-size heap partitions.
 *
 * This allocator implements a bump allocation strategy using std::pmr::monotonic_buffer_resource.
 * It is designed for heap partitions with predictable allocation patterns where deallocation
 * is not required (e.g., COOS kernel heap, WASM runtime heap). The allocator maintains a
 * fixed-size arena and allocates sequentially, returning memory only when the entire arena
 * is deallocated. This approach minimizes fragmentation and provides O(1) allocation time.
 *
 * Template Parameters:
 *   N   - Size of the arena in bytes (compile-time constant)
 *   Tag - Type tag for distinguishing multiple allocator instances
 */
template <uint32_t N, typename Tag>
struct bump_allocator : public std::pmr::monotonic_buffer_resource {
public:
  using this_type = bump_allocator;

  static this_type& instance() {
    static this_type inst;
    return inst;
  }

  template <typename T> struct allocator : public std::pmr::polymorphic_allocator<T> {
    allocator() : std::pmr::polymorphic_allocator<T>(&this_type::instance()) {}
  };

private:
  bump_allocator()
      : std::pmr::monotonic_buffer_resource(arena_, N, std::pmr::null_memory_resource()) {
    // nothing.
  }

  uint8_t arena_[N];
}; // struct bump_allocator : public std::pmr::monotonic_buffer_resource

} // namespace allocator
} // namespace fireball

#endif // #ifndef FIREBALL_ALLOCATOR_BUMP_ALLOCATOR_HXX
