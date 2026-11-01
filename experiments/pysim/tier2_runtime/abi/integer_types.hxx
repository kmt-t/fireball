#ifndef FIREBALL_PYSIM_WASM_INTEGER_TYPES_HXX
#define FIREBALL_PYSIM_WASM_INTEGER_TYPES_HXX

#include <stdint.h>

typedef uint16_t block_index_t;
typedef uint16_t frame_word_offset_t;
typedef uint32_t annotation_offset_t;

typedef uint32_t wasm_pc_t;
typedef uint32_t code_ip_t;
typedef uint32_t code_offset_t;

typedef uint16_t function_index_t;
typedef uint16_t type_index_t;
typedef uint16_t global_index_t;
typedef uint16_t local_count_t;
typedef uint8_t operand_words_t;
typedef uint8_t frame_depth_t;
typedef uint8_t instruction_width_t;

typedef uint8_t jit_stack_words_t;
typedef uint8_t jit_body_count_t;
typedef uint32_t card_index_t;

enum {
  FIREBALL_INVALID_BLOCK_INDEX = UINT16_MAX,
  FIREBALL_INVALID_U8_VALUE = UINT8_MAX,
};

#endif
