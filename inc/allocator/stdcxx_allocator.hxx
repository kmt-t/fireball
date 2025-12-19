/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef __STDCXX_ALLOCATOR_HXX__
#define __STDCXX_ALLOCATOR_HXX__

#include <allocator/specified_allocator.hxx>
#include <commons.hxx>
#include <new>

namespace fireball {
namespace allocator {

struct stdcxx_allocator_tag {};

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

#endif // #ifndef __STDCXX_ALLOCATOR_HXX__
