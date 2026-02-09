/**
 * vSoC (Virtual System-on-Chip) Manager Implementation.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#include "../../inc/vsoc/vsoc.hxx"

namespace fireball::vsoc {

runtime::runtime(const instance_config& config)
    : config_(config), context_{}, harness_{} {
  context_.state = execution_state::idle;
  context_.pc = 0;
  context_.irq_flags = 0;
}

runtime::runtime(const instance_config& config, const harness& harness)
    : config_(config), context_{}, harness_(harness) {
  context_.state = execution_state::idle;
  context_.pc = 0;
  context_.irq_flags = 0;
}

status runtime::load(binary_view bin) {
  (void)bin;
  context_.state = execution_state::ready;
  return status::success;
}

status runtime::step() {
  if (context_.state != execution_state::ready &&
      context_.state != execution_state::running) {
    return status::invalid_argument;
  }

  context_.state = execution_state::running;
  // TODO: Implement execution loop

  context_.state = execution_state::ready;
  return status::success;
}

void runtime::stop() { context_.state = execution_state::idle; }

status runtime::reset() {
  context_.state = execution_state::idle;
  context_.pc = 0;
  context_.irq_flags = 0;
  return status::success;
}

void runtime::notify_interrupt(std::uint32_t irq_id) {
  context_.irq_flags |= (1U << irq_id);
}

} // namespace fireball::vsoc
