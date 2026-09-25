/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

namespace fireball::utils {

/**
 * Report backtrace and terminate.
 */
[[noreturn]] extern void report_backtrace_and_terminate(const char *msg) noexcept;

/**
 * Report a fatal error with backtrace and terminate.
 */
#define BACKTRACE(msg)                                                         \
  do {                                                                         \
    fireball::utils::report_backtrace_and_terminate(msg);                      \
  } while (false);

/**
 * Report a fatal allocation error with backtrace and terminate.
 */
#define THROW_NESTED_BACKTRACE(msg, outer)                                     \
  BACKTRACE(msg)

#ifdef __DEBUG__
#define ASSERT_WITH_BACKTRACE(x)                                               \
  do {                                                                         \
    if (!(x)) {                                                                \
      BACKTRACE(#x);                                                           \
    }                                                                          \
  } while (false);
#else
#define ASSERT_WITH_BACKTRACE(x)
#endif // #ifdef __DEBUG__

#define NOT_IMPLEMENTED                                                        \
  do {                                                                         \
    BACKTRACE("this is not implemented yet.");                                 \
  } while (false);

} // namespace fireball::utils
