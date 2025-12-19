/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef FIREBALL_ALLOCATOR_STDCXX_ALLOCATOR_HXX
#define FIREBALL_ALLOCATOR_STDCXX_ALLOCATOR_HXX

#include <allocator/specified_allocator.hxx>
#include <commons.hxx>
#include <new>

namespace fireball {
namespace allocator {

/**
 * stdcxx_allocator_tag - Type tag for C++ standard library allocator.
 *
 * This tag distinguishes the C++ standard library allocator instance from other
 * specified_allocator instances in the system. It enables compile-time differentiation
 * of allocator instances while maintaining type safety.
 */
struct stdcxx_allocator_tag {};

/**
 * stdcxx_allocator - Global allocator for C++ standard library containers.
 *
 * This is a type alias for specified_allocator configured with the host heap size
 * (FIREBALL_HOST_HEAP_SIZE). It provides flexible allocation/deallocation for
 * standard library containers (vector, map, string, etc.) used throughout the
 * hypervisor. The allocator is implemented as a singleton to ensure all standard
 * library allocations share the same heap partition, preventing fragmentation
 * across multiple allocator instances.
 */
using stdcxx_allocator = specified_allocator<FIREBALL_HOST_HEAP_SIZE, stdcxx_allocator_tag>;

} // namespace allocator
} // namespace fireball

[[nodiscard]]
extern void* operator new(std::size_t num);

[[nodiscard]]
extern void* operator new(std::size_t num, std::align_val_t);

[[nodiscard]]
extern void* operator new(std::size_t num, const std::nothrow_t&) noexcept;

[[nodiscard]]
extern void* operator new(std::size_t num, std::align_val_t align, const std::nothrow_t&) noexcept;

extern void operator delete(void* ptr) noexcept;

extern void operator delete(void* ptr, std::size_t num) noexcept;

void operator delete(void* ptr, std::align_val_t align) noexcept;

extern void operator delete(void* ptr, std::size_t num, std::align_val_t align) noexcept;

extern void operator delete(void* ptr, const std::nothrow_t&) noexcept;

extern void operator delete(void* ptr, std::align_val_t align, const std::nothrow_t&) noexcept;

#endif // #ifndef FIREBALL_ALLOCATOR_STDCXX_ALLOCATOR_HXX
