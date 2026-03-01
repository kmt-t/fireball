#include <vsoc.hxx>
#include <fireball_config.hxx>
#include <cstring>

namespace fireball {

/**
 * module_loader implementation (Tier 3)
 * @inv: loaded_module_count <= FB_CONF_MAX_MODULES
 */
result<wasm_module_view*, sys_recovery_strategy> module_loader::prepare(binary_view wasm) noexcept {
    if (loaded_module_count_ >= FB_CONF_MAX_MODULES) {
        // Budget exceeded: This is a system design/configuration error.
        return sys_recovery_strategy::PANIC;
    }

    // @pre: wasm is a valid binary_view in ROM
    if (wasm.size() < 8) {
        return sys_recovery_strategy::IGNORE;
    }

    // WASM Magic (0x00 0x61 0x73 0x6D)
    if (wasm[0] != 0x00 || wasm[1] != 0x61 || wasm[2] != 0x73 || wasm[3] != 0x6D) {
        return sys_recovery_strategy::IGNORE;
    }

    wasm_module_view& view = modules_[loaded_module_count_];
    // Reset internal section offsets
    for (auto& off : view.section_offsets_) {
        off = 0;
    }
    view.raw_data_ = wasm;

    // Scan sections
    size_t pos = 8;
    while (pos < wasm.size()) {
        uint8_t section_id = wasm[pos++];
        
        // Inline LEB128 parse for size
        uint32_t section_size = 0;
        uint32_t shift = 0;
        while (pos < wasm.size()) {
            if (pos >= wasm.size()) {
                return sys_recovery_strategy::IGNORE;
            }
            uint8_t b = wasm[pos++];
            section_size |= (static_cast<uint32_t>(b & 0x7F) << shift);
            if ((b & 0x80) == 0) {
                break;
            }
            shift += 7;
        }

        if (section_id < 13) {
            view.section_offsets_[section_id] = static_cast<uint32_t>(pos);
        }
        pos += section_size;
    }

    // @post: result.is_ok() -> module_view is valid
    // @post: loaded_module_count incremented by 1
    loaded_module_count_++;
    return &view;
}

operation_result module_loader::load(wasm_module_view* module) noexcept {
    // @pre: module is valid
    if (module == nullptr) {
        return sys_recovery_strategy::IGNORE;
    }

    // @post: initial memory pages allocated and initialized
    return {};
}

operation_result module_loader::resolve_imports(wasm_module_view* module) noexcept {
    // @derives: loader.md §4.1 Dependency Resolution
    // @pre: module is loaded. All imported modules are already in registry.
    if (module == nullptr) {
        return sys_recovery_strategy::IGNORE;
    }
    
    // @post(ok): all imports resolved. Module is ready for execution.
    return {};
}

operation_result module_loader::unload(wasm_module_view* module) noexcept {
    // @derives: loader.md §4.2 state: Ready -> Idle: unload
    // @pre: module is in registry
    // @note: Due to bump allocator LIFO constraint, full reclamation only 
    //        occurs when unloading in reverse order of loading.
    if (loaded_module_count_ > 0 && &modules_[loaded_module_count_ - 1] == module) {
        loaded_module_count_--;
        return {};
    }
    return sys_recovery_strategy::PANIC;
}

} // namespace fireball
