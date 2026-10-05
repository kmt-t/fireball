#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>

#if !defined(__clang__) || __clang_major__ < 17
#error "Fireball requires Clang 17 or later"
#endif

namespace fireball {
// The platform owns the output endpoint; printk only borrows it for synchronous writes.
struct printk_writer {
  std::uintptr_t owner;
  std::size_t (*write)(std::uintptr_t, const std::uint8_t*, std::size_t);
};

inline std::size_t printk_write(const printk_writer& writer, std::span<const std::uint8_t> data) {
  return writer.write(writer.owner, data.data(), data.size());
}

inline std::size_t printk_event(const printk_writer& writer, std::span<std::uint8_t, 20> wire,
                               std::uint8_t level, std::uint32_t event,
                               std::span<const std::uint32_t> arguments) {
  wire[0] = level;
  for (std::size_t i = 0; i < 3; ++i) wire[i + 1] = event >> (8 * i);
  for (std::size_t i = 0; i < arguments.size(); ++i)
    for (std::size_t byte = 0; byte < 4; ++byte)
      wire[4 + 4 * i + byte] = arguments[i] >> (8 * byte);
  return printk_write(writer, wire.first(4 + 4 * arguments.size()));
}

inline bool printk_base64(const printk_writer& writer, std::span<std::uint8_t, 20> wire,
                          const std::uint8_t* data, std::size_t bytes, std::ptrdiff_t stride) {
  constexpr std::string_view alphabet =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::size_t used = 0;
  for (std::size_t i = 0; i < bytes;) {
    const auto remaining = bytes - i;
    const auto a = *data;
    const auto b = remaining > 1 ? data[stride] : 0;
    const auto c = remaining > 2 ? data[2 * stride] : 0;
    wire[used++] = alphabet[a >> 2];
    wire[used++] = alphabet[((a & 3) << 4) | (b >> 4)];
    wire[used++] = remaining > 1 ? alphabet[((b & 15) << 2) | (c >> 6)] : '=';
    wire[used++] = remaining > 2 ? alphabet[c & 63] : '=';
    const auto consumed = remaining < 3 ? remaining : 3;
    i += consumed;
    if (i < bytes) data += static_cast<std::ptrdiff_t>(consumed) * stride;
    if (used == wire.size() || i == bytes) {
      if (printk_write(writer, wire.first(used)) != used) return false;
      used = 0;
    }
  }
  wire[0] = '\n';
  return printk_write(writer, wire.first(1)) == 1;
}

// Numeric diagnostics are formatted where their state lives. Python sees only output bytes.
inline bool printk_u64(const printk_writer& writer, std::string_view label, std::uint64_t value) {
  std::array<std::uint8_t, 22> digits{};
  auto first = digits.size() - 1;
  digits[first] = '\n';
  do {
    digits[--first] = '0' + value % 10;
    value /= 10;
  } while (value != 0);
  digits[--first] = '=';
  return printk_write(writer, {reinterpret_cast<const std::uint8_t*>(label.data()), label.size()})
             == label.size() &&
         printk_write(writer, std::span(digits).subspan(first)) == digits.size() - first;
}
}  // namespace fireball
