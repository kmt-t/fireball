// QA owns inspection and explicit cache manipulation; none of these symbols enters the product library.
#include "../../native/tier3_plugins/jit/jit_runtime.cxx"
namespace fireball {
enum class cache_field : std::uint32_t {
  promotions = 3,
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
};
}
extern "C" FB_PYSIM_ABI_EXPORT std::uint64_t fb_jit_runtime_scalar(const JitRuntime* c, std::uint32_t field) {
  switch (static_cast<fireball::cache_field>(field)) {
    case fireball::cache_field::promotions:
      return c->promotions;
    case fireball::cache_field::evictions:
      return c->evictions;
    case fireball::cache_field::generation:
      return c->generation;
    case fireball::cache_field::aging_steps:
      return c->aging_steps;
    case fireball::cache_field::aging_units:
      return c->aging_units;
    case fireball::cache_field::aging_scanned:
      return c->aging_scanned;
    case fireball::cache_field::bank_used: {
      std::uint64_t bytes = 0;
      for (const auto& bank : c->banks) bytes += bank.used;
      return bytes;
    }
    case fireball::cache_field::bank_entry_capacity: {
      std::uint64_t capacity = 0;
      for (const auto& bank : c->banks) capacity += bank.entry_capacity;
      return capacity;
    }
    case fireball::cache_field::execution_count:
      return c->execution_count;
    case fireball::cache_field::history_overwritten:
      return c->history_overwritten;
    case fireball::cache_field::history_approximate:
      return c->history_approximate;
    case fireball::cache_field::trackable_generation:
      return c->trackable_generation;
    case fireball::cache_field::queue_count:
      return c->queue.count;
    case fireball::cache_field::resident_count: {
      std::uint64_t count = 0;
      for (const auto& bank : c->banks)
        for (std::uint32_t i = 0; i < bank.count; ++i)
          if (bank.entries[i].trace != nullptr) ++count;
      return count;
    }
    case fireball::cache_field::rotations:
      return c->rotations;
    case fireball::cache_field::aging_ns:
      return c->aging_ns;
    case fireball::cache_field::compile_attempts:
      return c->compile_attempts;
    case fireball::cache_field::compile_ns:
      return c->compile_ns;
    default:
      return 0;
  }
}
extern "C" FB_PYSIM_ABI_EXPORT std::uint64_t fb_jit_runtime_find(JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  auto* value = c->find(pc);
  return value == nullptr ? 0 : value->token;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_has_token(const JitRuntime* c, std::uint64_t token) {
  for (const auto& bank : c->banks) {
    for (std::uint32_t i = 0; i < bank.count; ++i) {
      if (bank.entries[i].trace != nullptr && bank.entries[i].token == token) return 1;
    }
  }
  return 0;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_insert(JitRuntime* c, jit_cache_trace* trace, std::uint64_t token) {
  c->error = 0;
  return c->insert(trace, token);
}
extern "C" FB_PYSIM_ABI_EXPORT std::uint64_t fb_jit_runtime_lookup(JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  return c->lookup(pc);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_rotate(JitRuntime* c) {
  c->error = 0;
  return c->rotate();
}
extern "C" FB_PYSIM_ABI_EXPORT void fb_jit_runtime_auto_age(JitRuntime* c, int enabled) { c->auto_age = enabled != 0; }
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_suppress(JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  return c->suppress(pc);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_record(JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  return c->record(pc);
}
extern "C" FB_PYSIM_ABI_EXPORT std::uint64_t fb_jit_runtime_filtered_lookup(JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  if (c->profile.enabled != 0 && (!c->trackable(pc) || c->card_state(pc >> c->card_shift) != 3))
    return 0;
  return c->lookup(pc);
}
extern "C" FB_PYSIM_ABI_EXPORT fireball::jit_cache_trace* fb_jit_runtime_trace(JitRuntime* c, std::uint64_t token) {
  return c->descriptor(token);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_snapshot(fireball::JitRuntime* c,
    fb_native_trace_descriptor* output, std::uint32_t capacity) {
  return build_dispatch_table(c, output, capacity);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_visits(fireball::JitRuntime* c,
    const std::uint32_t* pcs, std::uint32_t capacity, std::uint64_t total) {
  return record_dispatch_visits(c, pcs, capacity, total);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_analyze(fireball::JitRuntime* c, int yielded) {
  c->error = 0;
  return c->analyze(yielded != 0);
}
struct qa_dispatch_buffers {
  const fb_native_trace_descriptor* entries;
  std::uint32_t count;
  const std::uint8_t* mask;
  std::uint32_t cards, shift, mask_bytes;
  std::uint32_t* history;
  std::uint64_t history_bytes;
};
static fb_native_trace_view qa_resolve_traces(std::uintptr_t owner, std::uint32_t) {
  const auto& buffers = *reinterpret_cast<const qa_dispatch_buffers*>(owner);
  return {buffers.entries, buffers.count};
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_dispatch(
    int (*dispatch)(const fb_native_dispatch_call*, fb_native_result*),
    const qa_dispatch_buffers* buffers, const fb_native_dispatch_call* input, fb_native_result* result) {
  if (buffers->count != 0 && (buffers->entries == nullptr ||
      reinterpret_cast<std::uintptr_t>(buffers->entries) % alignof(fb_native_trace_descriptor) != 0))
    return 0;
  for (std::uint32_t i = 0; i < buffers->count; ++i) {
    const auto& entry = buffers->entries[i];
    if ((i != 0 && buffers->entries[i - 1].head_pc >= entry.head_pc) ||
        entry.byte_span == 0 || entry.result_words == 0 || entry.stack_words == 0 ||
        entry.frame_depth > FIREBALL_NATIVE_CONTROL_STACK_CAPACITY ||
        entry.has_return_value > 1 || entry.entry_address == 0 || entry.exec_count == nullptr ||
        reinterpret_cast<std::uintptr_t>(entry.exec_count) % alignof(std::uint32_t) != 0)
      return 0;
  }
  auto call = *input;
  fb_native_trace_source source{reinterpret_cast<std::uintptr_t>(buffers), qa_resolve_traces};
  call.trace_source = &source;
  call.trackable_mask = buffers->mask;
  call.trackable_card_count = buffers->cards;
  call.trackable_shift = buffers->shift;
  call.trackable_bytes = buffers->mask_bytes;
  call.block_history = buffers->history;
  call.block_history_bytes = buffers->history_bytes;
  return dispatch(&call, result);
}

extern "C" FB_PYSIM_ABI_EXPORT std::size_t fb_jit_runtime_size() { return sizeof(JitRuntime); }

extern "C" FB_PYSIM_ABI_EXPORT std::size_t fb_jit_runtime_alignment() { return alignof(JitRuntime); }

extern "C" FB_PYSIM_ABI_EXPORT JitRuntime* fb_jit_runtime_init(std::uint8_t* storage, std::size_t bytes,
                                           std::uint32_t bank_bytes, std::uint32_t entry_capacity,
                                           const std::uint32_t* offsets) {
  if (storage == nullptr || bytes < sizeof(JitRuntime) || offsets == nullptr ||
      reinterpret_cast<std::uintptr_t>(storage) % alignof(JitRuntime) != 0 ||
      bank_bytes < fireball::header_bytes || entry_capacity == 0 ||
      entry_capacity > fireball::max_entries)
    return nullptr;
  auto* cache = ::new (storage) JitRuntime{};
  if (!fb_jit_memory_init(&cache->memory, FB_CONF_JIT_CACHE_REGION_BYTES) ||
      !fireball::initialize_common_code(&cache->memory)) {
    cache->~JitRuntime();
    return nullptr;
  }
  for (std::uint32_t i = 0; i < fireball::bank_count; ++i) {
    cache->banks[i].capacity = bank_bytes;
    cache->banks[i].entry_capacity = entry_capacity < bank_bytes / fireball::header_bytes
                                         ? entry_capacity
                                         : bank_bytes / fireball::header_bytes;
    cache->banks[i].offset = offsets[i];
  }
  return cache;
}

extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_region_size(const std::uint32_t* lengths, const std::uint32_t* bases,
                                  std::uint32_t count, std::uint64_t* bytes) {
  if (bytes == nullptr || (count != 0 && lengths == nullptr)) return 0;
  std::uint64_t previous = 0, end = 0, cursor = 0;
  for (std::uint32_t i = 0; i < count; ++i) {
    const auto base = bases == nullptr ? cursor : bases[i];
    const auto next = base + lengths[i];
    if (base > UINT32_MAX || next > (std::uint64_t{1} << 32) || previous > base) return 0;
    if (lengths[i] != 0) end = next;
    previous = next;
    cursor = next + 1;
  }
  *bytes = end;
  return 1;
}

extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_bits(std::uint8_t* data, std::uint32_t count, std::uint32_t bits,
                           std::uint32_t index, std::uint32_t op, std::uint32_t value) {
  if ((bits != 1 && bits != 2) || (count != 0 && data == nullptr)) return -1;
  const auto bytes = (static_cast<std::uint64_t>(count) * bits + 7) / 8;
  if (op == 4) {
    for (std::uint64_t i = 0; i < bytes; ++i) data[i] = 0;
    return 0;
  }
  if (op == 5) return index < bytes ? data[index] : -1;
  if (op == 6) return (bytes == 0 ? value == 0 : value < bytes) ? static_cast<int>(value) : -1;
  if (index >= count) return -1;
  const auto per_byte = 8 / bits, shift = (index % per_byte) * bits;
  const auto mask = (1u << bits) - 1;
  const auto state = (data[index / per_byte] >> shift) & mask;
  if (op == 0) return static_cast<int>(state);
  std::uint32_t next = value;
  int result = 0;
  if (op == 1) {
    if (value > mask) return -1;
    result = static_cast<int>(value);
  } else if (op == 2 && bits == 2) {
    next = state < 2 ? state + 1 : state;
    result = next;
  } else if (op == 3 && bits == 2) {
    next = state == 1 ? 0 : state;
    result = state == 1 ? 1 : 0;
  } else
    return -1;
  data[index / per_byte] =
      static_cast<std::uint8_t>((data[index / per_byte] & ~(mask << shift)) | (next << shift));
  return result;
}

extern "C" FB_PYSIM_ABI_EXPORT std::int64_t fb_qa_block_score(const fireball::jit_wasm_block* block) {
  return fireball::score_block(*block);
}

extern "C" FB_PYSIM_ABI_EXPORT std::uint64_t fb_qa_owned_pc(const JitRuntime* c, std::uint32_t slot) {
  if (slot >= c->owned.size() || !c->owned[slot].used) return UINT64_MAX;
  return c->owned[slot].trace.head_pc;
}
struct qa_resident_record {
  std::uint64_t token;
  std::uint32_t pc;
  std::uint32_t count;
};
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_resident_record(const JitRuntime* c, std::uint32_t index,
    qa_resident_record* output) {
  for (const auto& bank : c->banks) {
    for (std::uint32_t i = 0; i < bank.count; ++i) {
      const auto& entry = bank.entries[i];
      if (entry.trace == nullptr) continue;
      if (index-- == 0) {
        *output = {entry.token, entry.pc,
                   entry.trace->exec_count == nullptr ? 0 : *entry.trace->exec_count};
        return 1;
      }
    }
  }
  return 0;
}

extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_age(JitRuntime* c) {
  c->error = 0;
  return c->age();
}
