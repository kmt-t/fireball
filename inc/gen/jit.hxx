/**
 * Auto-generated from WIT. Do not edit.
 */
#pragma once

#include <fireball_types.hxx>
#include <gen/types.hxx>
#include <fireball_config.hxx>
#include <cstdint>
#include <string_view>
#include <expected>
#include <optional>
#include <vector>
#include <tuple>

namespace fireball {

/**
 * Tier 3: Hotspot Detector
 * @inv: history_count <= FB_CONF_JIT_MAX_HISTORY
 * @inv: compile_queue_count <= FB_CONF_JIT_MAX_COMPILE_QUEUE
 */
class hotspot_detector {
public:
  hotspot_detector() = default;
  ~hotspot_detector() = default;

  /**
   * Records an execution of a WASM PC (Card Marking).
   * @pre: pc != 0
   */
  void record_execution(address pc) noexcept;

  /**
   * Processes execution history to identify hotspots.
   * @post: history_count == 0
   */
  void process_history() noexcept;

  /**
   * Gets the current 2-bit state of a card.
   */
  uint8_t get_card_state(address pc) noexcept;

};

/**
 * Tier 3: JIT Entry Index
 * @inv: entries is sorted by wasm_pc
 * @inv: entry_count <= FB_CONF_JIT_MAX_ENTRIES
 */
class jit_entry_index {
public:
  jit_entry_index() = default;
  ~jit_entry_index() = default;

  /**
   * Lookup native trace address.
   * @post: result.is_ok() -> result_addr != 0
   */
  std::expected<address, bool> lookup(address pc) noexcept;

  /**
   * Registers a new JIT entry.
   * @pre: native_offset < FB_CONF_JIT_CACHE_SIZE
   * @post: lookup(pc).is_ok()
   */
  void register_entry(address pc, address native_offset) noexcept;

  /**
   * Promotes an entry from Old to Active cache.
   * @pre: pc exists in Old bank
   * @post: lookup(pc).is_ok() in Active bank
   */
  std::expected<address, bool> promote(address pc) noexcept;

  /**
   * Gets the range in the entry table for a given card group.
   */
  std::expected<std::tuple<types>, bool> get_search_range(address pc) noexcept;

};

/**
 * Tier 3: Copy-and-Patch Engine
 * @inv: templates are immutable after boot
 */
class copy_and_patch_engine {
public:
  copy_and_patch_engine() = default;
  ~copy_and_patch_engine() = default;

  /**
   * Patches a WASM trace into native code.
   * @pre: pc != 0
   */
  operation_result patch_trace(address pc, uint32_t template_id) noexcept;

  /**
   * Resolves a template-id from a WASM opcode or pattern.
   */
  std::expected<uint32_t, bool> lookup_template(uint8_t opcode) noexcept;

};

/**
 * Tier 2: JIT Compiler Manager
 * @inv: active_bank.cache_offset <= FB_CONF_JIT_CACHE_SIZE
 * @inv: old_bank.cache_offset <= FB_CONF_JIT_CACHE_SIZE
 */
class jit_compiler {
public:
  jit_compiler() = default;
  ~jit_compiler() = default;

  /**
   * Initializes the JIT environment with injected dependencies.
   * @pre: !initialized
   * @pre: detector, index, engine are valid resource handles
   * @post: initialized && dependencies are bound
   */
  operation_result initialize(jit_config config, uintptr_t detector, uintptr_t index, uintptr_t engine) noexcept;

  /**
   * Fast lookup for execution.
   * @pre: initialized
   */
  std::expected<address, bool> lookup_trace(address pc) noexcept;

  /**
   * Batch compilation of hotspots.
   * @pre: initialized
   */
  void process_batch_compile() noexcept;

  /**
   * Swaps Active and Old banks.
   * @pre: initialized
   * @post: active_bank.cache_offset == 0
   * @post: old_bank.cache_offset == old(active_bank.cache_offset)
   */
  void swap_banks() noexcept;

};

} // namespace fireball
