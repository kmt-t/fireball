/**
 * Auto-generated from WIT. Do not edit.
 */
#pragma once

#include <fireball_types.hxx>
#include <gen/jit_types.hxx>
#include <gen/types.hxx>
#include <fireball_config.hxx>
#include <cstdint>
#include <string_view>
#include <expected>
#include <optional>
#include <tuple>

namespace fireball {

/**
 * Tier 3: Hotspot Detector
 * @inv: history_count <= FB_CONF_JIT_MAX_HISTORY
 * @inv: compile_queue_count <= FB_CONF_JIT_MAX_COMPILE_QUEUE
 */
class hotspot_detector_unit {
public:
  hotspot_detector_unit() = default;
  ~hotspot_detector_unit() = default;

  /**
   * Records an execution of a WASM PC (Card Marking).
   * @pre: pc != 0
   */
  void mark(mem_address pc) noexcept;

  /**
   * Processes execution history to identify hotspots.
   * @post: history_count == 0
   */
  void process_history() noexcept;

  /**
   * Gets the current 2-bit state of a card.
   */
  uint8_t get_card_state(mem_address pc) noexcept;

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
  std::expected<mem_address, bool> get_trace_address(mem_address pc) noexcept;

  /**
   * Registers a new JIT entry.
   * @pre: native_offset < FB_CONF_JIT_CACHE_SIZE
   * @post: lookup(pc).is_ok()
   */
  void register(mem_address pc, mem_address native_offset) noexcept;

  /**
   * Promotes an entry from Old to Active cache.
   * @pre: pc exists in Old bank
   * @post: lookup(pc).is_ok() in Active bank
   */
  std::expected<mem_address, bool> promote(mem_address pc) noexcept;

  /**
   * Gets the range in the entry table for a given card group.
   */
  std::expected<std::tuple<uint32_t, uint32_t>, bool> get_search_range(mem_address pc) noexcept;

};

/**
 * Tier 3: Copy-and-Patch Engine
 * @inv: templates are immutable after boot
 */
class patch_engine_unit {
public:
  patch_engine_unit() = default;
  ~patch_engine_unit() = default;

  /**
   * Patches a WASM trace into native code.
   * @pre: pc != 0
   */
  operation_result patch(mem_address pc, uint32_t template_id) noexcept;

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
class compiler_unit {
public:
  compiler_unit() = default;
  ~compiler_unit() = default;

  /**
   * Initializes the JIT environment with injected dependencies.
   * @pre: !initialized
   * @pre: detector, index, engine are valid resource handles
   * @post: initialized && dependencies are bound
   */
  operation_result init_compiler(jit_setup_record config, uintptr_t detector, uintptr_t index, uintptr_t engine) noexcept;

  /**
   * Fast lookup for execution.
   * @pre: initialized
   */
  std::expected<mem_address, bool> get_trace_address(mem_address pc) noexcept;

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
