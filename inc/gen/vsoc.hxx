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
 * WASM Section Types (standard 0.2/1.0)
 */
enum class wasm_section_type : uint8_t {
  CUSTOM,
  TYPE_SECTION,
  IMPORT_SECTION,
  FUNCTION_SECTION,
  TABLE_SECTION,
  MEMORY_SECTION,
  GLOBAL_SECTION,
  EXPORT_SECTION,
  START_SECTION,
  ELEMENT_SECTION,
  CODE_SECTION,
  DATA_SECTION,
  DATA_COUNT_SECTION,
};

/**
 * View for a specific WASM module's section metadata.
 */
struct wasm_section_view {
  wasm_section_type id;
  binary_view data;
};

/**
 * Resource for reading WASM binary data from ROM with cursor management.
 */
class binary_stream {
public:
  binary_stream() = default;
  ~binary_stream() = default;

  /**
   * Creates a stream from a binary view.
   */
  static uintptr_t from_view(binary_view view) noexcept;

  /**
   * Reads primitive types.
   */
  std::expected<uint8_t, bool> read_u8() noexcept;

  std::expected<int8_t, bool> read_s8() noexcept;

  std::expected<uint16_t, bool> read_u16() noexcept;

  std::expected<int16_t, bool> read_s16() noexcept;

  std::expected<uint32_t, bool> read_u32() noexcept;

  std::expected<int32_t, bool> read_s32() noexcept;

  std::expected<uint64_t, bool> read_u64() noexcept;

  std::expected<int64_t, bool> read_s64() noexcept;

  /**
   * Reads LEB128 encoded integers.
   */
  std::expected<uint32_t, bool> read_leb128_u32() noexcept;

  std::expected<int32_t, bool> read_leb128_s32() noexcept;

  std::expected<uint64_t, bool> read_leb128_u64() noexcept;

  std::expected<int64_t, bool> read_leb128_s64() noexcept;

  /**
   * Reads a block of data.
   */
  std::expected<binary_view, bool> read_bytes(byte_count len) noexcept;

  /**
   * Remaining bytes in the stream.
   */
  byte_count remaining() noexcept;

};

/**
 * Accessor for function-level metadata and instructions.
 */
class wasm_function_accessor {
public:
  wasm_function_accessor() = default;
  ~wasm_function_accessor() = default;

  /**
   * Gets the function signature index.
   */
  uint32_t get_type_index() noexcept;

  /**
   * Returns a stream for decoding local variables.
   */
  uintptr_t get_locals_stream() noexcept;

  /**
   * Returns a stream for the bytecode instructions.
   */
  uintptr_t get_code_stream() noexcept;

};

/**
 * Accessor for global variable metadata.
 */
class wasm_global_accessor {
public:
  wasm_global_accessor() = default;
  ~wasm_global_accessor() = default;

  /**
   * Gets the value type and mutability.
   */
  std::tuple<types> get_metadata() noexcept;

  /**
   * Returns a stream for the initialization expression.
   */
  uintptr_t get_init_expr_stream() noexcept;

};

/**
 * Tier 3: WASM Module View (Registry Entry Reference)
 * @inv: module_id is unique
 */
class wasm_module_view {
public:
  wasm_module_view() = default;
  ~wasm_module_view() = default;

  /**
   * Gets the raw ROM metadata for a specific section.
   */
  std::expected<wasm_section_view, bool> get_section(wasm_section_type stype) noexcept;

  /**
   * Lookups an exported function index by name.
   */
  std::expected<uint32_t, bool> lookup_export_func(std::string_view name) noexcept;

  /**
   * Gets an accessor for a specific function.
   * @pre: func_idx is valid
   */
  std::expected<uintptr_t, bool> get_function(uint32_t func_idx) noexcept;

  /**
   * Gets an accessor for a specific global.
   * @pre: global_idx is valid
   */
  std::expected<uintptr_t, bool> get_global(uint32_t global_idx) noexcept;

};

/**
 * Tier 3: WASM Module Loader
 */
class wasm_loader {
public:
  wasm_loader() = default;
  ~wasm_loader() = default;

  /**
   * Prepares a WASM module for execution from ROM.
   * @pre: wasm is a valid binary_view in ROM
   * @post: result.is_ok() -> module_view is valid
   */
  std::expected<uintptr_t, recovery_strategy> prepare(binary_view wasm) noexcept;

  /**
   * Loads a module's linear memory into guest RAM.
   * @pre: module is valid
   * @post: initial memory pages allocated and initialized
   */
  operation_result load(uintptr_t module) noexcept;

  /**
   * Lookups a loaded module from the registry.
   */
  std::expected<uintptr_t, bool> lookup(std::string_view name) noexcept;

};

/**
 * Tier 3: Virtual MMIO Manager
 * @inv: vmmio_base == FB_CONF_VMMIO_BASE
 */
class vmmio_manager {
public:
  vmmio_manager() = default;
  ~vmmio_manager() = default;

  /**
   * Dispatches a trapped access to the appropriate handler.
   * @pre: addr >= vmmio_base && addr < vmmio_base + vmmio_size
   */
  operation_result dispatch_access(address addr, binary_view buffer, bool is_write) noexcept;

  /**
   * Registers a host-side hook for a vMMIO region.
   * @pre: handler-addr != 0
   */
  operation_result register_hook(vmmio_hook_id hook_id, address handler_addr) noexcept;

  /**
   * Reserves static regions in the DYNAMIC space.
   * @pre: pages-count <= FB_CONF_VMMIO_DYNAMIC_PAGES
   */
  void reserve_static_regions(uint32_t pages_count) noexcept;

};

/**
 * Tier 2: vSoC Runtime
 * @inv: ram_size == FB_CONF_GUEST_RAM_SIZE
 */
class vsoc_runtime {
public:
  vsoc_runtime() = default;
  ~vsoc_runtime() = default;

  /**
   * Initializes the runtime environment with injected dependencies.
   * @pre: !initialized
   * @pre: loader, vmmio, memory are valid resource handles
   * @post: initialized && dependencies are bound
   */
  operation_result initialize(uintptr_t loader, uintptr_t vmmio, address memory) noexcept;

  /**
   * Steps execution until yield or trap.
   * @pre: initialized
   * @pre: state == running || state == halted
   * @post: result.is_ok() -> state updated
   */
  std::expected<execution_state, recovery_strategy> step() noexcept;

  /**
   * Notifies a virtual interrupt to the guest.
   * @pre: initialized
   * @pre: irq-id is valid
   */
  void notify_interrupt(uint32_t irq_id) noexcept;

};

} // namespace fireball
