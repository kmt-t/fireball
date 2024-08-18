/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2024 Takuya Matsunaga.
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

			static bump_allocator& instance() {
				static bump_allocator inst;
				return inst;
			}

			template <typename T>
			static std::pmr::polymorphic_allocator<T>& allocator() {
				static std::pmr::polymorphic_allocator<T> inst(&bump_allocator::instance());
				return inst;
			}

		private:

			bump_allocator() : std::pmr::monotonic_buffer_resource(
				arena_, N, std::pmr::null_memory_resource()) {
				// nothing.
			}

			uint8_t arena_[N];
		};  // struct bump_allocator : public std::pmr::monotonic_buffer_resource

	}  // namespace allocator {
}  // namespace fireball {

#endif  // #ifndef __BUMP_ALLOCATOR_HXX__
