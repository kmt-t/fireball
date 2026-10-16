#ifndef FIREBALL_PYSIM_JIT_RUNTIME_ABI_HXX
#define FIREBALL_PYSIM_JIT_RUNTIME_ABI_HXX

#include <cstddef>
#include <cstdint>

#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include "../../../tier2_runtime/abi/native_abi.hxx"
#include "executable_memory_abi.hxx"
#include "jit_types.hxx"
#include "trace_compiler_abi.hxx"

/** @brief ホストが所有する領域へ構築するJITプラグインのC ABI。
 * @pre 引数、領域サイズ、アラインメント、providerの寿命は各入口のassert契約に従う。
 * @details 失敗を既定値へ変換しない。コンパイル対象外とcache missは正常な選択とする。
 */
extern "C" {
FB_PYSIM_ABI_EXPORT std::size_t fb_jit_plugin_alignment();
FB_PYSIM_ABI_EXPORT std::size_t
fb_jit_plugin_required_bytes(const fireball_wasm_module_execution_view_native *view);
FB_PYSIM_ABI_EXPORT const fb_native_jit_provider *
fb_jit_plugin_init(std::uint8_t *region, std::size_t bytes,
                   const fireball_wasm_module_execution_view_native *view, std::uint32_t module_id);
FB_PYSIM_ABI_EXPORT void fb_jit_runtime_close(const fb_native_jit_provider *provider);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_flush(const fb_native_jit_provider *provider);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_compile(const fb_native_jit_provider *provider);
}
#endif
