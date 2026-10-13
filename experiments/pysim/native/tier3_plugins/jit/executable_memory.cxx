#include <cassert>
#include <cstring>

#include "executable_memory_abi.hxx"
#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <sys/mman.h>
#include <unistd.h>
#endif
namespace fireball {
namespace {
#if (defined(__linux__) && defined(__x86_64__)) ||                                                 \
    (defined(_WIN32) && (defined(__x86_64__) || defined(_M_X64)))
constexpr bool x64_host = true;
#else
constexpr bool x64_host = false;
#endif
#if defined(_WIN32)
constexpr std::uint32_t writable_no_execute = PAGE_READWRITE;
constexpr std::uint32_t executable_no_write = PAGE_EXECUTE_READ;
constexpr std::uint32_t read_write_execute = PAGE_EXECUTE_READWRITE;
#else
constexpr std::uint32_t writable_no_execute = PROT_READ | PROT_WRITE;
constexpr std::uint32_t executable_no_write = PROT_READ | PROT_EXEC;
constexpr std::uint32_t read_write_execute = PROT_READ | PROT_WRITE | PROT_EXEC;
#endif
// Linux/Windows x64 originally changed RW/RX protection for every trace
// promotion. Those system calls dominated promotion time, so grant execute
// permission once at mapping/borrowing. Keep begin/commit and the W^X
// permissions for other targets; the transaction still delimits writes and
// synchronizes instruction cache.
constexpr std::uint32_t writable = x64_host ? read_write_execute : writable_no_execute;
constexpr std::uint32_t executable = x64_host ? read_write_execute : executable_no_write;
[[maybe_unused]] bool valid(const executable_memory *m, code_offset_t offset, code_offset_t count) {
  return m != nullptr && m->base != 0 && offset <= m->bytes && count <= m->bytes - offset;
}
void protect(executable_memory &m, std::uint32_t protection) {
  if (m.protection == protection) {
    return;
  }
#if defined(_WIN32)
  DWORD previous = 0;
  [[maybe_unused]] const auto changed =
      VirtualProtect(reinterpret_cast<void *>(m.base), m.bytes, protection, &previous);
  assert(changed != 0);
#else
  [[maybe_unused]] const auto changed =
      mprotect(reinterpret_cast<void *>(m.base), m.bytes, static_cast<int>(protection));
  assert(changed == 0);
#endif
  m.protection = protection;
}
} // namespace
} // namespace fireball
extern "C" int fb_jit_memory_init(fireball::executable_memory *m, code_offset_t bytes) {
  assert(m != nullptr && bytes != 0 && m->base == 0);
#if defined(_WIN32)
  auto *address = VirtualAlloc(nullptr, bytes, MEM_COMMIT | MEM_RESERVE, fireball::writable);
  assert(address != nullptr);
#else
  auto *address = mmap(nullptr, bytes, static_cast<int>(fireball::writable),
                       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  assert(address != MAP_FAILED);
#endif
  *m = {reinterpret_cast<std::uintptr_t>(address), bytes, fireball::writable, 1};
  return 1;
}
extern "C" int fb_jit_memory_begin(fireball::executable_memory *m) {
  assert(fireball::valid(m, 0, 0) && !m->patching);
  fireball::protect(*m, fireball::writable);
  m->patching = 1;
  return 1;
}
extern "C" int fb_jit_memory_commit(fireball::executable_memory *m) {
  assert(fireball::valid(m, 0, 0) && m->patching);
  fireball::protect(*m, fireball::executable);
#if defined(_WIN32)
  [[maybe_unused]] const auto synced =
      FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<const void *>(m->base), m->bytes);
  assert(synced != 0);
#else
  auto *begin = reinterpret_cast<char *>(m->base);
  __builtin___clear_cache(begin, begin + m->bytes);
#endif
  m->patching = 0;
  return 1;
}
extern "C" int fb_jit_memory_finalize(fireball::executable_memory *m) {
  assert(fireball::valid(m, 0, 0));
  if (m->patching) {
    fb_jit_memory_commit(m);
  }
  return 1;
}
extern "C" int fb_jit_memory_no_rwx(const fireball::executable_memory *m) {
  assert(fireball::valid(m, 0, 0));
  return m->protection == fireball::writable_no_execute ||
         m->protection == fireball::executable_no_write;
}
extern "C" int fb_jit_memory_valid_protection(const fireball::executable_memory *m) {
  assert(fireball::valid(m, 0, 0));
  return m->protection == fireball::writable || m->protection == fireball::executable;
}
extern "C" int fb_jit_memory_write(fireball::executable_memory *m, code_offset_t offset,
                                   const std::uint8_t *source, code_offset_t count) {
  assert(source != nullptr && fireball::valid(m, offset, count) && m->patching &&
         m->protection == fireball::writable);
  std::memmove(reinterpret_cast<std::uint8_t *>(m->base) + offset, source, count);
  return 1;
}
extern "C" int fb_jit_memory_close(fireball::executable_memory *m) {
  assert(m != nullptr);
  if (m->base == 0) {
    return 1;
  }
#if defined(_WIN32)
  [[maybe_unused]] const auto released =
      VirtualFree(reinterpret_cast<void *>(m->base), 0, MEM_RELEASE);
  assert(released != 0);
#else
  [[maybe_unused]] const auto released = munmap(reinterpret_cast<void *>(m->base), m->bytes);
  assert(released == 0);
#endif
  m->base = 0;
  return 1;
}

extern "C" std::uintptr_t fb_jit_memory_address(fireball::executable_memory *m,
                                                code_offset_t offset) {
  assert(fireball::valid(m, offset, 1));
  fb_jit_memory_finalize(m);
  return m->base + offset;
}

std::size_t fireball::executable_region_alignment() {
#if defined(_WIN32)
  SYSTEM_INFO info{};
  GetSystemInfo(&info);
  return info.dwPageSize;
#else
  const auto alignment = sysconf(_SC_PAGESIZE);
  assert(alignment > 0);
  return static_cast<std::size_t>(alignment);
#endif
}
int fireball::borrow_executable_region(executable_memory *memory, std::uint8_t *region,
                                       code_offset_t bytes) {
  assert(memory != nullptr && region != nullptr && bytes != 0 &&
         reinterpret_cast<std::uintptr_t>(region) % executable_region_alignment() == 0);
  *memory = {reinterpret_cast<std::uintptr_t>(region), bytes, 0, 1};
  protect(*memory, writable);
  return 1;
}
