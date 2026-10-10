#ifndef FIREBALL_PYSIM_STENCILS_X64_HXX
#define FIREBALL_PYSIM_STENCILS_X64_HXX

#include "asm_stencils.hxx"
#include "stencils.hxx"

#include <array>
#include <cstddef>
#include <cstdint>

namespace fireball::pysim::jit {

enum class stencil_image : std::uint8_t { generated, assembly };

struct stencil_branch_fixup_view {
  std::size_t instruction_offset = 0;
  std::uint8_t instruction_size = 0;
  std::size_t target_offset = 0;
};

struct stencil_view {
  const char *symbol = nullptr;
  std::size_t code_offset = 0;
  std::size_t size = 0;
  std::size_t next_slot_offset = generated::no_link_slot;
  std::size_t abort_slot_offset = generated::no_link_slot;
  std::size_t start_slot_offset = generated::no_link_slot;
  std::size_t exit_slot_offset = generated::no_link_slot;
  std::size_t branch_offset = 0;
  std::size_t branch_count = 0;
  std::uint16_t code_id = 0;
  std::size_t patch_slot_offset = generated::no_link_slot;
  std::uint8_t patch_slot_width = 0;
  std::size_t instruction_slot_offset = generated::no_link_slot;
  std::uint8_t instruction_slot_width = 0;
  const std::uint8_t *image_start = nullptr;
  stencil_image image = stencil_image::generated;

  constexpr std::size_t branch_table_size() const {
    return image == stencil_image::generated
               ? generated::stencil_branch_fixups.size()
               : asm_generated::stencil_branch_fixups.size();
  }

  constexpr bool branch_range_valid() const {
    return branch_offset <= branch_table_size() &&
           branch_count <= branch_table_size() - branch_offset;
  }

  constexpr stencil_branch_fixup_view branch_fixup(std::size_t index) const {
    const auto table_index = branch_offset + index;
    if (index >= branch_count || !branch_range_valid())
      return {};
    if (image == stencil_image::generated) {
      const auto &branch = generated::stencil_branch_fixups[table_index];
      return {branch.instruction_offset, branch.instruction_size,
              branch.target_offset};
    }
    const auto &branch = asm_generated::stencil_branch_fixups[table_index];
    return {branch.instruction_offset, branch.instruction_size,
            branch.target_offset};
  }
};

constexpr stencil_view generated_stencil(generated::stencil_range range) {
  return {range.symbol,
          range.code_offset,
          range.code_size,
          range.next_slot_offset,
          range.abort_slot_offset,
          range.start_slot_offset,
          range.exit_slot_offset,
          range.branch_offset,
          range.branch_count,
          range.code_id,
          range.patch_slot_offset,
          range.patch_slot_width,
          range.instruction_slot_offset,
          range.instruction_slot_width,
          generated::fb_jit_stencils_start.data(),
          stencil_image::generated};
}

constexpr stencil_view generated_stencil(asm_generated::stencil_range range) {
  return {range.symbol,
          range.code_offset,
          range.code_size,
          range.next_slot_offset,
          range.abort_slot_offset,
          range.start_slot_offset,
          range.exit_slot_offset,
          range.branch_offset,
          range.branch_count,
          range.code_id,
          range.patch_slot_offset,
          range.patch_slot_width,
          range.instruction_slot_offset,
          range.instruction_slot_width,
          asm_generated::fb_jit_stencils_start.data(),
          stencil_image::assembly};
}

} // namespace fireball::pysim::jit

#endif
