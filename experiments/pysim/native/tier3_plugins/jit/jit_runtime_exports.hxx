#pragma once

// Each library selects its measurement type before including this shared C
// facade.
namespace {
using Api = fireball::configured_jit_plugin;

Api::Runtime *
runtime_from_provider(const fb_native_jit_provider *provider) {
  return provider == nullptr || provider->owner == 0
             ? nullptr
             : reinterpret_cast<Api::Runtime *>(provider->owner);
}
} // namespace

extern "C" int
fb_jit_runtime_flush(const fb_native_jit_provider *provider) {
  auto *runtime = runtime_from_provider(provider);
  return runtime == nullptr ? 0 : Api::fb_jit_runtime_flush(runtime);
}

extern "C" int
fb_jit_runtime_compile(const fb_native_jit_provider *provider,
                       std::uint32_t budget) {
  auto *runtime = runtime_from_provider(provider);
  return runtime == nullptr ? 0 : Api::fb_jit_runtime_compile(runtime, budget);
}

extern "C" void
fb_jit_runtime_close(const fb_native_jit_provider *provider) {
  auto *runtime = runtime_from_provider(provider);
  if (runtime != nullptr)
    Api::fb_jit_runtime_close(runtime);
}

extern "C" std::size_t fb_jit_plugin_alignment() {
  return Api::fb_jit_plugin_alignment();
}

extern "C" std::size_t fb_jit_plugin_required_bytes(
    const fireball_wasm_module_execution_view_native *view) {
  return Api::fb_jit_plugin_required_bytes(view);
}

extern "C" const fb_native_jit_provider *fb_jit_plugin_init(
    std::uint8_t *region, std::size_t bytes,
    const fireball_wasm_module_execution_view_native *view,
    std::uint32_t module_id) {
  auto *runtime =
      Api::fb_jit_plugin_init(region, bytes, view, module_id);
  return Api::fb_jit_plugin_provider(runtime);
}
