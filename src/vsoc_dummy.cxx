#include "../inc/vsoc/vsoc.hxx"
#include "../inc/fireball_config.hxx"
#include <vector>
#include <fstream>
#include <iostream>

// Dummy main to verify compilation and basic execution
int main(int argc, char** argv) {
  std::cout << "Fireball vSoC Standalone Harness starting..." << std::endl;

  static constexpr fireball::vsoc::instance_config dummy_config = {
      .jit_enabled = false,
      .jit_cache_size = FB_CONF_JIT_CACHE_SIZE,
      .ram_base_addr = FB_CONF_GUEST_RAM_BASE,
      .ram_size_bytes = FB_CONF_GUEST_RAM_SIZE,
      .vmmio_base_addr = FB_CONF_VMMIO_BASE,
      .vmmio_max_regions = FB_CONF_VMMIO_MAX_REGIONS};
  
  fireball::vsoc::runtime<dummy_config> runtime;

  if (argc > 1) {
    std::string filename = argv[1];
    std::ifstream file(filename, std::ios::binary | std::ios::ate);
    if (!file) {
      std::cerr << "Failed to open file: " << filename << std::endl;
      return 1;
    }

    std::streamsize size = file.tellg();
    file.seekg(0, std::ios::beg);

    std::vector<uint8_t> buffer(size);
    if (file.read(reinterpret_cast<char*>(buffer.data()), size)) {
      std::cout << "Loading WASM module: " << filename << " (" << size << " bytes)" << std::endl;
      fireball::status status = runtime.load(buffer);
      if (status != fireball::status::success) {
        std::cerr << "Failed to load module" << std::endl;
        return 1;
      }
    }
  } else {
    std::cout << "No WASM file provided. Using dummy module." << std::endl;
    std::vector<uint8_t> dummy_wasm = {0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00};
    runtime.load(dummy_wasm);
  }

  std::cout << "Initial State: " << static_cast<uint32_t>(runtime.get_state()) << ", PC: "
            << runtime.get_pc() << std::endl;

  std::cout << "Executing step..." << std::endl;
  fireball::status step_status = runtime.step();

  std::cout << "Step result: " << static_cast<uint32_t>(step_status) << std::endl;
  std::cout << "Final State: " << static_cast<uint32_t>(runtime.get_state()) << ", PC: "
            << runtime.get_pc() << std::endl;

  return 0;
}