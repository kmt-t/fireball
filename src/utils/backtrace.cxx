/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#include <iostream>
#include <sstream>
#include <stacktrace>
#include <utils/backtrace.hxx>

namespace fireball {
namespace utils {

namespace {

std::string make_massage(const std::string& msg) {
  std::ostringstream oss;
  oss << "message: " << msg << "\n";
  auto trace = std::stacktrace::current();
  if (!trace.empty()) {
    oss << "trace:\n";
    for (const auto& frame : trace) {
      oss << "  " << frame << std::endl;
    }
  }
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

} // namespace utils
} // namespace fireball
