/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2024 Takuya Matsunaga.
 */
#ifndef __BASE_DEVICE_HXX__
#define __BASE_DEVICE_HXX__

#include <commons.hxx>

namespace fireball {
namespace hal {

class event_notifier;

class base_device {
 public:
  virtual ~base_device() = default;

  virtual const event_notifier* notifier() const = 0;
  virtual event_notifier* notifier() = 0;
  virtual bool open() = 0;
  virtual void close() = 0;
};  // class base_device

}  // namespace hal
}  // namespace fireball

#endif  // #ifndef __BASE_DEVICE_HXX__
