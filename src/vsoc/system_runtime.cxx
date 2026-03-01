#include <vsoc.hxx>

namespace fireball {

/**
 * system_runtime implementation (Tier 2)
 */

operation_result system_runtime::initialize(vsoc_harness auto& harness) noexcept {
    loader_ = harness.loader();
    vmmio_ = harness.vmmio();

    if (loader_ == nullptr || vmmio_ == nullptr) {
        return sys_recovery_strategy::PANIC;
    }

    state_ = execution_state_category::READY;
    return {};
}

result<execution_state_category, sys_recovery_strategy> system_runtime::step() noexcept {
    if (state_ == execution_state_category::HALTED) {
        return execution_state_category::HALTED;
    }

    if (state_ == execution_state_category::TRAPPED) {
        return sys_recovery_strategy::RESTART;
    }

    state_ = execution_state_category::RUNNING;

    return state_;
}

void system_runtime::notify_interrupt(uint32_t irq_id) noexcept {
    if (vmmio_) {
        vmmio_->set_irq_bit(irq_id);
    }
}

} // namespace fireball
