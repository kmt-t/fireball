#include "profiling.hxx"

#include <algorithm>
#include <limits>

#include "jit_runtime_abi.hxx"

namespace fireball {
namespace {
bool valid_history(const jit_history* h) {
  return h != nullptr && h->records != nullptr && h->capacity > 0 && h->head < h->capacity &&
         h->count <= h->capacity;
}
}  // namespace
}  // namespace fireball
extern "C" int fb_jit_region_size(const std::uint32_t* lengths, const std::uint32_t* bases,
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
extern "C" std::int64_t fb_jit_card_count(std::uint64_t bytes, std::uint32_t shift) {
  if (shift >= 32 || bytes > (std::uint64_t{1} << 32)) return -1;
  return static_cast<std::int64_t>((bytes + (std::uint64_t{1} << shift) - 1) >> shift);
}
extern "C" std::int64_t fb_jit_card_index(std::uint64_t bytes, std::uint32_t shift,
                                          std::uint32_t pc) {
  if (shift >= 32 || pc >= bytes) return -1;
  return pc >> shift;
}
extern "C" int fb_jit_bits(std::uint8_t* data, std::uint32_t count, std::uint32_t bits,
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
int fireball::history_record(fireball::jit_history* h, std::uint32_t module, std::uint32_t pc) {
  if (!fireball::valid_history(h)) return 0;
  if (h->count == h->capacity) {
    if (h->dropped == UINT64_MAX) return 0;
    h->head = (h->head + 1) % h->capacity;
    --h->count;
    ++h->dropped;
  }
  const auto index = (static_cast<std::uint64_t>(h->head) + h->count) % h->capacity;
  h->records[index] = {module, pc};
  ++h->count;
  return 1;
}
int fireball::history_drop(fireball::jit_history* h, std::uint64_t count) {
  if (!fireball::valid_history(h) || count > UINT64_MAX - h->dropped) return 0;
  h->dropped += count;
  return 1;
}
std::int64_t fireball::queue_operation(fireball::jit_compile_queue* q, std::uint32_t op,
                                       std::uint32_t pc) {
  if (q == nullptr || q->pcs == nullptr || q->capacity == 0 || q->count > q->capacity) return -2;
  if (op == 0) {
    if (q->count == q->capacity) return -2;
    q->pcs[q->count++] = pc;
    return 1;
  }
  if (op == 1) return q->count == 0 ? -1 : static_cast<std::int64_t>(q->pcs[--q->count]);
  if (op == 2) {
    for (std::uint32_t i = 0; i < q->count; ++i)
      if (q->pcs[i] == pc) return 1;
    return 0;
  }
  if (op == 3) {
    q->count = 0;
    return 1;
  }
  return -2;
}
