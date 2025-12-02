#ifndef FIREBALL_COOS_MEM_HXX
#define FIREBALL_COOS_MEM_HXX

#include <cstddef>
#include <cstdint>

namespace fireball::coos {

// Opaque handle for a memory space (dlmalloc mspace)
using mspace_t = void*;

/**
 * @brief COOS Memory Manager
 * 
 * Wraps dlmalloc to provide isolated memory spaces (mspaces).
 * This allows for fault isolation: if one module corrupts its heap,
 * others remain unaffected.
 */
class co_mem {
public:
    /**
     * @brief Initialize the global memory system.
     * Should be called once at startup.
     */
    static void init();

    /**
     * @brief Create a new isolated memory space.
     * 
     * @param capacity Maximum size in bytes for this mspace.
     *                 The memory is allocated from the system heap.
     * @return mspace_t Handle to the new mspace, or nullptr on failure.
     */
    static mspace_t create_mspace(size_t capacity);

    /**
     * @brief Destroy a memory space and release its resources.
     * 
     * @param msp Handle to the mspace to destroy.
     * @return size_t Bytes released to the system.
     */
    static size_t destroy_mspace(mspace_t msp);

    /**
     * @brief Allocate memory from a specific mspace.
     * 
     * @param msp Handle to the mspace.
     * @param size Bytes to allocate.
     * @return void* Pointer to allocated memory, or nullptr.
     */
    static void* alloc(mspace_t msp, size_t size);

    /**
     * @brief Free memory to a specific mspace.
     * 
     * @param msp Handle to the mspace.
     * @param ptr Pointer to memory to free.
     */
    static void free(mspace_t msp, void* ptr);

    /**
     * @brief Reallocate memory in a specific mspace.
     */
    static void* realloc(mspace_t msp, void* ptr, size_t new_size);

    /**
     * @brief Get statistics for an mspace.
     * 
     * @param msp Handle to the mspace.
     * @param allocated_out [out] Total bytes currently allocated.
     * @param capacity_out [out] Total capacity of this mspace.
     */
    static void get_stats(mspace_t msp, size_t* allocated_out, size_t* capacity_out);
};

} // namespace fireball::coos

#endif // FIREBALL_COOS_MEM_HXX
