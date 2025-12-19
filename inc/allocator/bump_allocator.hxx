/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef __BUMP_ALLOCATOR_HXX__
#define __BUMP_ALLOCATOR_HXX__

#include <commons.hxx>
#include <memory_resource>

namespace fireball {
namespace allocator {

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

#endif // #ifndef __BUMP_ALLOCATOR_HXX__
