#pragma once

#include <cstdint>

namespace fireball::vsoc {

/**
 * @enum vmmio_hook_id
 * @brief Identifiers for vMMIO regions.
 */
enum vmmio_hook_id : uint32_t {
    HOOK_ID_SYSCTL  = 0,
    HOOK_ID_IPCR    = 1,
    HOOK_ID_VDMA    = 2,
    HOOK_ID_DYNAMIC = 3,
    HOOK_MAX_IDS
};

/**
 * @struct vmmio_static_region
 * @brief ROM-defined static region mapping.
 * @details {Static_Resolution}
 */
struct vmmio_static_region {
    uint32_t       page_index;  ///< Page index from vMMIO base (addr >> 16)
    uint32_t       page_count;  ///< Size in 64KB pages
    vmmio_hook_id  hook_id;     ///< Associated hook ID
};

/**
 * @brief Default vMMIO address map (ROM target).
 * @details Defined in docs/orders/components/vmmio.md
 */
static constexpr vmmio_static_region VMMIO_STATIC_MAP[] = {
    { 0,     1,      HOOK_ID_SYSCTL  }, // Page 0 (0x4000_0000)
    { 1,     1,      HOOK_ID_IPCR    }, // Page 1 (0x4001_0000)
    { 2,     1,      HOOK_ID_VDMA    }, // Page 2 (0x4002_0000)
    { 4096,  4096,   HOOK_ID_DYNAMIC }  // Page 4096 (0x5000_0000) - 256MB
};

} // namespace fireball::vsoc
