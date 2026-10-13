#ifndef FIREBALL_PYSIM_EXECUTABLE_MEMORY_ABI_HXX
#define FIREBALL_PYSIM_EXECUTABLE_MEMORY_ABI_HXX
#include "jit_types.hxx"
#include <cstddef>
#include <cstdint>

#include "trace_compiler_abi.hxx"
namespace fireball {
/** @brief 実行領域の非所有記述子。寿命と解放責務は確保元が持つ。 */
struct executable_memory {
  std::uintptr_t base;
  code_offset_t bytes;
  std::uint32_t protection, patching;
};
/** @brief ホストの実行領域に必要なアラインメントを返す。 */
std::size_t executable_region_alignment();
/** @brief 呼び出し元が所有する整列済み領域を借用して書き込みを開始する。 */
int borrow_executable_region(executable_memory *memory, std::uint8_t *region, code_offset_t bytes);
} // namespace fireball
extern "C" {
/** @brief ホストから実行領域を確保して書き込みを開始する。 */
int fb_jit_memory_init(fireball::executable_memory *memory, code_offset_t bytes);
/** @brief 有効な領域の書き込みを開始する。入れ子の開始は契約違反である。 */
int fb_jit_memory_begin(fireball::executable_memory *memory);
/** @brief 書き込みを確定し、ホストで必要な命令キャッシュ同期を実行する。 */
int fb_jit_memory_commit(fireball::executable_memory *memory);
/** @brief 未確定の書き込みがあれば確定する。 */
int fb_jit_memory_finalize(fireball::executable_memory *memory);
/** @brief 現在の保護がRWXを含まない場合に1を返す。 */
int fb_jit_memory_no_rwx(const fireball::executable_memory *memory);
/** @brief 現在の保護がホストの実行領域方針に合う場合に1を返す。 */
int fb_jit_memory_valid_protection(const fireball::executable_memory *memory);
/** @brief 書き込み中の有効範囲へコードをコピーする。 */
int fb_jit_memory_write(fireball::executable_memory *memory, code_offset_t offset,
                        const std::uint8_t *source, code_offset_t count);
/** @brief 所有する実行領域を解放する。解放済みの領域はそのままとする。 */
int fb_jit_memory_close(fireball::executable_memory *memory);
/** @brief 指定範囲の実行アドレスを返す。未確定の書き込みは確定する。 */
std::uintptr_t fb_jit_memory_address(fireball::executable_memory *memory, code_offset_t offset);
}
#endif
