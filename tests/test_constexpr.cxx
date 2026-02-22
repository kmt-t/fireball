#include <gen/jit.hxx>
#include <array>

using namespace fireball;

// Mock implementation of jit_harness 
struct MockHarness {
    constexpr jit_setup_record config() noexcept { return {}; }
    constexpr hotspot_detector_unit* detector() noexcept { return nullptr; }
    constexpr jit_entry_index* index() noexcept { return nullptr; }
    constexpr patch_engine_unit* engine() noexcept { return nullptr; }
};

static_assert(jit_harness<MockHarness>, "Mock does not satisfy concept");

// Compile time computation test using concept template
template <jit_harness T>
constexpr auto generate_config(T& harness) {
    return harness.config();
}

#include <stdio.h>

int main() {
    printf("ARM Cortex-M33 Booted via Semihosting\n");
    MockHarness harness;
    [[maybe_unused]] constexpr auto config = generate_config(harness);
    
    // Simulate hotspot detector check to verify C++ generation for normal resources
    hotspot_detector_unit detector;
    constexpr auto val = detector.get_card_state(0x1000);
    static_assert(val == 0, "Constexpr evaluation failed");
    
    return 0;
}
