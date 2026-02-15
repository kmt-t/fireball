/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <concepts>
#include <coroutine>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <source_location>
#include <span>
#include <string_view>
#include <type_traits>
#include <utility>
#include <variant>
#include <version>

#include <fireball_config.hxx>
#include <fireball_types.hxx>

/**
 * FIREBALL_HOST_HEAP_SIZE - Total size of the host heap partition.
 *
 * This macro defines the total size (in bytes) of the heap partition used for host-side
 * allocations (C++ standard library containers, system objects).
 */
#ifndef FIREBALL_HOST_HEAP_SIZE
#define FIREBALL_HOST_HEAP_SIZE FB_CONF_RUNTIME_HEAP_SIZE
#endif

/**
 * FIREBALL_TASK_HEAP_SIZE - Total size of the task heap partition.
 */
#ifndef FIREBALL_TASK_HEAP_SIZE
#define FIREBALL_TASK_HEAP_SIZE FB_CONF_TASK_HEAP_SIZE
#endif

/**
 * FIREBALL_GUEST_RAM_SIZE - Total size of the guest RAM.
 */
#ifndef FIREBALL_GUEST_RAM_SIZE
#define FIREBALL_GUEST_RAM_SIZE FB_CONF_GUEST_RAM_SIZE
#endif

namespace fireball {} // namespace fireball
