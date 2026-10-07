#pragma once
#include <algorithm>
#include <array>
#include <bit>
#include <cassert>
#include <limits>
#include <new>
#include <type_traits>

#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include "common_code_abi.hxx"
#include "jit_internal.hxx"
#include "jit_runtime_abi.hxx"
#include "profiling.hxx"

#ifndef FB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY
#error "FB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY is required"
#endif
#ifndef FB_CONF_JIT_CACHE_FAST_SLOT_COUNT
#error "FB_CONF_JIT_CACHE_FAST_SLOT_COUNT is required"
#endif
#ifndef FB_CONF_JIT_CACHE_BANK_CAPACITY_BYTES
#error "FB_CONF_JIT_CACHE_BANK_CAPACITY_BYTES is required"
#endif

namespace fireball {
namespace {
constexpr std::uint32_t header_bytes = FB_CONF_JIT_X64_TRACE_HEADER_BYTES;
constexpr std::uint32_t entry_stub_bytes =
    FB_CONF_JIT_X64_TRACE_ENTRY_STUB_BYTES;
constexpr auto no_pc = std::numeric_limits<std::uint32_t>::max();
constexpr std::uint32_t bank_count = 3;
constexpr std::uint32_t max_entries = FB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY;
constexpr std::uint32_t fast_slots = FB_CONF_JIT_CACHE_FAST_SLOT_COUNT;
constexpr std::uint32_t index_capacity = bank_count * max_entries;
constexpr std::size_t compile_output_bytes = header_bytes + kMaxBodyBytes;
constexpr std::uint16_t no_code_offset =
    std::numeric_limits<std::uint16_t>::max();
static_assert(fast_slots > 0 && (fast_slots & (fast_slots - 1)) == 0);
static_assert(max_entries > 0);
static_assert(index_capacity <= std::numeric_limits<std::uint8_t>::max() + 1u);
static_assert(compile_output_bytes <= FB_CONF_JIT_CACHE_BANK_CAPACITY_BYTES);

struct entry {
  std::uint32_t pc = no_pc;
  std::uint16_t code_offset = no_code_offset;
  std::uint16_t token = 0;
  bool resident() const { return code_offset != no_code_offset; }
};
static_assert(sizeof(entry) == 8);
struct cache_location {
  entry *value = nullptr;
  std::uint32_t bank_id = bank_count;
};
struct bank {
  std::array<entry, max_entries> entries{};
  std::uint32_t count = 0;
  std::uint32_t used = 0;
  std::uint32_t write_cursor = 0;
  std::uint32_t capacity = 0;
  std::uint32_t entry_capacity = 0;
  std::uint32_t offset = 0;

  std::uint32_t lower_bound(std::uint32_t pc) const {
    const auto end = entries.begin() + count;
    const auto found =
        std::lower_bound(entries.begin(), end, pc,
                         [](const entry &candidate, std::uint32_t value) {
                           return candidate.pc < value;
                         });
    return static_cast<std::uint32_t>(found - entries.begin());
  }
  entry *find(std::uint32_t pc) {
    const auto index = lower_bound(pc);
    return index < count && entries[index].pc == pc && entries[index].resident()
               ? &entries[index]
               : nullptr;
  }
  bool allocate(std::uint8_t *region, std::uint32_t pc, std::uint32_t size,
                std::uint16_t token, std::uint16_t &code_offset) {
    const auto index = lower_bound(pc);
    const auto existing = index < count && entries[index].pc == pc;
    const auto previous =
        existing && entries[index].resident() ? &entries[index] : nullptr;
    const auto old_size = previous == nullptr
                              ? 0
                              : reinterpret_cast<const jit_trace_header *>(
                                    region + previous->code_offset)
                                    ->blob_bytes;
    const auto reuse = previous != nullptr && size <= old_size;
    const auto added = reuse ? 0 : size;
    // A removed or promoted entry leaves a hole. Live-byte accounting cannot
    // locate the next safe write: retain a monotonic bank write cursor.
    if (size > capacity || added > capacity - write_cursor ||
        (!existing && count == entry_capacity))
      return false;
    const auto destination =
        reuse ? previous->code_offset : offset + write_cursor;
    if (destination > std::numeric_limits<std::uint16_t>::max())
      return false;
    code_offset = static_cast<std::uint16_t>(destination);
    write_cursor += added;
    if (!existing) {
      for (auto i = count; i > index; --i)
        entries[i] = entries[i - 1];
      ++count;
    }
    entries[index] = {pc, code_offset, token};
    used = used + size - old_size;
    return true;
  }
};
} // namespace

struct unmeasured_runtime {};
template <class Measurement> struct runtime_storage_types {
  using profile = typename Measurement::profile_type;
};
template <> struct runtime_storage_types<void> {
  using profile = jit_profile;
};

template <class Measurement>
  requires(std::is_void_v<Measurement> ||
           requires { typename Measurement::profile_type; })
class jit_runtime final
    : public std::conditional_t<std::is_void_v<Measurement>, unmeasured_runtime,
                                Measurement> {
public:
  std::array<bank, bank_count> banks{};
  std::array<std::uint8_t, index_capacity> sorted_index{};
  std::uint32_t sorted_index_count = 0;
  std::array<entry, fast_slots> fast{};
  std::array<std::uint8_t, fast_slots> fast_bank_ids{};
  std::uint32_t active = 0, warm = 1, oldest = 2;
  int error = 0;
  std::uint8_t *states = nullptr;
  std::uint8_t *dirty = nullptr;
  std::uint32_t cards = 0, card_shift = 0, dirty_bytes = 0;
  std::uint32_t *cursor = nullptr;
  std::uint32_t age_units = 1, age_scan = 1;
  executable_memory memory{};
  std::array<jit_history_record, FB_CONF_JIT_HISTORY_CAPACITY>
      history_records{};
  std::array<std::uint32_t, FB_CONF_JIT_COMPILE_QUEUE_CAPACITY> queue_pcs{};
  jit_history history{};
  jit_compile_queue queue{};
  fb_native_execution_extension extension{};
  int (*dispatch)(const fb_native_dispatch_call *,
                  fb_native_result *) = nullptr;
  ~jit_runtime() {
    if constexpr (!std::is_void_v<Measurement>)
      if (this->owns_memory)
        fb_jit_memory_close(&memory);
  }
  const fireball_wasm_module_execution_view_native *module = nullptr;
  std::uint32_t workspace_cursor = 0;
  using profile_type = typename runtime_storage_types<Measurement>::profile;
  profile_type profile{};
  const jit_wasm_block *block(std::uint32_t pc, jit_wasm_block &output) const {
    if constexpr (!std::is_void_v<Measurement>)
      if (module == nullptr)
        return Measurement::fixture_block(profile, pc);
    std::uint32_t first = 0, last = module->block_count;
    while (first < last) {
      const auto mid = first + (last - first) / 2;
      if (module->blocks[mid].head_pc < pc)
        first = mid + 1;
      else
        last = mid;
    }
    if (first == module->block_count || module->blocks[first].head_pc != pc)
      return nullptr;
    const auto &b = module->blocks[first];
    if (b.function_index >= module->function_count)
      return nullptr;
    const auto &f = module->functions[b.function_index];
    output = {b.head_pc,
              b.head_pc - f.code_pc_offset,
              b.byte_span,
              b.next_pc,
              b.loops_to,
              b.frame_depth,
              0,
              {f.code, f.code_size},
              {{reinterpret_cast<const std::uint8_t *>(f.local_offsets),
                static_cast<std::uint32_t>(
                    f.local_count * sizeof(std::uint16_t))},
               {f.local_sizes, f.local_count},
               f.local_count,
               f.local_slot_count},
              b.extension_data};
    return &output;
  }

  bool trackable(std::uint32_t pc) {
    const auto card = jit_card_index(profile.code_bytes, card_shift, pc);
    if (!check(card >= 0 && static_cast<std::uint64_t>(card) < cards))
      return false;
    return (profile.trackable[card / 8] & (1u << (card % 8))) != 0;
  }
  bool suppress(std::uint32_t pc) {
    const auto card = jit_card_index(profile.code_bytes, card_shift, pc);
    if (!check(card >= 0 && static_cast<std::uint64_t>(card) < cards))
      return false;
    profile.trackable[card / 8] &=
        static_cast<std::uint8_t>(~(1u << (card % 8)));
    if constexpr (!std::is_void_v<Measurement>)
      this->mask_changed();
    return true;
  }
  int compile_pending(std::uint32_t budget) {
    if (!check(queue.capacity != 0))
      return -1;
    std::uint32_t compiled = 0;
    // The budget bounds processed candidates, including unsupported/skipped
    // ones.
    for (std::uint32_t processed = 0; processed < budget && queue.count != 0;
         ++processed) {
      const auto pc = queue_operation(&queue, 1, 0);
      if (!check(pc >= 0 &&
                 jit_card_index(profile.code_bytes, card_shift, pc) >= 0))
        return -1;
      if (card_state(static_cast<std::uint32_t>(pc) >> card_shift) == 3)
        continue;
      if (find(pc).value != nullptr) {
        mark_compiled(pc);
        continue;
      }
      int status = 0;
      const auto compile_candidate = [&]() {
        if constexpr (!std::is_void_v<Measurement>)
          if (profile.compiler_enabled == 0)
            return 0;
        jit_wasm_block input_storage{};
        const auto *input =
            block(static_cast<std::uint32_t>(pc), input_storage);
        if (!check(input != nullptr))
          return -1;
        auto &active_bank = banks[active];
        if (active_bank.entry_capacity == 0 ||
            active_bank.count == active_bank.entry_capacity ||
            active_bank.capacity - active_bank.write_cursor <
                compile_output_bytes) {
          if (!rotate())
            return -1;
        }
        auto &target_bank = banks[active];
        if (!check(target_bank.capacity - target_bank.write_cursor >=
                   compile_output_bytes))
          return -1;
        const auto offset = target_bank.offset + target_bank.write_cursor;
        auto *output = reinterpret_cast<std::uint8_t *>(memory.base) + offset;
        std::array<std::int16_t, kMaxStackDepth> stack_locations{};
        if (!memory.patching && !effect(fb_jit_memory_begin(&memory)))
          return -1;
        status = fb_jit_compile_block(
            input, output, static_cast<std::uint32_t>(compile_output_bytes),
            output + header_bytes, stack_locations.data(),
            stack_locations.size());
        if (status == 1) {
          auto *header = reinterpret_cast<jit_trace_header *>(output);
          header->common_code_relative = -static_cast<std::int32_t>(offset);
          std::uint16_t installed_offset = no_code_offset;
          if (!target_bank.allocate(
                  reinterpret_cast<std::uint8_t *>(memory.base),
                  static_cast<std::uint32_t>(pc), header->blob_bytes, 0,
                  installed_offset)) {
            status = 0;
          } else {
            rebuild_index();
            if (installed_offset != offset)
              status = 0;
          }
        }
        if (!effect(fb_jit_memory_commit(&memory)))
          return -1;
        if (!check(status == 0 || status == 1))
          return -1;
        if (status == 1) {
          invalidate();
          entry compiled{static_cast<std::uint32_t>(pc),
                         static_cast<std::uint16_t>(offset), 0};
          if (!try_link(compiled))
            return -1;
          for (const auto id : {active, warm}) {
            auto &source_bank = banks[id];
            for (std::uint32_t i = 0; i < source_bank.count; ++i) {
              const auto source = source_bank.entries[i];
              if (source.resident() && source.pc != compiled.pc &&
                  eligible(source) && next_pc(source) == compiled.pc &&
                  !patch(source, &compiled))
                return -1;
            }
          }
          fast[slot(compiled.pc)] = compiled;
          mark_compiled(compiled.pc);
          if constexpr (!std::is_void_v<Measurement>)
            this->trace_inserted(compiled.pc);
          if constexpr (!std::is_void_v<Measurement>)
            this->cache_changed();
          if (!prepare_chains())
            return -1;
        }
        return status;
      };
      if constexpr (std::is_void_v<Measurement>) {
        status = compile_candidate();
      } else {
        typename Measurement::compile_scope timing(*this);
        status = compile_candidate();
      }
      if (status < 0)
        return -1;

      if (status == 1) {
        ++compiled;
        continue;
      }
      if (!suppress(pc))
        return -1;
    }
    return static_cast<int>(compiled);
  }
  bool record_observed(std::uint32_t pc) {
    // Configuration owns the ring invariants; the callback receives an observed
    // PC. Select the overwritten slot before advancing the head, without
    // division.
    auto &h = history;
    std::uint32_t index;
    if (h.count == h.capacity) {
      if constexpr (!std::is_void_v<Measurement>)
        if (!check(this->history_record_overwritten()))
          return false;
      index = h.head;
      h.head = h.head + 1 == h.capacity ? 0 : h.head + 1;
    } else {
      index = h.head + h.count;
      if (index >= h.capacity)
        index -= h.capacity;
      ++h.count;
    }
    h.records[index] = {profile.module_id, pc};
    if constexpr (!std::is_void_v<Measurement>)
      this->visits_recorded(1);
    return true;
  }
  bool analyze(bool yielded) {
    if (!check(history.capacity != 0 && queue.capacity != 0))
      return false;
    auto &h = history;
    if constexpr (!std::is_void_v<Measurement>)
      this->history_analyzed();
    while (h.count != 0) {
      const auto value = h.records[h.head];
      if (!check(value.module_id == profile.module_id &&
                 jit_card_index(profile.code_bytes, card_shift, value.pc) >= 0))
        return false;
      h.head = (h.head + 1) % h.capacity;
      --h.count;
      const auto card = value.pc >> card_shift;
      auto state = card_state(card);
      if (state < 2) {
        ++state;
        set_card(card, state);
        if (state == 1)
          dirty[card / 8] |= static_cast<std::uint8_t>(1u << (card % 8));
      }
      if (state == 2 && queue_operation(&queue, 2, value.pc) == 0) {
        if (!check(queue_operation(&queue, 0, value.pc) == 1))
          return false;
        if (queue.count == queue.capacity && compile_pending(queue.count) < 0)
          return false;
      }
    }
    if constexpr (!std::is_void_v<Measurement>)
      this->boundary_analyzed(yielded);
    return true;
  }

  bool check(bool valid) {
    if (!valid)
      error = 1;
    return valid;
  }
  bool effect(int success) {
    if (success != 1)
      error = 2;
    return success == 1;
  }
  static std::uint32_t slot(std::uint32_t pc) {
    pc ^= pc >> 16;
    pc ^= pc >> 8;
    pc ^= pc >> 4;
    return pc & (fast_slots - 1);
  }
  void invalidate() {
    fast.fill({});
    fast_bank_ids.fill(0);
  }
  entry &indexed_entry(std::uint8_t reference) {
    const auto bank_id = reference / max_entries;
    const auto entry_index = reference % max_entries;
    return banks[bank_id].entries[entry_index];
  }
  std::uint32_t indexed_bank(std::uint8_t reference) const {
    return reference / max_entries;
  }
  void rebuild_index() {
    sorted_index_count = 0;
    for (std::uint32_t bank_id = 0; bank_id < bank_count; ++bank_id) {
      const auto &source = banks[bank_id];
      for (std::uint32_t i = 0; i < source.count; ++i) {
        if (source.entries[i].resident())
          sorted_index[sorted_index_count++] =
              static_cast<std::uint8_t>(bank_id * max_entries + i);
      }
    }
    std::sort(sorted_index.begin(), sorted_index.begin() + sorted_index_count,
              [this](std::uint8_t left, std::uint8_t right) {
                const auto left_pc = indexed_entry(left).pc;
                const auto right_pc = indexed_entry(right).pc;
                return left_pc != right_pc ? left_pc < right_pc : left < right;
              });
  }
  [[gnu::always_inline]] inline cache_location find(std::uint32_t pc) {
    const auto end = sorted_index.begin() + sorted_index_count;
    const auto first =
        std::lower_bound(sorted_index.begin(), end, pc,
                         [this](std::uint8_t reference, std::uint32_t value) {
                           return indexed_entry(reference).pc < value;
                         });
    cache_location result{};
    for (auto found = first; found != end && indexed_entry(*found).pc == pc;
         ++found) {
      const auto bank_id = indexed_bank(*found);
      auto &candidate = indexed_entry(*found);
      assert(result.value == nullptr);
      result = {&candidate, bank_id};
    }
    return result;
  }
  jit_trace_header *header(entry value) const {
    return reinterpret_cast<jit_trace_header *>(
        reinterpret_cast<std::uint8_t *>(memory.base) + value.code_offset);
  }
  std::uintptr_t entry_address(entry value) const {
    return reinterpret_cast<std::uintptr_t>(header(value)) + header_bytes;
  }
  std::uintptr_t body_address(entry value) const {
    return entry_address(value) + entry_stub_bytes;
  }
  std::uint32_t next_pc(entry value) const {
    jit_wasm_block storage{};
    const auto *metadata = block(value.pc, storage);
    return metadata == nullptr ? no_pc : metadata->next_pc;
  }
  std::uint32_t loops_to(entry value) const {
    jit_wasm_block storage{};
    const auto *metadata = block(value.pc, storage);
    return metadata == nullptr ? no_pc : metadata->loops_to;
  }
  bool eligible(entry value) const {
    const auto *h = header(value);
    const auto successor = next_pc(value);
    if (successor == no_pc || successor <= value.pc ||
        loops_to(value) != no_pc || h->has_return_value != 0)
      return false;
    jit_wasm_block source_storage{}, target_storage{};
    const auto *source = block(value.pc, source_storage);
    const auto *target = block(successor, target_storage);
    if (source == nullptr || source->code.data == nullptr)
      return true;
    const auto terminator = source->offset + source->byte_span;
    if (terminator >= source->code.bytes)
      return true;
    switch (source->code.data[terminator]) {
    case 0x02: // block
    case 0x03: // loop
    case 0x04: // if
    case 0x05: // else
    case 0x0B: // end
    case 0x0D: // br_if
    case 0x0E: // br_table
    case 0x0F: // return
      return false;
    case 0x0C: // br has a loader-resolved target; common code adjusts frames
               // before chaining
      return true;
    default:
      return target == nullptr || source->frame_depth == target->frame_depth;
    }
  }
  entry *chain_next(entry value) {
    const auto address = header(value)->chain_target_address;
    if (address == 0)
      return nullptr;
    const auto base = reinterpret_cast<std::uintptr_t>(memory.base);
    if (address < base + header_bytes + entry_stub_bytes)
      return nullptr;
    const auto target_header = address - header_bytes - entry_stub_bytes;
    if (target_header < base + FB_CONF_JIT_CACHE_COMMON_CODE_BYTES ||
        target_header >= base + FB_CONF_JIT_CACHE_REGION_BYTES)
      return nullptr;
    return find(reinterpret_cast<const jit_trace_header *>(target_header)
                    ->head_pc)
        .value;
  }
  bool patch(entry source, const entry *target) {
    assert(source.resident());
    assert(target == nullptr || target->resident());
    assert(target == nullptr || eligible(source));
    assert(target == nullptr || next_pc(source) == target->pc);
    const auto address = target == nullptr ? 0 : body_address(*target);
    if (header(source)->chain_target_address == address)
      return true;
    if constexpr (!std::is_void_v<Measurement>)
      this->cache_changed();
    return effect(patch_trace_chain(&memory, source.code_offset, address));
  }
  bool try_link(entry source) {
    const auto target =
        eligible(source) ? find(next_pc(source)) : cache_location{};
    if (target.value != nullptr) {
      assert(target.bank_id < bank_count);
      if (target.bank_id != oldest)
        return patch(source, target.value);
      if (header(source)->chain_target_address == body_address(*target.value))
        return true;
    }
    return patch(source, nullptr);
  }
  std::uint32_t card_state(std::uint32_t card) const {
    return (states[card / 4] >> ((card % 4) * 2)) & 3;
  }
  void set_card(std::uint32_t card, std::uint32_t state) {
    const auto shift = (card % 4) * 2;
    states[card / 4] = static_cast<std::uint8_t>(
        (states[card / 4] & ~(3u << shift)) | (state << shift));
  }
  void mark_compiled(std::uint32_t pc) {
    if (states != nullptr && (pc >> card_shift) < cards)
      set_card(pc >> card_shift, 3);
  }
  int age() {
    if constexpr (std::is_void_v<Measurement>) {
      return age_sweep();
    } else {
      typename Measurement::aging_scope timing(*this);
      return age_sweep();
    }
  }
  int age_sweep() {
    if constexpr (!std::is_void_v<Measurement>)
      this->aging_started();
    if (dirty_bytes == 0)
      return 0;
    if (!check(cursor != nullptr && *cursor < dirty_bytes))
      return -1;
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
    if constexpr (!std::is_void_v<Measurement>)
      this->aging_processed(units, scanned);
    return decayed;
  }
  bool purge(std::uint32_t id) {
    auto &b = banks[id];
    for (std::uint32_t i = 0; i < b.count; ++i) {
      const auto value = b.entries[i];
      if (value.resident()) {
        if constexpr (!std::is_void_v<Measurement>)
          this->trace_evicted(value.pc);
        if (states != nullptr && (value.pc >> card_shift) < cards) {
          set_card(value.pc >> card_shift, 0);
        }
      }
      b.entries[i] = entry{};
    }
    b.count = b.used = b.write_cursor = 0;
    rebuild_index();
    return true;
  }
  bool unlink_inbound(std::uint32_t id) {
    const auto begin = reinterpret_cast<std::uintptr_t>(memory.base) +
                       banks[id].offset + header_bytes + entry_stub_bytes;
    const auto end = reinterpret_cast<std::uintptr_t>(memory.base) +
                     banks[id].offset + banks[id].capacity;
    for (auto &source_bank : banks) {
      for (std::uint32_t i = 0; i < source_bank.count; ++i) {
        const auto source = source_bank.entries[i];
        if (source.resident()) {
          const auto target = header(source)->chain_target_address;
          if (target >= begin && target < end && !patch(source, nullptr))
            return false;
        }
      }
    }
    return true;
  }
  bool rotate() {
    if (!unlink_inbound(oldest))
      return false;
    if (!purge(oldest))
      return false;
    const auto new_active = oldest;
    oldest = warm;
    warm = active;
    active = new_active;
    invalidate();
    if constexpr (std::is_void_v<Measurement>) {
      if (age() < 0)
        return false;
    } else {
      if (this->auto_age && age() < 0)
        return false;
    }
    if constexpr (!std::is_void_v<Measurement>)
      this->cache_rotated();
    if constexpr (!std::is_void_v<Measurement>)
      this->cache_changed();
    return prepare_chains();
  }
  bool install(entry value, const std::uint8_t *blob, std::uint32_t bytes) {
    std::uintptr_t ignored_entry = 0;
    return effect(fb_jit_common_install(&memory, value.code_offset, blob, bytes,
                                        &ignored_entry));
  }
  bool insert(const std::uint8_t *blob, std::uint32_t bytes,
              std::uint16_t cache_token) {
    if (!check(blob != nullptr && bytes >= header_bytes &&
               bytes <= FB_CONF_JIT_CACHE_BANK_CAPACITY_BYTES))
      return false;
    const auto *input_header = reinterpret_cast<const jit_trace_header *>(blob);
    const auto pc = input_header->head_pc;
    if (!check(input_header->blob_bytes == bytes && pc != no_pc &&
               banks[warm].find(pc) == nullptr &&
               banks[oldest].find(pc) == nullptr))
      return false;
    auto &current = banks[active];
    std::uint16_t offset = no_code_offset;
    if (!current.allocate(reinterpret_cast<std::uint8_t *>(memory.base), pc,
                          bytes, cache_token, offset)) {
      if (!rotate() ||
          !banks[active].allocate(reinterpret_cast<std::uint8_t *>(memory.base),
                                  pc, bytes, cache_token, offset))
        return false;
    }
    rebuild_index();
    invalidate();
    entry value{pc, offset, cache_token};
    if (!install(value, blob, bytes))
      return false;
    if (!try_link(value))
      return false;
    for (const auto id : {active, warm}) {
      auto &source_bank = banks[id];
      for (std::uint32_t i = 0; i < source_bank.count; ++i) {
        const auto source = source_bank.entries[i];
        if (source.resident() && source.pc != value.pc && eligible(source) &&
            next_pc(source) == value.pc && !patch(source, &value))
          return false;
      }
    }
    fast[slot(pc)] = value;
    fast_bank_ids[slot(pc)] = static_cast<std::uint8_t>(active);
    mark_compiled(pc);
    if constexpr (!std::is_void_v<Measurement>)
      this->trace_inserted(pc);
    if constexpr (!std::is_void_v<Measurement>)
      this->cache_changed();
    return prepare_chains();
  }
  [[gnu::always_inline]] inline cache_location lookup(std::uint32_t pc) {
    const auto slot_id = slot(pc);
    auto &cached = fast[slot_id];
    if (cached.resident() && cached.pc == pc)
      return {&cached, fast_bank_ids[slot_id]};
    const auto location = find(pc);
    if (location.value == nullptr)
      return {};
    if (location.bank_id != oldest) {
      cached = *location.value;
      fast_bank_ids[slot_id] = static_cast<std::uint8_t>(location.bank_id);
      return {&cached, location.bank_id};
    }
    auto *found = location.value;
    const auto value = *found;
    auto *source_header = header(value);
    const auto *source_blob =
        reinterpret_cast<const std::uint8_t *>(source_header);
    const auto source_bytes = source_header->blob_bytes;
    const auto old_body_address = body_address(value);
    found->code_offset = no_code_offset;
    found->token = 0;
    rebuild_index();
    auto &previous = banks[oldest];
    previous.used -= source_bytes;
    for (auto &source_bank : banks) {
      for (std::uint32_t i = 0; i < source_bank.count; ++i) {
        const auto source = source_bank.entries[i];
        if (source.resident() &&
            header(source)->chain_target_address == old_body_address &&
            !patch(source, nullptr))
          return {};
      }
    }
    std::uint16_t promoted_offset = no_code_offset;
    if (!banks[active].allocate(reinterpret_cast<std::uint8_t *>(memory.base),
                                pc, source_bytes, value.token,
                                promoted_offset)) {
      if (!rotate() || !check(banks[active].allocate(
                           reinterpret_cast<std::uint8_t *>(memory.base), pc,
                           source_bytes, value.token, promoted_offset)))
        return {};
    }
    rebuild_index();
    entry promoted{pc, promoted_offset, value.token};
    if (!install(promoted, source_blob, source_bytes) || !try_link(promoted))
      return {};
    for (const auto id : {active, warm}) {
      auto &source_bank = banks[id];
      for (std::uint32_t i = 0; i < source_bank.count; ++i) {
        const auto source = source_bank.entries[i];
        if (source.resident() && source.pc != pc && eligible(source) &&
            next_pc(source) == pc && !patch(source, &promoted))
          return {};
      }
    }
    if constexpr (!std::is_void_v<Measurement>)
      this->trace_promoted();
    cached = promoted;
    fast_bank_ids[slot_id] = static_cast<std::uint8_t>(active);
    mark_compiled(pc);
    if constexpr (!std::is_void_v<Measurement>)
      this->cache_changed();
    if (!prepare_chains())
      return {};
    return {&cached, active};
  }
  bool prepare_chains() {
    const auto began = !memory.patching;
    if (began && !effect(fb_jit_memory_begin(&memory)))
      return false;
    for (auto &bank : banks) {
      for (std::uint32_t i = 0; i < bank.count; ++i) {
        auto current_entry = bank.entries[i];
        if (!current_entry.resident())
          continue;
        auto *trace_header = header(current_entry);
        auto words = trace_header->stack_words;
        auto *current = &current_entry;
        for (std::uint32_t count = 0;
             current != nullptr && count < max_entries * bank_count; ++count) {
          auto *next = chain_next(*current);
          if (next == nullptr)
            break;
          if (header(*next)->stack_words > words)
            words = header(*next)->stack_words;
          current = next;
        }
        trace_header->chain_words = words;
      }
    }
    return !began || effect(fb_jit_memory_commit(&memory));
  }
};
} // namespace fireball

namespace fireball {
namespace {
constexpr std::int8_t opcode_benefit_value(std::uint8_t op) {
  switch (op) {
  case 0x6a:
  case 0x6b:
  case 0x71:
  case 0x72:
  case 0x73:
  case 0x74:
  case 0x75:
  case 0x76:
    return 7;
  case 0x6c:
  case 0x45:
  case 0x46:
  case 0x47:
  case 0x48:
  case 0x49:
  case 0x4a:
  case 0x4b:
  case 0x4c:
  case 0x4d:
  case 0x4e:
  case 0x4f:
  case 0x67:
  case 0x68:
  case 0x41:
  case 0x42:
  case 0x43:
  case 0x44:
  case 0x20:
  case 0x21:
  case 0x22:
    return 6;
  case 0x6d:
  case 0x6e:
  case 0x1a:
  case 0x1b:
  case 0x0f:
  case 0x01:
  case 0x6f:
  case 0x70:
    return 4;
  case 0x0c:
  case 0x0d:
    return 5;
  case 0x7c:
  case 0x7d:
  case 0x7e:
  case 0x92:
  case 0x93:
  case 0x94:
  case 0x95:
  case 0xa0:
  case 0xa1:
  case 0xa2:
  case 0xa3:
    return 3;
  case 0x02:
  case 0x03:
  case 0x05:
  case 0x0b:
    return 0;
  case 0x10:
  case 0x11:
  case 0x0e:
    return -1;
  case 0x40:
    return -2;
  case 0x00:
    return -8;
  case 0xfc:
    return -2;
  default:
    return -8;
  }
}
constexpr auto opcode_benefit_table = [] {
  std::array<std::uint8_t, 128> table{};
  for (std::uint32_t op = 0; op < 256; ++op)
    table[op / 2] |= (opcode_benefit_value(op) & 15) << (4 * (op % 2));
  return table;
}();
std::int64_t score_block(const jit_wasm_block &b) {
  std::int64_t score = 0;
  auto offset = b.offset;
  const auto end = offset + b.byte_span;
  if (end > b.code.bytes)
    return INT64_MIN;
  std::uint32_t operand = 0;
  auto leb = [&]() {
    operand = 0;
    for (std::uint32_t i = 0; i < 10 && offset < end; ++i) {
      const auto value = b.code.data[offset++];
      if (i < 5)
        operand |= static_cast<std::uint32_t>(value & 0x7f) << (i * 7);
      if ((value & 0x80) == 0)
        return true;
    }
    return false;
  };
  while (offset < end) {
    const auto op = b.code.data[offset++];
    const auto benefit = (opcode_benefit_table[op / 2] >> (4 * (op % 2))) & 15;
    score += benefit < 8 ? benefit : benefit - 16;
    if (op == 0x02 || op == 0x03 || op == 0x04 || op == 0x0c || op == 0x0d ||
        op == 0x10 || (op >= 0x20 && op <= 0x24) || op == 0x41 || op == 0x42) {
      if (!leb())
        return INT64_MIN;
    } else if (op == 0x11 || (op >= 0x28 && op <= 0x3e)) {
      if (!leb() || !leb())
        return INT64_MIN;
    } else if (op == 0x3f || op == 0x40) {
      if (!leb())
        return INT64_MIN;
    } else if (op == 0x43 || op == 0x44) {
      offset += op == 0x43 ? 4 : 8;
    } else if (op == 0x0e) {
      if (!leb())
        return INT64_MIN;
      const auto count = operand;
      if (count >= end - offset)
        return INT64_MIN;
      for (std::uint32_t i = 0; i <= count; ++i)
        if (!leb())
          return INT64_MIN;
    } else if (op == 0xfc) {
      if (!leb())
        return INT64_MIN;
      const auto sub = operand;
      if (sub == 10 && (!leb() || !leb()))
        return INT64_MIN;
      if (sub == 11 && !leb())
        return INT64_MIN;
      if (sub > 7 && sub != 10 && sub != 11)
        return INT64_MIN;
    }
  }
  return offset == end ? score : INT64_MIN;
}
std::uint64_t
module_code_bytes(const fireball_wasm_module_execution_view_native *view) {
  std::uint64_t bytes = 0;
  for (std::uint32_t i = view->imported_function_count;
       i < view->function_count; ++i) {
    const auto &f = view->functions[i];
    const auto end = static_cast<std::uint64_t>(f.code_pc_offset) + f.code_size;
    if (end > bytes)
      bytes = end;
  }
  return bytes;
}
} // namespace
} // namespace fireball

namespace fireball {
template <class Measurement> struct native_jit_plugin {
  using Runtime = jit_runtime<Measurement>;
  static int fb_jit_runtime_flush(Runtime *c) {
    c->error = 0;
    c->invalidate();
    for (std::uint32_t id = 0; id < fireball::bank_count; ++id) {
      auto &b = c->banks[id];
      for (std::uint32_t i = 0; i < b.count; ++i) {
        const auto value = b.entries[i];
        if (value.resident() && c->header(value)->chain_target_address != 0 &&
            !c->patch(value, nullptr))
          return 0;
      }
      if (!c->purge(id))
        return 0;
    }
    if constexpr (!std::is_void_v<Measurement>)
      c->cache_changed();
    return 1;
  }
  static int
  fb_jit_runtime_bind_cards(Runtime *c, std::uint8_t *states,
                            std::uint32_t state_bytes, std::uint8_t *dirty,
                            std::uint32_t dirty_bytes, std::uint32_t cards,
                            std::uint32_t shift, std::uint32_t *cursor,
                            std::uint32_t units, std::uint32_t scan_bytes) {
    c->error = 0;
    if (!c->check(shift < 32 && units > 0 && scan_bytes > 0 &&
                  cursor != nullptr &&
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
  static int fb_jit_runtime_bind_profile(Runtime *c,
                                         typename Runtime::profile_type p) {
    c->error = 0;
    if (!c->check(p.history_capacity > 0 &&
                  p.history_capacity <= c->history_records.size() &&
                  p.queue_capacity > 0 &&
                  p.queue_capacity <= c->queue_pcs.size() &&
                  p.mask_bytes == c->dirty_bytes &&
                  (c->cards == 0 || p.trackable != nullptr) && p.enabled <= 1 &&
                  jit_card_count(p.code_bytes, c->card_shift) == c->cards))
      return 0;
    if constexpr (!std::is_void_v<Measurement>) {
      if (!c->check(p.compiler_enabled <= 1 &&
                    (p.block_count == 0 || p.blocks != nullptr)))
        return 0;
      for (std::uint32_t i = 1; i < p.block_count; ++i)
        if (!c->check(p.blocks[i - 1].head_pc < p.blocks[i].head_pc))
          return 0;
    }
    c->history = {c->history_records.data(), p.history_capacity, 0, 0};
    c->queue = {c->queue_pcs.data(), p.queue_capacity, 0};
    c->profile = p;
    for (std::uint32_t i = 0; i < p.mask_bytes; ++i)
      p.trackable[i] = 0;
    const auto block_count = [&]() {
      if constexpr (!std::is_void_v<Measurement>)
        if (c->module == nullptr)
          return p.block_count;
      return c->module->block_count;
    }();
    for (std::uint32_t i = 0; i < block_count; ++i) {
      fireball::jit_wasm_block storage{};
      const auto *input = [&]() {
        if constexpr (!std::is_void_v<Measurement>)
          if (c->module == nullptr)
            return p.blocks + i;
        return c->block(c->module->blocks[i].head_pc, storage);
      }();
      if (!c->check(input != nullptr))
        return 0;
      const auto &block = *input;
      const auto card =
          jit_card_index(p.code_bytes, c->card_shift, block.head_pc);
      if (!c->check(card >= 0 && static_cast<std::uint64_t>(card) < c->cards))
        return 0;
      const auto score = [&]() {
        if constexpr (!std::is_void_v<Measurement>)
          if (c->module == nullptr)
            return block.jit_score;
        return fireball::score_block(block);
      }();
      if (block.byte_span >= p.min_trace_bytes &&
          score >= p.candidate_threshold)
        p.trackable[card / 8] |= static_cast<std::uint8_t>(1u << (card % 8));
    }
    if constexpr (!std::is_void_v<Measurement>)
      c->profile_bound();
    if constexpr (!std::is_void_v<Measurement>)
      c->mask_changed();
    return 1;
  }
  static int fb_jit_runtime_compile(Runtime *c, std::uint32_t budget) {
    c->error = 0;
    return c->compile_pending(budget);
  }
  static int finish_boundary(Runtime *c, int yielded, std::uint32_t budget) {
    c->error = 0;
    if (!c->analyze(yielded != 0))
      return 0;
    if (c->queue.count != 0 &&
        (yielded != 0 || c->queue.count == c->queue.capacity) &&
        c->compile_pending(budget) < 0)
      return 0;
    return 1;
  }
  static void fb_jit_runtime_close(Runtime *c) { c->~Runtime(); }
  static std::uint32_t
  execute_runtime_body(const fb_native_dispatch_call *input, std::uint32_t pc) {
    if (input == nullptr || input->context == nullptr ||
        input->stack == nullptr || input->locals == nullptr ||
        input->extension == nullptr || input->extension->owner == 0)
      return 0;
    auto &runtime = *reinterpret_cast<Runtime *>(input->extension->owner);
    const auto start_location = runtime.lookup(pc);
    auto *start = start_location.value;
    if (start == nullptr || runtime.error != 0)
      return 0;
    auto *terminal = start;
    std::uint32_t chain_count = 1;
    while (auto *next = runtime.chain_next(*terminal)) {
      terminal = next;
      if (++chain_count > fireball::max_entries * fireball::bank_count)
        return 0;
    }
    fireball::jit_wasm_block terminal_storage{};
    const auto *terminal_block = runtime.block(terminal->pc, terminal_storage);
    if (terminal_block == nullptr)
      return 0;
    auto &start_header = *runtime.header(*start);
    auto &terminal_header = *runtime.header(*terminal);
    auto &context =
        *static_cast<fireball_execution_context_native *>(input->context);
    auto &frame = context.call_stack->frames[context.call_stack->size - 1];
    if (context.sp_offset + start_header.chain_words > context.sp_capacity ||
        terminal_block->frame_depth >
            FIREBALL_NATIVE_CONTROL_STACK_CAPACITY - context.control_base) {
      return 0;
    }
    const auto expected_frames =
        context.control_base + terminal_block->frame_depth;
    frame.boundary_next_pc = terminal_block->next_pc;
    frame.boundary_loops_to = terminal_block->loops_to;
    auto *stack = input->stack;
    auto *locals = input->locals;
    using entry_fn = fireball_native_cps_handler;
    static_cast<void>(reinterpret_cast<entry_fn>(runtime.entry_address(*start))(
        &context, stack + context.sp_offset, locals + frame.local_base, 0));
    if (context.control_stack->size > expected_frames)
      context.control_stack->size = expected_frames;
#if FB_CONF_JIT_BLOCK_COUNTERS
    for (auto *current = start; current != nullptr;) {
      fireball::jit_wasm_block block_storage{};
      const auto *block = runtime.block(current->pc, block_storage);
      if (block != nullptr && block->extension_data != nullptr) {
        auto *count = static_cast<std::uint32_t *>(block->extension_data);
        if (*count != UINT32_MAX)
          ++*count;
      }
      current = runtime.chain_next(*current);
    }
#endif
    if (context.trap_code != 0)
      return chain_count;
    if (terminal_header.result_words != 0)
      context.sp_offset += terminal_header.result_words;
    context.ip = terminal_block->head_pc - frame.function_view->code_pc_offset +
                 terminal_block->byte_span;
    return chain_count;
  }
  static bool observe_runtime_body(const fb_native_dispatch_call *input,
                                   std::uint32_t pc) {
    if (input == nullptr || input->extension == nullptr ||
        input->extension->owner == 0)
      return false;
    auto &runtime = *reinterpret_cast<Runtime *>(input->extension->owner);
    return runtime.profile.enabled != 0 && runtime.trackable(pc) &&
           runtime.record_observed(pc);
  }
  static int fb_jit_runtime_run(const fb_native_dispatch_call *input,
                                fb_native_result *result) {
    if (input == nullptr || result == nullptr || input->extension == nullptr ||
        input->extension->owner == 0)
      return 0;
    auto *runtime = reinterpret_cast<Runtime *>(input->extension->owner);
    if (&runtime->extension != input->extension)
      return 0;
    runtime->error = 0;
    if (runtime->dispatch(input, result) != 1 || runtime->error != 0)
      return 0;
    return finish_boundary(runtime, result->status == 5, input->idle_budget);
  }
  static std::size_t
  workspace_size(const fireball_wasm_module_execution_view_native *view) {
    const auto cards = jit_card_count(fireball::module_code_bytes(view),
                                      FB_CONF_JIT_CARD_SHIFT);
    return cards < 0 ? 0 : (cards + 3) / 4 + 2 * ((cards + 7) / 8);
  }
  static std::size_t
  data_region_size(const fireball_wasm_module_execution_view_native *view) {
    const auto alignment = fireball::executable_region_alignment();
    const auto bytes = sizeof(Runtime) + workspace_size(view);
    return (bytes + alignment - 1) & ~(alignment - 1);
  }
  static std::size_t fb_jit_plugin_alignment() {
    return fireball::executable_region_alignment();
  }
  static std::size_t fb_jit_plugin_required_bytes(
      const fireball_wasm_module_execution_view_native *view) {
    if (view == nullptr)
      return 0;
    const auto alignment = fb_jit_plugin_alignment();
    return data_region_size(view) +
           ((FB_CONF_JIT_CACHE_REGION_BYTES + alignment - 1) &
            ~(alignment - 1));
  }
  static Runtime *fb_jit_plugin_init(
      std::uint8_t *region, std::size_t bytes,
      const fireball_wasm_module_execution_view_native *view,
      std::uint32_t module_id,
      int (*dispatch)(const fb_native_dispatch_call *, fb_native_result *)) {
    if (view == nullptr || region == nullptr ||
        bytes != fb_jit_plugin_required_bytes(view) ||
        reinterpret_cast<std::uintptr_t>(region) % fb_jit_plugin_alignment() !=
            0 ||
        dispatch == nullptr)
      return nullptr;
    auto *c = ::new (region) Runtime{};
    if constexpr (!std::is_void_v<Measurement>)
      c->owns_memory = false;
    c->dispatch = dispatch;
    c->extension = {reinterpret_cast<std::uintptr_t>(c), execute_runtime_body,
                    observe_runtime_body};
    if (!fireball::borrow_executable_region(&c->memory,
                                            region + data_region_size(view),
                                            FB_CONF_JIT_CACHE_REGION_BYTES) ||
        !fireball::initialize_common_code(&c->memory)) {
      c->~Runtime();
      return nullptr;
    }
    constexpr std::uint32_t offsets[] = {FB_CONF_JIT_CACHE_ACTIVE_OFFSET_BYTES,
                                         FB_CONF_JIT_CACHE_WARM_OFFSET_BYTES,
                                         FB_CONF_JIT_CACHE_OLDEST_OFFSET_BYTES};
    for (std::uint32_t i = 0; i < fireball::bank_count; ++i) {
      c->banks[i].capacity = FB_CONF_JIT_CACHE_BANK_CAPACITY_BYTES;
      c->banks[i].entry_capacity = fireball::max_entries;
      c->banks[i].offset = offsets[i];
    }
    c->module = view;
    const auto code_bytes = fireball::module_code_bytes(view);
    const auto cards = static_cast<std::uint32_t>(
        jit_card_count(code_bytes, FB_CONF_JIT_CARD_SHIFT));
    const auto state_bytes = (cards + 3) / 4;
    const auto mask_bytes = (cards + 7) / 8;
    auto *workspace = region + sizeof(Runtime);
    for (std::size_t i = 0; i < workspace_size(view); ++i)
      workspace[i] = 0;
    auto *dirty = workspace + state_bytes;
    auto *mask = dirty + mask_bytes;
    typename Runtime::profile_type profile{};
    static_cast<jit_profile &>(profile) = {mask,
                                           mask_bytes,
                                           module_id,
                                           1,
                                           9,
                                           1u << FB_CONF_JIT_CARD_SHIFT,
                                           code_bytes,
                                           FB_CONF_JIT_HISTORY_CAPACITY,
                                           FB_CONF_JIT_COMPILE_QUEUE_CAPACITY};
    if (!fb_jit_runtime_bind_cards(
            c, workspace, state_bytes, dirty, mask_bytes, cards,
            FB_CONF_JIT_CARD_SHIFT, &c->workspace_cursor,
            FB_CONF_JIT_AGING_STEP_UNITS, FB_CONF_JIT_AGING_STEP_SCAN_BYTES) ||
        !fb_jit_runtime_bind_profile(c, profile)) {
      c->~Runtime();
      return nullptr;
    }
    return c;
  }
};
} // namespace fireball
