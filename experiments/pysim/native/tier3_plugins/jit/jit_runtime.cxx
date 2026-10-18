/** @file
 * @brief JITのコンパイル、常駐索引、チェイニング、世代更新を実装する。
 * @details 検索とprovider callbackを同じ翻訳単位に置き、直接呼び出しの最適化を保つ。
 */
#include "jit_runtime.hxx"
#include "jit_runtime_apis.hxx"
#include <algorithm>
#include <bit>
#include <cassert>
#include <cstring>
#include <new>

#ifndef FB_JIT_RUNTIME_MEASUREMENT
#define FB_JIT_RUNTIME_MEASUREMENT void
#endif

namespace fireball {
std::uint32_t jit_cache_bank::lower_bound(wasm_pc_t pc) const {
  const auto end = entries.begin() + entry_count;
  const auto found = std::lower_bound(
      entries.begin(), end, pc,
      [](const jit_cache_entry &candidate, wasm_pc_t value) { return candidate.head_pc < value; });
  return static_cast<std::uint32_t>(found - entries.begin());
}

jit_cache_entry *jit_cache_bank::find_entry_by_pc(wasm_pc_t pc) {
  const auto index = lower_bound(pc);
  return index < entry_count && entries[index].head_pc == pc && entries[index].resident()
             ? &entries[index]
             : nullptr;
}

bool jit_cache_bank::allocate(std::uint8_t *region, wasm_pc_t pc, code_offset_t size,
                              std::uint16_t token, code_offset_t &code_offset) {
  const auto index = lower_bound(pc);
  const auto existing = index < entry_count && entries[index].head_pc == pc;
  const auto previous = existing && entries[index].resident() ? &entries[index] : nullptr;
  const auto old_size =
      previous == nullptr
          ? 0
          : reinterpret_cast<const jit_trace_header *>(region + previous->code_offset)->blob_bytes;
  const auto reuse = previous != nullptr && size <= old_size;
  constexpr auto allocation_alignment = alignof(std::uintptr_t);
  const auto allocation_size =
      static_cast<std::uint32_t>((size + allocation_alignment - 1) & ~(allocation_alignment - 1));
  const auto added = reuse ? 0 : allocation_size;
  // Replacing a trace can leave a hole. Live-byte accounting cannot locate
  // the next safe write, so retain a monotonic bank write position.
  if (size > capacity_bytes || added > capacity_bytes - write_offset_bytes ||
      (!existing && entry_count == entry_capacity)) {
    return false;
  }
  const auto destination = reuse ? previous->code_offset : region_offset_bytes + write_offset_bytes;
  assert(destination <= std::numeric_limits<std::uint16_t>::max());
  code_offset = destination;
  write_offset_bytes += added;
  if (!existing) {
    for (auto i = entry_count; i > index; --i) {
      entries[i] = entries[i - 1];
    }
    ++entry_count;
  }
  entries[index] = {pc, code_offset, token};
  resident_bytes = resident_bytes + size - old_size;
  return true;
}

template <jit_measurement_policy Measurement> jit_runtime<Measurement>::~jit_runtime() {
  if constexpr (!std::is_void_v<Measurement>) {
    if (measurements.owns_memory) {
      fb_jit_memory_close(&cache.code_memory);
    }
  }
}

template <jit_measurement_policy Measurement>
const jit_wasm_block *
jit_runtime<Measurement>::make_compiler_block_view(block_index_t index,
                                                   jit_wasm_block &output) const {
  assert(module_view != nullptr && index < module_view->block_count);
  const auto &b = module_view->blocks[index];
  assert(b.function_index < module_view->function_count);
  const auto &f = module_view->functions[b.function_index];
  auto *execution_count = module_view->block_execution_counts == nullptr
                              ? nullptr
                              : module_view->block_execution_counts + index;
  output = {b.head_pc,
            b.head_pc - f.code_pc_offset,
            b.byte_span,
            b.next_pc,
            b.loops_to,
            b.frame_depth,
            0,
            {f.code, f.code_size},
            {{reinterpret_cast<const std::uint8_t *>(f.local_offsets),
              static_cast<std::uint32_t>(f.local_count * sizeof(std::uint16_t))},
             {f.local_types, f.local_count},
             f.local_count,
             f.local_slot_count},
            execution_count,
            index,
            b.next_block_index,
            b.loops_to_block_index,
            {f.interpreter_annotation_pool, f.interpreter_annotation_byte_count},
            b.interpreter_annotation_byte_offset};
  return &output;
}

template <jit_measurement_policy Measurement>
const jit_wasm_block *jit_runtime<Measurement>::diagnostic_block_view(block_index_t index,
                                                                      jit_wasm_block &output) const
  requires(!std::is_void_v<Measurement>)
{
  if (module_view != nullptr) {
    return make_compiler_block_view(index, output);
  }
  if (index >= hotspots.configuration.block_count || hotspots.configuration.blocks == nullptr) {
    return nullptr;
  }
  return hotspots.configuration.blocks + index;
}

template <jit_measurement_policy Measurement>
const jit_wasm_block *
jit_runtime<Measurement>::block_metadata_for_trace(const jit_trace_header &trace,
                                                   jit_wasm_block &output) const {
  if constexpr (!std::is_void_v<Measurement>) {
    if (module_view == nullptr) {
      if (hotspots.configuration.block_count == 0) {
        return nullptr;
      }
      const auto *metadata = Measurement::fixture_block(hotspots.configuration, trace.head_pc);
      assert(metadata != nullptr);
      return metadata;
    }
  }
  assert(module_view != nullptr);
  const auto *metadata = make_compiler_block_view(trace.block_index, output);
  assert(metadata->head_pc == trace.head_pc);
  return metadata;
}

template <jit_measurement_policy Measurement>
bool jit_runtime<Measurement>::is_profiled_pc(wasm_pc_t pc) {
  assert(hotspots.card_shift < 32 && pc < hotspots.configuration.code_bytes);
  const auto card = pc >> hotspots.card_shift;
  assert(card < hotspots.card_count && hotspots.configuration.trackable != nullptr);
  return (hotspots.configuration.trackable[card / 8] & (1u << (card % 8))) != 0;
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::exclude_compile_candidate(wasm_pc_t pc) {
  assert(hotspots.card_shift < 32 && pc < hotspots.configuration.code_bytes);
  const auto card = pc >> hotspots.card_shift;
  assert(card < hotspots.card_count && hotspots.configuration.trackable != nullptr);
  hotspots.configuration.trackable[card / 8] &= static_cast<std::uint8_t>(~(1u << (card % 8)));
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.mask_changed();
  }
}

template <jit_measurement_policy Measurement>
jit_runtime<Measurement>::patch_transaction::patch_transaction(jit_runtime *owner)
    : runtime(owner), began(!owner->cache.code_memory.patching) {
  if (began) {
    fb_jit_memory_begin(&runtime->cache.code_memory);
  }
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::patch_transaction::close() {
  if (began) {
    began = false;
    fb_jit_memory_commit(&runtime->cache.code_memory);
  }
}

template <jit_measurement_policy Measurement>
jit_runtime<Measurement>::patch_transaction::~patch_transaction() {
  close();
}

template <jit_measurement_policy Measurement>
int jit_runtime<Measurement>::compile_one_candidate(block_index_t block_index, wasm_pc_t pc) {
  if constexpr (!std::is_void_v<Measurement>) {
    if (hotspots.configuration.compiler_enabled == 0) {
      return 0;
    }
  }
  jit_wasm_block input_storage{};
  const jit_wasm_block *input = nullptr;
  if constexpr (std::is_void_v<Measurement>) {
    assert(module_view != nullptr);
    assert(block_index < module_view->block_count);
    assert(module_view->blocks[block_index].head_pc == pc);
    input = make_compiler_block_view(block_index, input_storage);
  } else {
    if (module_view == nullptr) {
      input = Measurement::fixture_block(hotspots.configuration, pc);
    } else {
      assert(block_index < module_view->block_count);
      assert(module_view->blocks[block_index].head_pc == pc);
      input = make_compiler_block_view(block_index, input_storage);
    }
  }
  assert(input != nullptr);

  alignas(16) std::array<std::uint8_t, jit_compile_scratch_bytes> scratch{};
  auto *scratch_output = scratch.data();
  auto status = fb_jit_compile_block(input, scratch_output,
                                     static_cast<std::uint32_t>(jit_compile_scratch_bytes),
                                     scratch_output + jit_trace_header_bytes);
  if (status != 1) {
    assert(status == 0);
    return 0;
  }

  auto *scratch_header = reinterpret_cast<jit_trace_header *>(scratch_output);
  const auto blob_bytes = scratch_header->blob_bytes;
  assert(blob_bytes >= jit_trace_header_bytes && blob_bytes <= jit_compile_scratch_bytes);

  auto &active_bank = cache.banks[cache.active_bank_id];
  assert(active_bank.entry_capacity != 0);
  if (active_bank.entry_count == active_bank.entry_capacity ||
      active_bank.capacity_bytes - active_bank.write_offset_bytes < blob_bytes) {
    rotate_cache_banks();
  }
  auto &target_bank = cache.banks[cache.active_bank_id];
  assert(target_bank.capacity_bytes - target_bank.write_offset_bytes >= blob_bytes);

  patch_transaction compile_patch(this);

  const auto offset = target_bank.region_offset_bytes + target_bank.write_offset_bytes;
  auto *output = reinterpret_cast<std::uint8_t *>(cache.code_memory.base) + offset;
  std::memcpy(output, scratch_output, blob_bytes);

  auto *header = reinterpret_cast<jit_trace_header *>(output);
  header->common_code_relative =
      static_cast<std::int32_t>(FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET) -
      static_cast<std::int32_t>(offset);
  code_offset_t installed_offset = jit_invalid_code_offset;
  invalidate_block_cache_references();
  [[maybe_unused]] const auto allocated =
      target_bank.allocate(reinterpret_cast<std::uint8_t *>(cache.code_memory.base), pc,
                           header->blob_bytes, 0, installed_offset);
  assert(allocated && installed_offset == offset);
  rebuild_cache_indices();

  jit_cache_entry compiled{pc, offset, 0};
  link_resident_successor(compiled);
  for (auto reference = cache.inbound_chain_heads[chain_bucket_for_pc(compiled.head_pc)];
       reference != jit_cache_state::no_reference;
       reference = cache.inbound_chain_next[reference]) {
    const auto source = cache_entry_at_reference(reference);
    if (trace_successor_pc(source) == compiled.head_pc) {
      if constexpr (!std::is_void_v<Measurement>) {
        record_chain_construction(source);
      }
      if (bank_of_reference(reference) != cache.oldest_bank_id &&
          (trace_successor_block_index(source) == jit_invalid_block_index ||
           trace_successor_block_index(source) == trace_header(compiled)->block_index) &&
          can_chain_to(source, compiled)) {
        set_chain_target(source, &compiled);
      }
    }
  }
  mark_trace_card_compiled(compiled.head_pc);
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.trace_inserted(compiled.head_pc);
  }
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.cache_changed();
  }
  refresh_chain_requirements();
  compile_patch.close();
  return 1;
}

template <jit_measurement_policy Measurement>
int jit_runtime<Measurement>::compile_queued_candidates() {
  assert(hotspots.compile_queue.capacity != 0);
  std::uint32_t compiled = 0;
  while (hotspots.compile_queue.count != 0) {
    const auto item = queue_operation(&hotspots.compile_queue, 1, 0);
    assert(item >= 0);
    block_index_t block_index = jit_invalid_block_index;
    wasm_pc_t pc = static_cast<wasm_pc_t>(item);
    if constexpr (std::is_void_v<Measurement>) {
      assert(module_view != nullptr && item >= 0 && item < module_view->block_count);
      block_index = static_cast<block_index_t>(item);
      pc = module_view->blocks[block_index].head_pc;
    } else if (module_view != nullptr) {
      assert(item >= 0 && item < module_view->block_count);
      block_index = static_cast<block_index_t>(item);
      assert(block_index < module_view->block_count);
      pc = module_view->blocks[block_index].head_pc;
    }
    assert(pc < hotspots.configuration.code_bytes);
    if (card_state(static_cast<std::uint32_t>(pc) >> hotspots.card_shift) == 3) {
      continue;
    }
    if (lookup_cached_trace(block_index, pc).trace_entry != nullptr) {
      mark_trace_card_compiled(pc);
      continue;
    }
    int status = 0;
    if constexpr (std::is_void_v<Measurement>) {
      status = compile_one_candidate(block_index, pc);
    } else {
      typename Measurement::compile_scope timing(measurements);
      status = compile_one_candidate(block_index, pc);
    }
    if (status == 1) {
      ++compiled;
      continue;
    }
    exclude_compile_candidate(pc);
  }
  return static_cast<int>(compiled);
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::record_block_visit(block_index_t block_index, wasm_pc_t pc) {
  assert(hotspots.observation_history.capacity != 0 &&
         hotspots.observation_history.count <= hotspots.observation_history.capacity);
  assert(pc < hotspots.configuration.code_bytes);
  if constexpr (std::is_void_v<Measurement>) {
    assert(module_view != nullptr && block_index < module_view->block_count &&
           module_view->blocks[block_index].head_pc == pc);
  } else {
    assert(module_view != nullptr ? (block_index < module_view->block_count &&
                                     module_view->blocks[block_index].head_pc == pc)
                                  : (block_index == jit_invalid_block_index ||
                                     hotspots.configuration.block_count == 0 ||
                                     (block_index < hotspots.configuration.block_count &&
                                      hotspots.configuration.blocks[block_index].head_pc == pc)));
  }
  // Configuration owns the ring invariants; the callback receives an observed
  // PC. Select the overwritten slot before advancing the head, without
  // division.
  auto &h = hotspots.observation_history;
  std::uint32_t index;
  if (h.count == h.capacity) {
    if constexpr (!std::is_void_v<Measurement>) {
      measurements.history_record_overwritten();
    }
    index = h.head;
    h.head = h.head + 1 == h.capacity ? 0 : h.head + 1;
  } else {
    index = h.head + h.count;
    if (index >= h.capacity) {
      index -= h.capacity;
    }
    ++h.count;
  }
  h.records[index] = {block_index, pc};
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.visits_recorded(1);
  }
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::record_block_visit(wasm_pc_t pc)
  requires(!std::is_void_v<Measurement>)
{
  const auto *metadata = Measurement::fixture_block(hotspots.configuration, pc);
  assert(hotspots.configuration.block_count == 0 || metadata != nullptr);
  const auto block_index = metadata == nullptr ? jit_invalid_block_index : metadata->block_index;
  record_block_visit(block_index, pc);
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::analyze_observation_history(bool yielded) {
  assert(hotspots.observation_history.capacity != 0 && hotspots.compile_queue.capacity != 0);
  auto &h = hotspots.observation_history;
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.history_analyzed();
  }
  while (h.count != 0) {
    const auto value = h.records[h.head];
    h.head = h.head + 1 == h.capacity ? 0 : h.head + 1;
    --h.count;
    const auto card = value.head_pc >> hotspots.card_shift;
    auto state = card_state(card);
    if (state < 2) {
      ++state;
      set_card_state(card, state);
      if (state == 1) {
        hotspots.dirty_cards[card / 8] |= static_cast<std::uint8_t>(1u << (card % 8));
      }
    }
    const auto item = module_view == nullptr ? value.head_pc : value.block_index;
    if (state == 2 && queue_operation(&hotspots.compile_queue, 2, item) == 0) {
      [[maybe_unused]] const auto queued = queue_operation(&hotspots.compile_queue, 0, item);
      assert(queued == 1);
      if (hotspots.compile_queue.count == hotspots.compile_queue.capacity) {
        compile_queued_candidates();
      }
    }
  }
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.boundary_analyzed(yielded);
  }
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::invalidate_block_cache_references() {
  if constexpr (!std::is_void_v<Measurement>) {
    if (module_view == nullptr) {
      return;
    }
  }
  assert(module_view != nullptr);
  for (std::uint32_t i = 0; i < cache.pc_index_count; ++i) {
    const auto reference = cache.pc_index[i];
    const auto value = cache_entry_at_reference(reference);
    assert(value.resident());
    const auto block_index = trace_header(value)->block_index;
    assert(block_index < module_view->block_count);
    auto &block = module_view->blocks[block_index];
    assert(block.head_pc == value.head_pc);
    assert(block.jit_cache_reference == reference || block.jit_cache_reference == 0xFFu);
    block.jit_cache_reference = 0xFFu;
  }
}

template <jit_measurement_policy Measurement>
jit_cache_entry &jit_runtime<Measurement>::cache_entry_at_reference(std::uint8_t reference) {
  return cache.banks[reference / jit_cache_bank_entry_capacity]
      .entries[reference % jit_cache_bank_entry_capacity];
}

template <jit_measurement_policy Measurement>
std::uint32_t jit_runtime<Measurement>::bank_of_reference(std::uint8_t reference) const {
  assert(reference < jit_cache_total_entry_capacity);
  return reference / jit_cache_bank_entry_capacity;
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::rebuild_cache_indices() {
  cache.pc_index_count = 0;
  cache.inbound_chain_heads.fill(jit_cache_state::no_reference);
  for (std::uint32_t bank_id = 0; bank_id < jit_cache_bank_count; ++bank_id) {
    const auto &source = cache.banks[bank_id];
    for (std::uint32_t i = 0; i < source.entry_count; ++i) {
      if (!source.entries[i].resident()) {
        continue;
      }
      const auto reference = static_cast<std::uint8_t>(bank_id * jit_cache_bank_entry_capacity + i);
      cache.pc_index[cache.pc_index_count++] = reference;
      const auto successor = trace_successor_pc(source.entries[i]);
      if (successor != jit_invalid_pc) {
        const auto bucket = chain_bucket_for_pc(successor);
        cache.inbound_chain_next[reference] = cache.inbound_chain_heads[bucket];
        cache.inbound_chain_heads[bucket] = reference;
      }
    }
  }
  std::sort(cache.pc_index.begin(), cache.pc_index.begin() + cache.pc_index_count,
            [this](std::uint8_t left, std::uint8_t right) {
              const auto left_pc = cache_entry_at_reference(left).head_pc;
              const auto right_pc = cache_entry_at_reference(right).head_pc;
              return left_pc != right_pc ? left_pc < right_pc : left < right;
            });
  if constexpr (!std::is_void_v<Measurement>) {
    if (module_view == nullptr) {
      return;
    }
  }
  assert(module_view != nullptr);
  for (std::uint32_t i = 0; i < cache.pc_index_count; ++i) {
    const auto reference = cache.pc_index[i];
    const auto value = cache_entry_at_reference(reference);
    const auto block_index = trace_header(value)->block_index;
    assert(block_index < module_view->block_count);
    auto &block = module_view->blocks[block_index];
    assert(block.head_pc == value.head_pc);
    assert(block.jit_cache_reference == 0xFFu);
    block.jit_cache_reference = reference;
  }
}

template <jit_measurement_policy Measurement>
std::uint32_t jit_runtime<Measurement>::chain_bucket_for_pc(wasm_pc_t pc) {
  return (pc * 0x9e3779b1u) >> 26;
}

template <jit_measurement_policy Measurement>
jit_trace_location jit_runtime<Measurement>::find_cached_trace_by_pc(wasm_pc_t pc) {
  const auto end = cache.pc_index.begin() + cache.pc_index_count;
  const auto first = std::lower_bound(cache.pc_index.begin(), end, pc,
                                      [this](std::uint8_t reference, wasm_pc_t value) {
                                        return cache_entry_at_reference(reference).head_pc < value;
                                      });
  if (first == end || cache_entry_at_reference(*first).head_pc != pc) {
    return {};
  }
  return {&cache_entry_at_reference(*first), bank_of_reference(*first)};
}

template <jit_measurement_policy Measurement>
std::uint8_t jit_runtime<Measurement>::cache_reference(jit_trace_location location) const {
  assert(location.trace_entry != nullptr && location.bank_id < jit_cache_bank_count);
  const auto index = static_cast<std::uint32_t>(location.trace_entry -
                                                cache.banks[location.bank_id].entries.data());
  assert(index < cache.banks[location.bank_id].entry_count);
  const auto value = location.bank_id * jit_cache_bank_entry_capacity + index;
  assert(value < std::numeric_limits<std::uint8_t>::max());
  return static_cast<std::uint8_t>(value);
}

template <jit_measurement_policy Measurement>
jit_trace_location
jit_runtime<Measurement>::trace_location_from_reference(std::uint8_t reference_value) {
  assert(reference_value != std::numeric_limits<std::uint8_t>::max());
  const auto bank_id = bank_of_reference(reference_value);
  assert(bank_id < jit_cache_bank_count);
  auto &value = cache_entry_at_reference(reference_value);
  assert(value.resident());
  return {&value, bank_id};
}

template <jit_measurement_policy Measurement>
jit_trace_header *jit_runtime<Measurement>::trace_header(jit_cache_entry value) const {
  return reinterpret_cast<jit_trace_header *>(
      reinterpret_cast<std::uint8_t *>(cache.code_memory.base) + value.code_offset);
}

template <jit_measurement_policy Measurement>
std::uintptr_t jit_runtime<Measurement>::trace_entry_address(jit_cache_entry value) const {
  return reinterpret_cast<std::uintptr_t>(trace_header(value)) + jit_trace_header_bytes;
}

template <jit_measurement_policy Measurement>
wasm_pc_t jit_runtime<Measurement>::trace_successor_pc(jit_cache_entry value) const {
  jit_wasm_block storage{};
  const auto *metadata = block_metadata_for_trace(*trace_header(value), storage);
  return metadata == nullptr ? jit_invalid_pc : metadata->next_pc;
}

template <jit_measurement_policy Measurement>
block_index_t jit_runtime<Measurement>::trace_successor_block_index(jit_cache_entry value) const {
  jit_wasm_block storage{};
  const auto *metadata = block_metadata_for_trace(*trace_header(value), storage);
  return metadata == nullptr ? jit_invalid_block_index : metadata->next_block_index;
}

static bool chain_terminator_is_unsupported(const jit_trace_header *header,
                                            const jit_wasm_block &source) {
  if (header != nullptr && header->exit_kind == jit_trace_exit_kind::call_cps) {
    return true;
  }
  if (source.code.data == nullptr) {
    return false;
  }
  const auto terminator = source.offset + source.byte_span;
  if (terminator < source.code.bytes) {
    switch (source.code.data[terminator]) {
    case 0x04: // if remains an Interpreter CPS boundary.
    case 0x0C: // br remains an Interpreter CPS boundary.
    case 0x0D: // br_if remains an Interpreter CPS boundary.
    case 0x0E: // br_table
    case 0x0F: // return
    case 0x10: // call
    case 0x11: // call_indirect remains an Interpreter CPS boundary.
      return true;
    default:
      break;
    }
  }
  return false;
}

[[maybe_unused]] static jit_chain_reason
chain_source_rejection_reason(const jit_trace_header &header, const jit_wasm_block &source) {
  const auto terminator = source.offset + source.byte_span;
  if (chain_terminator_is_unsupported(&header, source)) {
    return jit_chain_reason::unsupported_control_flow;
  }
  const auto successor = source.next_pc;
  if (successor == source.head_pc) {
    return jit_chain_reason::self_loop;
  }
  if (successor < source.head_pc) {
    return jit_chain_reason::backedge;
  }
  if (source.loops_to != jit_invalid_pc) {
    return jit_chain_reason::loop_target;
  }
  const auto opcode = terminator < source.code.bytes && source.code.data != nullptr
                          ? source.code.data[terminator]
                          : std::uint8_t{0xFF};
  const bool syntax_delimiter =
      opcode == 0x02 || opcode == 0x03 || opcode == 0x05 || opcode == 0x0B;
  if (header.result_words != 0 && !syntax_delimiter) {
    return jit_chain_reason::result_words;
  }
  return chain_terminator_is_unsupported(&header, source)
             ? jit_chain_reason::unsupported_control_flow
             : jit_chain_reason::linked;
}

template <jit_measurement_policy Measurement>
bool jit_runtime<Measurement>::can_chain_from(jit_cache_entry value) const {
  const auto *header = trace_header(value);
  if (header->exit_kind == jit_trace_exit_kind::call_cps) {
    return false;
  }
  const auto successor = trace_successor_pc(value);
  const auto successor_index = trace_successor_block_index(value);
  if (successor == jit_invalid_pc || successor_index == jit_invalid_block_index) {
    return false;
  }
  if (module_view != nullptr) {
    assert(successor_index < module_view->block_count);
    assert(module_view->blocks[successor_index].head_pc == successor);
  }
  jit_wasm_block source_storage{};
  const auto *source = block_metadata_for_trace(*header, source_storage);
  assert(source != nullptr);
  if constexpr (!std::is_void_v<Measurement>) {
    if (source->code.data == nullptr) {
      // QA metadata-only fixtures can establish same-depth forward chaining.
      return header->result_words == 0;
    }
  }
  assert(source->code.data != nullptr);
  const auto terminator = source->offset + source->byte_span;
  if (terminator >= source->code.bytes) {
    return header->result_words == 0;
  }
  const auto opcode = source->code.data[terminator];
  const bool syntax_delimiter =
      opcode == 0x02 || opcode == 0x03 || opcode == 0x05 || opcode == 0x0B;
  if (header->result_words != 0 && !syntax_delimiter) {
    return false;
  }
  switch (opcode) {
  case 0x02: // Syntax delimiters have no emitted operation; loader metadata links past them.
  case 0x03:
  case 0x05:
  case 0x0B:
    return true;
  case 0x0E: // br_table
  case 0x0F: // return
  case 0x10: // call is executed by Interpreter CPS.
  case 0x11: // call_indirect is executed by Interpreter CPS.
    return false;
  case 0x04: // if returns through Interpreter CPS.
  case 0x0C: // br returns through Interpreter CPS.
  case 0x0D: // br_if returns through Interpreter CPS.
    return false;
  default:
    return true;
  }
}

template <jit_measurement_policy Measurement>
bool jit_runtime<Measurement>::can_chain_to(jit_cache_entry value, jit_cache_entry target) const {
  assert(value.resident() && target.resident());
  if (!can_chain_from(value) || trace_successor_pc(value) != target.head_pc) {
    return false;
  }
  const auto successor_index = trace_successor_block_index(value);
  if (successor_index != jit_invalid_block_index &&
      successor_index != trace_header(target)->block_index) {
    return false;
  }
  if (trace_header(value)->frame_depth == trace_header(target)->frame_depth) {
    return true;
  }
  jit_wasm_block source_storage{};
  const auto *source = block_metadata_for_trace(*trace_header(value), source_storage);
  assert(source != nullptr);
  if constexpr (!std::is_void_v<Measurement>) {
    if (source->code.data == nullptr) {
      // Metadata-only fixtures cannot prove a control-flow depth transition.
      return false;
    }
  }
  assert(source->code.data != nullptr);
  const auto terminator = source->offset + source->byte_span;
  if (terminator >= source->code.bytes) {
    return false;
  }
  switch (source->code.data[terminator]) {
  case 0x02: // block delimiter opens one static nesting level.
  case 0x03: // loop delimiter opens one static nesting level.
  case 0x05: // else delimiter skips to after the matching end.
  case 0x0B: // end delimiter closes one static nesting level.
    return true;
  default:
    return false;
  }
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::record_runtime_trace_exit(block_index_t source_block_index,
                                                         jit_stack_words_t result_words,
                                                         frame_depth_t frame_depth)
  requires(!std::is_void_v<Measurement>)
{
  auto &measurements = this->measurements;
  if (measurements.pending_runtime_source_block != jit_invalid_block_index) {
    ++measurements.chain_runtime_unobserved_trace_exits;
  }
  measurements.pending_runtime_source_block = jit_invalid_block_index;
  measurements.pending_runtime_result_words = 0;
  measurements.pending_runtime_frame_depth = 0;
  jit_wasm_block source_storage{};
  const auto *source = diagnostic_block_view(source_block_index, source_storage);
  if (source == nullptr) {
    ++measurements.chain_runtime_unobserved_trace_exits;
    return;
  }
  if (source->next_pc == jit_invalid_pc && source->loops_to == jit_invalid_pc) {
    return;
  }
  measurements.pending_runtime_source_block = source_block_index;
  measurements.pending_runtime_result_words = result_words;
  measurements.pending_runtime_frame_depth = frame_depth;
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::record_runtime_transition(block_index_t target_block_index,
                                                         wasm_pc_t target_pc)
  requires(!std::is_void_v<Measurement>)
{
  auto &measurements = this->measurements;
  const auto source_block_index = measurements.pending_runtime_source_block;
  if (source_block_index == jit_invalid_block_index) {
    return;
  }
  measurements.pending_runtime_source_block = jit_invalid_block_index;
  ++measurements.chain_runtime_transitions;
  jit_wasm_block source_storage{};
  jit_wasm_block target_storage{};
  const auto *source = diagnostic_block_view(source_block_index, source_storage);
  const auto *target_block = diagnostic_block_view(target_block_index, target_storage);
  if (source == nullptr || target_block == nullptr || target_pc == jit_invalid_pc ||
      target_block->head_pc != target_pc) {
    ++measurements.chain_runtime_unclassified;
    return;
  }
  const auto source_trace = lookup_cached_trace(source_block_index, source->head_pc);
  const auto *source_header = source_trace.trace_entry == nullptr
                                  ? nullptr
                                  : trace_header(*source_trace.trace_entry);
  jit_chain_reason reason = jit_chain_reason::count;
  if (target_pc == source->head_pc) {
    reason = jit_chain_reason::self_loop;
  } else if (target_pc < source->head_pc) {
    reason = jit_chain_reason::backedge;
  } else if (source->loops_to != jit_invalid_pc && target_pc == source->loops_to) {
    reason = jit_chain_reason::loop_target;
  } else if (measurements.pending_runtime_result_words != 0) {
    reason = jit_chain_reason::result_words;
  } else if (chain_terminator_is_unsupported(source_header, *source) ||
             (target_pc != source->next_pc && target_pc != source->loops_to)) {
    reason = jit_chain_reason::unsupported_control_flow;
  } else {
    const auto expected_index =
        target_pc == source->loops_to ? source->loops_to_block_index : source->next_block_index;
    const auto target = find_cached_trace_by_pc(target_pc);
    if (expected_index != target_block_index ||
        (target.trace_entry != nullptr &&
         trace_header(*target.trace_entry)->block_index != target_block_index)) {
      reason = jit_chain_reason::successor_block_index_mismatch;
    } else if (target.trace_entry == nullptr) {
      reason = jit_chain_reason::successor_not_resident;
    } else if (measurements.pending_runtime_frame_depth !=
               trace_header(*target.trace_entry)->frame_depth) {
      reason = jit_chain_reason::frame_depth_mismatch;
    } else if (target.bank_id == cache.oldest_bank_id) {
      reason = jit_chain_reason::oldest_bank;
    } else {
      reason = jit_chain_reason::linked;
    }
  }

  if (reason == jit_chain_reason::count) {
    ++measurements.chain_runtime_unclassified;
    return;
  }
  auto source_opcode = std::uint8_t{0xFF};
  const auto source_terminator = static_cast<std::uint64_t>(source->offset) + source->byte_span;
  if (source->code.data != nullptr && source_terminator < source->code.bytes) {
    source_opcode = source->code.data[source_terminator];
  }
  measurements.record_chain_runtime(reason, source_opcode);
  measurements.pending_runtime_result_words = 0;
  measurements.pending_runtime_frame_depth = 0;
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::record_chain_construction(jit_cache_entry source)
  requires(!std::is_void_v<Measurement>)
{
  const auto successor_pc = trace_successor_pc(source);
  const auto successor_index = trace_successor_block_index(source);
  if (successor_pc == jit_invalid_pc || successor_index == jit_invalid_block_index) {
    return;
  }

  const auto *header = trace_header(source);
  jit_wasm_block source_storage{};
  const auto *source_block = block_metadata_for_trace(*header, source_storage);
  assert(source_block != nullptr);
  auto reason = chain_source_rejection_reason(*header, *source_block);
  if (reason == jit_chain_reason::linked) {
    const auto target = find_cached_trace_by_pc(successor_pc);
    if (target.trace_entry == nullptr) {
      reason = jit_chain_reason::successor_not_resident;
    } else if (trace_header(*target.trace_entry)->block_index != successor_index) {
      reason = jit_chain_reason::successor_block_index_mismatch;
    } else if (!can_chain_to(source, *target.trace_entry)) {
      reason = header->frame_depth != trace_header(*target.trace_entry)->frame_depth
                   ? jit_chain_reason::frame_depth_mismatch
                   : jit_chain_reason::unsupported_control_flow;
    } else if (target.bank_id == cache.oldest_bank_id &&
               header->chain_target_address != trace_entry_address(*target.trace_entry)) {
      reason = jit_chain_reason::oldest_bank;
    }
  }
  measurements.record_chain_construction(reason);
}

template <jit_measurement_policy Measurement>
const jit_trace_header *
jit_runtime<Measurement>::chain_target_header(const jit_trace_header &source) const {
  const auto address = source.chain_target_address;
  if (address == 0) {
    return nullptr;
  }
  [[maybe_unused]] const auto base = reinterpret_cast<std::uintptr_t>(cache.code_memory.base);
  assert(address >= base + jit_trace_header_bytes);
  const auto target_header = address - jit_trace_header_bytes;
  assert(target_header >= base + FB_CONF_JIT_CACHE_COMMON_CODE_BYTES &&
         target_header < base + FB_CONF_JIT_CACHE_REGION_BYTES);
  return reinterpret_cast<const jit_trace_header *>(target_header);
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::set_chain_target(jit_cache_entry source,
                                                const jit_cache_entry *target) {
  assert(source.resident());
  assert(target == nullptr || target->resident());
  assert(target == nullptr || can_chain_to(source, *target));
  const auto address = target == nullptr ? 0 : trace_entry_address(*target);
  if (trace_header(source)->chain_target_address == address) {
    return;
  }
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.cache_changed();
  }
  patch_trace_chain(&cache.code_memory, source.code_offset, address);
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::link_resident_successor(jit_cache_entry source) {
  const auto source_can_chain = can_chain_from(source);
  const auto successor_pc = trace_successor_pc(source);
  const auto successor_index = trace_successor_block_index(source);
  const auto target =
      source_can_chain ? lookup_cached_trace(successor_index, successor_pc) : jit_trace_location{};
  const auto target_can_chain =
      target.trace_entry != nullptr && can_chain_to(source, *target.trace_entry);
  if constexpr (!std::is_void_v<Measurement>) {
    record_chain_construction(source);
  }
  if (target.trace_entry != nullptr) {
    if (target.bank_id != cache.oldest_bank_id && target_can_chain) {
      set_chain_target(source, target.trace_entry);
    } else if (target.bank_id == cache.oldest_bank_id && target_can_chain &&
               trace_header(source)->chain_target_address !=
                   trace_entry_address(*target.trace_entry)) {
      set_chain_target(source, nullptr);
    } else if (!target_can_chain) {
      set_chain_target(source, nullptr);
    }
  } else {
    set_chain_target(source, nullptr);
  }
}

template <jit_measurement_policy Measurement>
std::uint32_t jit_runtime<Measurement>::card_state(std::uint32_t card) const {
  return (hotspots.card_states[card / 4] >> ((card % 4) * 2)) & 3;
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::set_card_state(std::uint32_t card, std::uint32_t state) {
  const auto shift = (card % 4) * 2;
  hotspots.card_states[card / 4] = static_cast<std::uint8_t>(
      (hotspots.card_states[card / 4] & ~(3u << shift)) | (state << shift));
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::mark_trace_card_compiled(wasm_pc_t pc) {
  if constexpr (!std::is_void_v<Measurement>) {
    if (hotspots.card_states == nullptr) {
      return;
    }
  }
  assert(hotspots.card_states != nullptr && (pc >> hotspots.card_shift) < hotspots.card_count);
  set_card_state(pc >> hotspots.card_shift, 3);
}

template <jit_measurement_policy Measurement> int jit_runtime<Measurement>::age_hotspot_cards() {
  if constexpr (std::is_void_v<Measurement>) {
    return decay_dirty_cards();
  } else {
    typename Measurement::aging_scope timing(measurements);
    return decay_dirty_cards();
  }
}

template <jit_measurement_policy Measurement> int jit_runtime<Measurement>::decay_dirty_cards() {
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.aging_started();
  }
  if (hotspots.dirty_mask_bytes == 0) {
    return 0;
  }
  assert(hotspots.aging_cursor != nullptr && *hotspots.aging_cursor < hotspots.dirty_mask_bytes);
  std::uint32_t units = 0, scanned = 0;
  int decayed = 0;
  const auto limit = hotspots.aging_scan_bytes < hotspots.dirty_mask_bytes
                         ? hotspots.aging_scan_bytes
                         : hotspots.dirty_mask_bytes;
  while (units < hotspots.aging_step_units && scanned < limit) {
    const auto bits = hotspots.dirty_cards[*hotspots.aging_cursor];
    if (bits != 0) {
      for (std::uint32_t bit = 0; bit < 8; ++bit) {
        const auto card = static_cast<std::uint64_t>(*hotspots.aging_cursor) * 8 + bit;
        if ((bits & (1u << bit)) != 0 && card < hotspots.card_count) {
          const auto index = static_cast<std::uint32_t>(card);
          // GOTCHA-JITR-09: hotspots.compile_queue and residency state must survive aging.
          if (card_state(index) == 1) {
            set_card_state(index, 0);
            ++decayed;
          }
          hotspots.dirty_cards[*hotspots.aging_cursor] &= static_cast<std::uint8_t>(~(1u << bit));
        }
      }
      ++units;
    }
    *hotspots.aging_cursor =
        *hotspots.aging_cursor + 1 == hotspots.dirty_mask_bytes ? 0 : *hotspots.aging_cursor + 1;
    ++scanned;
  }
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.aging_processed(units, scanned);
  }
  return decayed;
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::retire_cache_bank(std::uint32_t id) {
  assert(id < jit_cache_bank_count);
  invalidate_block_cache_references();
  auto &b = cache.banks[id];
  for (std::uint32_t i = 0; i < b.entry_count; ++i) {
    const auto value = b.entries[i];
    if (value.resident()) {
      if constexpr (!std::is_void_v<Measurement>) {
        measurements.trace_evicted(value.head_pc);
      }
      if constexpr (!std::is_void_v<Measurement>) {
        if (hotspots.card_states == nullptr) {
          b.entries[i] = jit_cache_entry{};
          continue;
        }
      }
      assert(hotspots.card_states != nullptr &&
             (value.head_pc >> hotspots.card_shift) < hotspots.card_count);
      set_card_state(value.head_pc >> hotspots.card_shift, 0);
    }
    b.entries[i] = jit_cache_entry{};
  }
  b.entry_count = b.resident_bytes = b.write_offset_bytes = 0;
  rebuild_cache_indices();
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::unlink_retiring_bank(std::uint32_t id) {
  const auto &retiring = cache.banks[id];
  for (std::uint32_t i = 0; i < retiring.entry_count; ++i) {
    const auto target = retiring.entries[i];
    if (!target.resident()) {
      continue;
    }
    const auto address = trace_entry_address(target);
    for (auto reference = cache.inbound_chain_heads[chain_bucket_for_pc(target.head_pc)];
         reference != jit_cache_state::no_reference;
         reference = cache.inbound_chain_next[reference]) {
      const auto source = cache_entry_at_reference(reference);
      // Sources in the retiring bank disappear with their targets.
      assert(source.resident());
      if (bank_of_reference(reference) != id && trace_successor_pc(source) == target.head_pc &&
          trace_header(source)->chain_target_address == address) {
        set_chain_target(source, nullptr);
      }
    }
  }
}

template <jit_measurement_policy Measurement> void jit_runtime<Measurement>::rotate_cache_banks() {
  patch_transaction rotate_patch(this);
  unlink_retiring_bank(cache.oldest_bank_id);
  retire_cache_bank(cache.oldest_bank_id);
  const auto new_active = cache.oldest_bank_id;
  cache.oldest_bank_id = cache.warm_bank_id;
  cache.warm_bank_id = cache.active_bank_id;
  cache.active_bank_id = new_active;
  if constexpr (std::is_void_v<Measurement>) {
    age_hotspot_cards();
  } else {
    if (measurements.auto_age) {
      age_hotspot_cards();
    }
  }
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.cache_rotated();
  }
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.cache_changed();
  }
  refresh_chain_requirements();
  rotate_patch.close();
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::copy_trace_into_cache(jit_cache_entry value,
                                                     const std::uint8_t *blob,
                                                     std::uint32_t bytes) {
  std::uintptr_t ignored_entry = 0;
  auto block_index = jit_invalid_block_index;
  if (module_view != nullptr) {
    const auto *source = reinterpret_cast<const jit_trace_header *>(blob);
    block_index = source->block_index;
    assert(block_index < module_view->block_count);
    assert(module_view->blocks[block_index].head_pc == value.head_pc);
  } else if constexpr (!std::is_void_v<Measurement>) {
    if (hotspots.configuration.block_count != 0) {
      const auto *fixture = Measurement::fixture_block(hotspots.configuration, value.head_pc);
      assert(fixture != nullptr);
      block_index = fixture->block_index;
    }
  }
  fb_jit_common_install(&cache.code_memory, value.code_offset, blob, bytes, block_index,
                        &ignored_entry);
}

template <jit_measurement_policy Measurement>
bool jit_runtime<Measurement>::insert_trace_fixture(const std::uint8_t *blob, std::uint32_t bytes,
                                                    std::uint16_t cache_token)
  requires(!std::is_void_v<Measurement>)
{
  assert(blob != nullptr && bytes >= jit_trace_header_bytes &&
         bytes <= FB_CONF_JIT_CACHE_BANK_CAPACITY_BYTES);
  const auto *input_header = reinterpret_cast<const jit_trace_header *>(blob);
  const auto pc = input_header->head_pc;
  assert(input_header->blob_bytes == bytes && pc != jit_invalid_pc &&
         cache.banks[cache.warm_bank_id].find_entry_by_pc(pc) == nullptr &&
         cache.banks[cache.oldest_bank_id].find_entry_by_pc(pc) == nullptr);
  auto &current = cache.banks[cache.active_bank_id];
  code_offset_t offset = jit_invalid_code_offset;
  invalidate_block_cache_references();
  if (!current.allocate(reinterpret_cast<std::uint8_t *>(cache.code_memory.base), pc, bytes,
                        cache_token, offset)) {
    rebuild_cache_indices();
    rotate_cache_banks();
    invalidate_block_cache_references();
    if (!cache.banks[cache.active_bank_id].allocate(
            reinterpret_cast<std::uint8_t *>(cache.code_memory.base), pc, bytes, cache_token,
            offset)) {
      rebuild_cache_indices();
      return false;
    }
  }
  jit_cache_entry value{pc, offset, cache_token};
  copy_trace_into_cache(value, blob, bytes);
  rebuild_cache_indices();
  link_resident_successor(value);
  for (auto reference = cache.inbound_chain_heads[chain_bucket_for_pc(value.head_pc)];
       reference != jit_cache_state::no_reference;
       reference = cache.inbound_chain_next[reference]) {
    const auto source = cache_entry_at_reference(reference);
    if (trace_successor_pc(source) == value.head_pc) {
      if constexpr (!std::is_void_v<Measurement>) {
        record_chain_construction(source);
      }
      if (bank_of_reference(reference) != cache.oldest_bank_id && can_chain_to(source, value)) {
        set_chain_target(source, &value);
      }
    }
  }
  mark_trace_card_compiled(pc);
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.trace_inserted(pc);
  }
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.cache_changed();
  }
  refresh_chain_requirements();
  return true;
}

template <jit_measurement_policy Measurement>
jit_trace_location jit_runtime<Measurement>::lookup_cached_trace(block_index_t block_index,
                                                                 wasm_pc_t pc) {
  assert(pc != jit_invalid_pc);
  if constexpr (!std::is_void_v<Measurement>) {
    measurements.lookup_called();
    if (module_view == nullptr) {
      return find_cached_trace_by_pc(pc);
    }
  }
  assert(module_view != nullptr && block_index < module_view->block_count &&
         module_view->blocks[block_index].head_pc == pc);
  const auto &basic_block = module_view->blocks[block_index];
  if (basic_block.jit_cache_reference == jit_cache_state::no_reference) {
    return {};
  }
  // All generations execute in place; rotation owns retirement and unlinking.
  const auto location = trace_location_from_reference(basic_block.jit_cache_reference);
  assert(location.trace_entry->head_pc == pc &&
         trace_header(*location.trace_entry)->block_index == block_index);
  return location;
}

template <jit_measurement_policy Measurement>
jit_trace_location jit_runtime<Measurement>::lookup_cached_trace(wasm_pc_t pc)
  requires(!std::is_void_v<Measurement>)
{
  assert(module_view == nullptr);
  return lookup_cached_trace(jit_invalid_block_index, pc);
}

template <jit_measurement_policy Measurement>
void jit_runtime<Measurement>::refresh_chain_requirements() {
  const auto began = !cache.code_memory.patching;
  if (began) {
    fb_jit_memory_begin(&cache.code_memory);
  }
  const auto successor = [this](const jit_trace_header *header) {
    return header == nullptr ? nullptr : chain_target_header(*header);
  };
  for (std::uint32_t i = 0; i < cache.pc_index_count; ++i) {
    auto *root = trace_header(cache_entry_at_reference(cache.pc_index[i]));
    const auto *slow = root;
    const auto *fast = root;
    bool cycle_found = false;
    for (std::uint32_t step = 0; step < jit_cache_total_entry_capacity; ++step) {
      slow = successor(slow);
      fast = successor(successor(fast));
      if (slow == nullptr || fast == nullptr) {
        break;
      }
      if (slow == fast) {
        cycle_found = true;
        break;
      }
    }

    std::uint32_t body_count = 0;
    if (!cycle_found) {
      for (const auto *current = root; current != nullptr; current = successor(current)) {
        assert(body_count < jit_cache_total_entry_capacity);
        ++body_count;
      }
    } else {
      const auto *cycle_start = root;
      const auto *meeting = slow;
      std::uint32_t prefix_count = 0;
      while (cycle_start != meeting) {
        assert(prefix_count < jit_cache_total_entry_capacity);
        cycle_start = successor(cycle_start);
        meeting = successor(meeting);
        ++prefix_count;
      }
      std::uint32_t cycle_count = 1;
      for (const auto *current = successor(cycle_start); current != cycle_start;
           current = successor(current)) {
        assert(cycle_count < jit_cache_total_entry_capacity);
        ++cycle_count;
      }
      body_count = prefix_count + cycle_count;
    }
    assert(body_count != 0 && body_count <= jit_cache_total_entry_capacity);

    std::uint32_t chain_words = 0;
    const auto *current = root;
    for (std::uint32_t body = 0; body < body_count; ++body) {
      assert(current != nullptr);
      chain_words = std::max(chain_words, static_cast<std::uint32_t>(current->stack_words));
      current = successor(current);
    }
    root->chain_body_count = static_cast<jit_body_count_t>(body_count);
    root->chain_words = static_cast<jit_stack_words_t>(chain_words);
  }
  if (began) {
    fb_jit_memory_commit(&cache.code_memory);
  }
}
} // namespace fireball

namespace fireball {
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
  case 0x23:
  case 0x24:
  case 0x3f:
    return 4;
  case 0x0c:
  case 0x0d:
    return 5;
  case 0x28:
  case 0x2c:
  case 0x2d:
  case 0x2e:
  case 0x2f:
  case 0x36:
  case 0x3a:
  case 0x3b:
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
    break;
  }
  if ((op >= 0x28 && op <= 0x3e) || (op >= 0x50 && op <= 0x66) || op == 0x69 ||
      (op >= 0x79 && op <= 0x7b) || (op >= 0x7f && op <= 0x91) || (op >= 0x96 && op <= 0x9f) ||
      (op >= 0xa4 && op <= 0xc4)) {
    return 3;
  }
  return -8;
}
constexpr auto opcode_benefit_table = [] {
  std::array<std::uint8_t, 128> table{};
  for (std::uint32_t op = 0; op < 256; ++op) {
    table[op / 2] |= (opcode_benefit_value(op) & 15) << (4 * (op % 2));
  }
  return table;
}();
/** @brief 検証済み命令のLEB128を一度読み、下位32bitと次の位置を求める。
 * @pre Loaderが値幅、終端とブロック範囲を検証済みである。
 */
std::uint32_t read_score_operand(const std::uint8_t *code, [[maybe_unused]] code_offset_t end,
                                 code_offset_t &offset) {
  std::uint32_t operand = 0;
  for (std::uint32_t byte_index = 0;; ++byte_index) {
    assert(byte_index < 10 && offset < end);
    const auto byte = code[offset++];
    if (byte_index < 5) {
      operand |= static_cast<std::uint32_t>(byte & 0x7f) << (byte_index * 7);
    }
    if ((byte & 0x80) == 0) {
      return operand;
    }
  }
}

std::int64_t score_block(const jit_wasm_block &block) {
  assert(block.offset <= block.code.bytes && block.byte_span <= block.code.bytes - block.offset &&
         (block.byte_span == 0 || block.code.data != nullptr));
  std::int64_t score = 0;
  auto offset = block.offset;
  const auto end = offset + block.byte_span;
  while (offset < end) {
    const auto opcode = block.code.data[offset++];
    const auto benefit = (opcode_benefit_table[opcode / 2] >> (4 * (opcode % 2))) & 15;
    score += benefit < 8 ? benefit : benefit - 16;
    if (opcode == 0x02 || opcode == 0x03 || opcode == 0x04 || opcode == 0x0c || opcode == 0x0d ||
        opcode == 0x10 || (opcode >= 0x20 && opcode <= 0x24) || opcode == 0x41 || opcode == 0x42 ||
        opcode == 0x3f || opcode == 0x40) {
      read_score_operand(block.code.data, end, offset);
    } else if (opcode == 0x11 || (opcode >= 0x28 && opcode <= 0x3e)) {
      read_score_operand(block.code.data, end, offset);
      read_score_operand(block.code.data, end, offset);
    } else if (opcode == 0x43 || opcode == 0x44) {
      offset += opcode == 0x43 ? 4 : 8;
    } else if (opcode == 0x0e) {
      const auto label_count = read_score_operand(block.code.data, end, offset);
      assert(label_count < end - offset);
      for (std::uint32_t label = 0; label <= label_count; ++label) {
        read_score_operand(block.code.data, end, offset);
      }
    } else if (opcode == 0xfc) {
      const auto subopcode = read_score_operand(block.code.data, end, offset);
      assert(subopcode <= 7 || subopcode == 10 || subopcode == 11);
      if (subopcode == 10) {
        read_score_operand(block.code.data, end, offset);
        read_score_operand(block.code.data, end, offset);
      } else if (subopcode == 11) {
        read_score_operand(block.code.data, end, offset);
      }
    }
  }
  assert(offset == end);
  return score;
}
std::uint64_t module_code_bytes(const fireball_wasm_module_execution_view_native *view) {
  std::uint64_t bytes = 0;
  for (std::uint32_t i = view->imported_function_count; i < view->function_count; ++i) {
    const auto &f = view->functions[i];
    const auto end = static_cast<std::uint64_t>(f.code_pc_offset) + f.code_size;
    if (end > bytes) {
      bytes = end;
    }
  }
  return bytes;
}
} // namespace fireball

namespace fireball {
template <jit_measurement_policy Measurement>
int jit_provider_adapter<Measurement>::fb_jit_runtime_flush(runtime_type *runtime) {
  for (std::uint32_t id = 0; id < fireball::jit_cache_bank_count; ++id) {
    auto &b = runtime->cache.banks[id];
    for (std::uint32_t i = 0; i < b.entry_count; ++i) {
      const auto value = b.entries[i];
      if (value.resident() && runtime->trace_header(value)->chain_target_address != 0) {
        runtime->set_chain_target(value, nullptr);
      }
    }
    runtime->retire_cache_bank(id);
  }
  if constexpr (!std::is_void_v<Measurement>) {
    runtime->measurements.cache_changed();
  }
  return 1;
}

template <jit_measurement_policy Measurement>
int jit_provider_adapter<Measurement>::fb_jit_runtime_bind_cards(
    runtime_type *runtime, std::uint8_t *states, [[maybe_unused]] std::uint32_t state_bytes,
    std::uint8_t *dirty, std::uint32_t dirty_bytes, std::uint32_t cards, std::uint32_t shift,
    std::uint32_t *cursor, std::uint32_t units, std::uint32_t scan_bytes) {
  assert(shift < 32 && units > 0 && scan_bytes > 0 && cursor != nullptr &&
         state_bytes == (static_cast<std::uint64_t>(cards) + 3) / 4 &&
         dirty_bytes == (static_cast<std::uint64_t>(cards) + 7) / 8 &&
         (cards == 0 || (states != nullptr && dirty != nullptr)) &&
         (dirty_bytes == 0 ? *cursor == 0 : *cursor < dirty_bytes));
  runtime->hotspots.card_states = states;
  runtime->hotspots.dirty_cards = dirty;
  runtime->hotspots.card_count = cards;
  runtime->hotspots.card_shift = shift;
  runtime->hotspots.dirty_mask_bytes = dirty_bytes;
  runtime->hotspots.aging_cursor = cursor;
  runtime->hotspots.aging_step_units = units;
  runtime->hotspots.aging_scan_bytes = scan_bytes;
  runtime->provider.card_states = states;
  runtime->provider.card_count = cards;
  runtime->provider.card_shift = shift;
  return 1;
}

template <jit_measurement_policy Measurement>
int jit_provider_adapter<Measurement>::fb_jit_runtime_bind_profile(
    runtime_type *runtime, typename runtime_type::profile_type p) {
  assert(p.history_capacity > 0 && p.history_capacity <= runtime->hotspots.history_records.size() &&
         p.queue_capacity > 0 && p.queue_capacity <= runtime->hotspots.compile_queue_items.size() &&
         p.mask_bytes == runtime->hotspots.dirty_mask_bytes &&
         (runtime->hotspots.card_count == 0 || p.trackable != nullptr) && p.enabled <= 1 &&
         jit_card_count(p.code_bytes, runtime->hotspots.card_shift) ==
             runtime->hotspots.card_count);
  if constexpr (!std::is_void_v<Measurement>) {
    assert(p.compiler_enabled <= 1 && (p.block_count == 0 || p.blocks != nullptr));
    assert(p.block_count == 0 ||
           std::adjacent_find(p.blocks, p.blocks + p.block_count,
                              [](const jit_wasm_block &left, const jit_wasm_block &right) {
                                return left.head_pc >= right.head_pc;
                              }) == p.blocks + p.block_count);
  }
  runtime->hotspots.observation_history = {runtime->hotspots.history_records.data(),
                                           p.history_capacity, 0, 0};
  runtime->hotspots.compile_queue = {runtime->hotspots.compile_queue_items.data(), p.queue_capacity,
                                     0};
  runtime->hotspots.configuration = p;
  if (p.mask_bytes != 0) {
    std::memset(p.trackable, 0, p.mask_bytes);
  }
  std::uint32_t block_count = 0;
  if constexpr (!std::is_void_v<Measurement>) {
    block_count =
        runtime->module_view == nullptr ? p.block_count : runtime->module_view->block_count;
  } else {
    block_count = runtime->module_view->block_count;
  }
  for (std::uint32_t i = 0; i < block_count; ++i) {
    fireball::jit_wasm_block storage{};
    const fireball::jit_wasm_block *input = nullptr;
    if constexpr (!std::is_void_v<Measurement>) {
      input = runtime->module_view == nullptr ? p.blocks + i
                                              : runtime->make_compiler_block_view(i, storage);
    } else {
      input = runtime->make_compiler_block_view(i, storage);
    }
    assert(input != nullptr);
    const auto &block = *input;
    const auto card = jit_card_index(p.code_bytes, runtime->hotspots.card_shift, block.head_pc);
    assert(card >= 0 && static_cast<std::uint64_t>(card) < runtime->hotspots.card_count);
    std::int64_t score = 0;
    if constexpr (!std::is_void_v<Measurement>) {
      score = runtime->module_view == nullptr ? block.jit_score : fireball::score_block(block);
    } else {
      score = fireball::score_block(block);
    }
    if (block.byte_span != 0 && block.byte_span >= p.min_trace_bytes &&
        score >= p.candidate_threshold) {
      p.trackable[card / 8] |= static_cast<std::uint8_t>(1u << (card % 8));
    }
  }
  if constexpr (!std::is_void_v<Measurement>) {
    runtime->measurements.profile_bound();
  }
  if constexpr (!std::is_void_v<Measurement>) {
    runtime->measurements.mask_changed();
  }
  return 1;
}

template <jit_measurement_policy Measurement>
int jit_provider_adapter<Measurement>::fb_jit_runtime_compile(runtime_type *runtime) {
  return runtime->compile_queued_candidates();
}

template <jit_measurement_policy Measurement>
int jit_provider_adapter<Measurement>::finish_boundary(runtime_type *runtime, int yielded) {
  runtime->analyze_observation_history(yielded != 0);
  return 1;
}

template <jit_measurement_policy Measurement>
void jit_provider_adapter<Measurement>::fb_jit_runtime_close(runtime_type *runtime) {
  runtime->invalidate_block_cache_references();
  runtime->~runtime_type();
}

template <jit_measurement_policy Measurement>
int jit_provider_adapter<Measurement>::lookup_runtime_trace(std::uintptr_t owner,
                                                            block_index_t block_index, wasm_pc_t pc,
                                                            fb_native_jit_trace *output) {
  assert(owner != 0 && output != nullptr);
  auto &runtime = *reinterpret_cast<runtime_type *>(owner);
  if constexpr (!std::is_void_v<Measurement>) {
    runtime.record_runtime_transition(block_index, pc);
  }
  assert(runtime.hotspots.card_states != nullptr && runtime.hotspots.card_shift < 32);
  [[maybe_unused]] const auto card = pc >> runtime.hotspots.card_shift;
  assert(card < runtime.hotspots.card_count);
  assert(runtime.card_state(card) == 3);
  const auto start_location = runtime.lookup_cached_trace(block_index, pc);
  auto *start = start_location.trace_entry;
  if (start == nullptr) {
    return 0;
  }
  const auto *start_header = runtime.trace_header(*start);
  assert(start_header->const_rel_offset >= fireball::kTraceBoundaryMetadataBytes);
  assert(start_header->const_rel_offset <= start_header->blob_bytes);
  assert(start_header->chain_body_count != 0);
  const auto *rel = reinterpret_cast<const std::uint8_t *>(
      reinterpret_cast<const std::uint8_t *>(start_header) + start_header->const_rel_offset);
  *output = {runtime.trace_entry_address(*start),
             rel,
             start_header->frame_depth,
             start_header->chain_words,
             start_header->chain_body_count,
             static_cast<std::uint8_t>(start_header->exit_kind),
             reinterpret_cast<std::uintptr_t>(start_header)};
  return 1;
}

template <jit_measurement_policy Measurement>
int jit_provider_adapter<Measurement>::observe_runtime_block(std::uintptr_t owner,
                                                             block_index_t block_index,
                                                             wasm_pc_t pc) {
  assert(owner != 0);
  auto &runtime = *reinterpret_cast<runtime_type *>(owner);
  if constexpr (!std::is_void_v<Measurement>) {
    runtime.record_runtime_transition(block_index, pc);
  }
  if (runtime.hotspots.configuration.enabled == 0 || !runtime.is_profiled_pc(pc)) {
    return 0;
  }
  runtime.record_block_visit(block_index, pc);
  return 1;
}

template <jit_measurement_policy Measurement>
int jit_provider_adapter<Measurement>::finish_runtime_boundary(std::uintptr_t owner,
                                                               std::uint32_t status) {
  assert(owner != 0);
  auto *runtime = reinterpret_cast<runtime_type *>(owner);
  return finish_boundary(runtime, status == 5);
}

template <jit_measurement_policy Measurement>
std::size_t jit_provider_adapter<Measurement>::workspace_size(
    const fireball_wasm_module_execution_view_native *view) {
  const auto cards = jit_card_count(fireball::module_code_bytes(view), FB_CONF_JIT_CARD_SHIFT);
  assert(cards >= 0);
  return jit_api::runtime_function_table_bytes() + (cards + 3) / 4 +
         2 * ((cards + 7) / 8);
}

template <jit_measurement_policy Measurement>
std::size_t jit_provider_adapter<Measurement>::data_region_size(
    const fireball_wasm_module_execution_view_native *view) {
  const auto alignment = fireball::executable_region_alignment();
  const auto bytes = sizeof(runtime_type) + workspace_size(view);
  return (bytes + alignment - 1) & ~(alignment - 1);
}

template <jit_measurement_policy Measurement>
std::size_t jit_provider_adapter<Measurement>::fb_jit_plugin_alignment() {
  return fireball::executable_region_alignment();
}

template <jit_measurement_policy Measurement>
std::size_t jit_provider_adapter<Measurement>::fb_jit_plugin_required_bytes(
    const fireball_wasm_module_execution_view_native *view) {
  assert(view != nullptr);
  const auto alignment = fb_jit_plugin_alignment();
  return data_region_size(view) +
         ((FB_CONF_JIT_CACHE_REGION_BYTES + alignment - 1) & ~(alignment - 1));
}

template <jit_measurement_policy Measurement>
typename jit_provider_adapter<Measurement>::runtime_type *
jit_provider_adapter<Measurement>::fb_jit_plugin_init(
    std::uint8_t *region, [[maybe_unused]] std::size_t bytes,
    const fireball_wasm_module_execution_view_native *view, std::uint32_t module_id,
    const fireball_native_cps_handler *call_handlers) {
  assert(view != nullptr && region != nullptr && bytes == fb_jit_plugin_required_bytes(view) &&
         reinterpret_cast<std::uintptr_t>(region) % fb_jit_plugin_alignment() == 0);
  assert(call_handlers != nullptr && call_handlers[0] != nullptr && call_handlers[1] != nullptr);
  auto *runtime = ::new (region) runtime_type{};
  if constexpr (!std::is_void_v<Measurement>) {
    runtime->measurements.owns_memory = false;
  }
  runtime->provider = {reinterpret_cast<std::uintptr_t>(runtime),
                       nullptr,
                       0,
                       0,
                       {},
                       lookup_runtime_trace,
                       observe_runtime_block,
                       finish_runtime_boundary};
  fireball::borrow_executable_region(&runtime->cache.code_memory, region + data_region_size(view),
                                     FB_CONF_JIT_CACHE_REGION_BYTES);
  auto *workspace = region + sizeof(runtime_type);
  const auto *function_table =
      jit_api::initialize_runtime_function_table(workspace, call_handlers);
  fireball::initialize_common_code(&runtime->cache.code_memory, function_table);
  fb_jit_memory_commit(&runtime->cache.code_memory);
  constexpr code_offset_t offsets[] = {FB_CONF_JIT_CACHE_ACTIVE_OFFSET_BYTES,
                                       FB_CONF_JIT_CACHE_WARM_OFFSET_BYTES,
                                       FB_CONF_JIT_CACHE_OLDEST_OFFSET_BYTES};
  for (std::uint32_t i = 0; i < fireball::jit_cache_bank_count; ++i) {
    runtime->cache.banks[i].capacity_bytes = FB_CONF_JIT_CACHE_BANK_CAPACITY_BYTES;
    runtime->cache.banks[i].entry_capacity = fireball::jit_cache_bank_entry_capacity;
    runtime->cache.banks[i].region_offset_bytes = offsets[i];
  }
  runtime->module_view = view;
  const auto code_bytes = fireball::module_code_bytes(view);
  const auto cards = static_cast<std::uint32_t>(jit_card_count(code_bytes, FB_CONF_JIT_CARD_SHIFT));
  const auto state_bytes = (cards + 3) / 4;
  const auto mask_bytes = (cards + 7) / 8;
  auto *card_workspace = workspace + jit_api::runtime_function_table_bytes();
  const auto card_workspace_size = (state_bytes + 2 * mask_bytes);
  std::memset(card_workspace, 0, card_workspace_size);
  auto *dirty = card_workspace + state_bytes;
  auto *mask = dirty + mask_bytes;
  typename runtime_type::profile_type profile{};
  static_cast<jit_profile &>(profile) = {mask,
                                         mask_bytes,
                                         module_id,
                                         1,
                                         9,
                                         1u << FB_CONF_JIT_CARD_SHIFT,
                                         code_bytes,
                                         FB_CONF_JIT_HISTORY_CAPACITY,
                                         FB_CONF_JIT_COMPILE_QUEUE_CAPACITY};
  fb_jit_runtime_bind_cards(runtime, card_workspace, state_bytes, dirty, mask_bytes, cards,
                            FB_CONF_JIT_CARD_SHIFT, &runtime->hotspots.aging_cursor_storage,
                            FB_CONF_JIT_AGING_STEP_UNITS, FB_CONF_JIT_AGING_STEP_SCAN_BYTES);
  fb_jit_runtime_bind_profile(runtime, profile);
  return runtime;
}

template <jit_measurement_policy Measurement>
const fb_native_jit_provider *
jit_provider_adapter<Measurement>::fb_jit_plugin_provider(runtime_type *runtime) {
  return runtime == nullptr ? nullptr : &runtime->provider;
}
using configured_jit_provider_adapter = jit_provider_adapter<FB_JIT_RUNTIME_MEASUREMENT>;
template struct jit_runtime<FB_JIT_RUNTIME_MEASUREMENT>;
template struct jit_provider_adapter<FB_JIT_RUNTIME_MEASUREMENT>;
} // namespace fireball

// Each library selects its measurement type before including this shared C
// facade.
namespace {
using provider_adapter = fireball::configured_jit_provider_adapter;

provider_adapter::runtime_type *runtime_from_provider(const fb_native_jit_provider *provider) {
  assert(provider != nullptr && provider->owner != 0);
  return reinterpret_cast<provider_adapter::runtime_type *>(provider->owner);
}
} // namespace

extern "C" int fb_jit_runtime_flush(const fb_native_jit_provider *provider) {
  auto *runtime = runtime_from_provider(provider);
  return provider_adapter::fb_jit_runtime_flush(runtime);
}

extern "C" int fb_jit_runtime_compile(const fb_native_jit_provider *provider) {
  auto *runtime = runtime_from_provider(provider);
  return provider_adapter::fb_jit_runtime_compile(runtime);
}

extern "C" void fb_jit_runtime_close(const fb_native_jit_provider *provider) {
  auto *runtime = runtime_from_provider(provider);
  provider_adapter::fb_jit_runtime_close(runtime);
}

extern "C" std::size_t fb_jit_plugin_alignment() {
  return provider_adapter::fb_jit_plugin_alignment();
}

extern "C" std::size_t
fb_jit_plugin_required_bytes(const fireball_wasm_module_execution_view_native *view) {
  return provider_adapter::fb_jit_plugin_required_bytes(view);
}

extern "C" const fb_native_jit_provider *
fb_jit_plugin_init(std::uint8_t *region, std::size_t bytes,
                   const fireball_wasm_module_execution_view_native *view,
                   std::uint32_t module_id,
                   const fireball_native_cps_handler *call_handlers) {
  auto *runtime =
      provider_adapter::fb_jit_plugin_init(region, bytes, view, module_id, call_handlers);
  return provider_adapter::fb_jit_plugin_provider(runtime);
}
