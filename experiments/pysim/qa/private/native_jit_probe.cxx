// QA owns inspection and explicit cache manipulation.
#include "jit_measurements.hxx"
#include "../../native/tier3_plugins/jit/jit_runtime.hxx"

namespace fireball {
using configured_jit_plugin = native_jit_plugin<jit_measurements>;
}
#include "../../native/tier3_plugins/jit/jit_runtime_exports.hxx"

using JitRuntime = Api::Runtime;
using fireball::jit_cache_trace;
static_assert(sizeof(fireball::native_jit_plugin<void>::Runtime) < sizeof(JitRuntime));
static_assert(sizeof(jit_cache_trace) < sizeof(fireball::jit_measurements::trace_type));
extern "C" int fb_jit_runtime_bind_cards(fireball::JitRuntime* c, std::uint8_t* states,
    std::uint32_t state_bytes, std::uint8_t* dirty,
    std::uint32_t dirty_bytes, std::uint32_t cards,
    std::uint32_t shift, std::uint32_t* cursor,
    std::uint32_t units, std::uint32_t scan_bytes) {
  return Api::fb_jit_runtime_bind_cards(reinterpret_cast<Api::Runtime*>(c), states, state_bytes,
      dirty, dirty_bytes, cards, shift, cursor, units, scan_bytes);
}

extern "C" int fb_jit_runtime_bind_profile(fireball::JitRuntime* c, fireball::jit_profile p) {
  return Api::fb_jit_runtime_bind_profile(reinterpret_cast<Api::Runtime*>(c), p);
}

// Snapshot records are QA fixtures, not an interpreter/plugin contract.
struct fb_native_trace_descriptor {
  std::uint32_t head_pc;
  std::uintptr_t entry_address;
  std::uint32_t byte_span, result_words, has_return_value, stack_words, frame_depth;
  std::uint32_t next_pc, loops_to, chain_next_pc, chain_stack_words;
  std::uint32_t* exec_count;
};
static int build_dispatch_table(JitRuntime* c, fb_native_trace_descriptor* output,
                                       std::uint32_t capacity) {
  c->error = 0;
  std::array<std::uint32_t, fireball::bank_count> index{};
  std::uint32_t count = 0;
  while (true) {
    fireball::entry selected{};
    std::uint32_t selected_bank = fireball::bank_count;
    for (std::uint32_t id = 0; id < fireball::bank_count; ++id) {
      const auto& b = c->banks[id];
      while (index[id] < b.count && b.entries[index[id]].trace == nullptr) ++index[id];
      if (index[id] < b.count &&
          (selected.trace == nullptr || b.entries[index[id]].pc < selected.pc)) {
        selected = b.entries[index[id]];
        selected_bank = id;
      }
    }
    if (selected.trace == nullptr) break;
    if (!c->check(output != nullptr && count < capacity && selected.trace->entry_address != 0 &&
                  selected.trace->byte_span > 0 &&
                  fireball::jit_measurements::counter(selected.trace) != nullptr))
      return -1;
    const auto& t = *selected.trace;
    c->prepare_chains();
    const auto words = selected.trace->chain_words;
    if (c->error != 0) return -1;
    output[count++] = {
        t.head_pc,           t.entry_address, t.byte_span,   t.result_words,
        t.has_return_value,  t.stack_words,   t.frame_depth, t.dispatch_next_pc,
        t.dispatch_loops_to, t.chain_next_pc, words,
        fireball::jit_measurements::counter(&t)};
    ++index[selected_bank];
  }
  return static_cast<int>(count);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_error(const JitRuntime* c) { return c->error; }
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
extern "C" FB_PYSIM_ABI_EXPORT std::uint64_t fb_jit_runtime_scalar(
    const JitRuntime* c, std::uint32_t field) {
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
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_has_token(
    const JitRuntime* c, std::uint64_t token) {
  for (const auto& bank : c->banks) {
    for (std::uint32_t i = 0; i < bank.count; ++i) {
      if (bank.entries[i].trace != nullptr && bank.entries[i].token == token) return 1;
    }
  }
  return 0;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_insert(
    JitRuntime* c, jit_cache_trace* trace, std::uint64_t token) {
  c->error = 0;
  return c->insert(trace, token);
}
extern "C" FB_PYSIM_ABI_EXPORT std::uint64_t fb_jit_runtime_lookup(
    JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  return c->lookup(pc);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_rotate(JitRuntime* c) {
  c->error = 0;
  return c->rotate();
}
extern "C" FB_PYSIM_ABI_EXPORT void fb_jit_runtime_auto_age(JitRuntime* c, int enabled) {
  c->auto_age = enabled != 0;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_suppress(JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  return c->suppress(pc);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_runtime_record(JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  if (c->profile.enabled == 0) return 1;
  if (!c->trackable(pc)) return c->error == 0;
  return c->record_observed(pc);
}
extern "C" FB_PYSIM_ABI_EXPORT std::uint64_t fb_jit_runtime_filtered_lookup(
    JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  if (c->profile.enabled != 0 && (!c->trackable(pc) || c->card_state(pc >> c->card_shift) != 3))
    return 0;
  return c->lookup(pc);
}
extern "C" FB_PYSIM_ABI_EXPORT fireball::jit_cache_trace* fb_jit_runtime_trace(
    JitRuntime* c, std::uint64_t token) {
  return c->descriptor(token);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_snapshot(JitRuntime* c,
    fb_native_trace_descriptor* output, std::uint32_t capacity) {
  return build_dispatch_table(c, output, capacity);
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_visits(JitRuntime* c,
    const std::uint32_t* pcs, std::uint32_t capacity, std::uint64_t total) {
  c->error = 0;
  if (c->profile.enabled == 0) return c->check(total == 0);
  const auto retained = total < capacity ? total : capacity;
  if (!c->check((total == 0 || (capacity != 0 && pcs != nullptr)) &&
                retained == (total < c->history.capacity ? total : c->history.capacity) &&
                total <= UINT64_MAX - c->execution_count))
    return 0;
  // QA snapshots validate every supplied PC before publishing any of them.
  for (std::uint64_t i = total - retained; i < total; ++i)
    if (!c->check(c->trackable(pcs[i % capacity]))) return 0;
  for (std::uint64_t i = total - retained; i < total; ++i)
    if (!c->record_observed(pcs[i % capacity])) return 0;
  const auto omitted = total - retained;
  if (!c->check(omitted <= UINT64_MAX - c->history_dropped)) return 0;
  c->history_dropped += omitted;
  c->execution_count += total - retained;
  return 1;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_analyze(JitRuntime* c, int yielded) {
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
struct qa_dispatch_owner {
  const qa_dispatch_buffers& buffers;
  std::uint32_t visits = 0;
};
static const fb_native_trace_descriptor* qa_find(const qa_dispatch_buffers& b, std::uint32_t pc) {
  for (std::uint32_t i = 0; i < b.count; ++i) if (b.entries[i].head_pc == pc) return b.entries + i;
  return nullptr;
}
static std::uint32_t qa_execute(std::uintptr_t owner, void* raw,
    std::uint32_t* stack, std::uint32_t* locals, std::uint32_t pc) {
  const auto& b = reinterpret_cast<qa_dispatch_owner*>(owner)->buffers;
  const auto* start = qa_find(b, pc);
  if (start == nullptr) return 0;
  auto& ctx = *static_cast<fireball_execution_context_native*>(raw);
  if (ctx.sp_offset + start->chain_stack_words > ctx.sp_capacity) return 0;
  auto* terminal = start;
  std::uint32_t count = 1;
  while (terminal->chain_next_pc != fireball::no_pc) {
    terminal = qa_find(b, terminal->chain_next_pc);
    if (terminal == nullptr || ++count > b.count) return 0;
  }
  auto& frame = ctx.call_stack->frames[ctx.call_stack->size - 1];
  if (ctx.sp_offset + terminal->stack_words > ctx.sp_capacity ||
      terminal->frame_depth > FIREBALL_NATIVE_CONTROL_STACK_CAPACITY - ctx.control_base) return 0;
  const auto depth = ctx.control_base + terminal->frame_depth;
  if (ctx.control_stack->size > depth) ctx.control_stack->size = depth;
  frame.boundary_next_pc = terminal->next_pc;
  frame.boundary_loops_to = terminal->loops_to;
  using entry_fn = void (*)(void*, std::uint32_t*, std::uint32_t*, std::uint32_t);
  reinterpret_cast<entry_fn>(start->entry_address)(
      &ctx, stack + ctx.sp_offset, locals + frame.local_base, 0);
  for (auto* current = start; current != nullptr;) {
    if (*current->exec_count != fireball::no_pc) ++*current->exec_count;
    current = current->chain_next_pc == fireball::no_pc
        ? nullptr : qa_find(b, current->chain_next_pc);
  }
  if (terminal->has_return_value != 0) ctx.sp_offset += terminal->result_words;
  ctx.ip = terminal->head_pc - frame.function_view->code_pc_offset + terminal->byte_span;
  return count;
}
static bool qa_observe(std::uintptr_t owner, std::uint32_t pc) {
  const auto& b = reinterpret_cast<qa_dispatch_owner*>(owner)->buffers;
  const auto card = pc >> b.shift;
  return card < b.cards && ((b.mask[card >> 3] >> (card & 7)) & 1u) != 0;
}
static void qa_record(std::uintptr_t owner, std::uint32_t pc) {
  auto& o = *reinterpret_cast<qa_dispatch_owner*>(owner);
  o.buffers.history[o.visits++ % (o.buffers.history_bytes / sizeof(std::uint32_t))] = pc;
}
extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_dispatch(
    int (*dispatch)(const fb_native_dispatch_call*, fb_native_result*),
    const qa_dispatch_buffers* buffers, const fb_native_dispatch_call* input,
    fb_native_result* result) {
  if (buffers->shift >= 32 || buffers->mask_bytes < (buffers->cards + 7u) / 8u ||
      buffers->history_bytes < sizeof(std::uint32_t) || buffers->history == nullptr ||
      buffers->history_bytes % sizeof(std::uint32_t) != 0 ||
      (buffers->cards != 0 && buffers->mask == nullptr)) return 0;
  auto call = *input;
  qa_dispatch_owner owner{*buffers};
  fb_native_execution_extension extension{
      reinterpret_cast<std::uintptr_t>(&owner), qa_execute, qa_observe, qa_record};
  call.extension = &extension;
  return dispatch(&call, result);
}
extern "C" FB_PYSIM_ABI_EXPORT void fb_qa_runtime_bind_dispatch(JitRuntime* runtime,
    int (*dispatch)(const fb_native_dispatch_call*, fb_native_result*)) {
  runtime->dispatch = dispatch;
  runtime->extension = {reinterpret_cast<std::uintptr_t>(runtime), Api::execute_runtime_body,
                       Api::observe_runtime_body, Api::record_runtime_body};
}

extern "C" FB_PYSIM_ABI_EXPORT void fb_jit_runtime_reset_counts(JitRuntime* c) {
  c->reset_trace_counts(*c);
}

extern "C" FB_PYSIM_ABI_EXPORT int fb_qa_runtime_print_measurements(
    const JitRuntime* runtime, const fireball::printk_writer* writer) {
  return runtime->write_measurements(*writer);
}

extern "C" FB_PYSIM_ABI_EXPORT std::size_t fb_jit_runtime_size() { return sizeof(JitRuntime); }

extern "C" FB_PYSIM_ABI_EXPORT std::size_t fb_jit_runtime_alignment() {
  return alignof(JitRuntime);
}

extern "C" FB_PYSIM_ABI_EXPORT JitRuntime* fb_jit_runtime_init(
    std::uint8_t* storage, std::size_t bytes,
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

extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_region_size(
    const std::uint32_t* lengths, const std::uint32_t* bases,
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

extern "C" FB_PYSIM_ABI_EXPORT int fb_jit_bits(
    std::uint8_t* data, std::uint32_t count, std::uint32_t bits,
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

extern "C" FB_PYSIM_ABI_EXPORT std::int64_t fb_qa_block_score(
    const fireball::jit_wasm_block* block) {
  return fireball::score_block(*block);
}

extern "C" FB_PYSIM_ABI_EXPORT std::uint64_t fb_qa_owned_pc(
    const JitRuntime* c, std::uint32_t slot) {
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
                   fireball::jit_measurements::counter(entry.trace) == nullptr
                       ? 0 : *fireball::jit_measurements::counter(entry.trace)};
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
