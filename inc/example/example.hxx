// AUTO-GENERATED FILE - DO NOT EDIT
#pragma once

#ifndef FIREBALL_EXAMPLE_EXAMPLE_HXX_
#define FIREBALL_EXAMPLE_EXAMPLE_HXX_

#include <cstdint>

namespace fireball::example {

// API実行結果
enum class result_code : uint32_t {
  success = 0, // 成功
  fail = 1, // 失敗
  timeout = 2, // タイムアウト
};

// システム設定パラメータ
class config_params {
  uint32_t max_retry_; // 最大再試行回数
  uint32_t timeout_ms_; // タイムアウト(ms)
};

} // namespace fireball::example
#endif // FIREBALL_EXAMPLE_EXAMPLE_HXX_