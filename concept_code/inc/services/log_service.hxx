/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2024 Takuya Matsunaga.
 */
#ifndef __LOG_SERVICE_HXX__
#define __LOG_SERVICE_HXX__

#include <allocator/reserved_allocator.hxx>
#include <boost/circular_buffer.hpp>
#include <commons.hxx>
#include <coro/generator.hpp>
#include <coro/task.hpp>
#include <functional>
#include <variant>

namespace fireball {
namespace services {

class log_service {
 public:
  static constexpr uint32_t RINGBUFF_SIZE_SHIFT = 8U;
  static constexpr uint32_t RINGBUFF_SIZE = 1 << RINGBUFF_SIZE_SHIFT;
  static constexpr uint32_t RINGBUFF_SIZE_MASK = RINGBUFF_SIZE - 1U;

  static constexpr uint8_t CATEGORY_EVENT = 0U;
  static constexpr uint8_t CATEGORY_TIME = 1U;
  static constexpr uint8_t CATEGORY_TRACE = 2U;
  static constexpr uint8_t CATEGORY_VALUE_TAG = 3U;
  static constexpr uint8_t CATEGORY_VALUE_TAG_EOS = 4U;
  static constexpr uint8_t CATEGORY_VALUE_LOWWORD = 5U;
  static constexpr uint8_t CATEGORY_VALUE_LOWWORD_EOS = 6U;
  static constexpr uint8_t CATEGORY_VALUE_HIGHWORD_EOS = 7U;

  static constexpr uint8_t CODE_EVENT_INFO = 0U;
  static constexpr uint8_t CODE_EVENT_WARNING = 1U;
  static constexpr uint8_t CODE_EVENT_ERROR = 2U;
  static constexpr uint8_t CODE_EVENT_FATAL = 3U;
  static constexpr uint8_t CODE_EVENT_DEBUG = 4U;

  static constexpr uint8_t CODE_TIME_ABS = 0U;
  static constexpr uint8_t CODE_TIME_DELTA = 1U;

  static constexpr uint8_t CODE_TRACE_ENTER = 0U;
  static constexpr uint8_t CODE_TRACE_LEAVE = 1U;

  static constexpr uint8_t CODE_TYPE_S32 = 0U;
  static constexpr uint8_t CODE_TYPE_U32 = 1U;
  static constexpr uint8_t CODE_TYPE_F32 = 2U;

  static constexpr uint8_t LEVEL_INFO = 1 << CODE_EVENT_INFO;
  static constexpr uint8_t LEVEL_WARNING = 1 << CODE_EVENT_WARNING;
  static constexpr uint8_t LEVEL_ERROR = 1 << CODE_EVENT_ERROR;
  static constexpr uint8_t LEVEL_FATAL = 1 << CODE_EVENT_FATAL;
  static constexpr uint8_t LEVEL_DEBUG = 1 << CODE_EVENT_DEBUG;

  typedef union {
    uint32_t raw;
    struct {
      uint32_t category : 4U;
      uint32_t code : 4U;
      uint32_t arg : 24U;
    } packet;
  } entry_t;

  typedef struct {
    uint64_t ts;
    uint32_t level;
    uint32_t evt;
  } event_view_t;

  typedef struct {
    uint64_t ts;
    uint32_t evt;
    uint32_t tag;
  } trace_view_t;

  typedef std::variant<int8_t, uint8_t, int16_t, uint16_t, int32_t, uint32_t,
                       f32_t>
      value_t;

  typedef struct {
    uint64_t ts;
    uint32_t tag;
    value_t val;
  } value_view_t;

  typedef std::variant<event_view_t, trace_view_t, value_view_t> entry_view_t;

  static log_service& instance(void) {
    static log_service inst;
    return inst;
  }

  uint8_t loglevel() const { return level_; }

  void loglevel(uint8_t level) { level_ = level; }

  bool enable_trace() const { return enable_trace_; }

  void enable_trace(bool en) { enable_trace_ = en; }

  void start_transaction();

  void put_event(uint32_t level, uint32_t evid);

  void put_trace(uint32_t tag, uint32_t evid);

  void put_value(uint32_t tag, uint32_t val);

  void put_value(uint32_t tag, f32_t val);

  coro::task<void> start_service() const;

  coro::generator<entry_view_t> view() const;

 private:
  log_service(void);

  boost::circular_buffer<entry_t, std::pmr::polymorphic_allocator<entry_t>>
      ringbuffer_;
  uint64_t last_ts_;
  uint8_t level_;
  uint8_t enable_trace_;

};  // class log_service {

}  // namespace services
}  // namespace fireball

#endif  // #ifndef __LOG_SERVICE_HXX__
