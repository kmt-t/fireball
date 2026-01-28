/**
 * @file jit_engine.hxx
 * @brief Low-level Copy-and-Patch Engine for Fireball
 *
 * Responsible for template replication and hole patching.
 * {JIT_CopyAndPatch} {JIT_RegisterMapping}
 */

#ifndef FIREBALL_JIT_JIT_ENGINE_HXX
#define FIREBALL_JIT_JIT_ENGINE_HXX

#include <fireball.hxx>
#include <cstdint>

namespace fireball::jit {

/**
 * @brief Patch types for hole-filling in native code templates.
 * Architecture-specific patches.
 */
enum class patch_type : std::uint8_t {
    RV32_LW_POOL,  ///< Load from literal pool
    RV32_ADDI_PC,  ///< Update WASM_PC register
    ARM_LDR_IMM,   ///< ARM immediate load
    X64_MOV_IMM,   ///< x64 immediate move
    // ... extend as needed for ARM/RV32/x64
};

/**
 * @brief Low-level engine for generating native code from templates.
 * This class handles the actual byte-copying and patching.
 */
class jit_engine {
public:
    /**
     * @brief Emit a WASM opcode by copying its template and applying patches.
     * @param dst Destination address in the code cache.
     * @param opcode WASM opcode to emit.
     * @param immediate Potential immediate value for the opcode.
     * @return size_t Number of bytes written to dst.
     */
    std::size_t emit_opcode(std::uint8_t* dst, std::uint8_t opcode, std::uint32_t immediate);

    /**
     * @brief Apply a specific patch to an existing instruction.
     * @param patch_addr Address of the instruction to be patched.
     * @param type The type of patch to apply.
     * @param value The value to patch in.
     */
    void apply_patch(void* patch_addr, patch_type type, std::int32_t value);
};

} // namespace fireball::jit

#endif // FIREBALL_JIT_JIT_ENGINE_HXX
