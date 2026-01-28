/**
 * @file jit_compiler.hxx
 * @brief JIT Compiler Interface for Fireball
 *
 * Provides high-level JIT management, including hotspot detection and cache orchestration.
 * {LowLatencyJIT} {SimpleJITArchitecture}
 */

#ifndef FIREBALL_JIT_JIT_COMPILER_HXX
#define FIREBALL_JIT_JIT_COMPILER_HXX

#include <fireball.hxx>
#include <cstdint>
#include <cstddef>

namespace fireball {

/**
 * @brief JIT Entry linking WASM PC to Native Code Offset.
 * Memory-optimized using packed attribute.
 */
struct __attribute__((packed)) jit_entry {
    std::uint32_t pc;           ///< WASM bytecode offset
    std::uint16_t code_offset;  ///< Offset in cache (shifted by JIT_CODE_ALIGN_SHIFT)
};

/**
 * @brief JIT Cache Partition management.
 */
struct jit_cache_partition {
    std::uint8_t* base_addr;    ///< Partition start address
    std::uint32_t used_size;    ///< Current usage in bytes
    jit_entry* entries;         ///< Array of JIT entries
    std::uint16_t entry_count;  ///< Number of registered entries
    std::uint16_t* group_index; ///< Card group index array
};

/**
 * @brief JIT Configuration Parameters.
 * {ConfigurableSystem}
 */
struct jit_config {
    std::uint32_t cache_size_per_side;      ///< Default: 2KB
    std::uint16_t max_entries;              ///< Max entries per partition
    std::uint16_t history_buffer_size;      ///< Pipeline history buffer size
    std::uint8_t num_cards_shift;           ///< e.g., 10 -> 1024 cards
    std::uint8_t card_size_shift;           ///< e.g., 6 -> 64 bytes per card
    std::uint8_t cards_per_group_shift;     ///< e.g., 5 -> 32 cards per group
    std::uint8_t code_align_shift;          ///< e.g., 3 -> 8-byte alignment
};

/**
 * @brief High-level JIT Compiler Manager.
 * Orchestrates hotspot detection and the Copy-and-Patch engine.
 */
class jit_compiler {
public:
    /**
     * @brief Initialize the JIT engine.
     * @param config Pointer to static configuration.
     * @return status Zero on success, non-zero on error.
     * @pre None
     * @post JIT is ready to accept lookups and compile requests.
     */
    status initialize(const jit_config* config);

    /**
     * @brief Lookup a native code trace for a given WASM PC.
     * Uses card-marking and binary search for O(log N) lookup.
     * Implements Copy-GC: if not found in Active partition, searches Old partition
     * and promotes (copies) the trace to Active if found.
     * @param pc WASM bytecode offset.
     * @return void* Pointer to native code, or nullptr if not compiled.
     * @pre JIT is initialized.
     * @post If found in Old, the trace is copied to Active partition.
     */
    void* lookup_trace(std::uint32_t pc);

    /**
     * @brief Process accumulated hotspots and trigger background compilation.
     * Typically called during co_yield.
     * @param history Pointer to the PC history buffer.
     * @param len Number of entries in the history buffer.
     * @pre System is in an idle/yield state.
     * @post Compiled traces are added to the active cache.
     */
    void process_hotspots(const std::uint32_t* history, std::size_t len);

    /**
     * @brief Clear all JIT caches and entries.
     * Resets both Active and Old partitions.
     */
    void clear_cache();

private:
    const jit_config* config_;
    jit_cache_partition active_;
    jit_cache_partition old_;
    std::uint8_t* hotspot_bitmap_; // 2-bit per card status
};

} // namespace fireball

#endif // FIREBALL_JIT_JIT_COMPILER_HXX
