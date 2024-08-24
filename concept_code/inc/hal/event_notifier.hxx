/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2024 Takuya Matsunaga.
 */
#ifndef __NOTIFIER_HXX__
#define __NOTIFIER_HXX__

#include <commons.hxx>
#include <functional>

namespace fireball {
namespace hal {

class base_device;

class event_notifier {
 public:
  using event_slot_type = uint16_t;
  using event_handler_type = std::function<void(base_device*, event_slot_type)>;

  static constexpr event_slot_type EVENT_SLOT_0 = 1 << 0;
  static constexpr event_slot_type EVENT_SLOT_1 = 1 << 1;
  static constexpr event_slot_type EVENT_SLOT_2 = 1 << 2;
  static constexpr event_slot_type EVENT_SLOT_3 = 1 << 3;
  static constexpr event_slot_type EVENT_SLOT_4 = 1 << 4;
  static constexpr event_slot_type EVENT_SLOT_5 = 1 << 5;
  static constexpr event_slot_type EVENT_SLOT_6 = 1 << 6;
  static constexpr event_slot_type EVENT_SLOT_7 = 1 << 7;
  static constexpr event_slot_type EVENT_SLOT_8 = 1 << 8;
  static constexpr event_slot_type EVENT_SLOT_9 = 1 << 9;
  static constexpr event_slot_type EVENT_SLOT_10 = 1 << 10;
  static constexpr event_slot_type EVENT_SLOT_11 = 1 << 11;
  static constexpr event_slot_type EVENT_SLOT_12 = 1 << 12;
  static constexpr event_slot_type EVENT_SLOT_13 = 1 << 13;
  static constexpr event_slot_type EVENT_SLOT_14 = 1 << 14;
  static constexpr event_slot_type EVENT_SLOT_15 = 1 << 15;

  base_device* device() { return device_; }
  const base_device* device() const { return device_; }

  const event_handler_type& handler() const { return handler_; }
  void handler(const event_handler_type& handler) { handler_ = handler; }

  void mask(event_slot_type slot);

  void notify(event_slot_type slot);

  event_notifier(base_device* device);

 private:
  base_device* device_;
  event_slot_type mask_;
  event_handler_type handler_;
};  // class event_notifier

}  // namespace hal
}  // namespace fireball

#endif  // #ifndef __NOTIFIER_HXX__
