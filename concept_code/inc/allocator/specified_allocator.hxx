/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2024 Takuya Matsunaga.
 */
#ifndef __SPECIFIED_ALLOCATOR_HXX__
#define __SPECIFIED_ALLOCATOR_HXX__

#include <commons.hxx>
#include <array>
#include <memory_resource>

extern "C" {

	typedef void* mspace;
	extern void* create_mspace_with_base(void*, std::size_t, int);
	extern std::size_t destroy_mspace(mspace);
	extern void* mspace_memalign(mspace, std::size_t, std::size_t);
	extern void mspace_free(mspace, void*);

}  // extern "C" {

namespace fireball {
	namespace allocator {

		template <uint32_t N, typename Tag>
		struct specified_allocator : public std::pmr::memory_resource {
		public:

			static specified_allocator& instance() {
				static specified_allocator inst;
				return inst;
			}

			void* do_allocate(std::size_t bytes, std::size_t alignment) override {
				return mspace_ == nullptr ? nullptr : mspace_memalign(mspace_, alignment, bytes);
			}

			void do_deallocate(void* p, std::size_t bytes, [[maybe_unused]] std::size_t alignment) override {
				if (mspace_ != nullptr) {
					mspace_free(mspace_, p);
				}
			}

			bool do_is_equal(const memory_resource& other) const noexcept override {
				return this == &other;
			}

			template <typename T>
			static std::pmr::polymorphic_allocator<T>& allocator() {
			  static std::pmr::polymorphic_allocator<T> inst(specified_allocator::instance());
				return inst;
			}

		private:

			specified_allocator() : std::pmr::memory_resource(), arena_(){
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

		};  // struct specified_allocator : public std::pmr::memory_resource {

	}  // namespace allocator {
}  // namespace fireball {

#endif  // #ifndef __SPECIFIED_ALLOCATOR_HXX__
