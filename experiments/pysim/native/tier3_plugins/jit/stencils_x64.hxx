#ifndef FIREBALL_PYSIM_STENCILS_X64_HXX
#define FIREBALL_PYSIM_STENCILS_X64_HXX

#include <array>
#include <cstdint>

namespace fireball::pysim::jit {

inline constexpr std::array<std::uint8_t, 2> kLoadImmStencil = {0x41, 0xB9};
inline constexpr std::array<std::uint8_t, 3> kLoadLocalStencil = {0x45, 0x8B, 0x8A};
inline constexpr std::array<std::uint8_t, 3> kStoreLocalStencil = {0x45, 0x89, 0x8A};
inline constexpr std::array<std::uint8_t, 3> kStoreLocalImm32Stencil = {0x41, 0xC7, 0x82};
inline constexpr std::array<std::uint8_t, 4> kStoreTosStencil = {0x45, 0x89, 0x8C, 0x24};
inline constexpr std::array<std::uint8_t, 4> kStoreNosStencil = {0x45, 0x89, 0x9C, 0x24};
inline constexpr std::array<std::uint8_t, 4> kLoadTosStencil = {0x45, 0x8B, 0x8C, 0x24};
inline constexpr std::array<std::uint8_t, 4> kLoadNosStencil = {0x45, 0x8B, 0x9C, 0x24};
inline constexpr std::array<std::uint8_t, 3> kMoveNosFromTosStencil = {0x45, 0x89, 0xCB};
inline constexpr std::array<std::uint8_t, 3> kI32AddStencil = {0x45, 0x01, 0xD9};
inline constexpr std::array<std::uint8_t, 6> kI32SubStencil = {
    0x45, 0x29, 0xCB, 0x45, 0x89, 0xD9};
inline constexpr std::array<std::uint8_t, 4> kI32MulStencil = {0x45, 0x0F, 0xAF, 0xCB};
inline constexpr std::array<std::uint8_t, 3> kI32AndStencil = {0x45, 0x21, 0xD9};
inline constexpr std::array<std::uint8_t, 3> kI32OrStencil = {0x45, 0x09, 0xD9};
inline constexpr std::array<std::uint8_t, 3> kI32XorStencil = {0x45, 0x31, 0xD9};
inline constexpr std::array<std::uint8_t, 3> kI32XorZeroStencil = {0x45, 0x31, 0xC9};
inline constexpr std::array<std::uint8_t, 3> kI32IncStencil = {0x41, 0xFF, 0xC1};
inline constexpr std::array<std::uint8_t, 3> kI32DecStencil = {0x41, 0xFF, 0xC9};
inline constexpr std::array<std::uint8_t, 4> kI32Mul3Stencil = {0x47, 0x8D, 0x0C, 0x49};
inline constexpr std::array<std::uint8_t, 4> kI32Mul5Stencil = {0x47, 0x8D, 0x0C, 0x89};
inline constexpr std::array<std::uint8_t, 4> kI32And255Stencil = {0x45, 0x0F, 0xB6, 0xC9};
inline constexpr std::array<std::uint8_t, 4> kI32And65535Stencil = {0x45, 0x0F, 0xB7, 0xC9};
inline constexpr std::array<std::uint8_t, 10> kI32EqzStencil = {
    0x45, 0x85, 0xC9, 0x0F, 0x94, 0xC0, 0x44, 0x0F, 0xB6, 0xC8};
inline constexpr std::array<std::uint8_t, 6> kShiftPrefixStencil = {
    0x44, 0x89, 0xC9, 0x45, 0x89, 0xD9};
inline constexpr std::array<std::uint8_t, 3> kI32ShlStencil = {0x41, 0xD3, 0xE1};
inline constexpr std::array<std::uint8_t, 3> kI32ShrSStencil = {0x41, 0xD3, 0xF9};
inline constexpr std::array<std::uint8_t, 3> kI32ShrUStencil = {0x41, 0xD3, 0xE9};
inline constexpr std::array<std::uint8_t, 3> kI32ImulImm32Stencil = {0x45, 0x69, 0xC9};

inline constexpr std::array<std::uint8_t, 3> make_i32_imm32_stencil(std::uint8_t group) {
  return {0x41, 0x81, static_cast<std::uint8_t>(0xC1u | (group << 3u))};
}

inline constexpr std::array<std::uint8_t, 3> make_i32_imm8_stencil(std::uint8_t group) {
  return {0x41, 0x83, static_cast<std::uint8_t>(0xC1u | (group << 3u))};
}

inline constexpr std::array<std::uint8_t, 3> kI32AddImm32Stencil = make_i32_imm32_stencil(0);
inline constexpr std::array<std::uint8_t, 3> kI32OrImm32Stencil = make_i32_imm32_stencil(1);
inline constexpr std::array<std::uint8_t, 3> kI32AndImm32Stencil = make_i32_imm32_stencil(4);
inline constexpr std::array<std::uint8_t, 3> kI32SubImm32Stencil = make_i32_imm32_stencil(5);
inline constexpr std::array<std::uint8_t, 3> kI32XorImm32Stencil = make_i32_imm32_stencil(6);
inline constexpr std::array<std::uint8_t, 3> kI32AddImm8Stencil = make_i32_imm8_stencil(0);
inline constexpr std::array<std::uint8_t, 3> kI32OrImm8Stencil = make_i32_imm8_stencil(1);
inline constexpr std::array<std::uint8_t, 3> kI32AndImm8Stencil = make_i32_imm8_stencil(4);
inline constexpr std::array<std::uint8_t, 3> kI32SubImm8Stencil = make_i32_imm8_stencil(5);
inline constexpr std::array<std::uint8_t, 3> kI32XorImm8Stencil = make_i32_imm8_stencil(6);

inline constexpr std::array<std::uint8_t, 3> kI32ShlImm8Stencil = {0x41, 0xC1, 0xE1};
inline constexpr std::array<std::uint8_t, 3> kI32ShrSImm8Stencil = {0x41, 0xC1, 0xF9};
inline constexpr std::array<std::uint8_t, 3> kI32ShrUImm8Stencil = {0x41, 0xC1, 0xE9};
inline constexpr std::array<std::uint8_t, 15> kEntryStencil = {
    0x48, 0xB8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0xE9, 0x00, 0x00, 0x00, 0x00};
inline constexpr std::array<std::uint8_t, 12> kHelperTailStencil = {
    0x48, 0x8D, 0x05, 0x00, 0x00, 0x00, 0x00, 0xE9, 0x00, 0x00, 0x00, 0x00};
inline constexpr std::array<std::uint8_t, 4> kPublishNextPcStencil = {0x41, 0xC7, 0x45, 0x00};
inline constexpr std::array<std::uint8_t, 5> kChainDispatchStencil = {
    0xE9, 0x00, 0x00, 0x00, 0x00};
inline constexpr std::array<std::uint8_t, 5> kExitJumpStencil = {
    0xE9, 0x00, 0x00, 0x00, 0x00};

inline constexpr std::array<std::uint8_t, 10> make_compare_stencil(std::uint8_t condition) {
  return {0x45, 0x39, 0xCB, 0x0F, condition, 0xC0, 0x44, 0x0F, 0xB6, 0xC8};
}

inline constexpr auto kI32EqStencil = make_compare_stencil(0x94);
inline constexpr auto kI32NeStencil = make_compare_stencil(0x95);
inline constexpr auto kI32LtSStencil = make_compare_stencil(0x9C);
inline constexpr auto kI32LtUStencil = make_compare_stencil(0x92);
inline constexpr auto kI32GtSStencil = make_compare_stencil(0x9F);
inline constexpr auto kI32GtUStencil = make_compare_stencil(0x97);
inline constexpr auto kI32LeSStencil = make_compare_stencil(0x9E);
inline constexpr auto kI32LeUStencil = make_compare_stencil(0x96);
inline constexpr auto kI32GeSStencil = make_compare_stencil(0x9D);
inline constexpr auto kI32GeUStencil = make_compare_stencil(0x93);

}  // namespace fireball::pysim::jit

#endif
