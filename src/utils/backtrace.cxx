/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#include <utils/backtrace.hxx>
#include <sstream>

namespace fireball { namespace utils {

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

} // namespace {

exception_with_backtrace::exception_with_backtrace(const std::string& msg)
    : std::runtime_error(make_massage(msg)) {
}

} } // namespace fireball { namespace utils {
