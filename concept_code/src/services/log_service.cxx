/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2024 Takuya Matsunaga.
 */
#include <bit>
#include <services/log_service.hxx>

namespace fireball {
namespace services {
namespace {
static constexpr uint64_t TIMESTAMP_AMP = 10000U;
}  // namespace

log_service::log_service()
    : ringbuffer_(fireball::allocator::sys_allocator::allocator<entry_t>()),
      last_ts_(0U) {
  // nothing.
}

coro::task<void> log_service::start_service() const { co_return; }

coro::generator<log_service::entry_view_t> log_service::view() const {
  uint64_t curr_ts = 0U;
  uint32_t ty = 0U;
  uint32_t tag = 0U;
  uint32_t val = 0U;
  auto iter = ringbuffer_.cbegin();
  while (true) {
    if (iter == ringbuffer_.cend()) {
      co_return;
    }
    if (iter->packet.category == CATEGORY_TIME &&
        iter->packet.code == CODE_TIME_ABS) {
      curr_ts = static_cast<uint64_t>(iter->packet.arg) * TIMESTAMP_AMP;
      break;
    }
    ++iter;
  }

  for (; iter != ringbuffer_.end(); ++iter) {
    switch (iter->packet.category) {
      case CATEGORY_TIME:
        switch (iter->packet.code) {
          case CODE_TIME_ABS:
            curr_ts = static_cast<uint64_t>(iter->packet.arg) * TIMESTAMP_AMP;
            break;
          case CODE_TIME_DELTA:
            curr_ts = static_cast<uint64_t>(iter->packet.arg) * TIMESTAMP_AMP;
            break;
        }
        break;
      case CATEGORY_EVENT:
        co_yield event_view_t{curr_ts, iter->packet.code, iter->packet.arg};
        break;
      case CATEGORY_TRACE:
        co_yield trace_view_t{curr_ts, iter->packet.code, iter->packet.arg};
        break;
      case CATEGORY_VALUE_TAG:
        ty = iter->packet.code;
        tag = iter->packet.arg;
        val = 0U;
        break;
      case CATEGORY_VALUE_LOWWORD:
        assert(iter->packet.code == ty && iter->packet.arg == tag);
        val = iter->packet.arg;
        break;
      case CATEGORY_VALUE_TAG_EOS:
        switch (iter->packet.code) {
          case CODE_TYPE_S32:
            co_yield value_view_t{curr_ts, iter->packet.arg,
                                  static_cast<int32_t>(0U)};
            break;
          case CODE_TYPE_U32:
            co_yield value_view_t{curr_ts, iter->packet.arg,
                                  static_cast<uint32_t>(0U)};
            break;
          case CODE_TYPE_F32:
            co_yield value_view_t{curr_ts, iter->packet.arg,
                                  std::bit_cast<f32_t, uint32_t>(0U)};
            break;
        }
        break;
      case CATEGORY_VALUE_LOWWORD_EOS:
        assert(iter->packet.code == ty && iter->packet.arg == tag);
        switch (iter->packet.code) {
          case CODE_TYPE_S32:
            co_yield value_view_t{curr_ts, iter->packet.arg,
                                  static_cast<int32_t>(iter->packet.arg)};
            break;
          case CODE_TYPE_U32:
            co_yield value_view_t{curr_ts, iter->packet.arg,
                                  static_cast<uint32_t>(iter->packet.arg)};
            break;
          case CODE_TYPE_F32:
            co_yield value_view_t{curr_ts, iter->packet.arg,
                                  std::bit_cast<f32_t, uint32_t>(0U)};
            break;
        }
        break;
      case CATEGORY_VALUE_HIGHWORD_EOS:
        assert(iter->packet.code == ty && iter->packet.arg == tag);
        switch (iter->packet.code) {
          case CODE_TYPE_S32:
            co_yield value_view_t{
                curr_ts, iter->packet.arg,
                static_cast<int32_t>(val | (iter->packet.arg << 24U))};
            break;
          case CODE_TYPE_U32:
            co_yield value_view_t{
                curr_ts, iter->packet.arg,
                static_cast<uint32_t>(val | (iter->packet.arg << 24U))};
            break;
          case CODE_TYPE_F32:
            co_yield value_view_t{curr_ts, iter->packet.arg,
                                  std::bit_cast<f32_t, uint32_t>(
                                      val | (iter->packet.arg << 24U))};
            break;
        }
        break;
      default:
        assert(!"broken logs.");
        co_return;
    }
  }

  co_return;
}

void log_service::put(uint8_t level, uint16_t evid) {}

void log_service::put(uint16_t func, uint8_t evid) {}

void log_service::put(uint16_t tag, value_t val) {}

}  // namespace services
}  // namespace fireball
