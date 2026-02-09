/**
 * vSoC Component Harness.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include "../fireball.hxx"

namespace fireball::vsoc {

// Forward declarations of engine interfaces (Tier 2/3)
class wasm_loader;
class interpreter;
class jit_compiler;
class debugger;
class vmmio_controller;

/**
 * @brief vSoC Harness (Static DI).
 * Aggregates pointers to various engines.
 * Matches docs/orders/components/vsoc.md: vsoc_harness
 */
struct harness {
  wasm_loader* loader;
  interpreter* interp;
  jit_compiler* jit;
  debugger* dbg;
  vmmio_controller* vmmio;
};

} // namespace fireball::vsoc
