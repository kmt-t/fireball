/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2024 Takuya Matsunaga.
 */
#ifndef __CLOCK_DEVICE_HXX__
#define __CLOCK_DEVICE_HXX__

#include <commons.hxx>
#include <hal/base_device.hxx>
#include <hal/event_notifier.hxx>

namespace fireball {
namespace hal {

class clock_device : public base_device {
 public:
  static constexpr event_notifier::event_slot_type TICK_EVENT =
      event_notifier::EVENT_SLOT_0;

  uint64_t precision() const { return precision_; }
  uint64_t tick() const { return tick_; }

  void countup() {
    if (opened_ == true) {
      tick_ += precision_;
      notifier_.notify(TICK_EVENT);    
    }
  }

  const event_notifier* notifier() const override { return &notifier_; }
  event_notifier* notifier() override { return &notifier_; }
  bool open() override;
  void close() override;

  clock_device(uint64_t pred);

 private:

  event_notifier notifier_;
  bool opened_;
  uint64_t precision_;
  uint64_t tick_;
};  // class clock_device

}  // namespace hal
}  // namespace fireball

#endif  // #ifndef __CLOCK_DEVICE_HXX__
