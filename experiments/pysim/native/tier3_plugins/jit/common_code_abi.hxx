#ifndef FIREBALL_PYSIM_COMMON_CODE_ABI_HXX
#define FIREBALL_PYSIM_COMMON_CODE_ABI_HXX
#include <cstdint>

#include "executable_memory_abi.hxx"
#include "jit_types.hxx"
#include "trace_compiler_abi.hxx"

extern "C" {
int fb_jit_region_init(fireball::executable_memory *memory);

int fb_jit_runtime_api_slot(std::uint32_t opcode, std::uint32_t fc_prefix,
                            std::uint8_t *slot);

void fb_jit_common_layout(fireball::jit_common_layout *output);
int
fb_jit_common_install(fireball::executable_memory *memory, std::uint32_t offset,
                      const std::uint8_t *blob, std::uint32_t blob_bytes,
                      std::uint32_t block_index, std::uintptr_t *entry);
}
#endif
