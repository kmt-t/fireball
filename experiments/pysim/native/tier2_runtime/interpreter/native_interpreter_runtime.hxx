#pragma once
/** @file @brief Native Interpreterの手続き宣言。実装は単独の翻訳単位に置く。 */
#include "../../../tier2_runtime/abi/native_abi.hxx"
#include <type_traits>

namespace fireball::interpreter {
template <bool WithExtension>
using dispatch_input_t =
    std::conditional_t<WithExtension, fb_native_dispatch_extension_call, fb_native_dispatch_call>;

/**
 * @brief 実行境界まで命令をディスパッチする。
 * @param input スタック、モジュール、任意のJIT providerを含む非所有ビュー。
 * @param result 実行状態とtrapコードの出力先。
 * @pre inputとresult、およびinputの内部領域は有効である。
 * @return ABI呼び出しの成功を示す1。WASMの終了状態はresultへ格納する。
 * @details 計測を含む具体的な組合せは実装モジュールで明示的に生成する。
 */
template <bool CollectStats, bool WithJit, bool WithExtension, bool ReturnAtBlockBoundary = false,
          typename Debugger = void, typename ResultType = fb_native_result>
int run_native_dispatch_abi(const dispatch_input_t<WithExtension> *input, ResultType *result);

/**
 * @brief 一命令または制御命令を実行する。
 * @pre callとoutput、およびcallの指定する領域は有効である。
 * @param direct_control 制御命令だけを実行する場合はtrue。
 * @return ABI呼び出しの成功を示す1。実行状態とtrapはoutputに格納する。
 */
int run_native_step_abi(const fb_native_step_call *call, fb_native_result *output,
                        bool direct_control);
} // namespace fireball::interpreter
