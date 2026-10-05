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

extern "C" std::int64_t fb_jit_card_count(std::uint64_t bytes, std::uint32_t shift) {
  if (shift >= 32 || bytes > (std::uint64_t{1} << 32)) return -1;
  return static_cast<std::int64_t>((bytes + (std::uint64_t{1} << shift) - 1) >> shift);
}
extern "C" std::int64_t fb_jit_card_index(std::uint64_t bytes, std::uint32_t shift,
                                          std::uint32_t pc) {
  if (shift >= 32 || pc >= bytes) return -1;
  return pc >> shift;
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
