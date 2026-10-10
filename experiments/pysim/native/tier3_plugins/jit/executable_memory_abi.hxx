#ifndef FIREBALL_PYSIM_EXECUTABLE_MEMORY_ABI_HXX
#define FIREBALL_PYSIM_EXECUTABLE_MEMORY_ABI_HXX
#include <cstdint>
#include <cstddef>
#include "jit_types.hxx"

#include "trace_compiler_abi.hxx"
namespace fireball {
struct executable_memory {
  std::uintptr_t base;
  std::uint32_t bytes, protection, patching;
};
std::size_t executable_region_alignment();
int borrow_executable_region(executable_memory* memory, std::uint8_t* region, std::uint32_t bytes);
}  // namespace fireball
extern "C" {
int fb_jit_memory_init(fireball::executable_memory* memory,
                       std::uint32_t bytes);
int fb_jit_memory_begin(fireball::executable_memory* memory);
int fb_jit_memory_commit(fireball::executable_memory* memory);
int fb_jit_memory_finalize(fireball::executable_memory* memory);
int fb_jit_memory_no_rwx(const fireball::executable_memory* memory);
int fb_jit_memory_write(fireball::executable_memory* memory,
                        std::uint32_t offset, const std::uint8_t* source,
                        std::uint32_t count);
int fb_jit_memory_close(fireball::executable_memory* memory);
std::uintptr_t fb_jit_memory_address(fireball::executable_memory* memory,
                                     std::uint32_t offset);
}
#endif
