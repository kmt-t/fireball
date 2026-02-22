#include <zephyr/kernel.h>
#include <gen/jit.hxx>
#include <array>
#include <stdio.h>

using namespace fireball;

// Mock implementation of jit_harness 
struct MockHarness {
    constexpr jit_setup_record config() noexcept { return {}; }
    constexpr hotspot_detector_unit* detector() noexcept { return nullptr; }
    constexpr jit_entry_index* index() noexcept { return nullptr; }
    constexpr patch_engine_unit* engine() noexcept { return nullptr; }
};

static_assert(jit_harness<MockHarness>, "Mock does not satisfy concept");

template <jit_harness T>
constexpr auto generate_config(T& harness) {
    return harness.config();
}

int main(void) {
    printf("Fireball Zephyr Test Booted\n");
    printf("Target Architecture: %s\n", CONFIG_ARCH);

    MockHarness harness;
    [[maybe_unused]] constexpr auto config = generate_config(harness);
    
    hotspot_detector_unit detector;
    constexpr auto val = detector.get_card_state(0x1000);
    static_assert(val == 0, "Constexpr evaluation failed");

    printf("Test completed successfully\n");
    
    return 0;
}
