#ifndef FIREBALL_PYSIM_JIT_PROFILING_HXX
#define FIREBALL_PYSIM_JIT_PROFILING_HXX
#include "jit_types.hxx"
namespace fireball {
struct jit_history_record {
  std::uint32_t module_id, pc;
};
struct jit_history {
  jit_history_record* records;
  std::uint32_t capacity, head, count;
  std::uint64_t dropped;
};
struct jit_compile_queue {
  std::uint32_t* pcs;
  std::uint32_t capacity, count;
};

int history_record(jit_history* history, std::uint32_t module_id, std::uint32_t pc);
int history_drop(jit_history* history, std::uint64_t count);
std::int64_t queue_operation(jit_compile_queue* queue, std::uint32_t operation, std::uint32_t pc);
}  // namespace fireball
#endif
