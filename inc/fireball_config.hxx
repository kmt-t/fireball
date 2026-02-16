/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <cstdint>
#include <cassert>

// Debug and Validation
#define FB_ASSERT(expr) assert(expr)

// Memory Management
static constexpr std::uint32_t FB_CONF_TASK_HEAP_SIZE = 8192U;
static constexpr std::uint32_t FB_CONF_RUNTIME_HEAP_SIZE = 4096U;
static constexpr std::uint32_t FB_CONF_MAX_TASKS = 8U;

// Memory Pool (co_mem)
static constexpr std::uint32_t FB_CONF_MEMORY_POOL_SIZE = 32768U; // 32KB Total pool
static constexpr std::uint32_t FB_CONF_MAX_ALLOCATIONS = 32U;
static constexpr std::uint32_t FB_CONF_MAX_ALLOC_SIZE = 16384U;

// IPC Router
static constexpr std::uint32_t FB_CONF_ROUTER_MAX_SERVICES = 16U;
static constexpr std::uint32_t FB_CONF_MAX_KV_PER_MESSAGE = 16U;

// HAL
static constexpr std::uint32_t FB_CONF_HAL_MAX_DEVICES = 8U;
static constexpr std::uint32_t FB_CONF_HAL_BUFFER_SIZE = 256U;
static constexpr std::uint32_t FB_CONF_HAL_MAX_BUFFERS = 4U;

// vSoC / vMMIO
static constexpr std::uint32_t FB_CONF_JIT_CACHE_SIZE = 4096U;
static constexpr std::uint32_t FB_CONF_JIT_MAX_HISTORY = 16U;
static constexpr std::uint32_t FB_CONF_JIT_MAX_COMPILE_QUEUE = 8U;
static constexpr std::uint32_t FB_CONF_JIT_HOTSPOT_THRESHOLD = 16U; // Hotness threshold
static constexpr std::uint32_t FB_CONF_JIT_MAX_TRACKED_PCS = 32U;  // Frequency table size
static constexpr std::uint32_t FB_CONF_GUEST_RAM_BASE = 0x00000000U;
static constexpr std::uint32_t FB_CONF_GUEST_RAM_SIZE = 8192U;
static constexpr std::uint32_t FB_CONF_VMMIO_BASE = 0x40000000U;
static constexpr std::uint32_t FB_CONF_VMMIO_MAX_REGIONS = 8U;

// Logging
static constexpr std::uint32_t FB_CONF_LOG_BUFFER_SIZE = 256U;
static constexpr std::uint32_t FB_CONF_LOG_MAX_DICT_ENTRIES = 64U;

// Debugger
static constexpr std::uint32_t FB_CONF_DEBUG_MAX_BREAKPOINTS = 8U;
static constexpr std::uint32_t FB_CONF_DEBUG_PACKET_SIZE = 1024U;
