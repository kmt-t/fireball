/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2024 Takuya Matsunaga.
 */
#ifndef __LOG_SERVICE_HXX__
#define __LOG_SERVICE_HXX__

#include <commons.hxx>
#include <array>

namespace fireball {
	namespace devices {
		namespace vmcore {

			class vmtimer {
			public:

				class timespan {
				public:

					uint64_t span() const {
						return tm_->tick() - start_;
					}

					timespan(const vmtimer* tm) : tm_(tm), start_(tm->tick()) {
						// nothiong.
					}

				private:
					const vmtimer* tm_;
					uint64_t start_;
				};

				uint64_t tick() const {
					return tk_;
				}

				const timespan span() const {
					return timespan(this);
				}

				vmtimer() : tk_(0) {
					// nothing.
				}

			private:

				void countup() {
					++tk_;
				}

				uint64_t tk_;

				friend class global_timer_registory;
			};  // class vmtimer {

			class global_timer_registory {
			public:

				static constexpr uint16_t SLOT_NUM = 4;

				static vmtimer* sys() {
					return &sys__;
				}

				static void register_countup(uint8_t index, vmtimer* tm) {
					slots__[index] = tm;
				}

				static void unregister_countup(uint8_t index) {
					slots__[index] = nullptr;
				}

			private:
				static void countup() {
					sys__.countup();
					for (auto tm : slots__) {
						if (tm == nullptr) {
							continue;
						}
						tm->countup();
					}
				}

				static vmtimer sys__;
				static std::array<vmtimer*, SLOT_NUM> slots__;

				friend class vmirq;
			};  // class global_timer_registory {

		}  // namespace vmcore {
	}  // namespace devices {
}  // namespace fireball {

#endif  // #ifndef __LOG_SERVICE_HXX__
