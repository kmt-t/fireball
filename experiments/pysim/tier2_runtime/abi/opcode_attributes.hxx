#ifndef FIREBALL_PYSIM_OPCODE_ATTRIBUTES_HXX
#define FIREBALL_PYSIM_OPCODE_ATTRIBUTES_HXX

#include <array>
#include <cstdint>

namespace fireball {

/** @brief One property of a WebAssembly opcode. Each bit of the attribute byte is one property. */
inline constexpr std::uint8_t kOpcodeBlockBoundary = 1u << 0;
/** @brief The opcode ends a trace at compile time. */
inline constexpr std::uint8_t kOpcodeTraceTerminator = 1u << 1;
/** @brief The Interpreter CPS executes the opcode after a trace ends before it (pending). */
inline constexpr std::uint8_t kOpcodeInterpreterAfterTrace = 1u << 2;
/** @brief A block that ends with the opcode is never a chain source. */
inline constexpr std::uint8_t kOpcodeNotChainable = 1u << 3;
/** @brief The opcode is a block body member even though it is a block boundary or a prefix. */
inline constexpr std::uint8_t kOpcodeAlwaysBlockBody = 1u << 4;

/** @brief Builds the attribute byte of each opcode. Each set is listed once. */
inline constexpr std::array<std::uint8_t, 256> make_opcode_attributes() {
  std::array<std::uint8_t, 256> attributes{};
  // Control instructions. BLOCK (0x02) is a block boundary and also a block body member.
  for (const auto opcode :
       {0x02u, 0x03u, 0x04u, 0x05u, 0x0Bu, 0x0Cu, 0x0Du, 0x0Eu, 0x0Fu, 0x10u, 0x11u}) {
    attributes[opcode] |= kOpcodeBlockBoundary;
  }
  for (const auto opcode : {0x00u, 0x04u, 0x0Cu, 0x0Du, 0x0Eu, 0x0Fu, 0x10u, 0x11u}) {
    attributes[opcode] |= kOpcodeTraceTerminator;
  }
  for (const auto opcode : {0x04u, 0x0Cu, 0x0Du, 0x10u, 0x11u}) {
    attributes[opcode] |= kOpcodeInterpreterAfterTrace;
  }
  for (const auto opcode : {0x04u, 0x0Cu, 0x0Du, 0x0Eu, 0x0Fu, 0x10u, 0x11u}) {
    attributes[opcode] |= kOpcodeNotChainable;
  }
  for (const auto opcode : {0x02u, 0xFCu}) {
    attributes[opcode] |= kOpcodeAlwaysBlockBody;
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
