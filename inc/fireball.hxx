/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <cstdint>
#include <cstddef>
#include <version>
#include <span>
#include <string_view>
#include <optional>
#include <variant>
#include <utility>
#include <concepts>
#include <type_traits>
#include <coroutine>
#include <source_location>

#include <fireball_config.hxx>
#include <types.hxx>

/**
 * FIREBALL_HOST_HEAP_SIZE - Total size of the host heap partition.
 *
 * This macro defines the total size (in bytes) of the heap partition used for host-side
 * allocations (C++ standard library containers, system objects).
 */
#ifndef FIREBALL_HOST_HEAP_SIZE
#define FIREBALL_HOST_HEAP_SIZE FB_CONF_RUNTIME_HEAP_SIZE
#endif

namespace fireball {

} // namespace fireball
