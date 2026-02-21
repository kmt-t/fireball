/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#include <iostream>
#include <sstream>
#include <utils/backtrace.hxx>

namespace fireball::utils {

namespace {

std::string make_massage(const std::string& msg) {
  std::ostringstream oss;
  oss << "message: " << msg << "\n";
  // stacktrace is C++23, stubbing out for C++20 compliance
  oss << "[stacktrace not available in C++20]\n";
  return oss.str();
}

} // namespace

#if defined(__cpp_exceptions)
exception_with_backtrace::exception_with_backtrace(const std::string& msg)
    : std::runtime_error(make_massage(msg)) {}
#endif

void report_backtrace_and_terminate(const char* msg) noexcept {
#if defined(__cpp_exceptions)
  try {
    std::cerr << make_massage(msg) << std::endl;
  } catch (...) {
    // ignore.
  }
#else
  std::cerr << make_massage(msg) << std::endl;
#endif
  std::terminate();
}

} // namespace fireball::utils
