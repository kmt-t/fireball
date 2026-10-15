// QA owns inspection and explicit cache manipulation.
#include "../../native/tier3_plugins/jit/jit_runtime.hxx"
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
        const auto *exit = reinterpret_cast<const fb_native_jit_trace_exit *>(context->rel);
        runtime.record_runtime_trace_exit(header->block_index, exit->result_words,
                                          header->frame_depth);
        return;
      }
    }
    header = runtime.chain_target_header(*header);
  }
  ++runtime.measurements.chain_runtime_unobserved_trace_exits;
}

extern "C" FB_PYSIM_ABI_EXPORT int
fb_qa_runtime_dispatch(int (*dispatch)(const fb_native_dispatch_extension_call *,
                                       fb_native_result *),
                       const fb_native_dispatch_call *input, fb_native_result *result) {
  if (dispatch == nullptr || input == nullptr || result == nullptr ||
      input->jit_provider == nullptr || input->jit_provider->owner == 0) {
    return 0;
  }
  const fb_native_execution_extension extension{input->jit_provider->owner,
                                                 qa_runtime_observe,
                                                 qa_runtime_trace_executed};
  const fb_native_dispatch_extension_call extension_call{*input, &extension};
  return dispatch(&extension_call, result);
}

extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_flush(qa_jit_runtime *runtime) {
  return provider_adapter::fb_jit_runtime_flush(runtime);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_compile(qa_jit_runtime *runtime,
                                                         std::uint32_t budget) {
  return provider_adapter::fb_jit_runtime_compile(runtime, budget);
}
extern "C" FB_PYSIM_ABI_EXPORT void fb_qa_runtime_close(qa_jit_runtime *runtime) {
  provider_adapter::fb_jit_runtime_close(runtime);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_yield(qa_jit_runtime *runtime) {
  runtime->analyze_observation_history(true);
  return 1;
}
static_assert(sizeof(fireball::jit_provider_adapter<void>::runtime_type) < sizeof(qa_jit_runtime));
extern "C" int fb_jit_runtime_bind_cards(fireball::qa_jit_runtime *runtime, std::uint8_t *states,
                                         std::uint32_t state_bytes, std::uint8_t *dirty,
                                         std::uint32_t dirty_bytes, std::uint32_t cards,
                                         std::uint32_t shift, std::uint32_t *cursor,
                                         std::uint32_t units, std::uint32_t scan_bytes) {
  return provider_adapter::fb_jit_runtime_bind_cards(
      runtime, states, state_bytes, dirty, dirty_bytes, cards, shift, cursor, units, scan_bytes);
}

extern "C" std::int64_t fb_jit_card_index(std::uint64_t bytes, std::uint32_t shift,
                                          std::uint32_t pc) {
  return fireball::jit_card_index(bytes, shift, pc);
}

extern "C" std::int64_t fb_jit_card_count(std::uint64_t bytes, std::uint32_t shift) {
  return fireball::jit_card_count(bytes, shift);
}

extern "C" FB_PYSIM_ABI_EXPORT int
fb_jit_runtime_bind_profile(fireball::qa_jit_runtime *runtime,
                            fireball::jit_measurements::profile_type p) {
  return provider_adapter::fb_jit_runtime_bind_profile(runtime, p);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_bind_blocks(qa_jit_runtime *runtime,
                                                             const fireball::jit_wasm_block *blocks,
                                                             std::uint32_t count) {
  if (count != 0 && blocks == nullptr) {
    return 0;
  }
  runtime->hotspots.configuration.blocks = blocks;
  runtime->hotspots.configuration.block_count = count;
  return 1;
}

// Snapshot records are QA fixtures, not an interpreter/plugin contract.
struct fb_native_trace_descriptor {
  std::uint32_t head_pc;
  std::uintptr_t entry_address;
  std::uint32_t byte_span, result_words, stack_words, frame_depth;
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
    assert(output != nullptr && block != nullptr && runtime->trace_entry_address(selected) != 0 &&
           header->blob_bytes > 0);
    auto *executions = static_cast<std::uint32_t *>(block->extension_data);
    const auto *next = runtime->chain_target_header(*header);
    const auto next_pc = next == nullptr ? fireball::jit_invalid_pc : next->head_pc;
    const auto words = header->chain_words;
    output[count++] = {selected.head_pc,
                       runtime->trace_entry_address(selected),
                       block->byte_span,
                       header->result_words,
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
  execution_count = 15,
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
  case fireball::cache_field::execution_count:
    return runtime->measurements.execution_count;
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
  std::uint16_t token;
  std::uint8_t bank_id;
};
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_find(qa_jit_runtime *runtime, std::uint32_t pc,
                                                       qa_cache_lookup *output) {
  const auto location = runtime->find_cached_trace_by_pc(pc);
  if (location.trace_entry == nullptr) {
    return 0;
  }
  *output = {location.trace_entry->cache_token, static_cast<std::uint8_t>(location.bank_id)};
  return 1;
}
extern "C" FB_PYSIM_ABI_EXPORT const fireball::jit_trace_header *
fb_qa_runtime_header(qa_jit_runtime *runtime, std::uint32_t pc) {
  const auto location = runtime->find_cached_trace_by_pc(pc);
  return location.trace_entry == nullptr ? nullptr : runtime->trace_header(*location.trace_entry);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_has_token(const qa_jit_runtime *runtime,
                                                            std::uint16_t token) {
  for (const auto &bank : runtime->cache.banks) {
    for (std::uint32_t i = 0; i < bank.entry_count; ++i) {
      if (bank.entries[i].resident() && bank.entries[i].cache_token == token) {
        return 1;
      }
    }
  }
  return 0;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_insert(qa_jit_runtime *runtime,
                                                         const std::uint8_t *blob,
                                                         std::uint32_t bytes, std::uint16_t token) {
  if (blob == nullptr || bytes == 0) {
    return 0;
  }
  return runtime->insert_trace_fixture(blob, bytes, token);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_lookup(qa_jit_runtime *runtime, std::uint32_t pc,
                                                         qa_cache_lookup *output) {
  const auto location = runtime->lookup_cached_trace(pc);
  if (location.trace_entry == nullptr) {
    return 0;
  }
  *output = {location.trace_entry->cache_token, static_cast<std::uint8_t>(location.bank_id)};
  return 1;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_rotate(qa_jit_runtime *runtime) {
  runtime->rotate_cache_banks();
  return 1;
}
extern "C" FB_PYSIM_ABI_EXPORT void fb_jit_runtime_auto_age(qa_jit_runtime *runtime, int enabled) {
  runtime->measurements.auto_age = enabled != 0;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_suppress(qa_jit_runtime *runtime,
                                                           std::uint32_t pc) {
  runtime->exclude_compile_candidate(pc);
  return 1;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_record(qa_jit_runtime *runtime,
                                                         std::uint32_t pc) {
  if (runtime->hotspots.configuration.enabled == 0) {
    return 1;
  }
  if (!runtime->is_profiled_pc(pc)) {
    return 1;
  }
  runtime->record_block_visit(pc);
  return 1;
}
extern "C" FB_PYSIM_ABI_EXPORT int
fb_jit_runtime_filtered_lookup(qa_jit_runtime *runtime, std::uint32_t pc, qa_cache_lookup *output) {
  if (runtime->hotspots.configuration.enabled != 0 &&
      (!runtime->is_profiled_pc(pc) ||
       runtime->card_state(pc >> runtime->hotspots.card_shift) != 3)) {
    return 0;
  }
  const auto location = runtime->lookup_cached_trace(pc);
  if (location.trace_entry == nullptr) {
    return 0;
  }
  *output = {location.trace_entry->cache_token, static_cast<std::uint8_t>(location.bank_id)};
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
  if (runtime->hotspots.configuration.enabled == 0) {
    assert(total == 0);
    return 1;
  }
  const auto retained = total < capacity ? total : capacity;
  assert((total == 0 || (capacity != 0 && pcs != nullptr)) &&
         retained == (total < runtime->hotspots.observation_history.capacity
                          ? total
                          : runtime->hotspots.observation_history.capacity) &&
         total <= UINT64_MAX - runtime->measurements.execution_count);
  // Validate and record each visit once; contract violations stop at the
  // source.
  for (std::uint64_t i = total - retained; i < total; ++i) {
    assert(runtime->is_profiled_pc(pcs[i % capacity]));
    runtime->record_block_visit(pcs[i % capacity]);
  }
  const auto omitted = total - retained;
  assert(omitted <= UINT64_MAX - runtime->measurements.history_dropped);
  runtime->measurements.history_dropped += omitted;
  runtime->measurements.execution_count += total - retained;
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

static int qa_lookup_trace(std::uintptr_t owner, [[maybe_unused]] block_index_t block_index,
                           wasm_pc_t pc, fb_native_jit_trace *output) {
  if (owner == 0 || output == nullptr) {
    return -1;
  }
  const auto &b = reinterpret_cast<qa_dispatch_owner *>(owner)->buffers;
  const auto *start = qa_find(b, pc);
  if (start == nullptr) {
    return 0;
  }
  if (start->entry_address == 0 || start->entry_address < FB_CONF_JIT_X64_TRACE_HEADER_BYTES) {
    return -1;
  }
  const auto *header = reinterpret_cast<const fireball::jit_trace_header *>(
      start->entry_address - FB_CONF_JIT_X64_TRACE_HEADER_BYTES);
  if (header->blob_bytes < FB_CONF_JIT_X64_TRACE_HEADER_BYTES ||
      header->const_rel_offset > header->blob_bytes || start->chain_body_count == 0) {
    return -1;
  }
  const auto *rel = reinterpret_cast<const std::uint8_t *>(
      reinterpret_cast<const std::uint8_t *>(header) + header->const_rel_offset);
  *output = {start->entry_address,    rel,
             header->frame_depth,     static_cast<jit_stack_words_t>(start->chain_stack_words),
             static_cast<jit_body_count_t>(start->chain_body_count), 0,
             reinterpret_cast<std::uintptr_t>(start)};
  return 1;
}

static int qa_observe_block(std::uintptr_t owner, block_index_t, wasm_pc_t) {
  return owner == 0 ? -1 : 1;
}

static int qa_finish_boundary(std::uintptr_t owner, std::uint32_t) {
  return owner == 0 ? 0 : 1;
}

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
  auto *current = reinterpret_cast<const fb_native_trace_descriptor *>(trace_cookie);
  if (owner == nullptr || current == nullptr) {
    return;
  }
  const auto &b = owner->buffers;
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
      owner_address, all_compiled_cards, 8, 29, {}, qa_lookup_trace,
      qa_observe_block, qa_finish_boundary};
  call.jit_provider = &provider;
  fb_native_execution_extension extension{owner_address, qa_observe, qa_trace_executed};
  fb_native_dispatch_extension_call extension_call{call, &extension};
  return dispatch(&extension_call, result);
}

extern "C" FB_PYSIM_ABI_EXPORT const fb_native_jit_provider *
fb_qa_runtime_provider(qa_jit_runtime *runtime) {
  return &runtime->provider;
}

extern "C" FB_PYSIM_ABI_EXPORT void fb_jit_runtime_reset_counts(qa_jit_runtime *runtime) {
  runtime->measurements.reset_trace_counts(*runtime);
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

extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_block_counters_enabled() {
  return FB_CONF_JIT_BLOCK_COUNTERS;
}

extern "C" FB_PYSIM_ABI_EXPORT qa_jit_runtime *
fb_jit_runtime_init(std::uint8_t *storage, std::size_t bytes, std::uint32_t bank_bytes,
                    std::uint32_t entry_capacity, const std::uint32_t *offsets) {
  if (storage == nullptr || bytes < sizeof(qa_jit_runtime) || offsets == nullptr ||
      reinterpret_cast<std::uintptr_t>(storage) % alignof(qa_jit_runtime) != 0 ||
      bank_bytes < fireball::jit_trace_header_bytes || entry_capacity == 0 ||
      entry_capacity > fireball::jit_cache_bank_entry_capacity) {
    return nullptr;
  }
  auto *cache = ::new (storage) qa_jit_runtime{};
  cache->provider = {reinterpret_cast<std::uintptr_t>(cache),
                     nullptr,
                     0,
                     0,
                     {},
                     provider_adapter::lookup_runtime_trace,
                     provider_adapter::observe_runtime_block,
                     provider_adapter::finish_runtime_boundary};
  if (!fb_jit_memory_init(&cache->cache.code_memory, FB_CONF_JIT_CACHE_REGION_BYTES) ||
      !fireball::initialize_common_code(&cache->cache.code_memory) ||
      !fb_jit_memory_commit(&cache->cache.code_memory)) {
    cache->~qa_jit_runtime();
    return nullptr;
  }
  for (std::uint32_t i = 0; i < fireball::jit_cache_bank_count; ++i) {
    cache->cache.banks[i].capacity_bytes = bank_bytes;
    cache->cache.banks[i].entry_capacity =
        entry_capacity < bank_bytes / fireball::jit_trace_header_bytes
            ? entry_capacity
            : bank_bytes / fireball::jit_trace_header_bytes;
    cache->cache.banks[i].region_offset_bytes = offsets[i];
  }
  return cache;
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
  std::uint16_t token;
  std::uint8_t bank_id;
  std::uint8_t reserved;
  std::uint32_t pc;
  std::uint32_t count;
};
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
        fireball::jit_wasm_block storage{};
        const auto *trace = runtime->trace_header(resident_entry);
        const auto *block = runtime->block_metadata_for_trace(*trace, storage);
        const auto *executions =
            block == nullptr ? nullptr : static_cast<const std::uint32_t *>(block->extension_data);
        *output = {resident_entry.cache_token, bank_id, 0, resident_entry.head_pc,
                   executions == nullptr ? 0 : *executions};
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
