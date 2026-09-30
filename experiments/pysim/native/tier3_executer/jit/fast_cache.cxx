#include <bit>
#include <cstddef>
#include <cstdint>

#include "fast_cache_abi.hxx"

#ifndef FB_CONF_JIT_CACHE_FAST_SLOT_COUNT
#error "FB_CONF_JIT_CACHE_FAST_SLOT_COUNT must be defined by the build configuration"
#endif

namespace {

constexpr std::uint32_t kFastSlotCount = FB_CONF_JIT_CACHE_FAST_SLOT_COUNT;
static_assert(kFastSlotCount > 0);
static_assert(std::has_single_bit(kFastSlotCount));

std::uint32_t hash_slot(std::uint32_t pc) noexcept {
  auto folded = pc ^ (pc >> 16);
  folded ^= folded >> 8;
  folded ^= folded >> 4;
  return folded & (kFastSlotCount - 1);
}

bool valid_arrays(const std::uint32_t* keys, const std::uint64_t* handles,
                  const std::uint8_t* occupied) noexcept {
  return keys != nullptr && handles != nullptr && occupied != nullptr;
}

}  // namespace

extern "C" std::uint32_t fb_jit_fast_cache_slot_count() {
  return kFastSlotCount;
}

extern "C" std::uint32_t fb_jit_fast_cache_slot(std::uint32_t pc) {
  return hash_slot(pc);
}

extern "C" std::uint64_t fb_jit_fast_cache_lookup(
    const std::uint32_t* keys, const std::uint64_t* handles,
    const std::uint8_t* occupied, std::uint32_t pc) {
  if (!valid_arrays(keys, handles, occupied)) return 0;
  const auto slot = hash_slot(pc);
  return occupied[slot] != 0 && keys[slot] == pc ? handles[slot] : 0;
}

extern "C" int fb_jit_fast_cache_store(
    std::uint32_t* keys, std::uint64_t* handles, std::uint8_t* occupied,
    std::uint32_t pc, std::uint64_t handle) {
  if (!valid_arrays(keys, handles, occupied) || handle == 0) return 0;
  const auto slot = hash_slot(pc);
  keys[slot] = pc;
  handles[slot] = handle;
  occupied[slot] = 1;
  return 1;
}

extern "C" int fb_jit_fast_cache_clear(
    std::uint32_t* keys, std::uint64_t* handles, std::uint8_t* occupied) {
  if (!valid_arrays(keys, handles, occupied)) return 0;
  for (std::size_t slot = 0; slot < kFastSlotCount; ++slot) {
    keys[slot] = 0;
    handles[slot] = 0;
    occupied[slot] = 0;
  }
  return 1;
}
