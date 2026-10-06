#pragma once

// Each library selects its measurement type before including this shared C
// facade.
namespace {
using Api = fireball::configured_jit_plugin;

Api::Runtime *
runtime_from_extension(const fb_native_execution_extension *extension) {
  return extension == nullptr || extension->owner == 0
             ? nullptr
             : reinterpret_cast<Api::Runtime *>(extension->owner);
}
} // namespace

extern "C" int
fb_jit_runtime_flush(const fb_native_execution_extension *extension) {
  auto *runtime = runtime_from_extension(extension);
  return runtime == nullptr ? 0 : Api::fb_jit_runtime_flush(runtime);
}

extern "C" int
fb_jit_runtime_compile(const fb_native_execution_extension *extension,
                       std::uint32_t budget) {
  auto *runtime = runtime_from_extension(extension);
  return runtime == nullptr ? 0 : Api::fb_jit_runtime_compile(runtime, budget);
}

extern "C" void
fb_jit_runtime_close(const fb_native_execution_extension *extension) {
  auto *runtime = runtime_from_extension(extension);
  if (runtime != nullptr)
    Api::fb_jit_runtime_close(runtime);
}

extern "C" int fb_jit_runtime_run(const fb_native_dispatch_call *input,
                                  fb_native_result *result) {
  return Api::fb_jit_runtime_run(input, result);
}

extern "C" std::size_t fb_jit_plugin_alignment() {
  return Api::fb_jit_plugin_alignment();
}

extern "C" std::size_t fb_jit_plugin_required_bytes(
    const fireball_wasm_module_execution_view_native *view) {
  return Api::fb_jit_plugin_required_bytes(view);
}

extern "C" const fb_native_execution_extension *fb_jit_plugin_init(
    std::uint8_t *region, std::size_t bytes,
    const fireball_wasm_module_execution_view_native *view,
    std::uint32_t module_id,
    int (*dispatch)(const fb_native_dispatch_call *, fb_native_result *)) {
  auto *runtime =
      Api::fb_jit_plugin_init(region, bytes, view, module_id, dispatch);
  return runtime == nullptr ? nullptr : &runtime->extension;
}
