// Test-only linkage fixture for the existing runtime_syscall.md IDs 0x80..0x83.
// This is not the unimplemented libfireball WASI/HAL guest adapter.
#include "libfireball.hxx"
#include <cstdint>
#include <wasi/api.h>

namespace {
template <class T> fireball::u32 offset(T *pointer) {
  return static_cast<fireball::u32>(reinterpret_cast<std::uintptr_t>(pointer));
}
} // namespace

extern "C" __wasi_errno_t __wrap___wasi_fd_write(__wasi_fd_t fd, const __wasi_ciovec_t *iovs,
                                                 size_t count, __wasi_size_t *written) {
  return static_cast<__wasi_errno_t>(
      fireball::fireball_call4(0x80, fd, offset(iovs), count, offset(written)));
}

extern "C" __wasi_errno_t __wrap___wasi_fd_read(__wasi_fd_t fd, const __wasi_iovec_t *iovs,
                                                size_t count, __wasi_size_t *read) {
  return static_cast<__wasi_errno_t>(
      fireball::fireball_call4(0x81, fd, offset(iovs), count, offset(read)));
}

extern "C" __wasi_errno_t __wrap___wasi_fd_close(__wasi_fd_t fd) {
  return static_cast<__wasi_errno_t>(fireball::fireball_call1(0x82, fd));
}

extern "C" __wasi_errno_t __wrap___wasi_clock_time_get(__wasi_clockid_t id,
                                                       __wasi_timestamp_t precision,
                                                       __wasi_timestamp_t *timestamp) {
  // clock_gettime asks for precision=1. This fixture covers the u32 syscall contract.
  return static_cast<__wasi_errno_t>(
      fireball::fireball_call3(0x83, id, static_cast<fireball::u32>(precision), offset(timestamp)));
}
