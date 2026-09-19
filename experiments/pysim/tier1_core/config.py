"""Configuration constants shared by the reference simulator tiers."""

from __future__ import annotations

JIT_CARD_SHIFT: int = 2
JIT_CARD_BYTES: int = 1 << JIT_CARD_SHIFT
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
FB_CONF_MAX_EXPORTS: int = 64
FB_CONF_MAX_GLOBALS: int = 32
FB_CONF_MAX_TABLES: int = 16
FB_CONF_MAX_MEMORIES: int = 4
FB_CONF_MAX_ELEMENTS: int = 64
FB_CONF_MAX_DATA_SEGMENTS: int = 64
FB_CONF_MAX_BASIC_BLOCKS: int = 1024
FB_CONF_MAX_LOCALS: int = 256
FB_CONF_MAX_VALUE_STACK: int = 64
FB_CONF_DEBUG_MAX_BREAKPOINTS: int = 64
FB_CONF_DEBUG_MAX_ASSERTIONS: int = 64

JIT_CACHE_PAGE_BYTES: int = 4096
JIT_CACHE_REGION_BASE_ADDRESS: int = 0x20040000
JIT_CACHE_COMMON_CODE_BYTES: int = 2048
JIT_CACHE_COMMON_CODE_OFFSET_BYTES: int = 0
JIT_CACHE_ABSOLUTE_ADDRESS_POOL_BYTES: int = 256
JIT_CACHE_BANK_COUNT: int = 3
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
JIT_CACHE_MAX_INBOUND_SOURCES: int = 32
# The x64 simulator has its own header layout.  The 64-bit chain target is
# aligned as a native pointer, so it is not the ARM header layout.
JIT_X64_TRACE_HEADER_BYTES: int = 52
JIT_X64_CHAIN_TARGET_OFFSET: int = 0x10
JIT_X64_HELPER_TARGET_OFFSET: int = 0x28
# Compatibility name for existing simulator-wide capacity calculations.
JIT_TRACE_HEADER_BYTES: int = JIT_X64_TRACE_HEADER_BYTES
# Physical offsets in the trace header.  These are consumed by the common
# helper stub and are part of the trace/code-cache ABI.
# Common-code offsets are code-region offsets, not trace-header field offsets.
# Keep the AAPCS entry points in the shared 2KB prefix. Each helper contract has
# its own entry; the trace header selects that entry by offset.
JIT_TRACE_COMMON_PROLOGUE_OFFSET: int = 0
JIT_TRACE_COMMON_EPILOGUE_OFFSET: int = 32
JIT_TRACE_COMMON_HELPER_OFFSET: int = 48
JIT_TRACE_TYPED_I32_HELPER_OFFSET: int = 352
JIT_TRACE_TYPED_I32_HELPER_COUNT: int = 4
JIT_TRACE_WIDE_HELPER_OFFSET: int = 512
JIT_TRACE_WIDE_HELPER_COUNT: int = 11
JIT_TRACE_HELPER_ENTRY_BYTES: int = 32
JIT_TRACE_HELPER_TARGET_OFFSET: int = JIT_X64_HELPER_TARGET_OFFSET
JIT_TRACE_DEFAULT_BYTES: int = 64
RUNTIME_BLOCK_CACHE_SLOT_COUNT: int = 16
RUNTIME_DEBUG_REPORT_LINE_CAPACITY: int = RUNTIME_BLOCK_CACHE_SLOT_COUNT * 16
RUNTIME_DEBUG_TRACE_REPORT_CAPACITY: int = JIT_CACHE_BANK_COUNT * (
    JIT_CACHE_BANK_CAPACITY_BYTES // JIT_TRACE_HEADER_BYTES
)
