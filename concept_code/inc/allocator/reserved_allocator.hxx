/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2024 Takuya Matsunaga.
 */
#ifndef __RESERVED_ALLOCATOR_HXX__
#define __RESERVED_ALLOCATOR_HXX__

#include <commons.hxx>
#include <allocator/specified_allocator.hxx>
#include <allocator/bump_allocator.hxx>

namespace fireball {
	namespace allocator {

		static constexpr uint32_t SYS_ALLOCATOR_SIZE = 2048;
		static constexpr uint32_t VM_ALLOCATOR_SIZE = 4096;

		struct sys_allocator_tag {};
		struct vm_allocator_tag {};

		using sys_allocator = bump_allocator<SYS_ALLOCATOR_SIZE, sys_allocator_tag>;
		using vm_allocator = bump_allocator<VM_ALLOCATOR_SIZE, vm_allocator_tag>;

	}
}

#endif  // #ifndef __RESERVED_ALLOCATOR_HXX__
