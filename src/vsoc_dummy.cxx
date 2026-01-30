#include "../inc/vsoc/vsoc.hxx"
#include <vector>

// Dummy main to verify compilation
int main() {
    vsoc_config_t config = {
        .jit_enabled = false,
        .jit_cache_size = FB_CONF_JIT_CACHE_SIZE,
        .ram_base_addr = FB_CONF_GUEST_RAM_BASE,
        .ram_size_bytes = FB_CONF_GUEST_RAM_SIZE,
        .vmmio_base_addr = FB_CONF_VMMIO_BASE,
        .vmmio_max_regions = FB_CONF_VMMIO_MAX_REGIONS
    };
    vsoc_runtime runtime(config);
    
    std::vector<uint8_t> dummy_wasm = {0x00, 0x61, 0x73, 0x6d};
    runtime.load(dummy_wasm);
    
    runtime.step();
    return 0;
}
