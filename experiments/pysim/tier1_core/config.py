"""Configuration constants shared by the reference simulator tiers."""

from __future__ import annotations

JIT_CARD_SHIFT: int = 2
JIT_CARD_BYTES: int = 1 << JIT_CARD_SHIFT
# Initial estimate for a roughly 300 us quantum on a 100 MHz STM32 Cortex-M33:
# 30,000 cycles / 64 backedges = about 469 cycles per backedge.
# Calibrate this backedge count on the actual target and workload.
FB_CONF_RUNTIME_YIELD_THRESHOLD: int = 64
# Retry policy shared by recovery-capable components (system_config.md §3.3.8).
FB_CONF_RETRY_BACKOFF_MS: int = 10
FB_CONF_RETRY_MAX_ATTEMPTS: int = 3
# Aging sweep of the card table ({JIT_CardAgingSweep}); the public spelling is
# FB_CONF_JIT_AGING_STEP_UNITS / FB_CONF_JIT_AGING_STEP_SCAN_BYTES in system_config.md.
# A step ends after this many non-zero update-bitmap bytes (8 functions each) are
# processed, or after this many bytes (zero bytes included) are scanned.
FB_CONF_JIT_AGING_STEP_UNITS: int = 2
FB_CONF_JIT_AGING_STEP_SCAN_BYTES: int = 8

# Fixed capacities and physical cache dimensions used by the simulator.
FB_CONF_MAX_TYPES: int = 256
FB_CONF_MAX_IMPORTS: int = 32
FB_CONF_MAX_FUNCTIONS: int = 256
# PySIM runtime-owned allocation-accounting ceiling; this is not a target RAM budget.
FB_CONF_RUNTIME_BUMP_ARENA_BYTES: int = 131_072
FB_CONF_MAX_GLOBALS: int = 32
FB_CONF_MAX_TABLES: int = 16
FB_CONF_MAX_MEMORIES: int = 4
FB_CONF_MAX_WASM_PAGES: int = 16
FB_CONF_MAX_ELEMENTS: int = 64
FB_CONF_MAX_DATA_SEGMENTS: int = 64
FB_CONF_MAX_BASIC_BLOCKS: int = 1024
FB_CONF_MAX_LOCALS: int = 256
FB_CONF_MAX_VALUE_STACK: int = 64
FB_CONF_DEBUG_MAX_BREAKPOINTS: int = 64
FB_CONF_DEBUG_MAX_ASSERTIONS: int = 64
FB_CONF_LOG_DICT_MAX_ENTRIES: int = 128

JIT_CACHE_PAGE_BYTES: int = 4096
JIT_CACHE_COMMON_CODE_BYTES: int = 2048
JIT_CACHE_COMMON_CODE_OFFSET_BYTES: int = 0
JIT_CACHE_ABSOLUTE_ADDRESS_POOL_BYTES: int = 256
JIT_CACHE_BANK_COUNT: int = 3
# Per-bank metadata RAM budget, independent of variable generated code sizes.
JIT_CACHE_BANK_ENTRY_CAPACITY: int = 32
JIT_HISTORY_CAPACITY: int = 32
JIT_COMPILE_QUEUE_CAPACITY: int = 4
JIT_CACHE_BANK_CAPACITY_BYTES: int = 2048
JIT_CACHE_ACTIVE_OFFSET_BYTES: int = JIT_CACHE_COMMON_CODE_BYTES
JIT_CACHE_WARM_OFFSET_BYTES: int = JIT_CACHE_ACTIVE_OFFSET_BYTES + JIT_CACHE_BANK_CAPACITY_BYTES
JIT_CACHE_OLDEST_OFFSET_BYTES: int = JIT_CACHE_WARM_OFFSET_BYTES + JIT_CACHE_BANK_CAPACITY_BYTES
JIT_CACHE_REGION_BYTES: int = (
    JIT_CACHE_COMMON_CODE_BYTES + JIT_CACHE_BANK_COUNT * JIT_CACHE_BANK_CAPACITY_BYTES
)
# Public configuration spelling used by the architecture and target headers.
# Keep the derived layout above as the single source of truth.
FB_CONF_JIT_CACHE_SIZE: int = JIT_CACHE_REGION_BYTES
JIT_CACHE_REGION_PAGE_COUNT: int = JIT_CACHE_REGION_BYTES // JIT_CACHE_PAGE_BYTES
JIT_CACHE_FAST_SLOT_COUNT: int = 16
# The x64 simulator has its own compact header layout, separate from ARM.
JIT_X64_TRACE_HEADER_BYTES: int = 40
JIT_X64_TRACE_ENTRY_STUB_BYTES: int = 17
JIT_X64_CHAIN_TARGET_OFFSET: int = 0x00
JIT_X64_HELPER_TARGET_OFFSET: int = 0x08
JIT_X64_COMMON_CODE_RELATIVE_OFFSET: int = 0x10
# Compatibility name for existing simulator-wide capacity calculations.
JIT_TRACE_HEADER_BYTES: int = JIT_X64_TRACE_HEADER_BYTES
# Physical offsets in the trace header.  These are consumed by the common
# helper stub and are part of the trace/code-cache ABI.
# Common-code offsets are code-region offsets, not trace-header field offsets.
# Keep the x64 helper entry points in the shared code prefix. Each helper
# contract has its own entry; ARMv8-M physical placement is TBD.
JIT_TRACE_COMMON_PROLOGUE_OFFSET: int = 0
JIT_TRACE_COMMON_EPILOGUE_OFFSET: int = 40
JIT_TRACE_COMMON_HELPER_OFFSET: int = 56
JIT_CACHE_ABSOLUTE_ADDRESS_POOL_OFFSET: int = 88
JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET: int = 1024
JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES: int = 56
JIT_TRACE_TYPED_I32_HELPER_OFFSET: int = 352
JIT_TRACE_TYPED_I32_HELPER_COUNT: int = 4
JIT_TRACE_HELPER_ENTRY_BYTES: int = 32
JIT_TRACE_HELPER_TARGET_OFFSET: int = JIT_X64_HELPER_TARGET_OFFSET
# Default for synthetic traces; generated traces use their actual byte length.
JIT_TRACE_DEFAULT_BYTES: int = 64
RUNTIME_BLOCK_CACHE_SLOT_COUNT: int = 16
RUNTIME_DEBUG_REPORT_LINE_CAPACITY: int = RUNTIME_BLOCK_CACHE_SLOT_COUNT * 16
RUNTIME_DEBUG_TRACE_REPORT_CAPACITY: int = JIT_CACHE_BANK_COUNT * JIT_CACHE_BANK_ENTRY_CAPACITY
