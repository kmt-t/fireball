/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef __FIREBALL_HXX__
#define __FIREBALL_HXX__

#include <cstdint>
#include <cstddef>
#include <version>
#include <span>
#include <string_view>
#include <optional>
#include <variant>
#include <utility>
#include <concepts>
#include <type_traits>
#include <coroutine>
#include <source_location>

#include <fireball_config.hxx>

namespace fireball {

/**
 * @brief Common status codes for the system.
 * {Policy_Memory}
 */
enum class status_t : std::int32_t {
    ok = 0,               ///< Success
    error = 1,            ///< General error
    not_found = 2,        ///< Target not found
    permission_denied = 3, ///< Permission denied
    out_of_memory = 4,    ///< Out of memory
    invalid_argument = 5,  ///< Invalid argument
};

} // namespace fireball

#endif // #ifndef __FIREBALL_HXX__
