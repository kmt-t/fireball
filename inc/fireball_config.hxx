/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef FIREBALL_CONFIG_HXX
#define FIREBALL_CONFIG_HXX

#include <cstdint>

// Memory Management
static constexpr std::uint32_t FB_CONF_KERNEL_HEAP_SIZE = 8192U;
static constexpr std::uint32_t FB_CONF_RUNTIME_HEAP_SIZE = 4096U;
static constexpr std::uint32_t FB_CONF_SUBSYSTEM_HEAP_SIZE = 4096U;

// IPC Router 
static constexpr std::uint32_t FB_CONF_ROUTER_MAX_SERVICES = 16U;
// FB_CONF_ROUTER_ROLE_MATRIX will be defined in a specific header or as a complex structure later.

// HAL
static constexpr std::uint32_t FB_CONF_HAL_MAX_DEVICES = 8U;
static constexpr std::uint32_t FB_CONF_HAL_BUFFER_SIZE = 256U;
static constexpr std::uint32_t FB_CONF_HAL_MAX_BUFFERS = 4U;

// vSoC / vMMIO
static constexpr std::uint32_t FB_CONF_JIT_CACHE_SIZE = 4096U;
static constexpr std::uint32_t FB_CONF_GUEST_RAM_BASE = 0x00000000U;
static constexpr std::uint32_t FB_CONF_GUEST_RAM_SIZE = 16384U;
static constexpr std::uint32_t FB_CONF_VMMIO_BASE = 0x40000000U;
static constexpr std::uint32_t FB_CONF_VMMIO_MAX_REGIONS = 8U;
// FB_CONF_VMMIO_ALLOWED_ADDRS will be defined later.

// Logging
static constexpr std::uint32_t FB_CONF_LOG_BUFFER_SIZE = 512U;

// Debugger
static constexpr std::uint32_t FB_CONF_DEBUG_MAX_BREAKPOINTS = 8U;
static constexpr std::uint32_t FB_CONF_DEBUG_PACKET_SIZE = 1024U;

#endif // #ifndef FIREBALL_CONFIG_HXX
