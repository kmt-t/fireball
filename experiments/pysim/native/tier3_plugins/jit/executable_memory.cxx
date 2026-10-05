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
#if defined(_WIN32)
constexpr std::uint32_t writable = PAGE_READWRITE;
constexpr std::uint32_t executable = PAGE_EXECUTE_READ;
#else
constexpr std::uint32_t writable = PROT_READ | PROT_WRITE;
constexpr std::uint32_t executable = PROT_READ | PROT_EXEC;
#endif
bool valid(const executable_memory* m, std::uint32_t offset, std::uint32_t count) {
  return m != nullptr && m->base != 0 && offset <= m->bytes && count <= m->bytes - offset;
}
bool protect(executable_memory& m, std::uint32_t protection) {
#if defined(_WIN32)
  DWORD previous = 0;
  if (!VirtualProtect(reinterpret_cast<void*>(m.base), m.bytes, protection, &previous))
    return false;
#else
  if (mprotect(reinterpret_cast<void*>(m.base), m.bytes, static_cast<int>(protection)) != 0)
    return false;
#endif
  m.protection = protection;
  return true;
}
}  // namespace
}  // namespace fireball
extern "C" int fb_jit_memory_init(fireball::executable_memory* m, std::uint32_t bytes) {
  if (m == nullptr || bytes == 0 || m->base != 0) return 0;
#if defined(_WIN32)
  auto* address = VirtualAlloc(nullptr, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
  if (address == nullptr) return 0;
#else
  auto* address = mmap(nullptr, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (address == MAP_FAILED) return 0;
#endif
  *m = {reinterpret_cast<std::uintptr_t>(address), bytes, fireball::writable, 1};
  return 1;
}
extern "C" int fb_jit_memory_begin(fireball::executable_memory* m) {
  if (!fireball::valid(m, 0, 0) || m->patching || !fireball::protect(*m, fireball::writable))
    return 0;
  m->patching = 1;
  return 1;
}
extern "C" int fb_jit_memory_commit(fireball::executable_memory* m) {
  if (!fireball::valid(m, 0, 0) || !m->patching || !fireball::protect(*m, fireball::executable))
    return 0;
#if defined(_WIN32)
  if (!FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<const void*>(m->base), m->bytes))
    return 0;
#else
  auto* begin = reinterpret_cast<char*>(m->base);
  __builtin___clear_cache(begin, begin + m->bytes);
#endif
  m->patching = 0;
  return 1;
}
extern "C" int fb_jit_memory_finalize(fireball::executable_memory* m) {
  return fireball::valid(m, 0, 0) && (!m->patching || fb_jit_memory_commit(m));
}
extern "C" int fb_jit_memory_no_rwx(const fireball::executable_memory* m) {
  return fireball::valid(m, 0, 0) &&
         (m->protection == fireball::writable || m->protection == fireball::executable);
}
extern "C" int fb_jit_memory_write(fireball::executable_memory* m, std::uint32_t offset,
                                   const std::uint8_t* source, std::uint32_t count) {
  if (source == nullptr || !fireball::valid(m, offset, count) || !m->patching ||
      m->protection != fireball::writable)
    return 0;
  std::memmove(reinterpret_cast<std::uint8_t*>(m->base) + offset, source, count);
  return 1;
}
extern "C" int fb_jit_memory_close(fireball::executable_memory* m) {
  if (m == nullptr) return 0;
  if (m->base == 0) return 1;
#if defined(_WIN32)
  if (!VirtualFree(reinterpret_cast<void*>(m->base), 0, MEM_RELEASE)) return 0;
#else
  if (munmap(reinterpret_cast<void*>(m->base), m->bytes) != 0) return 0;
#endif
  m->base = 0;
  return 1;
}

extern "C" std::uintptr_t fb_jit_memory_address(fireball::executable_memory* m,
                                                std::uint32_t offset) {
  if (!fireball::valid(m, offset, 1) || !fb_jit_memory_finalize(m)) return 0;
  return m->base + offset;
}

std::size_t fireball::executable_region_alignment() {
#if defined(_WIN32)
  SYSTEM_INFO info{};
  GetSystemInfo(&info);
  return info.dwPageSize;
#else
  return static_cast<std::size_t>(sysconf(_SC_PAGESIZE));
#endif
}
int fireball::borrow_executable_region(executable_memory* memory, std::uint8_t* region,
                                     std::uint32_t bytes) {
  if (memory == nullptr || region == nullptr || bytes == 0 ||
      reinterpret_cast<std::uintptr_t>(region) % executable_region_alignment() != 0) return 0;
  *memory = {reinterpret_cast<std::uintptr_t>(region), bytes, writable, 1};
  return protect(*memory, writable);
}
