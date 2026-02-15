/**
 * vSoC (Virtual System-on-Chip) Manager.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <fireball.hxx>

namespace fireball::vsoc {

enum class execution_state : std::uint32_t {
  IDLE,
  READY,
  RUNNING,
  ERROR,
};

struct runtime_instance_config {
  bool jit_enabled;
  byte_count code_cache_size;
  guest_phys_addr ram_base;
  byte_count ram_size;
  guest_phys_addr vmmio_base;
  entry_count vmmio_max_regions;
  guest_phys_addr shm_base;
  byte_count shm_size;
};

struct execution_context {
  execution_state state;
  interrupt_flags irq_flags;
  wasm_pc pc;
};

template <typename Harness, const runtime_instance_config& Config>
class runtime {
public:
  runtime();
  ~runtime() = default;

  runtime(const runtime&) = delete;
  runtime& operator=(const runtime&) = delete;

  operation_result load(binary_view bin);

  operation_result run();

  operation_result step();

  void notify_interrupt(std::uint32_t irq_id);

  execution_state get_state() const { return context_.state; }
  wasm_pc get_pc() const { return context_.pc; }
  
  static constexpr const runtime_instance_config& config() { return Config; }

private:
  using harness_type = Harness;

  execution_context context_;
  harness_type harness_;
};

} // namespace fireball::vsoc
