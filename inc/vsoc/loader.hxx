#pragma once

#include <cstdint>
#include <span>
#include "vsoc.hxx" // For vsoc_status

namespace fireball::vsoc {

struct module_view; // Forward declaration

/**
 * @class wasm_loader
 * @brief Interface for WASM binary loader and parser.
 * @details Extracts ModuleView from ROM binary. {ROMParsing}
 */
class wasm_loader {
public:
    virtual ~wasm_loader() = default;

    /**
     * @brief Parses a WASM binary and creates a module view.
     * @param binary The binary data to parse.
     * @param view Output pointer to the created module view.
     * @return vsoc_status Result of the loading operation.
     */
    virtual vsoc_status load(std::span<const uint8_t> binary, module_view** view) = 0;

    /**
     * @brief Unloads a module and releases associated resources.
     * @param view The module view to unload.
     */
    virtual void unload(module_view* view) = 0;
};

} // namespace fireball::vsoc
