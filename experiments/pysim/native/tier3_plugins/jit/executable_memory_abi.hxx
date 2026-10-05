#ifndef FIREBALL_PYSIM_EXECUTABLE_MEMORY_ABI_HXX
#define FIREBALL_PYSIM_EXECUTABLE_MEMORY_ABI_HXX
#include <cstdint>

#include "trace_compiler_abi.hxx"
namespace fireball {
struct executable_memory {
  std::uintptr_t base;
  std::uint32_t bytes, protection, patching, dsb_count, isb_count;
};
}  // namespace fireball
extern "C" {
FB_PYSIM_ABI_EXPORT int fb_jit_memory_init(fireball::executable_memory* memory,
                                           std::uint32_t bytes);
FB_PYSIM_ABI_EXPORT int fb_jit_memory_begin(fireball::executable_memory* memory);
FB_PYSIM_ABI_EXPORT int fb_jit_memory_commit(fireball::executable_memory* memory);
FB_PYSIM_ABI_EXPORT int fb_jit_memory_finalize(fireball::executable_memory* memory);
FB_PYSIM_ABI_EXPORT int fb_jit_memory_no_rwx(const fireball::executable_memory* memory);
FB_PYSIM_ABI_EXPORT int fb_jit_memory_write(fireball::executable_memory* memory,
                                            std::uint32_t offset, const std::uint8_t* source,
                                            std::uint32_t count);
FB_PYSIM_ABI_EXPORT int fb_jit_memory_read(const fireball::executable_memory* memory,
                                           std::uint32_t offset, std::uint8_t* output,
                                           std::uint32_t count);
FB_PYSIM_ABI_EXPORT int fb_jit_memory_close(fireball::executable_memory* memory);
FB_PYSIM_ABI_EXPORT std::uintptr_t fb_jit_memory_address(fireball::executable_memory* memory,
                                                         std::uint32_t offset);
}
#endif
