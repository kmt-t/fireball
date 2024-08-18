/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2024 Takuya Matsunaga.
 */
#ifndef __STDCXX_ALLOCATOR_HXX__
#define __STDCXX_ALLOCATOR_HXX__

#include <commons.hxx>
#include <new>
#include <allocator/specified_allocator.hxx>

namespace fireball {
  namespace allocator {
    struct stdcxx_allocator_tag {};
    static constexpr uint32_t STDCXX_ALLOCATOR_SIZE = 2048;
    using stdcxx_allocator = specified_allocator<STDCXX_ALLOCATOR_SIZE, stdcxx_allocator_tag>;
  }  // namespace allocator {
}  // namespace firevball {

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

extern void operator delete(void* ptr, std::align_val_t align) noexcept;

extern void operator delete(void* ptr, std::size_t num, std::align_val_t align) noexcept;

extern void operator delete(void* ptr, const std::nothrow_t&) noexcept;

extern void operator delete(void* ptr, std::align_val_t align, const std::nothrow_t&) noexcept;

#endif  // #ifndef __STDCXX_ALLOCATOR_HXX__
