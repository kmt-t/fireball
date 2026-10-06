#ifndef FIREBALL_PYSIM_COMMON_CODE_ABI_HXX
#define FIREBALL_PYSIM_COMMON_CODE_ABI_HXX
#include <cstdint>

#include "executable_memory_abi.hxx"
#include "jit_types.hxx"
#include "trace_compiler_abi.hxx"

extern "C" {
FB_JIT_QA_EXPORT int fb_jit_region_init(fireball::executable_memory *memory);

FB_JIT_QA_EXPORT void fb_jit_common_layout(fireball::jit_common_layout *output);
FB_JIT_QA_EXPORT int fb_jit_common_install(fireball::executable_memory *memory,
                                           std::uint32_t offset,
                                           const std::uint8_t *blob,
                                           std::uint32_t blob_bytes,
                                           std::uintptr_t *entry);
}
#endif
