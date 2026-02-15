/**
 * Test for specified_allocator.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#include <allocator/specified_allocator.hxx>
#include <fireball_config.hxx>
#include <cassert>
#include <vector>
#include <iostream>

void test_specified_allocator_basic() {
  std::cout << "Testing specified_allocator basic allocation (8KB Arena)..." << std::endl;
  
  struct test_tag {};
  constexpr uint32_t arena_size = FB_CONF_TASK_HEAP_SIZE; // 8192U
  using test_allocator = fireball::allocator::specified_allocator<arena_size, test_tag>;
  
  auto& alloc = test_allocator::instance();
  
  // Test simple allocation
  void* p1 = alloc.allocate(128);
  assert(p1 != nullptr);
  std::cout << "Allocated 128 bytes at " << p1 << std::endl;
  
  void* p2 = alloc.allocate(256);
  assert(p2 != nullptr);
  assert(p1 != p2);
  std::cout << "Allocated 256 bytes at " << p2 << std::endl;
  
  // Test deallocation
  alloc.deallocate(p1, 128);
  alloc.deallocate(p2, 256);
  
  // Re-allocate
  void* p3 = alloc.allocate(512);
  assert(p3 != nullptr);
  std::cout << "Allocated 512 bytes at " << p3 << std::endl;
  alloc.deallocate(p3, 512);
  
  std::cout << "Basic allocation test passed!" << std::endl;
}

void test_specified_allocator_alignment() {
  std::cout << "Testing specified_allocator alignment (4KB Arena)..." << std::endl;
  
  struct align_tag {};
  constexpr uint32_t arena_size = 4096;
  using align_allocator = fireball::allocator::specified_allocator<arena_size, align_tag>;
  
  auto& alloc = align_allocator::instance();
  
  // Test various alignments
  size_t alignments[] = {8, 16, 32, 64, 128};
  for (auto a : alignments) {
    void* p = alloc.allocate(128, a);
    assert(p != nullptr);
    assert(reinterpret_cast<uintptr_t>(p) % a == 0);
    std::cout << "Allocated 128 bytes with alignment " << a << " at " << p << std::endl;
    alloc.deallocate(p, 128, a);
  }
  
  std::cout << "Alignment test passed!" << std::endl;
}

int main() {
  test_specified_allocator_basic();
  test_specified_allocator_alignment();
  
  std::cout << "All allocator tests passed!" << std::endl;
  return 0;
}
