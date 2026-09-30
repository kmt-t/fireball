#ifndef FIREBALL_PYSIM_FAST_CACHE_ABI_HXX
#define FIREBALL_PYSIM_FAST_CACHE_ABI_HXX

#include <cstdint>

#if defined(_WIN32)
#define FB_PYSIM_CACHE_EXPORT __declspec(dllexport)
#else
#define FB_PYSIM_CACHE_EXPORT __attribute__((visibility("default")))
#endif

extern "C" {

FB_PYSIM_CACHE_EXPORT std::uint32_t fb_jit_fast_cache_slot_count();
FB_PYSIM_CACHE_EXPORT std::uint32_t fb_jit_fast_cache_slot(std::uint32_t pc);
FB_PYSIM_CACHE_EXPORT std::uint64_t fb_jit_fast_cache_lookup(
    const std::uint32_t* keys, const std::uint64_t* handles,
    const std::uint8_t* occupied, std::uint32_t pc);
FB_PYSIM_CACHE_EXPORT int fb_jit_fast_cache_store(
    std::uint32_t* keys, std::uint64_t* handles, std::uint8_t* occupied,
    std::uint32_t pc, std::uint64_t handle);
FB_PYSIM_CACHE_EXPORT int fb_jit_fast_cache_clear(
    std::uint32_t* keys, std::uint64_t* handles, std::uint8_t* occupied);

}

#endif
