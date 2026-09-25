#include <cstdio>
#include <cstdlib>
#include <utils/backtrace.hxx>

namespace fireball::utils {

[[noreturn]] void report_backtrace_and_terminate(const char *msg) noexcept {
  printf("message: %s\n", msg);
  printf("[stacktrace not available in bare-metal environment]\n");
  std::abort();
}

} // namespace fireball::utils
