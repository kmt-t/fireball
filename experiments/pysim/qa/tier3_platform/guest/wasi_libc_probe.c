// Real SDK headers and wasi-libc POSIX wrappers; no replacement writev/read/clock_gettime.
#include <errno.h>
#include <stdint.h>
#include <sys/uio.h>
#include <time.h>
#include <unistd.h>

static unsigned char payload[800];
static struct {
  int32_t rc;
  int32_t error;
  uint64_t clock;
  unsigned char data[64];
} result;

_Static_assert(sizeof(result) == 80, "wasm32 probe result layout");

uint32_t result_address(void) { return (uint32_t)(uintptr_t)&result; }

int write_probe(int fd, unsigned count) {
  if (count > sizeof(payload))
    return -2;
  for (unsigned i = 0; i < count; ++i)
    payload[i] = (unsigned char)(37 * i + 11);
  // An empty middle vector must neither drop nor duplicate either neighboring range.
  struct iovec vec[3] = {
      {payload, count / 3}, {payload + count / 3, 0}, {payload + count / 3, count - count / 3}};
  errno = 0;
  result.rc = (int32_t)writev(fd, vec, 3);
  result.error = errno;
  return result.rc;
}

int read_probe(int fd, unsigned count) {
  if (count > sizeof(result.data))
    return -2;
  for (unsigned i = 0; i < sizeof(result.data); ++i)
    result.data[i] = 0xa5;
  errno = 0;
  result.rc = (int32_t)read(fd, result.data, count);
  result.error = errno;
  return result.rc;
}

int clock_probe(void) {
  struct timespec ts = {0};
  errno = 0;
  result.rc = clock_gettime(CLOCK_MONOTONIC, &ts);
  result.error = errno;
  result.clock = (uint64_t)ts.tv_sec * 1000000000 + (uint64_t)ts.tv_nsec;
  return result.rc;
}

int close_probe(int fd) {
  errno = 0;
  result.rc = close(fd);
  result.error = errno;
  return result.rc;
}
