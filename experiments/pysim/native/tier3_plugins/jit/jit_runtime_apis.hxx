#pragma once
/** @file @brief JIT専用Runtime APIカタログの宣言。 */
#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include <cstddef>
#include <cstdint>

namespace fireball::jit_api {
/** @brief JIT stencilから呼ぶ、一操作だけを実行する手続き型。 */
using jit_runtime_api_pointer = bool (*)(fireball_execution_context_native *, std::uint32_t *&,
                                         std::uint32_t *&, const std::uint8_t *);
/** @brief x64 function-pointer bits stored contiguously for native stencils. */
using jit_function_table_entry = std::uint8_t[sizeof(std::uintptr_t)];
/** @brief 読み取り専用の静的API表の先頭を借用する。 */
const jit_runtime_api_pointer *runtime_api_table_data();
/** @brief JIT APIとInterpreter CPS呼出しhandlerを連続配置する表のサイズを返す。 */
std::size_t runtime_function_table_bytes();
/** @brief 借用領域にAPI表とCPS handlerを配置し、その先頭を返す。 */
const void *initialize_runtime_function_table(void *storage,
                                              const fireball_native_cps_handler *handlers);
/** @brief call opcodeに対応する、共通関数表内のCPS handler添字を返す。 */
std::uint8_t jit_call_handler_slot(bool indirect);
} // namespace fireball::jit_api
