// QA owns inspection and explicit cache manipulation.
#include "../../native/tier3_plugins/jit/jit_runtime.hxx"
#include "../../native/tier3_plugins/jit/jit_runtime_apis.hxx"
#include "jit_measurements.hxx"
#include "jit_native_probe.hxx"

struct fb_native_dispatch_extension_call {
  fb_native_dispatch_call call;
  const fb_native_execution_extension *extension;
};

namespace fireball {
using configured_jit_provider_adapter = jit_provider_adapter<jit_measurements>;
}
using provider_adapter = fireball::configured_jit_provider_adapter;

using qa_jit_runtime = provider_adapter::runtime_type;
static bool qa_runtime_observe([[maybe_unused]] const fb_native_dispatch_call *input,
                               [[maybe_unused]] std::uint32_t pc) {
  return false;
}

static void qa_runtime_trace_executed(const fb_native_dispatch_call *input,
                                      std::uintptr_t trace_cookie) {
  if (input == nullptr || input->context == nullptr || input->jit_provider == nullptr ||
      input->jit_provider->owner == 0 || trace_cookie == 0) {
    return;
  }
  auto &runtime = *reinterpret_cast<qa_jit_runtime *>(input->jit_provider->owner);
  const auto *context = static_cast<const fireball_execution_context_native *>(input->context);
  auto *header = reinterpret_cast<const fireball::jit_trace_header *>(trace_cookie);
  for (std::uint32_t visited = 0;
       header != nullptr &&
       visited < fireball::jit_cache_bank_count * fireball::jit_cache_bank_entry_capacity;
       ++visited) {
    if (header->const_rel_offset <= header->blob_bytes) {
      const auto *rel = reinterpret_cast<const std::uint8_t *>(header) + header->const_rel_offset;
      if (context->rel == rel) {
        return;
      }
    }
    header = runtime.chain_target_header(*header);
  }
  // Not a CPS exit of the chain: the trace completed. The terminal body is the current block.
  runtime.record_runtime_trace_exit(context->current_block_index);
}

extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_dispatch(
    int (*dispatch)(const fb_native_dispatch_extension_call *, fb_native_result *),
    const fb_native_dispatch_call *input, fb_native_result *result) {
  if (dispatch == nullptr || input == nullptr || result == nullptr ||
      input->jit_provider == nullptr || input->jit_provider->owner == 0) {
    return 0;
  }
  const fb_native_execution_extension extension{input->jit_provider->owner, qa_runtime_observe,
                                                qa_runtime_trace_executed};
  const fb_native_dispatch_extension_call extension_call{*input, &extension};
  const auto status = dispatch(&extension_call, result);
  // A trace end with no lookup or observation after it in this dispatch is unobserved.
  auto &runtime = *reinterpret_cast<qa_jit_runtime *>(input->jit_provider->owner);
  auto &measurements = runtime.measurements;
  if (measurements.pending_runtime_source_block != fireball::jit_invalid_block_index) {
    ++measurements.chain_runtime_unobserved_trace_exits;
    measurements.pending_runtime_source_block = fireball::jit_invalid_block_index;
  }
  return status;
}

extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_flush(qa_jit_runtime *runtime) {
  return provider_adapter::fb_jit_runtime_flush(runtime);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_compile(qa_jit_runtime *runtime) {
  return provider_adapter::fb_jit_runtime_compile(runtime);
}
extern "C" FB_PYSIM_ABI_EXPORT void fb_qa_runtime_close(qa_jit_runtime *runtime) {
  provider_adapter::fb_jit_runtime_close(runtime);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_yield(qa_jit_runtime *runtime) {
  runtime->analyze_observation_history(true);
  return 1;
}
static_assert(sizeof(fireball::jit_provider_adapter<void>::runtime_type) < sizeof(qa_jit_runtime));

extern "C" std::int64_t fb_jit_card_index(std::uint64_t bytes, std::uint32_t shift,
                                          std::uint32_t pc) {
  return fireball::jit_card_index(bytes, shift, pc);
}

extern "C" std::int64_t fb_jit_card_count(std::uint64_t bytes, std::uint32_t shift) {
  return fireball::jit_card_count(bytes, shift);
}

// Snapshot records are QA fixtures, not an interpreter/plugin contract.
struct fb_native_trace_descriptor {
  std::uint32_t head_pc;
  std::uintptr_t entry_address;
  std::uint32_t byte_span, stack_words, frame_depth;
  std::uint32_t next_pc, loops_to, chain_next_pc, chain_stack_words;
  std::uint32_t chain_body_count;
  std::uint32_t *exec_count;
};
static int build_dispatch_table(qa_jit_runtime *runtime, fb_native_trace_descriptor *output,
                                std::uint32_t capacity) {
  std::array<std::uint32_t, fireball::jit_cache_bank_count> index{};
  std::uint32_t count = 0;
  while (true) {
    fireball::jit_cache_entry selected{};
    std::uint32_t selected_bank = fireball::jit_cache_bank_count;
    for (std::uint32_t id = 0; id < fireball::jit_cache_bank_count; ++id) {
      const auto &b = runtime->cache.banks[id];
      while (index[id] < b.entry_count && !b.entries[index[id]].resident()) {
        ++index[id];
      }
      if (index[id] < b.entry_count &&
          (!selected.resident() || b.entries[index[id]].head_pc < selected.head_pc)) {
        selected = b.entries[index[id]];
        selected_bank = id;
      }
    }
    if (!selected.resident()) {
      break;
    }
    auto *header = runtime->trace_header(selected);
    fireball::jit_wasm_block block_storage{};
    const auto *block = runtime->block_metadata_for_trace(*header, block_storage);
    if (count == capacity) {
      return -1;
    }
    assert(output != nullptr && block != nullptr && runtime->trace_body_address(selected) != 0 &&
           header->blob_bytes > 0);
    auto *executions = runtime->measurements.body_counter(header->block_index);
    const auto *next = runtime->chain_target_header(*header);
    const auto next_pc = next == nullptr ? fireball::jit_invalid_pc : next->head_pc;
    const auto words = header->chain_words;
    output[count++] = {selected.head_pc,
                       runtime->trace_body_address(selected),
                       block->byte_span,
                       header->stack_words,
                       block->frame_depth,
                       block->next_pc,
                       block->loops_to,
                       next_pc,
                       words,
                       header->chain_body_count,
                       executions};
    ++index[selected_bank];
  }
  return static_cast<int>(count);
}
namespace fireball {
enum class cache_field : std::uint32_t {
  evictions = 4,
  generation = 5,
  aging_steps = 6,
  aging_units = 7,
  aging_scanned = 8,
  bank_used = 9,
  bank_entry_capacity = 11,
  history_visits = 15,
  history_overwritten = 16,
  history_approximate = 17,
  trackable_generation = 18,
  queue_count = 19,
  resident_count = 20,
  rotations = 21,
  aging_ns = 22,
  compile_attempts = 23,
  compile_ns = 24,
  lookup_calls = 25,
};
}
extern "C" FB_PYSIM_ABI_EXPORT std::uint64_t fb_jit_runtime_scalar(const qa_jit_runtime *runtime,
                                                                   std::uint32_t field) {
  switch (static_cast<fireball::cache_field>(field)) {
  case fireball::cache_field::evictions:
    return runtime->measurements.evictions;
  case fireball::cache_field::generation:
    return runtime->measurements.generation;
  case fireball::cache_field::aging_steps:
    return runtime->measurements.aging_steps;
  case fireball::cache_field::aging_units:
    return runtime->measurements.aging_units;
  case fireball::cache_field::aging_scanned:
    return runtime->measurements.aging_scanned;
  case fireball::cache_field::bank_used: {
    std::uint64_t bytes = 0;
    for (const auto &bank : runtime->cache.banks) {
      bytes += bank.resident_bytes;
    }
    return bytes;
  }
  case fireball::cache_field::bank_entry_capacity: {
    std::uint64_t capacity = 0;
    for (const auto &bank : runtime->cache.banks) {
      capacity += bank.entry_capacity;
    }
    return capacity;
  }
  case fireball::cache_field::history_visits:
    return runtime->measurements.history_visits;
  case fireball::cache_field::history_overwritten:
    return runtime->measurements.history_overwritten;
  case fireball::cache_field::history_approximate:
    return runtime->measurements.history_approximate;
  case fireball::cache_field::trackable_generation:
    return runtime->measurements.trackable_generation;
  case fireball::cache_field::queue_count:
    return runtime->hotspots.compile_queue.count;
  case fireball::cache_field::resident_count: {
    std::uint64_t count = 0;
    for (const auto &bank : runtime->cache.banks) {
      for (std::uint32_t i = 0; i < bank.entry_count; ++i) {
        if (bank.entries[i].resident()) {
          ++count;
        }
      }
    }
    return count;
  }
  case fireball::cache_field::rotations:
    return runtime->measurements.rotations;
  case fireball::cache_field::aging_ns:
    return runtime->measurements.aging_ns;
  case fireball::cache_field::compile_attempts:
    return runtime->measurements.compile_attempts;
  case fireball::cache_field::compile_ns:
    return runtime->measurements.compile_ns;
  case fireball::cache_field::lookup_calls:
    return runtime->measurements.lookup_calls;
  default:
    return 0;
  }
}
struct qa_cache_lookup {
  std::uint8_t bank_id;
};
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_find(qa_jit_runtime *runtime, std::uint32_t pc,
                                                       qa_cache_lookup *output) {
  const auto location = runtime->find_cached_trace_by_pc(pc);
  if (location.trace_entry == nullptr) {
    return 0;
  }
  *output = {static_cast<std::uint8_t>(location.bank_id)};
  return 1;
}
extern "C" FB_PYSIM_ABI_EXPORT const fireball::jit_trace_header *
fb_qa_runtime_header(qa_jit_runtime *runtime, std::uint32_t pc) {
  const auto location = runtime->find_cached_trace_by_pc(pc);
  return location.trace_entry == nullptr ? nullptr : runtime->trace_header(*location.trace_entry);
}
extern "C" FB_PYSIM_ABI_EXPORT int
fb_jit_runtime_insert(qa_jit_runtime *runtime, const std::uint8_t *blob, std::uint32_t bytes) {
  runtime->install_trace(blob, bytes);
  return 1;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_lookup(qa_jit_runtime *runtime, std::uint32_t pc,
                                                         qa_cache_lookup *output) {
  const auto location = runtime->lookup_cached_trace(pc);
  if (location.trace_entry == nullptr) {
    return 0;
  }
  *output = {static_cast<std::uint8_t>(location.bank_id)};
  return 1;
}
// The aging sweep parameters are build-time configuration of the library; QA reads them back.
// Card state of a product runtime, read back for QA views. The runtime owns these arrays.
struct qa_card_views {
  std::uintptr_t states;
  std::uintptr_t dirty;
  std::uintptr_t trackable;
  std::uintptr_t cursor;
  std::uint32_t cards;
  std::uint32_t state_bytes;
  std::uint32_t dirty_bytes;
};
extern "C" FB_PYSIM_ABI_EXPORT void fb_qa_runtime_card_views(const qa_jit_runtime *runtime,
                                                             qa_card_views *output) {
  const auto &hotspots = runtime->hotspots;
  *output = {reinterpret_cast<std::uintptr_t>(hotspots.card_states),
             reinterpret_cast<std::uintptr_t>(hotspots.dirty_cards),
             reinterpret_cast<std::uintptr_t>(hotspots.configuration.trackable),
             reinterpret_cast<std::uintptr_t>(hotspots.aging_cursor),
             hotspots.card_count,
             (hotspots.card_count + 3) / 4,
             (hotspots.card_count + 7) / 8};
}
extern "C" FB_PYSIM_ABI_EXPORT std::uint32_t fb_qa_aging_step_units() {
  return FB_CONF_JIT_AGING_STEP_UNITS;
}
extern "C" FB_PYSIM_ABI_EXPORT std::uint32_t fb_qa_aging_step_scan_bytes() {
  return FB_CONF_JIT_AGING_STEP_SCAN_BYTES;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_rotate(qa_jit_runtime *runtime) {
  runtime->rotate_cache_banks();
  return 1;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_suppress(qa_jit_runtime *runtime,
                                                           std::uint32_t pc) {
  runtime->exclude_compile_candidate(pc);
  return 1;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_record(qa_jit_runtime *runtime,
                                                         std::uint32_t pc) {
  if (!runtime->is_profiled_pc(pc)) {
    return 1;
  }
  runtime->record_block_visit(pc);
  return 1;
}
extern "C" FB_PYSIM_ABI_EXPORT int
fb_jit_runtime_filtered_lookup(qa_jit_runtime *runtime, std::uint32_t pc, qa_cache_lookup *output) {
  if (!runtime->is_profiled_pc(pc) || runtime->card_state(pc >> FB_CONF_JIT_CARD_SHIFT) != 3) {
    return 0;
  }
  const auto location = runtime->lookup_cached_trace(pc);
  if (location.trace_entry == nullptr) {
    return 0;
  }
  *output = {static_cast<std::uint8_t>(location.bank_id)};
  return 1;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_snapshot(qa_jit_runtime *runtime,
                                                          fb_native_trace_descriptor *output,
                                                          std::uint32_t capacity) {
  return build_dispatch_table(runtime, output, capacity);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_visits(qa_jit_runtime *runtime,
                                                        const std::uint32_t *pcs,
                                                        std::uint32_t capacity,
                                                        std::uint64_t total) {
  if (total == 0) {
    return 1;
  }
  const auto retained = total < capacity ? total : capacity;
  assert((total == 0 || (capacity != 0 && pcs != nullptr)) &&
         retained == (total < runtime->hotspots.observation_history.capacity
                          ? total
                          : runtime->hotspots.observation_history.capacity) &&
         total <= UINT64_MAX - runtime->measurements.history_visits);
  // Validate and record each visit once; contract violations stop at the
  // source.
  for (std::uint64_t i = total - retained; i < total; ++i) {
    assert(runtime->is_profiled_pc(pcs[i % capacity]));
    runtime->record_block_visit(pcs[i % capacity]);
  }
  const auto omitted = total - retained;
  assert(omitted <= UINT64_MAX - runtime->measurements.history_dropped);
  runtime->measurements.history_dropped += omitted;
  runtime->measurements.history_visits += total - retained;
  return 1;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_analyze(qa_jit_runtime *runtime, int yielded) {
  runtime->analyze_observation_history(yielded != 0);
  return 1;
}
struct qa_dispatch_buffers {
  const fb_native_trace_descriptor *entries;
  std::uint32_t count;
  const std::uint8_t *mask;
  std::uint32_t cards, shift, mask_bytes;
  std::uint32_t *history;
  std::uint64_t history_bytes;
  // The shared trace entry: the callable of every trace in `entries`.
  std::uintptr_t common_trace_entry;
};
struct qa_dispatch_owner {
  const qa_dispatch_buffers &buffers;
  std::uint32_t visits = 0;
};

static const fb_native_trace_descriptor *qa_find(const qa_dispatch_buffers &b, wasm_pc_t pc) {
  for (std::uint32_t i = 0; i < b.count; ++i) {
    if (b.entries[i].head_pc == pc) {
      return b.entries + i;
    }
  }
  return nullptr;
}

static const fireball::jit_trace_header *
qa_lookup_trace(std::uintptr_t owner, [[maybe_unused]] block_index_t block_index, wasm_pc_t pc) {
  assert(owner != 0);
  const auto &b = reinterpret_cast<qa_dispatch_owner *>(owner)->buffers;
  const auto *start = qa_find(b, pc);
  if (start == nullptr) {
    return nullptr;
  }
  assert(start->entry_address != 0 && start->entry_address >= FB_CONF_JIT_X64_TRACE_HEADER_BYTES);
  const auto *header = reinterpret_cast<const fireball::jit_trace_header *>(
      start->entry_address - FB_CONF_JIT_X64_TRACE_HEADER_BYTES);
  assert(header->blob_bytes >= FB_CONF_JIT_X64_TRACE_HEADER_BYTES);
  assert(header->const_rel_offset <= header->blob_bytes && start->chain_body_count != 0);
  assert(b.common_trace_entry != 0);
  return header;
}

static int qa_observe_block(std::uintptr_t owner, block_index_t, wasm_pc_t) {
  return owner == 0 ? -1 : 1;
}

static int qa_finish_boundary(std::uintptr_t owner, std::uint32_t) { return owner == 0 ? 0 : 1; }

static qa_dispatch_owner *qa_owner(const fb_native_dispatch_call *input) {
  if (input == nullptr) {
    return nullptr;
  }
  const auto *extension_call = reinterpret_cast<const fb_native_dispatch_extension_call *>(input);
  if (extension_call->extension == nullptr || extension_call->extension->owner == 0) {
    return nullptr;
  }
  return reinterpret_cast<qa_dispatch_owner *>(extension_call->extension->owner);
}

static bool qa_observe(const fb_native_dispatch_call *input, std::uint32_t pc) {
  auto *owner = qa_owner(input);
  if (owner == nullptr) {
    return false;
  }
  auto &dispatch = *owner;
  const auto &b = dispatch.buffers;
  const auto card = pc >> b.shift;
  if (card >= b.cards || b.history_bytes == 0 || ((b.mask[card >> 3] >> (card & 7)) & 1u) == 0) {
    return false;
  }
  b.history[dispatch.visits++ % (b.history_bytes / sizeof(std::uint32_t))] = pc;
  return true;
}

static void qa_trace_executed(const fb_native_dispatch_call *input, std::uintptr_t trace_cookie) {
  auto *owner = qa_owner(input);
  if (owner == nullptr || trace_cookie == 0) {
    return;
  }
  const auto &b = owner->buffers;
  const auto *header = reinterpret_cast<const fireball::jit_trace_header *>(trace_cookie);
  auto *current = qa_find(b, header->head_pc);
  if (current == nullptr) {
    return;
  }
  std::uint32_t count = 0;
  while (current != nullptr && count++ < b.count) {
    if (current->exec_count != nullptr && *current->exec_count != fireball::jit_invalid_pc) {
      ++*current->exec_count;
    }
    current = current->chain_next_pc == fireball::jit_invalid_pc
                  ? nullptr
                  : qa_find(b, current->chain_next_pc);
  }
}

extern "C" FB_PYSIM_ABI_EXPORT int
fb_qa_dispatch(int (*dispatch)(const fb_native_dispatch_extension_call *, fb_native_result *),
               const qa_dispatch_buffers *buffers, const fb_native_dispatch_call *input,
               fb_native_result *result) {
  if (dispatch == nullptr || buffers == nullptr || input == nullptr || result == nullptr ||
      buffers->shift >= 32 || buffers->mask_bytes < (buffers->cards + 7u) / 8u ||
      buffers->history_bytes < sizeof(std::uint32_t) || buffers->history == nullptr ||
      buffers->history_bytes % sizeof(std::uint32_t) != 0 ||
      (buffers->cards != 0 && buffers->mask == nullptr) ||
      (buffers->count != 0 && buffers->entries == nullptr)) {
    return 0;
  }
  auto call = *input;
  qa_dispatch_owner owner{*buffers};
  const auto owner_address = reinterpret_cast<std::uintptr_t>(&owner);
  static constexpr std::uint8_t all_compiled_cards[] = {0xFFu, 0xFFu};
  const fb_native_jit_provider provider{
      owner_address,      all_compiled_cards,         8, 29, {}, qa_lookup_trace, qa_observe_block,
      qa_finish_boundary, buffers->common_trace_entry};
  call.jit_provider = &provider;
  fb_native_execution_extension extension{owner_address, qa_observe, qa_trace_executed};
  fb_native_dispatch_extension_call extension_call{call, &extension};
  return dispatch(&extension_call, result);
}

// Offsets of fb_native_jit_provider as the compiler sees them; the QA test checks its ctypes
// mirror.
extern "C" FB_PYSIM_ABI_EXPORT void fb_qa_native_provider_layout(std::uint32_t *output) {
  assert(output != nullptr);
  output[0] = static_cast<std::uint32_t>(sizeof(fb_native_jit_provider));
  output[1] = static_cast<std::uint32_t>(offsetof(fb_native_jit_provider, owner));
  output[2] = static_cast<std::uint32_t>(offsetof(fb_native_jit_provider, card_states));
  output[3] = static_cast<std::uint32_t>(offsetof(fb_native_jit_provider, card_count));
  output[4] = static_cast<std::uint32_t>(offsetof(fb_native_jit_provider, card_shift));
  output[5] = static_cast<std::uint32_t>(offsetof(fb_native_jit_provider, reserved0));
  output[6] = static_cast<std::uint32_t>(offsetof(fb_native_jit_provider, lookup));
  output[7] = static_cast<std::uint32_t>(offsetof(fb_native_jit_provider, observe_block));
  output[8] = static_cast<std::uint32_t>(offsetof(fb_native_jit_provider, finish_boundary));
  output[9] = static_cast<std::uint32_t>(offsetof(fb_native_jit_provider, trace_entry_address));
}

// Block-record marks as the native code defines them; the QA test checks the Python mirror.
extern "C" FB_PYSIM_ABI_EXPORT void fb_qa_no_trace_markers(std::uint8_t *output) {
  assert(output != nullptr);
  output[0] = static_cast<std::uint8_t>(FB_NATIVE_JIT_NO_TRACE_OBSERVED);
  output[1] = static_cast<std::uint8_t>(FB_NATIVE_JIT_NO_TRACE_UNOBSERVED);
}

extern "C" FB_PYSIM_ABI_EXPORT const fb_native_jit_provider *
fb_qa_runtime_provider(qa_jit_runtime *runtime) {
  return &runtime->provider;
}

extern "C" FB_PYSIM_ABI_EXPORT std::uintptr_t fb_qa_body_counter_address(qa_jit_runtime *runtime,
                                                                     std::uint32_t block_index) {
  return reinterpret_cast<std::uintptr_t>(runtime->measurements.body_counter(block_index));
}

extern "C" FB_PYSIM_ABI_EXPORT void fb_qa_enable_body_counters(qa_jit_runtime *runtime,
                                                                  std::uint32_t enabled) {
  runtime->measurements.body_counters_enabled = enabled != 0;
}

extern "C" FB_PYSIM_ABI_EXPORT void fb_jit_runtime_reset_counts(qa_jit_runtime *runtime) {
  runtime->measurements.reset_trace_counts();
}

extern "C" FB_PYSIM_ABI_EXPORT void
fb_jit_runtime_reset_chain_measurements(qa_jit_runtime *runtime) {
  runtime->measurements.reset_chain_runtime();
}

extern "C" FB_PYSIM_ABI_EXPORT int
fb_qa_runtime_print_measurements(const qa_jit_runtime *runtime,
                                 const fireball::printk_writer *writer) {
  return runtime->measurements.write_measurements(*writer);
}

extern "C" FB_PYSIM_ABI_EXPORT std::size_t fb_jit_runtime_size() { return sizeof(qa_jit_runtime); }

extern "C" FB_PYSIM_ABI_EXPORT std::size_t fb_jit_runtime_alignment() {
  return alignof(qa_jit_runtime);
}

extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_region_size(const std::uint32_t *lengths,
                                                      const std::uint32_t *bases,
                                                      std::uint32_t count, std::uint64_t *bytes) {
  if (bytes == nullptr || (count != 0 && lengths == nullptr)) {
    return 0;
  }
  std::uint64_t previous = 0, end = 0, cursor = 0;
  for (std::uint32_t i = 0; i < count; ++i) {
    const auto base = bases == nullptr ? cursor : bases[i];
    const auto next = base + lengths[i];
    if (base > UINT32_MAX || next > (std::uint64_t{1} << 32) || previous > base) {
      return 0;
    }
    if (lengths[i] != 0) {
      end = next;
    }
    previous = next;
    cursor = next + 1;
  }
  *bytes = end;
  return 1;
}

extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_bits(std::uint8_t *data, std::uint32_t count,
                                               std::uint32_t bits, std::uint32_t index,
                                               std::uint32_t op, std::uint32_t value) {
  if ((bits != 1 && bits != 2) || (count != 0 && data == nullptr)) {
    return -1;
  }
  const auto bytes = (static_cast<std::uint64_t>(count) * bits + 7) / 8;
  if (op == 4) {
    for (std::uint64_t i = 0; i < bytes; ++i) {
      data[i] = 0;
    }
    return 0;
  }
  if (op == 5) {
    return index < bytes ? data[index] : -1;
  }
  if (op == 6) {
    return (bytes == 0 ? value == 0 : value < bytes) ? static_cast<int>(value) : -1;
  }
  if (index >= count) {
    return -1;
  }
  const auto per_byte = 8 / bits, shift = (index % per_byte) * bits;
  const auto mask = (1u << bits) - 1;
  const auto state = (data[index / per_byte] >> shift) & mask;
  if (op == 0) {
    return static_cast<int>(state);
  }
  std::uint32_t next = value;
  int result = 0;
  if (op == 1) {
    if (value > mask) {
      return -1;
    }
    result = static_cast<int>(value);
  } else if (op == 2 && bits == 2) {
    next = state < 2 ? state + 1 : state;
    result = next;
  } else if (op == 3 && bits == 2) {
    next = state == 1 ? 0 : state;
    result = state == 1 ? 1 : 0;
  } else {
    return -1;
  }
  data[index / per_byte] =
      static_cast<std::uint8_t>((data[index / per_byte] & ~(mask << shift)) | (next << shift));
  return result;
}

extern "C" FB_PYSIM_ABI_EXPORT std::int64_t
fb_qa_block_score(const fireball::jit_wasm_block *block) {
  return fireball::score_block(*block);
}

extern "C" FB_PYSIM_ABI_EXPORT std::uint64_t fb_qa_owned_pc(const qa_jit_runtime *runtime,
                                                            std::uint32_t slot) {
  if (slot >= runtime->measurements.compilation_count) {
    return UINT64_MAX;
  }
  return runtime->measurements.compilation_order[slot];
}
struct qa_resident_record {
  std::uint8_t bank_id;
  std::uint8_t reserved;
  std::uint32_t pc;
  std::uint32_t count;
};
extern "C" FB_PYSIM_ABI_EXPORT std::uintptr_t
fb_qa_runtime_common_trace_entry(const qa_jit_runtime *runtime) {
  return runtime->common_trace_entry_address();
}

extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_resident_record(const qa_jit_runtime *runtime,
                                                         std::uint32_t index,
                                                         qa_resident_record *output) {
  std::uint8_t bank_id = 0;
  for (const auto &bank : runtime->cache.banks) {
    for (std::uint32_t i = 0; i < bank.entry_count; ++i) {
      const auto &resident_entry = bank.entries[i];
      if (!resident_entry.resident()) {
        continue;
      }
      if (index-- == 0) {
        const auto *trace = runtime->trace_header(resident_entry);
        *output = {bank_id, 0, resident_entry.head_pc, runtime->measurements.body_count(trace->block_index)};
        return 1;
      }
    }
    ++bank_id;
  }
  return 0;
}

extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_age(qa_jit_runtime *runtime) {
  return runtime->age_hotspot_cards();
}

// Admission record of one block (upper three bits strikes, lower five bits lifetime).
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_admission_record(const qa_jit_runtime *runtime,
                                                          std::uint32_t block_index) {
  assert(runtime != nullptr && block_index < runtime->module_view->block_count);
  return runtime->hotspots.admission_records[block_index];
}

// Evictions counted in the current group of rotations, and the previous completed group.
extern "C" FB_PYSIM_ABI_EXPORT void fb_qa_admission_window(const qa_jit_runtime *runtime,
                                                           std::uint32_t *output) {
  assert(runtime != nullptr && output != nullptr);
  const auto &window = runtime->hotspots.admission_window;
  output[0] = window.evictions;
  output[1] = window.previous_evictions;
  output[2] = window.rotations;
}
