#pragma once
/** @file @brief JIT専用Runtime APIカタログの宣言。 */
#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include <cstdint>

namespace fireball::jit_api {
/** @brief JIT stencilから呼ぶ、一操作だけを実行する手続き型。 */
using jit_runtime_api_pointer = bool (*)(fireball_execution_context_native *, std::uint32_t *&,
                                         std::uint32_t *&, const std::uint8_t *);
/** @brief 読み取り専用の静的API表の先頭を借用する。 */
const jit_runtime_api_pointer *runtime_api_table_data();
} // namespace fireball::jit_api
