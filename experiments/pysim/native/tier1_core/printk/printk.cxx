#include "printk.hxx"

#if defined(_WIN32)
#define FB_PRINTK_EXPORT __declspec(dllexport)
#else
#define FB_PRINTK_EXPORT __attribute__((visibility("default")))
#endif

extern "C" FB_PRINTK_EXPORT int fb_printk_base64(
    const fireball::printk_writer* writer, std::uint8_t* workspace,
    const std::uint8_t* data, std::size_t bytes, std::ptrdiff_t stride) {
  return fireball::printk_base64(*writer, std::span<std::uint8_t, 20>(workspace, 20),
                                data, bytes, stride);
}

extern "C" FB_PRINTK_EXPORT std::size_t fb_printk_event(
    const fireball::printk_writer* writer, std::uint8_t* workspace, std::uint8_t level,
    std::uint32_t event, std::uint32_t count, const std::uint32_t* arguments) {
  return fireball::printk_event(*writer, std::span<std::uint8_t, 20>(workspace, 20),
                               level, event, {arguments, count});
}

extern "C" FB_PRINTK_EXPORT int fb_printk_u64(
    const fireball::printk_writer* writer, const char* label, std::size_t label_bytes,
    std::uint64_t value) {
  return fireball::printk_u64(*writer, {label, label_bytes}, value);
}
