#pragma once

#include <array>
#include <cstdint>
#include <string_view>

namespace fireball {
enum class jit_chain_reason : std::uint32_t {
  linked,
  backedge,
  self_loop,
  loop_target,
  result_words,
  oldest_bank,
  frame_depth_mismatch,
  successor_not_resident,
  successor_block_index_mismatch,
  unsupported_control_flow,
  count,
};

inline constexpr std::array<std::string_view, static_cast<std::size_t>(jit_chain_reason::count)>
    jit_chain_reason_names{
        "linked",
        "backedge",
        "self_loop",
        "loop_target",
        "result_words",
        "oldest_bank",
        "frame_depth_mismatch",
        "successor_not_resident",
        "successor_block_index_mismatch",
        "unsupported_control_flow",
    };
} // namespace fireball
