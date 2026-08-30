/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <fireball.hxx>
#include <stdexcept>

namespace fireball::utils {

#if defined(__cpp_exceptions)
/**
 * Exception with backtrace.
 */
class exception_with_backtrace : public std::runtime_error {
public:
  exception_with_backtrace(const std::string& msg);
  virtual ~exception_with_backtrace() noexcept = default;
};
#endif

/**
 * Report backtrace and terminate (for exception-disabled environments).
 */
extern void report_backtrace_and_terminate(const char* msg) noexcept;

#if defined(__cpp_exceptions)
/**
 * Throw exception with backtrace.
 */
#define BACKTRACE(msg)                                                                             \
  do {                                                                                             \
    throw fireball::utils::exception_with_backtrace(msg);                                          \
  } while (false);

/**
 * Throw nested exception with backtrace.
 */
#define THROW_NESTED_BACKTRACE(msg, outer)                                                         \
  do {                                                                                             \
    try {                                                                                          \
      throw fireball::utils::exception_with_backtrace(msg);                                        \
    } catch (...) {                                                                                \
      std::throw_with_nested(outer());                                                             \
    }                                                                                              \
  } while (false);
#else
/**
 * Report backtrace and terminate.
 */
#define BACKTRACE(msg)                                                                             \
  do {                                                                                             \
    fireball::utils::report_backtrace_and_terminate(msg);                                          \
  } while (false);

/**
 * Report backtrace and terminate.
 */
#define THROW_NESTED_BACKTRACE(msg, outer)                                                         \
  do {                                                                                             \
    fireball::utils::report_backtrace_and_terminate(msg);                                          \
  } while (false);
#endif

#ifdef __DEBUG__
#define ASSERT_WITH_BACKTRACE(x)                                                                   \
  do {                                                                                             \
    if (!(x)) {                                                                                    \
      BACKTRACE(#x);                                                                               \
    }                                                                                              \
  } while (false);
#else
#define ASSERT_WITH_BACKTRACE(x)
#endif // #ifdef __DEBUG__

#define NOT_IMPLEMENTED                                                                            \
  do {                                                                                             \
    BACKTRACE("this is not implemented yet.");                                                     \
  } while (false);

} // namespace fireball::utils
