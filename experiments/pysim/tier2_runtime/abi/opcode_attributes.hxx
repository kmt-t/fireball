#ifndef FIREBALL_PYSIM_OPCODE_ATTRIBUTES_HXX
#define FIREBALL_PYSIM_OPCODE_ATTRIBUTES_HXX

#include <array>
#include <cstdint>

#include "wasm_opcodes.hxx"

namespace fireball {

/** @brief One property of a WebAssembly opcode. Each bit of the attribute byte is one property. */
inline constexpr std::uint8_t kOpcodeBlockBoundary = 1u << 0;
/** @brief The opcode ends a trace at compile time. */
inline constexpr std::uint8_t kOpcodeTraceTerminator = 1u << 1;
/** @brief The Interpreter CPS executes the opcode after a trace ends before it (pending). */
inline constexpr std::uint8_t kOpcodeInterpreterAfterTrace = 1u << 2;
/** @brief The successor of a block that ends with the opcode is decided at run time. */
inline constexpr std::uint8_t kOpcodeDynamicTarget = 1u << 3;
/** @brief The opcode is a block body member even though it is a block boundary or a prefix. */
inline constexpr std::uint8_t kOpcodeAlwaysBlockBody = 1u << 4;
/** @brief The opcode only delimits a block (block, loop, else, end) and emits no operation. */
inline constexpr std::uint8_t kOpcodeStructural = 1u << 5;
/** @brief The opcode carries one immediate operand that the score scan reads once. */
inline constexpr std::uint8_t kOpcodeScoreOperand = 1u << 6;
/** @brief The opcode is a linear memory load or store and carries two immediate operands. */
inline constexpr std::uint8_t kOpcodeMemoryAccess = 1u << 7;

/** @brief Builds the attribute byte of each opcode. Each set is listed once. */
inline constexpr std::array<std::uint8_t, 256> make_opcode_attributes() {
  std::array<std::uint8_t, 256> attributes{};
  // Control instructions. block is a block boundary and also a block body member.
  for (const auto opcode :
       {kBlock, kLoop, kIf, kElse, kEnd, kBr, kBrIf, kBrTable, kReturn, kCall, kCallIndirect}) {
    attributes[opcode] |= kOpcodeBlockBoundary;
  }
  for (const auto opcode :
       {kUnreachable, kIf, kBr, kBrIf, kBrTable, kReturn, kCall, kCallIndirect}) {
    attributes[opcode] |= kOpcodeTraceTerminator;
  }
  for (const auto opcode : {kIf, kBr, kBrIf, kCall, kCallIndirect}) {
    attributes[opcode] |= kOpcodeInterpreterAfterTrace;
  }
  for (const auto opcode : {kBrTable, kReturn}) {
    attributes[opcode] |= kOpcodeDynamicTarget;
  }
  for (const auto opcode : {kBlock, kFcPrefix}) {
    attributes[opcode] |= kOpcodeAlwaysBlockBody;
  }
  for (const auto opcode : {kBlock, kLoop, kElse, kEnd}) {
    attributes[opcode] |= kOpcodeStructural;
  }
  for (const auto opcode : {kBlock, kLoop, kCall, kLocalGet, kLocalSet, kLocalTee, kGlobalGet,
                            kGlobalSet, kI32Const, kI64Const, kMemorySize, kMemoryGrow}) {
    attributes[opcode] |= kOpcodeScoreOperand;
  }
  for (auto opcode = kI32Load; opcode <= kI64Store32; ++opcode) {
    attributes[opcode] |= kOpcodeMemoryAccess;
  }
  return attributes;
}

inline constexpr auto kOpcodeAttributes = make_opcode_attributes();

/** @brief Returns whether the opcode has the given property bit. */
inline constexpr bool opcode_has_attribute(std::uint8_t opcode, std::uint8_t attribute) {
  return (kOpcodeAttributes[opcode] & attribute) != 0;
}

/** @brief Returns whether the opcode belongs to a block body. Block boundaries are not body
 * members unless they are marked as always body members. */
inline constexpr bool opcode_is_block_body(std::uint8_t opcode) {
  return opcode_has_attribute(opcode, kOpcodeAlwaysBlockBody) ||
         !opcode_has_attribute(opcode, kOpcodeBlockBoundary);
}

} // namespace fireball

#endif
