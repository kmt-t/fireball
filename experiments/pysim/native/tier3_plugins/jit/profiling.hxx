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
};
struct jit_compile_queue {
  std::uint32_t* pcs;
  std::uint32_t capacity, count;
};

std::int64_t queue_operation(jit_compile_queue* queue, std::uint32_t operation, std::uint32_t pc);
}  // namespace fireball
#endif
