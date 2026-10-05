#include <array>
#include <chrono>
#include <limits>
#include <new>
#include <type_traits>

#include "common_code_abi.hxx"
#include "jit_internal.hxx"
#include "jit_runtime_abi.hxx"
#include "profiling.hxx"

#ifndef FB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY
#error "FB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY is required"
#endif
#ifndef FB_CONF_JIT_CACHE_MAX_INBOUND_SOURCES
#error "FB_CONF_JIT_CACHE_MAX_INBOUND_SOURCES is required"
#endif
#ifndef FB_CONF_JIT_CACHE_FAST_SLOT_COUNT
#error "FB_CONF_JIT_CACHE_FAST_SLOT_COUNT is required"
#endif

namespace fireball {
namespace {
constexpr std::uint32_t header_bytes = FB_CONF_JIT_X64_TRACE_HEADER_BYTES;
constexpr std::uint32_t entry_stub_bytes = FB_CONF_JIT_X64_TRACE_ENTRY_STUB_BYTES;
constexpr auto no_pc = std::numeric_limits<std::uint32_t>::max();
constexpr std::uint32_t bank_count = 3;
constexpr std::uint32_t max_entries = FB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY;
constexpr std::uint32_t max_inbound = FB_CONF_JIT_CACHE_MAX_INBOUND_SOURCES;
constexpr std::uint32_t fast_slots = FB_CONF_JIT_CACHE_FAST_SLOT_COUNT;
static_assert(fast_slots > 0 && (fast_slots & (fast_slots - 1)) == 0);

struct entry {
  std::uint32_t pc = 0;
  jit_cache_trace* trace = nullptr;
  std::uint64_t token = 0;
};
struct bank {
  std::array<entry, max_entries> entries{};
  std::array<std::uint32_t, max_inbound> inbound{};
  std::uint32_t count = 0;
  std::uint32_t inbound_count = 0;
  std::uint32_t used = 0;
  std::uint32_t write_cursor = 0;
  std::uint32_t capacity = 0;
  std::uint32_t entry_capacity = 0;
  std::uint32_t offset = 0;

  std::uint32_t lower_bound(std::uint32_t pc) const {
    std::uint32_t first = 0, last = count;
    while (first < last) {
      const auto middle = first + (last - first) / 2;
      if (entries[middle].pc < pc)
        first = middle + 1;
      else
        last = middle;
    }
    return first;
  }
  entry* find(std::uint32_t pc) {
    const auto index = lower_bound(pc);
    return index < count && entries[index].pc == pc && entries[index].trace != nullptr
               ? &entries[index]
               : nullptr;
  }
  bool allocate(jit_cache_trace* trace, std::uint64_t token) {
    const auto index = lower_bound(trace->head_pc);
    const auto existing = index < count && entries[index].pc == trace->head_pc;
    const auto previous = existing ? entries[index].trace : nullptr;
    const auto old_size = previous == nullptr ? 0 : previous->size_bytes;
    const auto reuse = previous != nullptr && trace->size_bytes <= old_size;
    const auto added = reuse ? 0 : trace->size_bytes;
    // A removed or promoted entry leaves a hole. Live-byte accounting cannot
    // locate the next safe write: retain a monotonic bank write cursor.
    if (added > capacity - write_cursor || (!existing && count == entry_capacity)) return false;
    trace->code_offset = reuse ? previous->code_offset : offset + write_cursor;
    write_cursor += added;
    if (!existing) {
      for (auto i = count; i > index; --i) entries[i] = entries[i - 1];
      ++count;
    }
    entries[index] = {trace->head_pc, trace, token};
    used = used + trace->size_bytes - old_size;
    return true;
  }
  void remove_inbound(std::uint32_t pc) {
    for (std::uint32_t i = 0; i < inbound_count; ++i) {
      if (inbound[i] == pc) {
        for (auto j = i + 1; j < inbound_count; ++j) inbound[j - 1] = inbound[j];
        --inbound_count;
        return;
      }
    }
  }
};
}  // namespace

class JitRuntime final {
 public:
  std::array<bank, bank_count> banks{};
  std::array<entry, fast_slots> fast{};
  std::uint32_t active = 0, warm = 1, oldest = 2;
  std::uint64_t promotions = 0, evictions = 0, generation = 0, rotations = 0;
  jit_cache_host host{};
  int error = 0;
  std::uint8_t* states = nullptr;
  std::uint8_t* dirty = nullptr;
  std::uint32_t cards = 0, card_shift = 0, dirty_bytes = 0;
  std::uint32_t* cursor = nullptr;
  std::uint32_t age_units = 1, age_scan = 1;
  std::uint64_t aging_steps = 0, aging_units = 0, aging_scanned = 0, aging_ns = 0;
  std::uint64_t compile_attempts = 0, compile_ns = 0;
  bool auto_age = true;
  executable_memory memory{};
  std::array<jit_history_record, FB_CONF_JIT_HISTORY_CAPACITY> history_records{};
  std::array<std::uint32_t, FB_CONF_JIT_COMPILE_QUEUE_CAPACITY> queue_pcs{};
  jit_history history{};
  jit_compile_queue queue{};
  struct owned_trace {
    jit_cache_trace trace{};
    std::uint32_t executions = 0;
    bool used = false;
  };
  std::array<owned_trace, max_entries * bank_count + 1> owned{};
  static constexpr std::uint64_t owned_bit = std::uint64_t{1} << 63;
  ~JitRuntime() { fb_jit_memory_close(&memory); }
  const jit_wasm_block* block(std::uint32_t pc) const {
    std::uint32_t first = 0, last = profile.block_count;
    while (first < last) {
      const auto mid = first + (last - first) / 2;
      if (profile.blocks[mid].head_pc < pc)
        first = mid + 1;
      else
        last = mid;
    }
    return first < profile.block_count && profile.blocks[first].head_pc == pc
               ? profile.blocks + first
               : nullptr;
  }
  std::uint64_t reserve_owned() {
    for (std::uint32_t i = 0; i < owned.size(); ++i) {
      if (!owned[i].used) {
        owned[i] = {};
        owned[i].used = true;
        owned[i].trace.exec_count = &owned[i].executions;
        owned[i].trace.code_offset = owned[i].trace.chain_next_pc = no_pc;
        return owned_bit | (i + 1);
      }
    }
    check(false);
    return 0;
  }
  jit_cache_trace* descriptor(std::uint64_t token) {
    if ((token & owned_bit) != 0) {
      const auto index = (token & ~owned_bit) - 1;
      return index < owned.size() && owned[index].used ? &owned[index].trace : nullptr;
    }
    for (auto& bank : banks)
      for (std::uint32_t i = 0; i < bank.count; ++i)
        if (bank.entries[i].token == token && bank.entries[i].trace != nullptr)
          return bank.entries[i].trace;
    return nullptr;
  }
  jit_profile profile{};
  std::uint64_t execution_count = 0, history_overwritten = 0, last_overwritten = 0;
  std::uint64_t trackable_generation = 0;
  bool history_approximate = false;

  bool trackable(std::uint32_t pc) {
    const auto card = fb_jit_card_index(profile.code_bytes, card_shift, pc);
    if (!check(card >= 0 && static_cast<std::uint64_t>(card) < cards)) return false;
    return (profile.trackable[card / 8] & (1u << (card % 8))) != 0;
  }
  bool suppress(std::uint32_t pc) {
    const auto card = fb_jit_card_index(profile.code_bytes, card_shift, pc);
    if (!check(card >= 0 && static_cast<std::uint64_t>(card) < cards)) return false;
    profile.trackable[card / 8] &= static_cast<std::uint8_t>(~(1u << (card % 8)));
    ++trackable_generation;
    return true;
  }
  int compile_pending(std::uint32_t budget) {
    if (!check(queue.capacity != 0)) return -1;
    std::uint32_t compiled = 0;
    // The budget bounds processed candidates, including unsupported/skipped ones.
    for (std::uint32_t processed = 0; processed < budget && queue.count != 0; ++processed) {
      const auto pc = queue_operation(&queue, 1, 0);
      if (!check(pc >= 0 && fb_jit_card_index(profile.code_bytes, card_shift, pc) >= 0)) return -1;
      if (card_state(static_cast<std::uint32_t>(pc) >> card_shift) == 3) continue;
      if (find(pc) != nullptr) {
        mark_compiled(pc);
        continue;
      }
      int status = 0;
      const auto started = std::chrono::steady_clock::now();
      const auto age_before = aging_ns;
      if (profile.compiler_enabled != 0) {
        const auto* input = block(static_cast<std::uint32_t>(pc));
        if (!check(input != nullptr)) return -1;
        const auto token = reserve_owned();
        if (token == 0) return -1;
        auto* trace = descriptor(token);
        std::array<std::uint8_t, 8192 + header_bytes> output{};
        status = fb_jit_compile_block(input, trace, &trace->fixups, output.data(), output.size());
        if (!check(status == 0 || status == 1)) return -1;
        if (status == 1) {
          trace->byte_span = input->byte_span;
          trace->frame_depth = input->frame_depth;
          trace->dispatch_next_pc = input->next_pc;
          trace->dispatch_loops_to = input->loops_to;
          trace->code_blob = output.data();
          trace->blob_bytes = trace->size_bytes;
          status = insert(trace, token) ? 1 : 0;
          if (error != 0) return -1;
        }
        if (status == 0) owned[(token & ~owned_bit) - 1].used = false;
      }
      const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
                               std::chrono::steady_clock::now() - started)
                               .count();
      ++compile_attempts;
      compile_ns += elapsed - (aging_ns - age_before);
      if (host.compiled != nullptr && !effect(host.compiled(pc, status, elapsed))) return -1;
      if (status == 1) {
        ++compiled;
        continue;
      }
      if (!suppress(pc)) return -1;
    }
    return static_cast<int>(compiled);
  }
  bool record(std::uint32_t pc) {
    if (profile.enabled == 0) return true;
    if (!trackable(pc)) return error == 0;
    if (!check(execution_count != UINT64_MAX) ||
        !check(history_record(&history, profile.module_id, pc) == 1))
      return false;
    ++execution_count;
    return true;
  }
  bool analyze(bool yielded) {
    if (!check(history.capacity != 0 && queue.capacity != 0)) return false;
    auto& h = history;
    history_overwritten = h.dropped;
    history_approximate = h.dropped > last_overwritten;
    last_overwritten = h.dropped;
    while (h.count != 0) {
      const auto value = h.records[h.head];
      if (!check(value.module_id == profile.module_id &&
                 fb_jit_card_index(profile.code_bytes, card_shift, value.pc) >= 0))
        return false;
      h.head = (h.head + 1) % h.capacity;
      --h.count;
      const auto card = value.pc >> card_shift;
      auto state = card_state(card);
      if (state < 2) {
        ++state;
        set_card(card, state);
        if (state == 1) dirty[card / 8] |= static_cast<std::uint8_t>(1u << (card % 8));
      }
      if (state == 2 && queue_operation(&queue, 2, value.pc) == 0) {
        if (!check(queue_operation(&queue, 0, value.pc) == 1)) return false;
        if (queue.count == queue.capacity && compile_pending(queue.count) < 0) return false;
      }
    }
    if (yielded) execution_count = 0;
    return true;
  }

  bool check(bool valid) {
    if (!valid) error = 1;
    return valid;
  }
  bool effect(int success) {
    if (success != 1) error = 2;
    return success == 1;
  }
  static std::uint32_t slot(std::uint32_t pc) {
    pc ^= pc >> 16;
    pc ^= pc >> 8;
    pc ^= pc >> 4;
    return pc & (fast_slots - 1);
  }
  void invalidate() { fast.fill({}); }
  entry* find(std::uint32_t pc, std::uint32_t* bank_id = nullptr) {
    for (std::uint32_t i = 0; i < bank_count; ++i) {
      if (auto* value = banks[i].find(pc); value != nullptr) {
        if (bank_id != nullptr) *bank_id = i;
        return value;
      }
    }
    return nullptr;
  }
  static bool eligible(const jit_cache_trace& trace) {
    return trace.next_pc != no_pc && trace.loops_to == no_pc && trace.has_return_value == 0;
  }
  bool patch(entry source, const entry* target) {
    source.trace->chain_next_pc = target == nullptr ? no_pc : target->pc;
    const auto address = target == nullptr || target->trace->entry_address == 0
                             ? 0
                             : target->trace->entry_address + entry_stub_bytes;
    source.trace->chain_target_address = address;
    ++generation;
    return source.trace->entry_address == 0 ||
           effect(patch_trace_chain(&memory, source.trace->code_offset, address));
  }
  bool link(entry source, entry target) {
    std::uint32_t target_bank = 0;
    if (!check(eligible(*source.trace) && source.trace->next_pc == target.pc &&
               find(target.pc, &target_bank) != nullptr &&
               (target_bank == active || target_bank == warm)))
      return false;
    auto& b = banks[target_bank];
    bool registered = false;
    for (std::uint32_t i = 0; i < b.inbound_count; ++i) {
      if (b.inbound[i] == source.pc) registered = true;
    }
    if (!registered) {
      if (!check(b.inbound_count < max_inbound)) return false;
      b.inbound[b.inbound_count++] = source.pc;
    }
    return patch(source, &target);
  }
  bool try_link(entry source) {
    std::uint32_t target_bank = 0;
    auto* target = eligible(*source.trace) ? find(source.trace->next_pc, &target_bank) : nullptr;
    if (target != nullptr) {
      if (target_bank == active || target_bank == warm) return link(source, *target);
      // A promoted source may already have a retained chain into Oldest.
      // Relocation preserves that chain; it must not register a new link.
      if (source.trace->chain_next_pc == target->pc) {
        const auto& inbound = banks[target_bank];
        for (std::uint32_t i = 0; i < inbound.inbound_count; ++i) {
          if (inbound.inbound[i] == source.pc) return patch(source, target);
        }
      }
    }
    return patch(source, nullptr);
  }
  std::uint32_t card_state(std::uint32_t card) const {
    return (states[card / 4] >> ((card % 4) * 2)) & 3;
  }
  void set_card(std::uint32_t card, std::uint32_t state) {
    const auto shift = (card % 4) * 2;
    states[card / 4] =
        static_cast<std::uint8_t>((states[card / 4] & ~(3u << shift)) | (state << shift));
  }
  void mark_compiled(std::uint32_t pc) {
    if (states != nullptr && (pc >> card_shift) < cards) set_card(pc >> card_shift, 3);
  }
  int age() {
    const auto started = std::chrono::steady_clock::now();
    const auto result = age_sweep();
    aging_ns += std::chrono::duration_cast<std::chrono::nanoseconds>(
                    std::chrono::steady_clock::now() - started)
                    .count();
    return result;
  }
  int age_sweep() {
    ++aging_steps;
    if (dirty_bytes == 0) return 0;
    if (!check(cursor != nullptr && *cursor < dirty_bytes)) return -1;
    std::uint32_t units = 0, scanned = 0;
    int decayed = 0;
    const auto limit = age_scan < dirty_bytes ? age_scan : dirty_bytes;
    while (units < age_units && scanned < limit) {
      const auto bits = dirty[*cursor];
      if (bits != 0) {
        for (std::uint32_t bit = 0; bit < 8; ++bit) {
          const auto card = static_cast<std::uint64_t>(*cursor) * 8 + bit;
          if ((bits & (1u << bit)) != 0 && card < cards) {
            const auto index = static_cast<std::uint32_t>(card);
            // GOTCHA-JITR-09: queue and residency state must survive aging.
            if (card_state(index) == 1) {
              set_card(index, 0);
              ++decayed;
            }
            dirty[*cursor] &= static_cast<std::uint8_t>(~(1u << bit));
          }
        }
        ++units;
      }
      *cursor = *cursor + 1 == dirty_bytes ? 0 : *cursor + 1;
      ++scanned;
    }
    aging_units += units;
    aging_scanned += scanned;
    return decayed;
  }
  bool retire(entry value) {
    const auto count = value.trace->exec_count == nullptr ? 0 : *value.trace->exec_count;
    if (host.retired != nullptr && !effect(host.retired(value.pc, count))) return false;
    if ((value.token & owned_bit) != 0)
      owned[(value.token & ~owned_bit) - 1].used = false;
    else if (!check(host.release_external != nullptr) ||
             !effect(host.release_external(value.token)))
      return false;
    return true;
  }
  bool purge(std::uint32_t id) {
    auto& b = banks[id];
    for (std::uint32_t i = 0; i < b.count; ++i) {
      const auto value = b.entries[i];
      if (value.trace != nullptr) {
        if (!retire(value)) return false;
        ++evictions;
        if (states != nullptr && (value.pc >> card_shift) < cards) {
          set_card(value.pc >> card_shift, 0);
        }
      }
      b.entries[i] = {};
    }
    b.count = b.used = b.write_cursor = b.inbound_count = 0;
    return true;
  }
  bool unlink_inbound(std::uint32_t id) {
    auto& b = banks[id];
    // Only registered inbound sources are considered. No global unlink sweep.
    for (std::uint32_t i = 0; i < b.inbound_count; ++i) {
      auto* source = find(b.inbound[i]);
      if (source != nullptr && source->trace->chain_next_pc != no_pc &&
          b.find(source->trace->chain_next_pc) != nullptr) {
        if (!patch(*source, nullptr)) return false;
      }
    }
    return true;
  }
  bool rotate() {
    if (!unlink_inbound(oldest)) return false;
    if (!purge(oldest)) return false;
    const auto new_active = oldest;
    oldest = warm;
    warm = active;
    active = new_active;
    // Existing chains survive Warm -> Oldest. New links are restricted
    // to Active/Warm by link(); purge still detaches inbound chains.
    invalidate();
    if (auto_age && age() < 0) return false;
    ++rotations;
    ++generation;
    return true;
  }
  bool install(entry value) {
    auto& trace = *value.trace;
    if (trace.code_blob == nullptr) return true;
    if (!effect(fb_jit_common_install(&memory, trace.code_offset, trace.code_blob, trace.blob_bytes,
                                      &trace.fixups, &trace.entry_address)))
      return false;
    trace.code_blob = reinterpret_cast<const std::uint8_t*>(memory.base) + trace.code_offset;
    return true;
  }
  bool insert(jit_cache_trace* trace, std::uint64_t token) {
    if (!check(trace != nullptr && token != 0 && trace->size_bytes >= fireball::header_bytes &&
               banks[warm].find(trace->head_pc) == nullptr &&
               banks[oldest].find(trace->head_pc) == nullptr))
      return false;
    auto* old = banks[active].find(trace->head_pc);
    const auto previous = old == nullptr ? entry{} : *old;
    if (!banks[active].allocate(trace, token)) {
      if (previous.trace != nullptr) return false;
      if (!rotate() || !banks[active].allocate(trace, token)) return false;
    }
    invalidate();
    const entry value{trace->head_pc, trace, token};
    if (!install(value)) return false;
    if (previous.trace != nullptr && previous.trace != trace) {
      // Retained chains from Oldest must also follow a replacement's new address.
      auto& inbound = banks[active];
      for (std::uint32_t i = 0; i < inbound.inbound_count; ++i) {
        auto* source = find(inbound.inbound[i]);
        if (source != nullptr && source->trace->chain_next_pc == trace->head_pc &&
            !patch(*source, &value))
          return false;
      }
      if (!retire(previous)) return false;
    }
    if (!try_link(value)) return false;
    for (const auto id : {active, warm}) {
      auto& b = banks[id];
      for (std::uint32_t i = 0; i < b.count; ++i) {
        const auto source = b.entries[i];
        if (source.trace != nullptr &&
            (source.trace->chain_next_pc == no_pc ||
             source.trace->chain_next_pc == trace->head_pc) &&
            eligible(*source.trace) && source.trace->next_pc == trace->head_pc) {
          if (!link(source, value)) return false;
        }
      }
    }
    fast[slot(trace->head_pc)] = value;
    mark_compiled(trace->head_pc);
    ++generation;
    return true;
  }
  std::uint64_t lookup(std::uint32_t pc) {
    const auto cached = fast[slot(pc)];
    if (cached.trace != nullptr && cached.pc == pc) return cached.token;
    for (const auto id : {active, warm}) {
      if (auto* value = banks[id].find(pc); value != nullptr) {
        fast[slot(pc)] = *value;
        return value->token;
      }
    }
    auto* found = banks[oldest].find(pc);
    if (found == nullptr) return 0;
    const auto value = *found;
    found->trace = nullptr;
    found->token = 0;
    auto& previous = banks[oldest];
    previous.used -= value.trace->size_bytes;
    value.trace->flags |= 1;
    // GOTCHA-JITR-02: capture inbound registrations before rotation can purge
    // their previous bank. Sources themselves may be evicted by that rotation.
    std::array<std::uint32_t, max_inbound> following{};
    std::uint32_t count = 0;
    for (std::uint32_t i = 0; i < previous.inbound_count; ++i) {
      auto* source = find(previous.inbound[i]);
      if (source != nullptr && source->trace->chain_next_pc == pc) following[count++] = source->pc;
    }
    for (std::uint32_t i = 0; i < count; ++i) {
      previous.remove_inbound(following[i]);
      if (auto* source = find(following[i]); source != nullptr && !patch(*source, nullptr))
        return 0;
    }
    if (!banks[active].allocate(value.trace, value.token)) {
      if (!rotate() || !check(banks[active].allocate(value.trace, value.token))) return 0;
    }
    if (!install(value)) return 0;
    for (std::uint32_t i = 0; i < count; ++i) {
      if (auto* source = find(following[i]); source != nullptr && !link(*source, value)) return 0;
    }
    if (!try_link(value)) return 0;
    ++promotions;
    fast[slot(pc)] = value;
    mark_compiled(pc);
    ++generation;
    return value.token;
  }
  std::uint32_t chain_words(std::uint32_t pc) {
    std::array<std::uint32_t, max_entries * bank_count> visited{};
    std::uint32_t count = 0, words = 0;
    while (pc != no_pc) {
      for (std::uint32_t i = 0; i < count; ++i)
        if (visited[i] == pc) return words;
      if (!check(count < visited.size())) return 0;
      auto* value = find(pc);
      if (!check(value != nullptr)) return 0;
      visited[count++] = pc;
      if (value->trace->stack_words > words) words = value->trace->stack_words;
      pc = value->trace->chain_next_pc;
    }
    return words;
  }
};
}  // namespace fireball

using fireball::jit_cache_trace;
using fireball::JitRuntime;

extern "C" std::size_t fb_jit_runtime_size() { return sizeof(JitRuntime); }
extern "C" std::size_t fb_jit_runtime_alignment() { return alignof(JitRuntime); }
extern "C" JitRuntime* fb_jit_runtime_init(std::uint8_t* storage, std::size_t bytes,
                                           std::uint32_t bank_bytes, std::uint32_t entry_capacity,
                                           const std::uint32_t* offsets,
                                           fireball::jit_cache_host host) {
  if (storage == nullptr || bytes < sizeof(JitRuntime) || offsets == nullptr ||
      reinterpret_cast<std::uintptr_t>(storage) % alignof(JitRuntime) != 0 ||
      bank_bytes < fireball::header_bytes || entry_capacity == 0 ||
      entry_capacity > fireball::max_entries)
    return nullptr;
  auto* cache = ::new (storage) JitRuntime{};
  cache->host = host;
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
extern "C" int fb_jit_runtime_error(const JitRuntime* cache) { return cache->error; }
extern "C" std::uint64_t fb_jit_runtime_scalar(const JitRuntime* c, std::uint32_t field,
                                               std::uint32_t id) {
  if (id > fireball::bank_count) return 0;
  // Bank == bank_count requests an aggregate, used by host measurements and
  // dispatch-table sizing. Internal entry arrays never cross this boundary.
  if (id == fireball::bank_count) {
    std::uint64_t total = 0;
    for (std::uint32_t bank_id = 0; bank_id < fireball::bank_count; ++bank_id)
      total += fb_jit_runtime_scalar(c, field, bank_id);
    return total;
  }
  switch (static_cast<fireball::cache_field>(field)) {
    case fireball::cache_field::active_bank:
      return c->active;
    case fireball::cache_field::warm_bank:
      return c->warm;
    case fireball::cache_field::oldest_bank:
      return c->oldest;
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
    case fireball::cache_field::bank_used:
      return c->banks[id].used;
    case fireball::cache_field::bank_entry_capacity:
      return c->banks[id].entry_capacity;
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
      for (std::uint32_t i = 0; i < c->banks[id].count; ++i)
        if (c->banks[id].entries[i].trace != nullptr) ++count;
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
extern "C" std::uint64_t fb_jit_runtime_find(JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  auto* value = c->find(pc);
  return value == nullptr ? 0 : value->token;
}
extern "C" int fb_jit_runtime_bank(JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  std::uint32_t id = 0;
  return c->find(pc, &id) == nullptr ? -1 : static_cast<int>(id);
}
extern "C" int fb_jit_runtime_has_token(const JitRuntime* c, std::uint64_t token) {
  for (const auto& bank : c->banks) {
    for (std::uint32_t i = 0; i < bank.count; ++i) {
      if (bank.entries[i].trace != nullptr && bank.entries[i].token == token) return 1;
    }
  }
  return 0;
}
extern "C" int fb_jit_runtime_insert(JitRuntime* c, jit_cache_trace* trace, std::uint64_t token) {
  c->error = 0;
  return c->insert(trace, token);
}
extern "C" std::uint64_t fb_jit_runtime_lookup(JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  return c->lookup(pc);
}
extern "C" int fb_jit_runtime_rotate(JitRuntime* c) {
  c->error = 0;
  return c->rotate();
}
extern "C" int fb_jit_runtime_flush(JitRuntime* c) {
  c->error = 0;
  c->invalidate();
  for (std::uint32_t id = 0; id < fireball::bank_count; ++id) {
    auto& b = c->banks[id];
    for (std::uint32_t i = 0; i < b.count; ++i) {
      const auto value = b.entries[i];
      if (value.trace != nullptr && value.trace->chain_next_pc != fireball::no_pc &&
          !c->patch(value, nullptr))
        return 0;
    }
    if (!c->purge(id)) return 0;
  }
  ++c->generation;
  return 1;
}
extern "C" int fb_jit_runtime_bind_cards(JitRuntime* c, std::uint8_t* states,
                                         std::uint32_t state_bytes, std::uint8_t* dirty,
                                         std::uint32_t dirty_bytes, std::uint32_t cards,
                                         std::uint32_t shift, std::uint32_t* cursor,
                                         std::uint32_t units, std::uint32_t scan_bytes) {
  c->error = 0;
  if (!c->check(shift < 32 && units > 0 && scan_bytes > 0 && cursor != nullptr &&
                state_bytes == (static_cast<std::uint64_t>(cards) + 3) / 4 &&
                dirty_bytes == (static_cast<std::uint64_t>(cards) + 7) / 8 &&
                (cards == 0 || (states != nullptr && dirty != nullptr)) &&
                (dirty_bytes == 0 ? *cursor == 0 : *cursor < dirty_bytes)))
    return 0;
  c->states = states;
  c->dirty = dirty;
  c->cards = cards;
  c->card_shift = shift;
  c->dirty_bytes = dirty_bytes;
  c->cursor = cursor;
  c->age_units = units;
  c->age_scan = scan_bytes;
  return 1;
}
extern "C" int fb_jit_runtime_age(JitRuntime* c) {
  c->error = 0;
  return c->age();
}
extern "C" void fb_jit_runtime_auto_age(JitRuntime* c, int enabled) { c->auto_age = enabled != 0; }
extern "C" void fb_jit_runtime_reset_counts(JitRuntime* c) {
  for (auto& bank : c->banks) {
    for (std::uint32_t i = 0; i < bank.count; ++i) {
      const auto* trace = bank.entries[i].trace;
      if (trace != nullptr && trace->exec_count != nullptr) *trace->exec_count = 0;
    }
  }
}
extern "C" int fb_jit_runtime_snapshot(JitRuntime* c, fb_native_trace_descriptor* output,
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
                  selected.trace->byte_span > 0 && selected.trace->exec_count != nullptr))
      return -1;
    const auto& t = *selected.trace;
    const auto words = c->chain_words(selected.pc);
    if (c->error != 0) return -1;
    output[count++] = {
        t.head_pc,           t.entry_address, t.byte_span,   t.result_words,
        t.has_return_value,  t.stack_words,   t.frame_depth, t.dispatch_next_pc,
        t.dispatch_loops_to, t.chain_next_pc, words,         selected_bank == c->oldest ? 1u : 0u,
        t.exec_count};
    ++index[selected_bank];
  }
  return static_cast<int>(count);
}

extern "C" int fb_jit_runtime_bind_profile(JitRuntime* c, fireball::jit_profile p) {
  c->error = 0;
  if (!c->check(p.history_capacity > 0 && p.history_capacity <= c->history_records.size() &&
                p.queue_capacity > 0 && p.queue_capacity <= c->queue_pcs.size() &&
                p.compiler_enabled <= 1 && (p.block_count == 0 || p.blocks != nullptr) &&
                p.mask_bytes == c->dirty_bytes && (c->cards == 0 || p.trackable != nullptr) &&
                p.enabled <= 1 && fb_jit_card_count(p.code_bytes, c->card_shift) == c->cards))
    return 0;
  for (std::uint32_t i = 1; i < p.block_count; ++i)
    if (!c->check(p.blocks[i - 1].head_pc < p.blocks[i].head_pc)) return 0;
  c->history = {c->history_records.data(), p.history_capacity, 0, 0, 0};
  c->queue = {c->queue_pcs.data(), p.queue_capacity, 0};
  c->profile = p;
  for (std::uint32_t i = 0; i < p.mask_bytes; ++i) p.trackable[i] = 0;
  for (std::uint32_t i = 0; i < p.block_count; ++i) {
    const auto& block = p.blocks[i];
    const auto card = fb_jit_card_index(p.code_bytes, c->card_shift, block.head_pc);
    if (!c->check(card >= 0 && static_cast<std::uint64_t>(card) < c->cards)) return 0;
    if (block.byte_span >= p.min_trace_bytes && block.jit_score >= p.candidate_threshold)
      p.trackable[card / 8] |= static_cast<std::uint8_t>(1u << (card % 8));
  }
  c->execution_count = c->history_overwritten = c->last_overwritten = 0;
  c->history_approximate = false;
  ++c->trackable_generation;
  return 1;
}
extern "C" int fb_jit_runtime_suppress(JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  return c->suppress(pc);
}
extern "C" int fb_jit_runtime_record(JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  return c->record(pc);
}
extern "C" int fb_jit_runtime_visits(JitRuntime* c, const std::uint32_t* pcs,
                                     std::uint32_t capacity, std::uint64_t total) {
  c->error = 0;
  if (c->profile.enabled == 0) return c->check(total == 0);
  const auto retained = total < capacity ? total : capacity;
  if (!c->check((total == 0 || (capacity != 0 && pcs != nullptr)) &&
                retained == (total < c->history.capacity ? total : c->history.capacity) &&
                total <= UINT64_MAX - c->execution_count))
    return 0;
  // Validate every input before publishing a partial history.
  for (std::uint64_t i = total - retained; i < total; ++i)
    if (!c->check(c->trackable(pcs[i % capacity]))) return 0;
  for (std::uint64_t i = total - retained; i < total; ++i)
    if (!c->check(history_record(&c->history, c->profile.module_id, pcs[i % capacity]) == 1))
      return 0;
  if (!c->check(history_drop(&c->history, total - retained) == 1)) return 0;
  c->execution_count += total;
  return 1;
}
extern "C" int fb_jit_runtime_analyze(JitRuntime* c, int yielded) {
  c->error = 0;
  return c->analyze(yielded != 0);
}
extern "C" int fb_jit_runtime_compile(JitRuntime* c, std::uint32_t budget) {
  c->error = 0;
  return c->compile_pending(budget);
}
extern "C" std::uint64_t fb_jit_runtime_filtered_lookup(JitRuntime* c, std::uint32_t pc) {
  c->error = 0;
  if (c->profile.enabled != 0 && (!c->trackable(pc) || c->card_state(pc >> c->card_shift) != 3))
    return 0;
  return c->lookup(pc);
}
extern "C" int fb_jit_runtime_finish(JitRuntime* c, int yielded, std::uint32_t budget) {
  c->error = 0;
  if (!c->analyze(yielded != 0)) return 0;
  if (c->queue.count != 0 && (yielded != 0 || c->queue.count == c->queue.capacity) &&
      c->compile_pending(budget) < 0)
    return 0;
  return 1;
}

extern "C" fireball::executable_memory* fb_jit_runtime_memory(JitRuntime* c) { return &c->memory; }
extern "C" fireball::jit_cache_trace* fb_jit_runtime_trace(JitRuntime* c, std::uint64_t token) {
  return c->descriptor(token);
}
extern "C" void fb_jit_runtime_close(JitRuntime* c) { c->~JitRuntime(); }
