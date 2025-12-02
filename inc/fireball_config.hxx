#ifndef FIREBALL_CONFIG_HXX
#define FIREBALL_CONFIG_HXX

// 最大コルーチン数
// このマクロにより、スケジューラや関連するデータ構造のサイズが決定されます。
#define FIREBALL_MAX_COROUTINES         8       // デフォルト8個に固定

// 各ゲストモジュールに割り当てる最大ヒープサイズ（KB単位）
// co_memがこのサイズを元にm_spaceを初期化します。
#define FIREBALL_GUEST_HEAP_SIZE_KB     16      // 1ゲストあたり16KB

// コルーチンスタックのデフォルトサイズ（KB単位）
// 各コルーチンが確保するスタックメモリの上限を定義します。
#define FIREBALL_COROUTINE_STACK_SIZE_KB 8      // 1コルーチンあたり8KB

#endif // FIREBALL_CONFIG_HXX
