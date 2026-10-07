/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <allocator/specified_allocator.hxx>

namespace fireball::allocator {

/**
 * host_allocator_tag - Type tag for an explicitly sized host arena.
 *
 * The caller supplies capacity and uses the project heap API directly.
 */
struct host_allocator_tag {};

/**
 * host_allocator - Fixed arena with an explicit capacity in bytes.
 * Standard dynamic containers and global new/delete are not enabled by this
 * alias.
 */
template <std::uint32_t N>
using host_allocator = specified_allocator<N, host_allocator_tag>;

} // namespace fireball::allocator
