"""Semantic integer types shared by the PySIM WASM runtime layers."""

from __future__ import annotations

from typing import Final, NewType

block_index_t = NewType("block_index_t", int)
frame_word_offset_t = NewType("frame_word_offset_t", int)
annotation_offset_t = NewType("annotation_offset_t", int)

wasm_pc_t = NewType("wasm_pc_t", int)
code_ip_t = NewType("code_ip_t", int)
code_offset_t = NewType("code_offset_t", int)

function_index_t = NewType("function_index_t", int)
type_index_t = NewType("type_index_t", int)
global_index_t = NewType("global_index_t", int)
local_count_t = NewType("local_count_t", int)
operand_words_t = NewType("operand_words_t", int)
frame_depth_t = NewType("frame_depth_t", int)
instruction_width_t = NewType("instruction_width_t", int)

jit_stack_words_t = NewType("jit_stack_words_t", int)
jit_body_count_t = NewType("jit_body_count_t", int)
card_index_t = NewType("card_index_t", int)

INVALID_BLOCK_INDEX: Final[block_index_t] = block_index_t(0xFFFF)
INVALID_U8_VALUE: Final[int] = 0xFF
ZERO_OPERAND_WORDS: Final[operand_words_t] = operand_words_t(0)
