/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef FIREBALL_ALLOCATOR_SPECIFIED_ALLOCATOR_HXX
#define FIREBALL_ALLOCATOR_SPECIFIED_ALLOCATOR_HXX

#include <array>
#include <commons.hxx>
#include <memory_resource>

extern "C" {

typedef void* mspace;
extern void* create_mspace_with_base(void*, std::size_t, int);
extern std::size_t destroy_mspace(mspace);
extern void* mspace_memalign(mspace, std::size_t, std::size_t);
extern void mspace_free(mspace, void*);

} // extern "C" {

namespace fireball {
namespace allocator {

/**
 * specified_allocator - Flexible memory allocator using dlmalloc (mspace).
 *
 * This allocator wraps dlmalloc's mspace interface to provide flexible allocation and
 * deallocation within a fixed-size arena. Unlike bump_allocator, it supports arbitrary
 * allocation patterns with full deallocation capability, making it suitable for heap
 * partitions with dynamic memory requirements (e.g., subsystem heap, service heap).
 * The allocator manages fragmentation through dlmalloc's internal strategies and provides
 * O(log n) allocation/deallocation time complexity.
 *
 * Template Parameters:
 *   N   - Size of the arena in bytes (compile-time constant)
 *   Tag - Type tag for distinguishing multiple allocator instances
 */
template <uint32_t N, typename Tag> struct specified_allocator : public std::pmr::memory_resource {
public:
  using this_type = specified_allocator;

  static this_type& instance() {
    static this_type inst;
    return inst;
  }

  void* do_allocate(std::size_t bytes, std::size_t alignment) override {
    return mspace_ == nullptr ? nullptr : mspace_memalign(mspace_, alignment, bytes);
  }

  void do_deallocate(void* p, [[maybe_unused]] std::size_t bytes,
                     [[maybe_unused]] std::size_t alignment) override {
    if (mspace_ != nullptr) {
      mspace_free(mspace_, p);
    }
  }

  bool do_is_equal(const memory_resource& other) const noexcept override { return this == &other; }

  template <typename T> struct allocator : public std::pmr::polymorphic_allocator<T> {
    allocator() : std::pmr::polymorphic_allocator<T>(&this_type::instance()) {}
  };

private:
  specified_allocator() : std::pmr::memory_resource(), arena_() {
    mspace_ = create_mspace_with_base(arena_, N, 0);
  }

  ~specified_allocator() {
    if (mspace_ != nullptr) {
      destroy_mspace(mspace_);
      mspace_ = nullptr;
    }
  }

  void* mspace_;
  uint8_t arena_[N];

}; // struct specified_allocator : public std::pmr::memory_resource {

} // namespace allocator
} // namespace fireball

#endif // #ifndef FIREBALL_ALLOCATOR_SPECIFIED_ALLOCATOR_HXX
