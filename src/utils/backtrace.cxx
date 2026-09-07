#include <cstdio>
#include <utils/backtrace.hxx>

namespace fireball::utils {

void report_backtrace_and_terminate(const char *msg) noexcept {
  printf("message: %s\n", msg);
  printf("[stacktrace not available in bare-metal environment]\n");
  // In a real bare-metal env, we might want a custom terminate or spin loop
  // but for QEMU testing, we'll let it proceed to abort.
}

} // namespace fireball::utils
