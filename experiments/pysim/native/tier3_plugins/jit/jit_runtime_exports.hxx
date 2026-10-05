#pragma once

// Each library selects its measurement type before including this shared C facade.
namespace {
using Api = fireball::configured_jit_plugin;
}

extern "C" int fb_jit_runtime_flush(fireball::JitRuntime* c) {
  return Api::fb_jit_runtime_flush(reinterpret_cast<Api::Runtime*>(c));
}

extern "C" int fb_jit_runtime_compile(fireball::JitRuntime* c, std::uint32_t budget) {
  return Api::fb_jit_runtime_compile(reinterpret_cast<Api::Runtime*>(c), budget);
}

extern "C" void fb_jit_runtime_close(fireball::JitRuntime* c) {
  Api::fb_jit_runtime_close(reinterpret_cast<Api::Runtime*>(c));
}

extern "C" int fb_jit_runtime_run(const fb_native_dispatch_call* input, fb_native_result* result) {
  return Api::fb_jit_runtime_run(input, result);
}

extern "C" std::size_t fb_jit_plugin_alignment() {
  return Api::fb_jit_plugin_alignment();
}

extern "C" std::size_t fb_jit_plugin_required_bytes(
    const fireball_wasm_module_execution_view_native* view) {
  return Api::fb_jit_plugin_required_bytes(view);
}

extern "C" fireball::JitRuntime* fb_jit_plugin_init(std::uint8_t* region, std::size_t bytes,
    const fireball_wasm_module_execution_view_native* view,
    std::uint32_t hotspots, std::uint32_t module_id,
    int (*dispatch)(const fb_native_dispatch_call*, fb_native_result*),
    std::uint8_t* compile_byte_storage, std::size_t compile_byte_storage_bytes,
    std::int16_t* compile_stack_locations, std::size_t compile_stack_capacity) {
  return reinterpret_cast<fireball::JitRuntime*>(
      Api::fb_jit_plugin_init(region, bytes, view, hotspots, module_id, dispatch,
                              compile_byte_storage, compile_byte_storage_bytes,
                              compile_stack_locations, compile_stack_capacity));
}
