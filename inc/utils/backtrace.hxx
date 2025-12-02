/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef __BACKTRACE_HXX__
#define __BACKTRACE_HXX__

#include <commons.hxx>
#include <stdexcept>
#include <stacktrace>

namespace fireball { namespace utils {

/**
 * バックトレース付き例外.
 */
class exception_with_backtrace : public std::runtime_error {
 public:
  /**
   * コンストラクタ.
   */
  exception_with_backtrace(const std::string& msg);

  /**
   * デストラクタ.
   */
  virtual ~exception_with_backtrace() noexcept = default;
};

#define BACKTRACE(msg)                                    \
  do {                                                    \
    throw fireball::utils::exception_with_backtrace(msg); \
  } while (false);

#ifdef __DEBUG__
#define ASSERT_WITH_BACKTRACE(x) \
  do {                           \
    if (!(x)) {                  \
      BACKTRACE(#x);             \
    }                            \
  } while (false);
#else
#define ASSERT_WITH_BACKTRACE(x)
#endif  // #ifdef __DEBUG__

#define NOT_IMPLEMENTED                               \
  do {                                                \
    BACKTRACE("this is not implemented yet."); \
  } while (false);

} } // namespace fireball { namespace utils {

#endif  // #ifndef __BACKTRACE_HXX__
